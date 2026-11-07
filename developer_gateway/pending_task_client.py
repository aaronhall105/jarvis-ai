"""Packaged, read-only client for the root-owned pending-task socket."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


SOCKET = "/run/jarvis-autonomy-broker/development-task.sock"
MAX_FRAME = 16_384
REQUIRED = {
    "version",
    "action",
    "goal",
    "task_id",
    "request_ref",
    "source_sha",
    "parameters_sha256",
    "allowed_paths",
    "provider",
    "model",
    "max_calls",
    "max_cost_usd",
    "deadline_seconds",
    "expires_at",
    "guardian_generation",
}


def _receive(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = connection.recv(size - len(output))
        if not chunk:
            raise ValueError("publication_response_truncated")
        output.extend(chunk)
    return bytes(output)


def fetch_pending_task() -> dict[str, Any] | None:
    """Fetch one authenticated envelope; no database or task path is accepted."""
    raw = b'{"version":1}'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(SOCKET)
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        header = _receive(connection, 4)
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > MAX_FRAME:
            raise ValueError("publication_response_size")
        body = json.loads(_receive(connection, size))
    if not isinstance(body, dict):
        raise ValueError("publication_response_schema")
    if body.get("status") == "none":
        return None
    if set(body) != REQUIRED or body.get("action") != "approve_development":
        raise ValueError("publication_envelope_schema")
    return body
