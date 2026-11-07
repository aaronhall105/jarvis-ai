"""Fixed local publication of the controller's pending owner-review task."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import struct
from pathlib import Path
from typing import Any

from tools.supervised_development_controller import (
    CONTROLLER_DATABASE,
    LifecycleError,
    SupervisedDevelopmentController,
)

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
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
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
        try:
            reader = SupervisedDevelopmentController(self.database, read_only=True)
            try:
                envelope = reader.pending_owner_task()
            finally:
                reader.close()
        except (LifecycleError, sqlite3.DatabaseError):
            # Unavailable or damaged state is never equivalent to no task.
            connection.sendall(
                _frame({"version": 1, "status": "unavailable", "reason": "controller_unavailable"})
            )
            return
        connection.sendall(_frame(envelope or {"version": 1, "status": "none"}))


def serve_listener(listener: socket.socket, *, database: Path, gateway_uid: int) -> None:
    """Serve the fixed publication protocol on an already systemd-owned socket."""
    # The publication process is strictly read-only; it must never create,
    # migrate or chmod the controller database.
    publisher = PendingTaskPublisher.__new__(PendingTaskPublisher)
    publisher.database = Path(database)
    publisher.gateway_uid = gateway_uid

    try:
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    publisher.serve_connection(connection)
                except (
                    OSError,
                    PermissionError,
                    ValueError,
                    LifecycleError,
                    sqlite3.DatabaseError,
                    json.JSONDecodeError,
                ):
                    # A malformed or unauthorised request receives no task and
                    # never changes controller state. The next connection is
                    # independent; details are intentionally not disclosed.
                    try:
                        connection.sendall(_frame({"version": 1, "status": "none"}))
                    except OSError:
                        pass
    finally:
        listener.close()


def serve_activation() -> None:
    """Run only under systemd socket activation with fixed production paths."""
    if os.getenv("LISTEN_PID") != str(os.getpid()) or os.getenv("LISTEN_FDS") != "1":
        raise RuntimeError("protected_socket_activation_required")
    listener = socket.socket(fileno=3)
    if listener.family != socket.AF_UNIX or listener.getsockname() != SOCKET:
        raise RuntimeError("wrong_publication_socket")
    # The installed broker policy pins operator_uid; the service has no
    # caller-selectable database or peer identity. Runtime imports this helper
    # only after validating the protected policy.
    from tools.autonomy_broker_runtime import installed_policy

    policy = installed_policy()
    serve_listener(listener, database=CONTROLLER_DATABASE, gateway_uid=int(policy["operator_uid"]))


def main() -> int:
    serve_activation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


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
