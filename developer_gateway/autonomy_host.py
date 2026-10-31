"""Fixed read-only host observations and signed operator IPC relay.

The relay has no signing key, enrollment route or unsigned stop/resume command.
Developer authentication alone cannot authorize a Guardian transition.
"""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
import urllib.request
import uuid
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(url: str) -> dict[str, Any]:
    if url not in {
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/api/system/status",
        "http://127.0.0.1:8765/health",
    }:
        raise ValueError("Observation source denied")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=4) as response:
        raw = response.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Response too large")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("Invalid health response")
    return body


def collect() -> dict[str, bool]:
    return collect_report()[0]


def collect_report() -> tuple[dict[str, bool], tuple[str, ...]]:
    """Whitelist booleans; missing observations are not invented failures."""
    readings: dict[str, bool] = {}
    unavailable = []
    try:
        core = _read("http://127.0.0.1:8000/health")
        if core.get("status") in {"healthy", "unhealthy"}:
            readings["core"] = core["status"] == "healthy"
        else:
            unavailable.append("core")
    except Exception:
        unavailable.append("core")  # Unknown, not a fabricated product defect.
    try:
        system = _read("http://127.0.0.1:8000/api/system/status")
        for component, field in {
            "database": "healthy",
            "home_assistant": "connected",
            "task_centre": "healthy",
            "followup_worker": "healthy",
        }.items():
            entry = system.get(component)
            value = entry.get(field) if isinstance(entry, dict) else None
            if type(value) is bool:
                readings[component] = value
            elif "system" not in unavailable:
                unavailable.append("system")
    except Exception:
        unavailable.append("system")
    try:
        developer = _read("http://127.0.0.1:8765/health")
        if developer.get("status") in {"healthy", "unhealthy"}:
            readings["developer"] = developer["status"] == "healthy"
        if all(
            type(developer.get(field)) is bool
            for field in ("api_backend_healthy", "api_backend_configured")
        ):
            configured, healthy = (
                developer["api_backend_configured"],
                developer["api_backend_healthy"],
            )
            observed = developer.get("api_backend_health")
            if not configured:
                readings["api_developer"] = False
            elif healthy and observed in {None, "HEALTHY"}:
                readings["api_developer"] = True
            elif not healthy and observed == "UNHEALTHY":
                readings["api_developer"] = False
            # Legacy false, initial UNKNOWN, or contradictory observations are
            # unavailable evidence, never a fabricated API failure or success.
        if "developer" not in readings or "api_developer" not in readings:
            unavailable.append("developer")
    except Exception:
        unavailable.append("developer")
    return readings, tuple(unavailable)


def guardian_status() -> dict[str, Any]:
    unknown = {"state": "UNKNOWN", "trusted": False, "generation": None}
    try:
        challenge = b"JGS1" + secrets.token_bytes(32)
        deadline = time.monotonic() + 3
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect("/run/jarvis-autonomy-status/status.sock")
            pid, uid, gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            # Socket activation preserves the LISTENING socket creator's kernel
            # credentials: host system manager PID 1. Root UID/GID may appear as
            # overflow IDs inside the user manager's mount/user sandbox. Neither
            # root UID alone nor overflow UID alone is authentication.
            if pid != 1 or uid not in {0, 65534} or gid not in {0, 65534}:
                raise ValueError("Unauthenticated status endpoint")
            connection.sendall(challenge)
            connection.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                chunk = connection.recv(293 - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 292:
                    raise ValueError("Status too large")
        if time.monotonic() >= deadline or raw[:36] != challenge:
            raise ValueError("Stale status response")
        body = json.loads(raw[36:], object_pairs_hook=_unique_fields)
        if not isinstance(body, dict) or set(body) != {"state", "trusted", "generation"}:
            raise ValueError("Invalid status fields")
        if body["state"] not in {"STOPPED", "ACTIVE"} or body["trusted"] is not True:
            raise ValueError("Untrusted state")
        if str(uuid.UUID(body["generation"])) != body["generation"]:
            raise ValueError("Invalid generation")
        return {key: body[key] for key in ("state", "trusted", "generation")}
    except Exception:
        return unknown


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate status field")
        result[key] = value
    return result


def relay_operator(body: dict[str, Any]) -> dict[str, Any]:
    fields = {"version", "action", "key_id", "request_id", "expires_at", "generation", "signature"}
    if (
        set(body) != fields
        or not isinstance(body.get("action"), str)
        or body["action"] not in {"status", "stop", "resume"}
    ):
        return {"ok": False, "category": "request_denied"}
    raw = json.dumps(body, separators=(",", ":")).encode()
    if len(raw) > 8192:
        return {"ok": False, "category": "request_denied"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect("/run/jarvis-autonomy-broker/operator.sock")
            connection.sendall(struct.pack("!I", len(raw)) + raw)
            header = _receive(connection, 4)
            length = struct.unpack("!I", header)[0]
            if length > 8192:
                raise ValueError("Invalid frame")
            result = json.loads(_receive(connection, length))
        # Never propagate arbitrary broker error content or identifiers to logs.
        return {
            "ok": result.get("ok") is True,
            "category": "accepted" if result.get("ok") is True else "request_denied",
        }
    except Exception:
        return {"ok": False, "category": "operator_unavailable"}


def _receive(connection: socket.socket, size: int) -> bytes:
    result = b""
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            raise ValueError("Incomplete frame")
        result += part
    return result
