from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid

import pytest

from tools.supervised_development_controller import LifecycleError, SupervisedDevelopmentController
from tools.supervised_development_publication import (
    CONTROLLER_DATABASE as PUBLICATION_DATABASE,
    PendingTaskPublisher,
    fetch_pending_task,
)
from tools.supervised_development_runtime import CONTROLLER_DATABASE as RUNTIME_DATABASE
from tools import supervised_development_runtime as runtime


def _serve_once(listener, publisher):
    connection, _ = listener.accept()
    with connection:
        try:
            publisher.serve_connection(connection)
        except (PermissionError, OSError, ValueError):
            pass


def test_controller_task_reaches_authenticated_publication_socket(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    generation = str(uuid.uuid4())
    task = controller.create_task(
        base_sha="a" * 40,
        allowed_paths=[
            "tools/developer_session_diagnostics.py",
            "tools/tests/test_developer_session_diagnostics.py",
        ],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1,
        deadline_seconds=120,
        goal="Bound a diagnostics warning.",
    )
    params = controller.snapshot(task)["parameters"]
    controller.record_approval_request(
        task,
        owner_id="owner",
        parameters_sha256=hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        request_ref=str(uuid.uuid4()),
        guardian_generation=generation,
        expires_at=int(time.time()) + 60,
    )
    path = str(tmp_path / "development-task.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    thread = threading.Thread(
        target=_serve_once,
        args=(listener, PendingTaskPublisher(controller, gateway_uid=os.getuid())),
    )
    thread.start()
    envelope = fetch_pending_task(path)
    thread.join()
    listener.close()
    assert envelope is not None
    assert envelope["task_id"] == task
    assert envelope["request_ref"] == controller.snapshot(task)["approval"]["request_ref"]
    assert (
        envelope["parameters_sha256"]
        == hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    controller.close()


def test_modified_or_expired_controller_approval_is_not_published(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task = controller.create_task(
        base_sha="a" * 40,
        allowed_paths=["tools/example.py"],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1,
        deadline_seconds=120,
        goal="Bound a diagnostics warning.",
    )
    params = controller.snapshot(task)["parameters"]
    controller.record_approval_request(
        task,
        owner_id="owner",
        parameters_sha256=hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        request_ref=str(uuid.uuid4()),
        guardian_generation=str(uuid.uuid4()),
        expires_at=int(time.time()) - 1,
    )
    assert controller.pending_owner_task() is None
    controller.close()


def test_publication_rejects_wrong_peer_without_controller_mutation(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    path = str(tmp_path / "development-task.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    thread = threading.Thread(
        target=_serve_once,
        args=(listener, PendingTaskPublisher(controller, gateway_uid=os.getuid() + 1)),
    )
    thread.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        connection.sendall(struct.pack("!I", len(b'{"version":1}')) + b'{"version":1}')
        try:
            assert connection.recv(1024) == b""
        except ConnectionResetError:
            pass
    thread.join()
    listener.close()
    assert controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    controller.close()


def test_read_only_publisher_does_not_create_or_modify_database(tmp_path):
    database = tmp_path / "controller.sqlite3"
    controller = SupervisedDevelopmentController(database)
    controller.close()
    before = database.stat()
    reader = SupervisedDevelopmentController(database, read_only=True)
    assert reader.pending_owner_task() is None
    reader.close()
    after = database.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )


def test_malformed_publication_request_returns_no_task(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    path = str(tmp_path / "development-task.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    thread = threading.Thread(
        target=_serve_once,
        args=(listener, PendingTaskPublisher(controller, gateway_uid=os.getuid())),
    )
    thread.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        body = b'{"version":3}'
        connection.sendall(struct.pack("!I", len(body)) + body)
        try:
            assert connection.recv(4) == b""
        except ConnectionResetError:
            pass
    thread.join()
    listener.close()
    controller.close()


def test_read_only_publisher_rejects_damaged_database(tmp_path):
    database = tmp_path / "damaged.sqlite3"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(LifecycleError, match="controller_schema_invalid"):
        SupervisedDevelopmentController(database, read_only=True)


@pytest.mark.parametrize("database_kind", ["missing", "damaged", "corrupt-bytes"])
def test_unavailable_database_is_not_reported_as_empty_and_survives_requests(
    tmp_path, database_kind
):
    database = tmp_path / "controller.sqlite3"
    if database_kind == "damaged":
        import sqlite3

        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
    elif database_kind == "corrupt-bytes":
        database.write_bytes(b"not a sqlite database and not a valid header")
    publisher = PendingTaskPublisher.__new__(PendingTaskPublisher)
    publisher.database = database
    publisher.gateway_uid = os.getuid()
    for _ in range(2):
        left, right = socket.socketpair()
        thread = threading.Thread(target=publisher.serve_connection, args=(right,))
        thread.start()
        left.sendall(struct.pack("!I", len(b'{"version":1}')) + b'{"version":1}')
        header = left.recv(4)
        size = struct.unpack("!I", header)[0]
        assert json.loads(left.recv(size)) == {
            "version": 1,
            "status": "unavailable",
            "reason": "controller_unavailable",
        }
        left.close()
        thread.join()


def test_production_components_share_one_fixed_database_path():
    assert PUBLICATION_DATABASE == RUNTIME_DATABASE


def test_owner_authenticated_initialization_creates_only_empty_schema(tmp_path, monkeypatch):
    database = tmp_path / "work" / "supervised-development.sqlite3"
    database.parent.mkdir(mode=0o700)
    monkeypatch.setattr(runtime, "CONTROLLER_DATABASE", database)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.os, "chown", lambda *_args: None)
    monkeypatch.setattr(runtime, "guardian_status", lambda: {"state": "STOPPED", "trusted": True})
    monkeypatch.setenv("SUDO_USER", "aaron")
    monkeypatch.setenv("SUDO_UID", "1000")
    runtime.initialize_controller_database(
        {"operator_uid": 1000, "autonomy_uid": 998, "autonomy_gid": 998}
    )
    reader = SupervisedDevelopmentController(database, read_only=True)
    assert reader.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    reader.close()
    assert database.stat().st_mode & 0o777 == 0o660
