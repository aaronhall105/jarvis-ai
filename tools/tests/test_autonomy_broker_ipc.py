import json
import os
import socket
import struct
import threading

import pytest

from tools.autonomy_broker import MAX_REQUEST_BYTES, Denied, canonical
from tools.autonomy_broker_ipc import handle, receive


def framed(value):
    raw = canonical(value)
    return struct.pack("!I", len(raw)) + raw


def read_response(sock):
    size = struct.unpack("!I", sock.recv(4))[0]
    raw = bytearray()
    while len(raw) < size:
        raw.extend(sock.recv(size - len(raw)))
    return json.loads(raw)


def test_peer_from_kernel_and_bounded_response():
    client, server = socket.socketpair()
    try:
        client.sendall(framed({"operation": "inspect_release"}))
        seen = []

        def dispatch(raw, uid):
            seen.append((json.loads(raw), uid))
            return {"outcome": "inspected"}

        handle(server, dispatch)
        assert read_response(client) == {"ok": True, "result": {"outcome": "inspected"}}
        assert seen == [({"operation": "inspect_release"}, os.getuid())]
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("size", [0, MAX_REQUEST_BYTES + 1, 2**32 - 1])
def test_overlong_or_empty_frame_never_dispatches(size):
    client, server = socket.socketpair()
    try:
        client.sendall(struct.pack("!I", size))
        handle(server, lambda *_: pytest.fail("must not dispatch"))
        assert read_response(client) == {"ok": False, "category": "request_denied"}
    finally:
        client.close()
        server.close()


def test_incomplete_frame_rejected():
    client, server = socket.socketpair()
    try:
        client.sendall(struct.pack("!I", 20) + b"abc")
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(Denied, match="incomplete_ipc_request"):
            receive(server)
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize(
    "error", [Denied("synthetic-private-error"), IndexError("synthetic-private-error")]
)
def test_no_exception_content_crosses_ipc(error):
    client, server = socket.socketpair()
    try:
        client.sendall(framed({}))

        def dispatch(*_):
            raise error

        handle(server, dispatch)
        result = read_response(client)
        assert result["ok"] is False
        assert "private" not in json.dumps(result)
    finally:
        client.close()
        server.close()


def test_client_disconnect_does_not_cancel_server_owned_work():
    client, server = socket.socketpair()
    started, released, completed = threading.Event(), threading.Event(), []

    def dispatch(*_):
        started.set()
        assert released.wait(2)
        completed.append("durable-result")
        return {"outcome": "passed"}

    worker = threading.Thread(target=handle, args=(server, dispatch))
    try:
        worker.start()
        client.sendall(framed({}))
        assert started.wait(2)
        client.close()
        released.set()
        worker.join(2)
        assert not worker.is_alive()
        assert completed == ["durable-result"]
    finally:
        released.set()
        worker.join(2)
        client.close()
        server.close()
