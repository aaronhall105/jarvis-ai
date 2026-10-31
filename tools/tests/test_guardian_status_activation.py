"""Activation regressions. All state, sockets and subprocesses are disposable."""

import os
import json
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tools import autonomy_guardian_status as service


def activation(monkeypatch):
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "2")
    monkeypatch.setenv("LISTEN_FDNAMES", "guardian-status:guardian-host-userns")
    monkeypatch.setattr(service, "same_host_namespace", lambda fd: fd == 4)


def test_capability_free_root_does_not_introspect_pid1(monkeypatch):
    """PID1 nsfs dereference needs ptrace permission, even for capability-free root."""
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    original = service.os.stat

    def protected_stat(path, *args, **kwargs):
        if str(path) == "/proc/1/ns/user":
            raise PermissionError(13, "Permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(service.os, "stat", protected_stat)
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "0 0 4294967295\n")
    activation(monkeypatch)
    reached = []

    def loader():
        reached.append("trusted_loader")
        raise OSError("End of bounded startup probe")

    monkeypatch.setattr(service, "load_guardian", loader)
    assert service.main() == 1
    assert reached == ["trusted_loader"]


@pytest.mark.parametrize(
    "mapping", ["0 1000 1", "1000 1000 1", "0 0 1", "", "bad", "0 0 4294967295\n1 1 1"]
)
@pytest.mark.parametrize("kind", ["uid_map", "gid_map"])
def test_partial_or_malformed_identity_mapping_rejected(monkeypatch, mapping, kind):
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        Path, "read_text", lambda self, **kwargs: mapping if self.name == kind else "0 0 4294967295"
    )
    activation(monkeypatch)
    monkeypatch.setattr(service, "load_guardian", lambda: pytest.fail("Not host identity"))
    assert service.main() == 1


@pytest.mark.parametrize(
    "pid,fds", [("", "2"), ("1", "2"), ("self", "0"), ("self", "1"), ("self", "3")]
)
def test_activation_environment_remains_strict(monkeypatch, pid, fds):
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "0 0 4294967295")
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()) if pid == "self" else pid)
    monkeypatch.setenv("LISTEN_FDS", fds)
    monkeypatch.setattr(service, "load_guardian", lambda: pytest.fail("Bad activation"))
    assert service.main() == 1


@pytest.mark.parametrize(
    "names",
    [
        "",
        "guardian-status",
        "guardian-status:guardian-status",
        "guardian-status:other",
        "guardian-status:guardian-host-userns:other",
    ],
)
def test_missing_duplicate_or_unknown_fd_names_rejected(monkeypatch, names):
    activation(monkeypatch)
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "read_text", lambda self: "0 0 4294967295")
    monkeypatch.setenv("LISTEN_FDNAMES", names)
    monkeypatch.setattr(service, "load_guardian", lambda: pytest.fail("Bad activation"))
    assert service.main() == 1


def test_real_namespace_anchor_is_consumed():
    fd = os.open("/proc/self/ns/user", os.O_RDONLY)
    assert service.same_host_namespace(fd) is True
    with pytest.raises(OSError):
        os.fstat(fd)


def test_regular_file_is_not_a_namespace_anchor(tmp_path):
    path = tmp_path / "not-a-namespace"
    path.touch()
    fd = os.open(path, os.O_RDONLY)
    with pytest.raises(OSError):
        service.same_host_namespace(fd)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_non_user_namespace_anchor_is_rejected():
    fd = os.open("/proc/self/ns/net", os.O_RDONLY)
    assert service.same_host_namespace(fd) is False


def test_identity_mapped_privileged_child_namespace_still_denied(monkeypatch):
    activation(monkeypatch)
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "read_text", lambda self: "0 0 4294967295")
    monkeypatch.setattr(service, "same_host_namespace", lambda fd: False)
    monkeypatch.setattr(service, "load_guardian", lambda: pytest.fail("Child namespace"))
    assert service.main() == 1


def test_namespace_comparison_checks_inode_and_device(monkeypatch):
    from types import SimpleNamespace

    for field in ("st_dev", "st_ino"):
        fd = os.open("/proc/self/ns/user", os.O_RDONLY)
        info = os.fstat(fd)
        changed = {"st_dev": info.st_dev, "st_ino": info.st_ino}
        changed[field] += 1
        monkeypatch.setattr(service.os, "stat", lambda _: SimpleNamespace(**changed))
        assert service.same_host_namespace(fd) is False


def test_unreadable_mapping_fails_closed(monkeypatch):
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)

    def denied(self):
        raise PermissionError(13, "Unavailable")

    monkeypatch.setattr(Path, "read_text", denied)
    assert service.main() == 1


@pytest.mark.parametrize("kind", ["missing", "file", "datagram", "not_listening", "wrong_path"])
def test_inherited_descriptor_validation_not_weakened(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "read_text", lambda self: "0 0 4294967295")
    activation(monkeypatch)
    monkeypatch.setattr(service, "load_guardian", lambda: object())
    if kind in {"missing", "file"}:

        def invalid(*args, **kwargs):
            assert kwargs == {"fileno": 3}
            raise OSError(9 if kind == "missing" else 88, "Invalid inherited descriptor")

        monkeypatch.setattr(service.socket, "socket", invalid)
        assert service.main() == 1
        return
    listener = socket.socket(
        socket.AF_UNIX, socket.SOCK_DGRAM if kind == "datagram" else socket.SOCK_STREAM
    )
    path = str(tmp_path / "listener.sock")
    listener.bind(path)
    if kind == "wrong_path":
        listener.listen(1)
    monkeypatch.setattr(service, "SOCKET", path if kind != "wrong_path" else path + "-other")

    def inherited(*args, **kwargs):
        assert kwargs == {"fileno": 3}
        return listener

    monkeypatch.setattr(service.socket, "socket", inherited)
    assert service.main() == 1
    assert listener.fileno() == -1


