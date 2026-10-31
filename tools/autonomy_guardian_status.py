"""Root system-manager socket-activated Guardian status. No mutation dispatch.

Run installed with python -I in the system manager's user/PID namespaces. The
existing Guardian module is loaded from its fixed protected installation, never
from the checkout, user environment, or Developer sandbox.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import struct
import time
import uuid
from pathlib import Path

SOCKET = "/run/jarvis-autonomy-status/status.sock"
GUARDIAN = Path("/usr/local/libexec/jarvis-autonomy/guardian.py")
MAGIC = b"JGS1"
NONCE_BYTES = 32
MAX_REPLY = 256
TIMEOUT = 2.0
UNKNOWN = {"state": "UNKNOWN", "trusted": False, "generation": None}


def protected(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        info.st_uid != 0
        or info.st_mode & 0o022
        or not kind(info.st_mode)
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("Untrusted status installation")


def load_guardian():
    protected(GUARDIAN)
    for parent in GUARDIAN.parents:
        protected(parent, directory=True)
    spec = importlib.util.spec_from_file_location("installed_guardian", GUARDIAN)
    if spec is None or spec.loader is None:
        raise ValueError("Guardian unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Guardian()


def status(controller, actor: int) -> dict:
    """Each request revalidates full Guardian trust, never caches a generation."""
    try:
        first = controller.status()
        if first.get("trusted") is not True or actor != controller.policy()["operator_uid"]:
            return dict(UNKNOWN)
        # Detect a concurrent transition or installation tampering during validation.
        second = controller.status()
        if first != second or set(second) != {"state", "trusted", "generation"}:
            return dict(UNKNOWN)
        if second["state"] not in {"ACTIVE", "STOPPED"} or second["trusted"] is not True:
            return dict(UNKNOWN)
        if str(uuid.UUID(second["generation"])) != second["generation"]:
            return dict(UNKNOWN)
        return {key: second[key] for key in ("state", "trusted", "generation")}
    except Exception:
        return dict(UNKNOWN)


def handle(connection: socket.socket, controller) -> None:
    """A fixed challenge, not an action/argv/path protocol. Exactly one response."""
    deadline = time.monotonic() + TIMEOUT

    def receive(size):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        connection.settimeout(remaining)
        return connection.recv(size)

    try:
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        request = bytearray()
        while len(request) < len(MAGIC) + NONCE_BYTES:
            chunk = receive(len(MAGIC) + NONCE_BYTES - len(request))
            if not chunk:
                return
            request.extend(chunk)
        if request[:4] != MAGIC or receive(1):
            return
        body = json.dumps(status(controller, uid), separators=(",", ":")).encode()
        if len(body) > MAX_REPLY or time.monotonic() >= deadline:
            return
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(bytes(request) + body)
    except Exception:
        # No request, exception text, state contents or credential enters logs.
        return


def main() -> int:
    if os.geteuid() != 0:
        return 1
    try:
        # A root-like UID in a user namespace must not replace host validation.
        if os.stat("/proc/self/ns/user").st_ino != os.stat("/proc/1/ns/user").st_ino:
            return 1
        if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
            return 1
        controller = load_guardian()
        with socket.socket(fileno=3) as listener:
            if (
                listener.family != socket.AF_UNIX
                or listener.type != socket.SOCK_STREAM
                or listener.getsockname() != SOCKET
                or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            ):
                return 1
            while True:
                connection, _ = listener.accept()
                with connection:
                    handle(connection, controller)
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
