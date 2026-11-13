"""Fixed, authenticated IPC for the native supervised-development interface.

No database, source path, command, credential or remote URL is accepted here.
The gateway transports signed requests; only the separate protected services
can authorize their effects. Ambiguous sends are never automatically retried.
"""

from __future__ import annotations

import json
import pwd
import re
import socket
import struct
import time
import uuid
from typing import Any

from .pending_task_client import _trusted_publication_peer

PUBLICATION_SOCKET = "/run/jarvis-autonomy-broker/development-task.sock"
OPERATOR_SOCKET = "/run/jarvis-autonomy-broker/operator.sock"
INTEGRATION_SOCKET = "/run/jarvis-development-integration/integration.sock"
MAX_REQUEST = 65_536
MAX_RESPONSE = 1_048_576
COMMON = {"version", "action", "key_id", "request_id", "expires_at", "signature"}
FIELDS = {
    "prepare_development": COMMON
    | {
        "candidate_id",
        "source_sha",
        "evidence_sha256",
        "candidate_sha256",
        "goal",
        "allowed_paths",
        "provider",
        "model",
        "max_calls",
        "max_cost_usd",
        "deadline_seconds",
        "guardian_generation",
    },
    "activate_development": COMMON
    | {
        "task_id",
        "approval_id",
        "source_sha",
        "parameters_sha256",
        "guardian_generation",
    },
    "create_development_pr": COMMON
    | {
        "task_id",
        "repository",
        "base_branch",
        "base_sha",
        "base_tree_sha",
        "head_tree_sha",
        "allowed_paths",
        "parameters_sha256",
        "evidence_sha256",
        "diff_sha256",
        "proposal_sha256",
    },
}
VIEWS = {
    "prepare": "prepare_development",
    "activation": "activate_development",
    "proposal": "create_development_pr",
}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("nonfinite_number")


def decode(raw: bytes | bytearray) -> dict[str, Any]:
    body = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if not isinstance(body, dict):
        raise ValueError("invalid_object")
    return body


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _receive(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + 10
    while len(output) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("response_timeout")
        connection.settimeout(remaining)
        part = connection.recv(size - len(output))
        if not part:
            raise ValueError("response_truncated")
        output.extend(part)
    return bytes(output)


def _trusted_peer(path: str, pid: int, uid: int, gid: int, *, integration: bool) -> bool:
    """Accept only fixed trusted socket identities across restricted UID mappings."""
    if integration:
        expected_uid = pwd.getpwnam("jarvis-integration").pw_uid
        return expected_uid != 0 and (
            uid == expected_uid
            or (path == INTEGRATION_SOCKET and _trusted_publication_peer(pid, uid, gid))
        )
    return uid == 0 or (
        path in (PUBLICATION_SOCKET, OPERATOR_SOCKET) and _trusted_publication_peer(pid, uid, gid)
    )


def _exchange(path: str, body: dict[str, Any], *, integration: bool = False) -> dict[str, Any]:
    raw = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    if not 0 < len(raw) <= MAX_REQUEST:
        raise ValueError("request_size")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(path)
        pid, uid, gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if not _trusted_peer(path, pid, uid, gid, integration=integration):
            raise PermissionError("untrusted_service_peer")
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        size = struct.unpack("!I", _receive(connection, 4))[0]
        if not 0 < size <= MAX_RESPONSE:
            raise ValueError("response_size")
        return decode(_receive(connection, size))


def preview(view: str, identifier: int | str) -> dict[str, Any]:
    if view not in VIEWS:
        raise ValueError("view_invalid")
    if view == "prepare":
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("candidate_invalid")
        query = {"version": 3, "view": view, "candidate_id": identifier}
    else:
        if not _uuid(identifier):
            raise ValueError("task_invalid")
        query = {"version": 3, "view": view, "task_id": identifier}
    try:
        response = _exchange(PUBLICATION_SOCKET, query)
        status = response.get("status")
        if response.get("version") != 3 or status not in {"ready", "blocked", "unavailable"}:
            raise ValueError("publication_schema")
        if set(response) - {"version", "status", "request", "reason", "proposal", "handoff"}:
            raise ValueError("publication_fields")
        envelope = response.get("request")
        if status == "ready":
            action = VIEWS[view]
            if (
                not isinstance(envelope, dict)
                or set(envelope) != FIELDS[action] - {"key_id", "signature"}
                or envelope.get("action") != action
                or type(envelope.get("version")) is not int
                or envelope["version"] != 1
                or not _uuid(envelope.get("request_id"))
                or type(envelope.get("expires_at")) is not int
                or envelope["expires_at"] <= int(time.time())
                or envelope.get("candidate_id" if view == "prepare" else "task_id") != identifier
            ):
                raise ValueError("publication_envelope")
        elif envelope is not None:
            raise ValueError("ineligible_approval")
        reason = response.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason)
        ):
            raise ValueError("publication_reason")
        if view == "proposal" and status == "ready":
            if not isinstance(envelope, dict):
                raise ValueError("publication_envelope")
            handoff = handoff_status(str(identifier), envelope)
            response["handoff"] = handoff
            if handoff is not None:
                # Includes unavailable/uncertain states. Display the verified
                # diff, but never offer another mutation without a proven
                # empty integration journal.
                response["request"] = None
        return response
    except (OSError, ValueError, KeyError, RecursionError):
        return {
            "version": 3,
            "status": "unavailable",
            "request": None,
            "reason": "publisher_unavailable",
        }


