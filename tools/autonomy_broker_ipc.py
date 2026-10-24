"""Bounded one-request Unix IPC. Kernel identity is the only peer identity.

Worker and operator listeners must be separate protected sockets/services, so a
long worker operation cannot starve STOP. This module neither creates sockets
nor grants authority; the root-owned installed service supplies the dispatcher.
Client disconnect does not cancel a dispatched server-owned transaction.
"""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable
from typing import Any

from tools.autonomy_broker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    Denied,
    canonical,
    peer_uid,
)


def receive(connection: socket.socket) -> bytes:
    deadline = time.monotonic() + 3

    def exact(length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Denied("ipc_deadline")
            connection.settimeout(remaining)
            chunk = connection.recv(length - len(chunks))
            if not chunk:
                raise Denied("incomplete_ipc_request")
            chunks.extend(chunk)
        return bytes(chunks)

    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= MAX_REQUEST_BYTES:
        raise Denied("request_size")
    return exact(size)


def handle(connection: socket.socket, dispatch: Callable[[bytes, int], dict[str, Any]]) -> None:
    """Never serialize exception text, traceback, request bodies or source output."""
    try:
        actor = peer_uid(connection)
        raw = receive(connection)
        result = dispatch(raw, actor)
        response = canonical({"ok": True, "result": result})
        if len(response) > MAX_RESPONSE_BYTES:
            raise Denied("result_size")
    except Denied:
        response = canonical({"ok": False, "category": "request_denied"})
    except Exception:
        response = canonical({"ok": False, "category": "broker_unavailable_or_uncertain"})
    try:
        connection.settimeout(3)
        connection.sendall(struct.pack("!I", len(response)) + response)
    except OSError:
        # Execution/result persistence precedes response delivery. A disconnected
        # client retrieves the completed result by the same request identity.
        return
