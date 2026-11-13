"""Real Unix peer/framing boundaries; disposable integration journal only."""

import json
import os
import socket
import struct
import threading
import uuid

import pytest

from tools.autonomy_broker import canonical
from tools.autonomy_broker_store import Records
from tools.supervised_development_controller import LifecycleError, SupervisedDevelopmentController
from tools.supervised_development_publication import PendingTaskPublisher
from tools import supervised_development_integration_service as service


def exchange(body, *, gateway_uid=None, raw=None):
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    errors = []

    def run():
        with server:
            try:
                service.serve_connection(
                    server, gateway_uid=os.getuid() if gateway_uid is None else gateway_uid
                )
            except BaseException as exc:
                errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    with client:
        client.settimeout(4)
        message = canonical(body)
        frame = raw if raw is not None else struct.pack("!I", len(message)) + message
        if frame:
            client.sendall(frame)
            client.shutdown(socket.SHUT_WR)
        response = json.loads(service.receive(client, 65536))
    thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    return response


def test_actual_unix_peer_denied_before_any_handoff(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "publish", lambda *args: calls.append(args))
    result = exchange(
        {"action": "create_development_pr", "task_id": str(uuid.uuid4())},
        gateway_uid=os.getuid() + 1,
        raw=b"",  # Peer denial happens before the server reads any request.
    )
    assert result == {"ok": False, "error": "integration_peer_denied"}
    assert not calls


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"action": "merge"},
        {"action": "handoff_status", "version": True, "task_id": str(uuid.uuid4())},
        {
            "action": "handoff_status",
            "version": 1,
            "task_id": str(uuid.uuid4()),
            "database": "/arbitrary",
        },
    ],
)
def test_malformed_commands_never_publish(body, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "publish", lambda *args: calls.append(args))
    result = exchange(body)
    assert result["ok"] is False
    assert result["error"] in {"integration_approval_schema", "integration_status_schema"}
    assert not calls


@pytest.mark.parametrize(
    "raw,reason",
    [
        (struct.pack("!I", 0), "integration_frame_size"),
        (struct.pack("!I", 65537), "integration_frame_size"),
        (b"\x00\x00", "integration_frame_truncated"),
        (struct.pack("!I", 10) + b"{}", "integration_frame_truncated"),
    ],
)
def test_frame_failure_is_explicit_without_side_effects(raw, reason, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "publish", lambda *args: calls.append(args))
    assert exchange({}, raw=raw) == {"ok": False, "error": reason}
    assert not calls


def test_handoff_status_reads_only_fixed_own_journal(tmp_path, monkeypatch):
    journal = tmp_path / "integration"
    journal.mkdir(mode=0o700)
    task_id = str(uuid.uuid4())
    record = {
        "status": "PR_REVIEW_REQUIRED",
        "phase": "complete",
        "pr_url": "https://example.invalid/pull/1",
    }
    Records(journal, owner=os.getuid(), check_parents=False).write("handoff", task_id, record)
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in journal.iterdir()
    }
    policy_checks = []
    monkeypatch.setattr(service, "JOURNAL", journal)
    monkeypatch.setattr(service, "_policy", lambda: policy_checks.append(True))
    monkeypatch.setattr(service, "publish", lambda *_: pytest.fail("status must not publish"))
    response = exchange({"version": 1, "action": "handoff_status", "task_id": task_id})
    assert response == {"ok": True, "result": {"handoff": record}}
    assert policy_checks == [True]
    assert before == {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in journal.iterdir()
    }
    absent = exchange({"version": 1, "action": "handoff_status", "task_id": str(uuid.uuid4())})
    assert absent == {"ok": True, "result": {"handoff": None}}
    assert len(list(journal.iterdir())) == len(before)


def test_handoff_status_rejects_symlink_record_without_following(tmp_path, monkeypatch):
    journal = tmp_path / "integration"
    journal.mkdir(mode=0o700)
    task_id = str(uuid.uuid4())
    outside = tmp_path / "private-fixture"
    outside.write_text("not-a-public-record")
    (journal / f"handoff-{task_id}.json").symlink_to(outside)
    monkeypatch.setattr(service, "JOURNAL", journal)
    monkeypatch.setattr(service, "_policy", lambda: {})
    result = exchange({"version": 1, "action": "handoff_status", "task_id": task_id})
    assert result == {"ok": False, "error": "integration_unavailable"}
    assert "not-a-public-record" not in json.dumps(result)
    assert outside.read_text() == "not-a-public-record"


def test_unprovisioned_policy_blocks_status(monkeypatch):
    def blocked():
        raise LifecycleError("integration_authority_unprovisioned")

    monkeypatch.setattr(service, "_policy", blocked)
    result = exchange({"version": 1, "action": "handoff_status", "task_id": str(uuid.uuid4())})
    assert result == {"ok": False, "error": "integration_authority_unprovisioned"}


def test_untrusted_error_text_is_not_returned_to_gateway(monkeypatch):
    def blocked(*_):
        raise LifecycleError("private body or credentials must not leave the service")

    monkeypatch.setattr(service, "publish", blocked)
    result = exchange({"action": "create_development_pr", "task_id": str(uuid.uuid4())})
    assert result == {"ok": False, "error": "integration_unavailable"}


def test_publisher_export_not_available_to_gateway_uid(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.db")
    publisher = PendingTaskPublisher(controller, gateway_uid=os.getuid())
    publisher.integration_uid = os.getuid() + 1
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    body = canonical({"version": 3, "view": "integration_export", "task_id": str(uuid.uuid4())})
    try:
        client.sendall(struct.pack("!I", len(body)) + body)
        with pytest.raises(PermissionError, match="publication_peer_denied"):
            publisher.serve_connection(server)
        assert controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    finally:
        server.close()
        client.close()
        controller.close()


def test_integration_uid_cannot_query_other_publication_views(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.db")
    publisher = PendingTaskPublisher(controller, gateway_uid=os.getuid() + 1)
    publisher.integration_uid = os.getuid()
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    body = canonical({"version": 2})
    try:
        client.sendall(struct.pack("!I", len(body)) + body)
        with pytest.raises(PermissionError, match="publication_peer_denied"):
            publisher.serve_connection(server)
    finally:
        server.close()
        client.close()
        controller.close()


def test_export_client_rejects_real_nonroot_publisher_peer(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip("non-root fixture demonstrates root-only publisher authentication")
    path = str(tmp_path / "publisher.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    monkeypatch.setattr(service, "PUBLISHER_SOCKET", path)
    try:
        with pytest.raises(LifecycleError, match="integration_publisher_identity"):
            service.fetch_proposal(str(uuid.uuid4()))
        connection, _ = listener.accept()
        with connection:
            assert connection.recv(1) == b""  # No task identifier sent to an untrusted endpoint.
    finally:
        listener.close()
