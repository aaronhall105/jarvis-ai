"""Read-only status over real Unix sockets; every database is disposable."""

import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_publication import PendingTaskPublisher


@pytest.fixture(autouse=True)
def disposable_product_paths(tmp_path, monkeypatch):
    from tools import supervised_development_product as product
    from tools import supervised_development_runtime as runtime
    from tools import autonomy_broker_runtime, autonomy_broker_host

    monkeypatch.setattr(product, "READINESS_RECEIPT", tmp_path / "readiness.json")
    monkeypatch.setattr(product, "ADMISSION_DATABASE", tmp_path / "admission.sqlite3")
    monkeypatch.setattr(product, "OPERATOR_DATABASE", tmp_path / "operator.sqlite3")
    monkeypatch.setattr(runtime, "WORKTREE_ROOT", tmp_path / "worktrees")
    monkeypatch.setattr(autonomy_broker_host, "CANONICAL", tmp_path / "source")
    monkeypatch.setattr(
        autonomy_broker_runtime, "installed_policy", lambda: {"autonomy_uid": os.getuid()}
    )


def exchange(controller, version=2):
    publisher = PendingTaskPublisher(controller, gateway_uid=os.getuid())
    left, right = socket.socketpair()
    errors = []

    def serve():
        try:
            with right:
                publisher.serve_connection(right)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    with left:
        left.settimeout(3)
        raw = json.dumps({"version": version}).encode()
        left.sendall(struct.pack("!I", len(raw)) + raw)
        length = struct.unpack("!I", left.recv(4))[0]
        data = bytearray()
        while len(data) < length:
            data.extend(left.recv(length - len(data)))
    thread.join(3)
    assert not thread.is_alive()
    assert not errors
    return json.loads(data)


def task(controller, *, expires=None):
    task_id = controller.create_task(
        base_sha="a" * 40,
        allowed_paths=["bridge/app/ai_engine.py", "bridge/tests/test_executive_agent.py"],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1,
        deadline_seconds=120,
        goal="Reduce latency safely.",
    )
    params = controller.snapshot(task_id)["parameters"]
    controller.record_approval_request(
        task_id,
        owner_id="owner",
        request_ref=str(uuid.uuid4()),
        parameters_sha256=hashlib.sha256(
            json.dumps(params, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        guardian_generation=str(uuid.uuid4()),
        expires_at=expires or int(time.time()) + 60,
    )
    return task_id


def test_pending_status_keeps_exact_signed_envelope_and_database_unchanged(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller)
    before = controller.snapshot(task_id)
    first = exchange(controller)
    assert first["status"] == "approval_requested"
    assert first["task_id"] == task_id
    assert first["development_task"] == exchange(controller, 1)
    assert set(first) == {
        "version",
        "status",
        "task_id",
        "lifecycle_state",
        "provider_calls",
        "development_task",
        "candidate_id",
        "approval_accepted",
        "proposal_available",
    }
    assert first["candidate_id"] is None
    assert first["approval_accepted"] is False
    assert first["proposal_available"] is False
    assert controller.snapshot(task_id) == before
    controller.close()
    # Reconnect and process restart do not alter task identity or authority.
    reader = SupervisedDevelopmentController(tmp_path / "controller.sqlite3", read_only=True)
    assert exchange(reader) == first
    reader.close()


@pytest.mark.parametrize(
    "state,status",
    [
        ("PROPOSED", "pending"),
        ("RUNNING", "running"),
        ("REVIEW_PENDING", "review_pending"),
        ("REVIEW_REQUIRED", "review_required"),
        ("FAILED", "failed"),
        ("RECONCILIATION_REQUIRED", "blocked"),
    ],
)
def test_durable_nonapproval_states_never_expose_an_approval(tmp_path, state, status):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller)
    controller.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task_id))
    value = exchange(controller)
    assert value["status"] == status
    assert value["lifecycle_state"] == state
    assert value["development_task"] is None
    assert "approved" not in value.values()
    controller.close()


def test_expired_and_retired_tasks_are_not_idle_or_authorizable(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller, expires=int(time.time()) - 1)
    before = controller.snapshot(task_id)
    assert exchange(controller)["status"] == "expired"
    assert exchange(controller)["development_task"] is None
    assert controller.snapshot(task_id) == before
    controller.expire_stale_approval_requests()
    assert exchange(controller)["status"] == "expired"
    assert controller.snapshot(task_id)["state"] == "FAILED"
    controller.close()