def _verified_receipt(receipt: Any, expected: dict[str, Any]) -> bool:
    required = {"status", "number", "head_sha", "base_sha", "proposal_sha256", "required_gates"}
    return (
        isinstance(receipt, dict)
        and set(receipt) == required
        and receipt.get("status") == "PR_REVIEW_REQUIRED"
        and type(receipt.get("number")) is int
        and receipt["number"] > 0
        and receipt.get("base_sha") == expected["base_sha"]
        and receipt.get("proposal_sha256") == expected["proposal_sha256"]
        and isinstance(receipt.get("head_sha"), str)
        and bool(re.fullmatch(r"[a-f0-9]{40}", receipt["head_sha"]))
        and receipt.get("required_gates")
        == ["independent_human_review", "protected_ci", "codeql", "owner_merge_approval"]
    )


def handoff_status(task_id: str, expected: dict[str, Any]) -> dict[str, Any] | None:
    """Read only a known task's integration receipt; never resubmit approval."""
    try:
        response = _exchange(
            INTEGRATION_SOCKET,
            {"version": 1, "action": "handoff_status", "task_id": task_id},
            integration=True,
        )
        if response.get("ok") is not True or set(response.get("result", {})) != {"handoff"}:
            raise ValueError("integration_status_invalid")
        record = response["result"]["handoff"]
        if record is None:
            return None
        if _verified_receipt(record, expected):
            return {
                **record,
                "url": f"https://github.com/aaronhall105/jarvis-ai/pull/{record['number']}",
            }
        if (
            isinstance(record, dict)
            and record.get("status") in {"DISPATCHING", "RECONCILIATION_REQUIRED"}
            and record.get("proposal_sha256") == expected["proposal_sha256"]
        ):
            return {"status": record["status"], "proposal_sha256": record["proposal_sha256"]}
        raise ValueError("integration_status_mismatch")
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return {"status": "UNAVAILABLE", "reason": "integration_unavailable"}


def relay(action: str, body: dict[str, Any]) -> dict[str, Any]:
    if (
        action not in FIELDS
        or set(body) != FIELDS[action]
        or body.get("action") != action
        or type(body.get("version")) is not int
        or body["version"] != 1
        or not _uuid(body.get("request_id"))
        or not isinstance(body.get("key_id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", body["key_id"])
        or not isinstance(body.get("signature"), str)
        or not body["signature"]
    ):
        return {"ok": False, "category": "request_denied"}
    if type(body.get("expires_at")) is not int or body["expires_at"] <= int(time.time()):
        return {"ok": False, "category": "approval_expired"}
    integration = action == "create_development_pr"
    try:
        result = _exchange(
            INTEGRATION_SOCKET if integration else OPERATOR_SOCKET, body, integration=integration
        )
        if result.get("ok") is not True:
            # Only fixed error categories cross this boundary, never exception
            # text, signatures, credentials or arbitrary protected diagnostics.
            code = result.get("error")
            known = {
                "approval_expired",
                "request_expired",
                "source_changed",
                "guardian_untrusted",
                "guardian_generation_changed",
                "approval_required",
                "replay_denied",
                "integration_reconciliation_required",
                "integration_authority_unprovisioned",
            }
            return {"ok": False, "category": code if code in known else "request_denied"}
        if integration:
            receipt = result.get("result")
            if not isinstance(receipt, dict) or not _verified_receipt(receipt, body):
                raise ValueError("integration_receipt_invalid")
            return {
                "ok": True,
                "category": "accepted",
                "result": receipt,
                "url": f"https://github.com/aaronhall105/jarvis-ai/pull/{receipt['number']}",
            }
        operator_result = result.get("result")
        if not isinstance(operator_result, dict):
            raise ValueError("operator_receipt_invalid")
        result = operator_result
        expected = "preparation_requested" if action == "prepare_development" else "active"
        if (
            result.get("status") != expected
            or result.get("request_id") != body["request_id"]
            or (
                action == "activate_development"
                and (not _uuid(result.get("task_id")) or result["task_id"] != body["task_id"])
            )
        ):
            raise ValueError("operator_receipt_invalid")
        return {
            "ok": True,
            "category": "accepted",
            "status": expected,
            "task_id": result.get("task_id"),
            "request_id": body["request_id"],
        }
    except (OSError, ValueError, KeyError, RecursionError):
        # A timeout may follow a committed effect. Surface uncertainty; neither
        # the gateway nor Android may turn this into an automatic resend.
        return {"ok": False, "category": "outcome_unconfirmed"}
