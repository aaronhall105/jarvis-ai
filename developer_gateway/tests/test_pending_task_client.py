from __future__ import annotations

import json
import socket
import struct
import threading

from developer_gateway import autonomy_host
from developer_gateway import pending_task_client


def _server(path, response, ready):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    ready.set()
    connection, _ = listener.accept()
    with connection:
        header = connection.recv(4)
        size = struct.unpack("!I", header)[0]
        assert connection.recv(size) == b'{"version":1}'
        raw = json.dumps(response, separators=(",", ":")).encode()
        connection.sendall(struct.pack("!I", len(raw)) + raw)
    listener.close()


def test_installed_style_client_reads_fixed_envelope(tmp_path, monkeypatch):
    path = str(tmp_path / "development-task.sock")
    envelope = {
        "version": 1,
        "action": "approve_development",
        "goal": "Bound diagnostics",
        "task_id": "task",
        "request_ref": "request",
        "source_sha": "a" * 40,
        "parameters_sha256": "b" * 64,
        "allowed_paths": ["tools/example.py"],
        "provider": "openai",
        "model": "gpt-5.3-codex",
        "max_calls": 1,
        "max_cost_usd": 1.0,
        "deadline_seconds": 120,
        "expires_at": 2_000_000_000,
        "guardian_generation": "generation",
    }
    monkeypatch.setattr(pending_task_client, "SOCKET", path)
    ready = threading.Event()
    thread = threading.Thread(target=_server, args=(path, envelope, ready))
    thread.start()
    ready.wait(1)
    assert pending_task_client.fetch_pending_task() == envelope
    thread.join()


def test_gateway_reports_publisher_unavailable(monkeypatch):
    monkeypatch.setattr(
        pending_task_client,
        "fetch_pending_task",
        lambda: (_ for _ in ()).throw(OSError("missing")),
    )
    assert autonomy_host.pending_development_task() == {
        "status": "unavailable",
        "reason": "publisher_unavailable",
    }


def test_expired_development_approval_is_rejected_before_socket_use(monkeypatch):
    monkeypatch.setattr(autonomy_host.time, "time", lambda: 2_000)
    result = autonomy_host.relay_operator(
        {
            "action": "approve_development",
            "expires_at": 1_999,
        }
    )
    assert result == {"ok": False, "category": "approval_expired"}