def test_idle_requires_successful_empty_database_read(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    before = list(controller.db.iterdump())
    result = exchange(controller)
    assert result["status"] == "idle"
    assert result["candidate_id"] is None
    assert result["approval_accepted"] is False
    assert result["proposal_available"] is False
    assert exchange(controller, 1) == {"version": 1, "status": "none"}
    assert list(controller.db.iterdump()) == before
    controller.close()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "malformed-record", "unknown-state"])
def test_damage_is_unavailable_not_idle_and_never_crashes_publisher(tmp_path, damage):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller)
    if damage == "malformed-record":
        controller.db.execute("UPDATE tasks SET approval='[]' WHERE id=?", (task_id,))
    elif damage == "unknown-state":
        controller.db.execute("UPDATE tasks SET state='APPROVED' WHERE id=?", (task_id,))
    controller.close()
    if damage == "missing":
        controller.database.unlink()
    elif damage == "corrupt":
        controller.database.write_bytes(b"not a SQLite database")
    before = controller.database.read_bytes() if controller.database.exists() else None
    for _ in range(2):
        assert exchange(controller) == {
            "version": 2,
            "status": "unavailable",
            "reason": "controller_unavailable",
        }
    after = controller.database.read_bytes() if controller.database.exists() else None
    assert after == before


def test_altered_approval_and_multiple_tasks_fail_closed(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller)
    approval = controller.snapshot(task_id)["approval"]
    approval["parameters_sha256"] = "b" * 64
    controller.db.execute("UPDATE tasks SET approval=? WHERE id=?", (json.dumps(approval), task_id))
    assert exchange(controller)["reason"] == "invalid_approval_request"
    task(controller)
    assert exchange(controller)["reason"] == "multiple_active_tasks"
    assert exchange(controller)["development_task"] is None
    controller.close()


@pytest.mark.skipif(
    os.environ.get("JARVIS_PUBLICATION_DOCKER_TEST") != "1",
    reason="opt-in disposable root-publisher process with real host peer credentials",
)
def test_root_publisher_process_reaches_authenticated_gateway(tmp_path, monkeypatch):
    """No admission stubs: exercise the actual publisher and installed-style client.

    Root is restricted to a disposable socket directory and read-only source/DB;
    the container has no network, capabilities, credentials or Docker socket.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace

    from developer_gateway import autonomy_api, pending_task_client

    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task_id = task(controller)
    expected = controller.pending_owner_task()
    # DELETE-mode is a supported SQLite fixture. WAL readers have separate
    # existing production-boundary tests; this exercises actual UID transport.
    controller.db.execute("PRAGMA journal_mode=DELETE")
    controller.close()
    controller.database.chmod(0o644)
    socket_dir = tmp_path / "ipc"
    socket_dir.mkdir(mode=0o777)
    socket_dir.chmod(0o777)
    path = socket_dir / "publication.sock"
    name = "jarvis-status-fixture-" + uuid.uuid4().hex
    source = Path(__file__).resolve().parents[2]
    code = (
        "import os,socket; from pathlib import Path; "
        "from tools.supervised_development_publication import serve_listener; "
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
        "s.bind('/ipc/publication.sock'); os.chmod('/ipc/publication.sock',0o666); "
        "s.listen(4); serve_listener(s,database=Path('/controller.sqlite3'),"
        f"gateway_uid={os.getuid()})"
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=0:0",
        "--mount",
        f"type=bind,source={source},target=/src,readonly",
        "--mount",
        f"type=bind,source={controller.database},target=/controller.sqlite3,readonly",
        "--mount",
        f"type=bind,source={socket_dir},target=/ipc",
        "--workdir=/src",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "python:3.12-slim",
        "python",
        "-c",
        code,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert path.exists(), "disposable publisher did not start"
        monkeypatch.setattr(pending_task_client, "SOCKET", str(path))
        state = pending_task_client.fetch_lifecycle()
        assert state["task_id"] == task_id
        assert state["development_task"] == expected
        monkeypatch.setattr(
            autonomy_api, "guardian_status", lambda: {"state": "STOPPED", "trusted": True}
        )
        app = FastAPI()
        app.include_router(
            autonomy_api.router(
                lambda: SimpleNamespace(snapshot=lambda guardian: {"status": guardian["state"]}),
                lambda authorization: authorization == "Bearer fixture",
            )
        )
        with TestClient(app) as client:
            assert client.get("/api/autonomy").status_code == 401
            result = client.get("/api/autonomy", headers={"Authorization": "Bearer fixture"}).json()
        assert result["development_task"] == expected
        assert result["development_status"]["status"] == "approval_requested"
        assert result["status"] == "STOPPED"
    finally:
        subprocess.run(
            ["docker", "stop", "--time=1", name], capture_output=True, timeout=10, check=False
        )
        process.communicate(timeout=10)
