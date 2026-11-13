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


def _fetch(version: int) -> dict[str, Any]:
    raw = json.dumps({"version": version}, separators=(",", ":")).encode("ascii")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(SOCKET)
        if version == 2:
            _, peer_uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if peer_uid != 0:
                raise PermissionError("untrusted_publication_peer")
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        header = _receive(connection, 4)
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > MAX_FRAME:
            raise ValueError("publication_response_size")
        body = json.loads(_receive(connection, size))
    if not isinstance(body, dict):
        raise ValueError("publication_response_schema")
    return body


def fetch_pending_task() -> dict[str, Any] | None:
    """Legacy envelope-only client; no database or task path is accepted."""
    body = _fetch(1)
    if body.get("status") == "none":
        return None
    if set(body) != REQUIRED or body.get("action") != "approve_development":
        raise ValueError("publication_envelope_schema")
    return body


def fetch_lifecycle() -> dict[str, Any]:
    """Read protected lifecycle state and its separate unsigned review envelope."""
    body = _fetch(2)
    if body == {"version": 2, "status": "unavailable", "reason": "controller_unavailable"}:
        return {
            "status": "unavailable",
            "reason": "controller_unavailable",
            "development_task": None,
        }
    required = {
        "version",
        "status",
        "task_id",
        "lifecycle_state",
        "provider_calls",
        "development_task",
    }
    statuses = {
        "idle",
        "pending",
        "approval_requested",
        "expired",
        "blocked",
        "running",
        "review_pending",
        "review_required",
        "failed",
    }
    if (
        not required <= set(body)
        or set(body)
        - required
        - {"reason", "candidate_id", "approval_accepted", "proposal_available"}
        or body.get("version") != 2
        or body.get("status") not in statuses
        or type(body.get("provider_calls")) is not int
        or body["provider_calls"] < 0
    ):
        raise ValueError("publication_lifecycle_schema")
    if (
        "candidate_id" in body
        and body["candidate_id"] is not None
        and (type(body["candidate_id"]) is not int or body["candidate_id"] <= 0)
    ):
        raise ValueError("publication_candidate_invalid")
    for proof, eligible_status in (
        ("approval_accepted", "approval_requested"),
        ("proposal_available", "review_required"),
    ):
        if proof in body and (
            type(body[proof]) is not bool or (body[proof] and body["status"] != eligible_status)
        ):
            raise ValueError("publication_proof_invalid")
    task = body["development_task"]
    if body["status"] == "approval_requested":
        if (
            not isinstance(task, dict)
            or set(task) != REQUIRED
            or task.get("action") != "approve_development"
            or task.get("task_id") != body["task_id"]
        ):
            raise ValueError("publication_envelope_schema")
    elif task is not None:
        raise ValueError("unexpected_publication_approval")
    return body
