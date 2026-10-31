"""Root-free status transport/trust tests; no installed state or units changed."""

import json
import os
import socket
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import autonomy_guardian as g
from tools import autonomy_guardian_status as service
from tools.tests.test_autonomy_guardian import guardian as guardian


def exchange(controller, request):
    client, server = socket.socketpair()
    worker = threading.Thread(target=service.handle, args=(server, controller))
    try:
        worker.start()
        client.settimeout(3)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        worker.join(3)
        assert not worker.is_alive()
        server.close()
        raw = client.recv(1024)
        return raw
    finally:
        client.close()
        server.close()
        worker.join(3)


def test_full_guardian_validation_and_live_generation(guardian):
    first = service.status(guardian, 1000)
    assert first == guardian.status()
    guardian.write_state("STOPPED")
    assert service.status(guardian, 1000)["generation"] != first["generation"]


@pytest.mark.parametrize("actor", [0, 998, 65534, 1001])
def test_only_existing_operator_uid_can_read(guardian, actor):
    assert service.status(guardian, actor) == service.UNKNOWN


@pytest.mark.parametrize("kind", ["hash", "mode", "state", "manifest", "missing"])
def test_existing_guardian_tamper_checks_are_authoritative(guardian, kind):
    if kind == "hash":
        guardian.policy_path.write_text(guardian.policy_path.read_text() + " ")
    elif kind == "mode":
        guardian.policy_path.chmod(0o666)
    elif kind == "state":
        (guardian.base / "state.json").write_text("{}")
    elif kind == "manifest":
        (guardian.base / "installed.json").write_text("[]")
    else:
        (guardian.base / "state.json").unlink()
    assert guardian.status()["trusted"] is False
    assert service.status(guardian, 1000) == service.UNKNOWN


def test_namespace_remapped_ownership_is_still_rejected_by_guardian():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(st_uid=65534, st_mode=0o100644, st_nlink=1)
    )
    with pytest.raises(g.UnsafeState, match="ownership"):
        g.trusted(path)


def test_transition_during_validation_fails_closed(guardian, monkeypatch):
    original = guardian.status
    calls = []

    def changing():
        if calls:
            guardian.write_state("STOPPED")
        calls.append(True)
        return original()

    monkeypatch.setattr(guardian, "status", changing)
    assert service.status(guardian, 1000) == service.UNKNOWN


def test_fixed_socket_protocol_returns_only_three_status_fields(guardian, monkeypatch):
    # Transport fixture uses this process UID, independently of runner identity.
    # Full unchanged Guardian trust validation is exercised by the tests above.
    monkeypatch.setattr(guardian, "policy", lambda: {"operator_uid": os.getuid()})
    challenge = service.MAGIC + b"a" * 32
    raw = exchange(guardian, challenge)
    assert raw[:36] == challenge
    assert json.loads(raw[36:]) == guardian.status()
    assert len(raw) <= 292


def test_kernel_caller_identity_cannot_be_supplied_in_request(guardian, monkeypatch):
    monkeypatch.setattr(guardian, "policy", lambda: {"operator_uid": os.getuid() + 1})
    challenge = service.MAGIC + b"a" * 32
    assert json.loads(exchange(guardian, challenge)[36:]) == service.UNKNOWN


@pytest.mark.parametrize(
    "uid,mode,links", [(65534, 0o100644, 1), (0, 0o100666, 1), (0, 0o120777, 1), (0, 0o100644, 2)]
)
def test_guardian_loader_rejects_untrusted_module(uid, mode, links):
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(st_uid=uid, st_mode=mode, st_nlink=links))
    with pytest.raises(ValueError, match="Untrusted"):
        service.protected(path)


@pytest.mark.parametrize(
    "payload", [b"stop", b"resume", b' {"action":"stop"}', b"JGS1" + b"a" * 33, b"JGS1"]
)
def test_unsigned_actions_and_malformed_frames_have_no_dispatch(payload):
    class NeverCalled:
        def status(self):
            pytest.fail("Malformed request must never dispatch")

    assert exchange(NeverCalled(), payload) == b""


def test_unavailable_does_not_expose_exception():
    class Broken:
        def status(self):
            raise OSError("INERT-PRIVATE-DIAGNOSTIC")

    challenge = service.MAGIC + b"a" * 32
    raw = exchange(Broken(), challenge)
    assert json.loads(raw[36:]) == service.UNKNOWN
    assert b"PRIVATE" not in raw


def test_host_entry_rejects_unprivileged_or_wrong_user_namespace(monkeypatch):
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    assert service.main() == 1
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        service.os, "stat", lambda path: SimpleNamespace(st_ino=1 if "self" in path else 2)
    )
    assert service.main() == 1


def test_new_units_keep_status_read_only_and_separate():
    root = Path(__file__).resolve().parents[2]
    unit = (root / "systemd/autonomy/jarvis-autonomy-status.service").read_text()
    socket_unit = (root / "systemd/autonomy/jarvis-autonomy-status.socket").read_text()
    for setting in (
        "User=root",
        "Group=root",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "PrivateTmp=true",
        "PrivateUsers=no",
        "CapabilityBoundingSet=\n",
        "PrivateNetwork=true",
        "RestrictAddressFamilies=AF_UNIX",
        "StandardOutput=null",
    ):
        assert setting in unit
    assert "ReadWritePaths=" not in unit
    assert "SocketUser=root" in socket_unit and "SocketMode=0660" in socket_unit
    assert "SocketGroup=aaron" in socket_unit
    assert service.SOCKET in socket_unit
    assert "operator.sock" not in unit + socket_unit
