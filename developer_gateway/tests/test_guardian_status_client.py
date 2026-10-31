import json
import socket
import struct
import uuid

import pytest

from developer_gateway import autonomy_host as host

STOPPED = {"state": "STOPPED", "trusted": True, "generation": str(uuid.UUID(int=1))}
UNKNOWN = {"state": "UNKNOWN", "trusted": False, "generation": None}


class Connection:
    def __init__(self, *, peer=(1, 0, 0), result=None, stale=False, raw=None):
        self.peer = peer
        self.result = STOPPED if result is None else result
        self.stale = stale
        self.raw = raw
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, value):
        assert 0 < value <= 3

    def connect(self, path):
        assert path == "/run/jarvis-autonomy-status/status.sock"

    def getsockopt(self, *args):
        assert args == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack("3i", *self.peer)

    def sendall(self, value):
        self.sent = value
        nonce = b"old!" + b"x" * 32 if self.stale else value
        self.response = nonce + (
            self.raw if self.raw is not None else json.dumps(self.result).encode()
        )

    def shutdown(self, how):
        assert how == socket.SHUT_WR

    def recv(self, size):
        chunk, self.response = self.response[:size], self.response[size:]
        return chunk


@pytest.mark.parametrize("peer", [(1, 0, 0), (1, 65534, 65534)])
def test_system_manager_identity_survives_namespace_uid_remapping(monkeypatch, peer):
    connection = Connection(peer=peer)
    monkeypatch.setattr(host.socket, "socket", lambda *args: connection)
    assert host.guardian_status() == STOPPED
    assert connection.sent[:4] == b"JGS1" and len(connection.sent) == 36


@pytest.mark.parametrize("peer", [(2, 0, 0), (999, 65534, 65534), (1, 1000, 1000), (0, 0, 0)])
def test_wrong_kernel_credentials_rejected_before_request(monkeypatch, peer):
    connection = Connection(peer=peer)
    monkeypatch.setattr(host.socket, "socket", lambda *args: connection)
    assert host.guardian_status() == UNKNOWN
    assert connection.sent is None


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {**STOPPED, "trusted": 1},
        {**STOPPED, "state": "INVALID"},
        {**STOPPED, "generation": None},
        {**STOPPED, "generation": "bad"},
        {**STOPPED, "extra": "INERT-PRIVATE-MARKER"},
        UNKNOWN,
    ],
)
def test_malformed_or_untrusted_status_has_no_generation(monkeypatch, result):
    monkeypatch.setattr(host.socket, "socket", lambda *args: Connection(result=result))
    assert host.guardian_status() == UNKNOWN


@pytest.mark.parametrize("raw", [b"broken", b"x" * 257, b'{"state":"ACTIVE","state":"STOPPED"}'])
def test_bounded_unique_json(monkeypatch, raw):
    monkeypatch.setattr(host.socket, "socket", lambda *args: Connection(raw=raw))
    assert host.guardian_status() == UNKNOWN


def test_old_response_cannot_supply_a_stale_generation(monkeypatch):
    monkeypatch.setattr(host.socket, "socket", lambda *args: Connection(stale=True))
    assert host.guardian_status() == UNKNOWN


def test_late_response_and_unavailable_fail_closed_without_cached_status(monkeypatch):
    monkeypatch.setattr(host.socket, "socket", lambda *args: Connection())
    assert host.guardian_status() == STOPPED
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(host.time, "monotonic", lambda: next(ticks))
    assert host.guardian_status() == UNKNOWN

    def unavailable(*args):
        raise OSError("INERT-PRIVATE-MARKER")

    monkeypatch.setattr(host.socket, "socket", unavailable)
    assert host.guardian_status() == UNKNOWN