def test_real_systemd_activation_fd3_and_environment(tmp_path):
    activator = shutil.which("systemd-socket-activate")
    if not activator:
        pytest.skip("systemd socket activator unavailable")
    # Only the root/trusted-controller fixture is substituted. Real systemd
    # supplies LISTEN_PID/FDS; main checks real inherited FDs, namespace and listener.
    # No unit is installed and no production socket/state is accessed.
    path = str(tmp_path / "status.sock")
    harness = tmp_path / "activation.py"
    harness.write_text(
        "import os, runpy\n"
        f"m = runpy.run_path({service.__file__!r})\n"
        "class Fixture:\n"
        " def policy(self): return {'operator_uid':os.getuid()}\n"
        " def status(self): return {'state':'STOPPED','trusted':True,'generation':'00000000-0000-0000-0000-000000000001'}\n"
        "main=m['main']; main.__globals__['load_guardian']=Fixture\n"
        f"main.__globals__['SOCKET']={path!r}\n"
        "os.geteuid=lambda:0\n"
        "assert os.environ['LISTEN_PID']==str(os.getpid())\n"
        "assert os.environ['LISTEN_FDS']=='1'\n"
        "assert os.environ['LISTEN_FDNAMES']=='guardian-status'\n"
        "assert os.open('/proc/self/ns/user',os.O_RDONLY)==4\n"
        "os.environ.update(LISTEN_FDS='2',LISTEN_FDNAMES='guardian-status:guardian-host-userns')\n"
        "raise SystemExit(main())\n"
    )
    # The CLI activator accepts only sockets. Model OpenFile delivery with a
    # real namespace FD; the separate user-manager test exercises actual OpenFile.
    child = subprocess.Popen(
        [
            activator,
            "--fdname=guardian-status",
            "--listen=" + path,
            sys.executable,
            "-I",
            str(harness),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not Path(path).exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        challenge = b"JGS1" + b"a" * 32
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(path)
            connection.sendall(challenge)
            connection.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while chunk := connection.recv(293 - len(raw)):
                raw.extend(chunk)
                assert len(raw) <= 292
        assert raw[:36] == challenge
        assert json.loads(raw[36:]) == {
            "state": "STOPPED",
            "trusted": True,
            "generation": "00000000-0000-0000-0000-000000000001",
        }
        assert child.poll() is None
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as malformed:
            malformed.settimeout(3)
            malformed.connect(path)
            malformed.sendall(b"resume")
            malformed.shutdown(socket.SHUT_WR)
            assert malformed.recv(1) == b""
        assert child.poll() is None
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def test_real_user_manager_openfile_activation(tmp_path):
    if (
        not shutil.which("systemd-run")
        or subprocess.run(
            ["systemctl", "--user", "is-system-running"], capture_output=True, timeout=5
        ).returncode
    ):
        pytest.skip("Disposable user systemd manager unavailable")
    unit = "jarvis-status-regression-" + uuid.uuid4().hex
    path = str(tmp_path / "manager.sock")
    harness = tmp_path / "manager.py"
    harness.write_text(
        "import os,runpy\n"
        f"m=runpy.run_path({service.__file__!r}); main=m['main']\n"
        "class Fixture:\n"
        " def policy(self): return {'operator_uid':os.getuid()}\n"
        " def status(self): return {'state':'STOPPED','trusted':True,'generation':'00000000-0000-0000-0000-000000000001'}\n"
        "main.__globals__['load_guardian']=Fixture\n"
        f"main.__globals__['SOCKET']={path!r}\n"
        "os.geteuid=lambda:0\n"
        "raise SystemExit(main())\n"
    )
    try:
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                "--collect",
                "--unit=" + unit,
                "--socket-property=ListenStream=" + path,
                "--socket-property=FileDescriptorName=guardian-status",
                "-p",
                "OpenFile=/proc/self/ns/user:guardian-host-userns:read-only",
                "-p",
                "NoNewPrivileges=yes",
                "-p",
                "StartLimitBurst=1",
                "-p",
                "RuntimeMaxSec=15",
                "-p",
                "TimeoutStopSec=2",
                sys.executable,
                "-I",
                str(harness),
            ],
            check=True,
            capture_output=True,
            timeout=8,
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(path)
            challenge = b"JGS1" + b"m" * 32
            connection.sendall(challenge)
            connection.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while chunk := connection.recv(293 - len(raw)):
                raw.extend(chunk)
                assert len(raw) <= 292
        assert raw[:36] == challenge
        assert json.loads(raw[36:])["trusted"] is True
        # Actual restricted user namespace must still fail even if the test
        # substitutes a root-like EUID. No Guardian/controller is reached.
        denied = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                "--wait",
                "--pipe",
                "--collect",
                "-p",
                "PrivateUsers=yes",
                "-p",
                "ProtectSystem=strict",
                "-p",
                "PrivateTmp=yes",
                "-p",
                "NoNewPrivileges=yes",
                "-p",
                "RuntimeMaxSec=5",
                sys.executable,
                "-I",
                "-c",
                "import os,runpy; "
                + f"m=runpy.run_path({service.__file__!r}); "
                + "os.geteuid=lambda:0; raise SystemExit(m['main']())",
            ],
            capture_output=True,
            timeout=8,
        )
        assert denied.returncode == 1
    finally:
        subprocess.run(
            ["systemctl", "--user", "stop", unit + ".socket", unit + ".service"],
            capture_output=True,
            timeout=8,
            check=False,
        )
