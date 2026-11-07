from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import uuid

from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_publication import PendingTaskPublisher, fetch_pending_task


def _serve_once(listener, publisher):
    connection, _ = listener.accept()
    with connection:
        publisher.serve_connection(connection)


def test_controller_task_reaches_authenticated_publication_socket(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    generation = str(uuid.uuid4())
    task = controller.create_task(
        base_sha="a" * 40,
        allowed_paths=["tools/developer_session_diagnostics.py", "tools/tests/test_developer_session_diagnostics.py"],
        provider="openai", model="gpt-5.3-codex", max_calls=1, max_cost_usd=1,
        deadline_seconds=120, goal="Bound a diagnostics warning.",
    )
    params = controller.snapshot(task)["parameters"]
    controller.record_approval_request(
        task, owner_id="owner", parameters_sha256=hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(), request_ref=str(uuid.uuid4()), guardian_generation=generation,
        expires_at=int(time.time()) + 60,
    )
    path = str(tmp_path / "development-task.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path); listener.listen(1)
    thread = threading.Thread(target=_serve_once, args=(listener, PendingTaskPublisher(controller, gateway_uid=os.getuid())))
    thread.start()
    envelope = fetch_pending_task(path)
    thread.join(); listener.close()
    assert envelope is not None
    assert envelope["task_id"] == task
    assert envelope["request_ref"] == controller.snapshot(task)["approval"]["request_ref"]
    assert envelope["parameters_sha256"] == hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    controller.close()


def test_modified_or_expired_controller_approval_is_not_published(tmp_path):
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    task = controller.create_task(
        base_sha="a" * 40, allowed_paths=["tools/example.py"], provider="openai",
        model="gpt-5.3-codex", max_calls=1, max_cost_usd=1, deadline_seconds=120,
        goal="Bound a diagnostics warning.",
    )
    params = controller.snapshot(task)["parameters"]
    controller.record_approval_request(
        task, owner_id="owner", parameters_sha256=hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(), request_ref=str(uuid.uuid4()), guardian_generation=str(uuid.uuid4()),
        expires_at=int(time.time()) - 1,
    )
    assert controller.pending_owner_task() is None
    controller.close()
