"""Incomplete privileged payloads must not be published with final read access."""

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import autonomy_broker_install as broker_install
from tools import autonomy_broker_snapshot as snapshot
from tools import autonomy_guardian_install as guardian_install


@pytest.mark.parametrize("writer", [guardian_install.write, broker_install.exclusive])
@pytest.mark.parametrize("final_mode", [0o600, 0o644, 0o755])
def test_install_content_is_private_until_complete(tmp_path, monkeypatch, writer, final_mode):
    target = tmp_path / "payload"
    payload = b"complete reviewed public payload\n"
    original_open, original_chmod = os.open, os.fchmod
    events = []

    def opening(path, flags, mode=0o777, **kwargs):
        fd = original_open(path, flags, mode, **kwargs)
        if Path(path) == target:
            assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
            assert mode == 0o600
            assert stat.S_IMODE(os.fstat(fd).st_mode) & 0o077 == 0
            events.append("private")
        return fd

    def publish(fd, mode):
        assert target.read_bytes() == payload
        assert mode == final_mode
        assert events == ["private"]
        events.append("published")
        return original_chmod(fd, mode)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fchmod", publish)
    writer(target, payload, final_mode)
    assert events == ["private", "published"]
    assert stat.S_IMODE(target.stat().st_mode) == final_mode


def test_snapshot_is_private_until_source_complete(tmp_path, monkeypatch):
    parent = tmp_path / "protected"
    parent.mkdir(mode=0o700)
    destination = parent / ("a" * 64) / "test.py"
    payload = b"assert True\n"
    original_open, original_chmod, original_lstat = os.open, os.fchmod, Path.lstat
    events = []

    def lstat(path, **kwargs):
        # Root-free rehearsal: tmp_path is the emulated protected filesystem root.
        if path in tmp_path.parents:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=os.getuid())
        return original_lstat(path, **kwargs)

    def opening(path, flags, mode=0o777, **kwargs):
        fd = original_open(path, flags, mode, **kwargs)
        if Path(path) == destination:
            assert mode == 0o600
            assert stat.S_IMODE(os.fstat(fd).st_mode) & 0o077 == 0
            events.append("private")
        return fd

    def publish(fd, mode):
        assert destination.read_bytes() == payload and mode == 0o444
        assert events == ["private"]
        events.append("published")
        return original_chmod(fd, mode)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fchmod", publish)
    snapshot.materialize_snapshot(parent, "a" * 64, {"test.py": payload}, os.getuid())
    assert events == ["private", "published"]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o444
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o755
