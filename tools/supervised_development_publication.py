"""Fixed local publication of the controller's pending owner-review task."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

from tools.supervised_development_controller import SupervisedDevelopmentController

SOCKET = "/run/jarvis-autonomy-broker/development-task.sock"
MAX_FRAME = 16_384


def _frame(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(raw) > MAX_FRAME:
        raise ValueError("publication_frame_too_large")
    return struct.pack("!I", len(raw)) + raw


class PendingTaskPublisher:
    def __init__(self, controller: SupervisedDevelopmentController, *, gateway_uid: int):
        self.database = controller.database
        self.gateway_uid = gateway_uid

    def serve_connection(self, connection: socket.socket) -> None:
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != self.gateway_uid:
            raise PermissionError("publication_peer_denied")
        header = connection.recv(4)
        if len(header) != 4:
            raise ValueError("publication_request_truncated")
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > MAX_FRAME:
            raise ValueError("publication_request_size")
        body = json.loads(_receive(connection, size))
        if body != {"version": 1}:
            raise ValueError("publication_request_schema")
        reader = SupervisedDevelopmentController(self.database)
        try:
            envelope = reader.pending_owner_task()
        finally:
            reader.close()
        connection.sendall(_frame(envelope or {"version": 1, "status": "none"}))


def fetch_pending_task(path: str = SOCKET) -> dict[str, Any] | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(path)
        connection.sendall(_frame({"version": 1}))
        header = _receive(connection, 4)
        if len(header) != 4:
            raise ValueError("publication_response_truncated")
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > MAX_FRAME:
            raise ValueError("publication_response_size")
        body = json.loads(_receive(connection, size))
    if body.get("status") == "none":
        return None
    required = {
        "version", "action", "goal", "task_id", "request_ref", "source_sha",
        "parameters_sha256", "allowed_paths", "provider", "model", "max_calls",
        "max_cost_usd", "deadline_seconds", "expires_at", "guardian_generation",
    }
    if set(body) != required or body.get("action") != "approve_development":
        raise ValueError("publication_envelope_schema")
    return body


def _receive(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise ValueError("publication_response_truncated")
        result.extend(chunk)
    return bytes(result)
