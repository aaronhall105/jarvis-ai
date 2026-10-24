"""Fixed read-only host observations and signed operator IPC relay.

The relay has no signing key, enrollment route or unsigned stop/resume command.
Developer authentication alone cannot authorize a Guardian transition.
"""

from __future__ import annotations

import json
import socket
import struct
import subprocess
import urllib.request
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
            readings["api_developer"] = (
                developer["api_backend_healthy"] and developer["api_backend_configured"]
            )
        if "developer" not in readings or "api_developer" not in readings:
            unavailable.append("developer")
    except Exception:
        unavailable.append("developer")
    return readings, tuple(unavailable)


def guardian_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["/usr/local/bin/jarvis-autonomy", "status"],
            capture_output=True,
            timeout=5,
            check=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        body = json.loads(result.stdout)
        if body.get("state") not in {"STOPPED", "ACTIVE"} or body.get("trusted") is not True:
            raise ValueError("Untrusted state")
        return {key: body[key] for key in ("state", "trusted", "generation")}
    except Exception:
        return {"state": "UNKNOWN", "trusted": False}


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
