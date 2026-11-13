"""Fixed, separately provisioned draft-PR service; never runs as broker/worker."""

from __future__ import annotations

import json
import os
import pwd
import socket
import struct
from typing import Any

from tools.autonomy_broker import canonical, identifier
from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_integration import JOURNAL, _digest, _policy, publish
from tools.autonomy_broker_store import Records

SOCKET = "/run/jarvis-development-integration/integration.sock"
PUBLISHER_SOCKET = "/run/jarvis-autonomy-broker/development-task.sock"
MAX_CAPSULE = 8 * 1024 * 1024


def receive(connection: socket.socket, maximum: int) -> bytes:
    def exact(size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            part = connection.recv(size - len(result))
            if not part:
                raise LifecycleError("integration_frame_truncated")
            result.extend(part)
        return bytes(result)

    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= maximum:
        raise LifecycleError("integration_frame_size")
    return exact(size)


def fetch_proposal(task_id: str) -> dict[str, Any]:
    identifier(task_id)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(PUBLISHER_SOCKET)
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != 0:
            raise LifecycleError("integration_publisher_identity")
        raw = canonical({"version": 3, "view": "integration_export", "task_id": task_id})
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        response = json.loads(receive(connection, MAX_CAPSULE))
    proposal = response.get("proposal")
    if (
        response.get("status") != "ready"
        or not isinstance(proposal, dict)
        or proposal.get("task_id") != task_id
        or proposal.get("proposal_sha256")
        != _digest({k: v for k, v in proposal.items() if k != "proposal_sha256"})
    ):
        raise LifecycleError("integration_proposal_unavailable")
    return proposal


def serve_connection(connection: socket.socket, *, gateway_uid: int) -> None:
    try:
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != gateway_uid:
            raise LifecycleError("integration_peer_denied")
        connection.settimeout(3)
        body = json.loads(receive(connection, 65536))
        if isinstance(body, dict) and body.get("action") == "handoff_status":
            if (
                set(body) != {"version", "action", "task_id"}
                or type(body["version"]) is not int
                or body["version"] != 1
            ):
                raise LifecycleError("integration_status_schema")
            identifier(body["task_id"])
            _policy()
            result = {
                "handoff": Records(JOURNAL, owner=os.getuid(), check_parents=False).read(
                    "handoff", body["task_id"]
                )
            }
        elif not isinstance(body, dict) or body.get("action") != "create_development_pr":
            raise LifecycleError("integration_approval_schema")
        else:
            result = publish(body.get("task_id", ""), body)
        response = {"ok": True, "result": result}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, LifecycleError) else "integration_unavailable"
        # Only fixed code-like errors are exposed; never HTTP bodies/credentials.
        if not reason.startswith("integration_") or not reason.replace("_", "").isalnum():
            reason = "integration_unavailable"
        response = {"ok": False, "error": reason}
    raw = canonical(response)
    connection.settimeout(3)
    connection.sendall(struct.pack("!I", len(raw)) + raw)


def main() -> int:
    _policy()  # Non-root, non-worker identity; fixed root-owned policy only.
    if os.getenv("LISTEN_PID") != str(os.getpid()) or os.getenv("LISTEN_FDS") != "1":
        raise LifecycleError("integration_socket_activation_required")
    listener = socket.socket(fileno=3)
    if listener.family != socket.AF_UNIX or listener.getsockname() != SOCKET:
        raise LifecycleError("integration_wrong_socket")
    gateway_uid = pwd.getpwnam("aaron").pw_uid
    while True:
        connection, _ = listener.accept()
        with connection:
            try:
                serve_connection(connection, gateway_uid=gateway_uid)
            except OSError:
                pass
