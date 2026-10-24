import os
from pathlib import Path

import pytest

from tools.autonomy_broker import Denied
from tools.autonomy_broker_snapshot import read_source_tree


def test_bounded_snapshot_preserves_source_not_git_metadata(tmp_path):
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge/app.py").write_text("value = 1\n")
    (tmp_path / ".git").write_text("must never enter sandbox")
    files, digest = read_source_tree(tmp_path, os.getuid())
    assert files == {"bridge/app.py": b"value = 1\n"}
    assert len(digest) == 64
    assert read_source_tree(tmp_path, os.getuid())[1] == digest


@pytest.mark.parametrize("attack", ["symlink_file", "symlink_dir", "hardlink", "fifo", "secret"])
def test_escape_and_special_sources_rejected(tmp_path, attack):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("not source")
    if attack == "symlink_file":
        (workspace / "source.py").symlink_to(outside)
    elif attack == "symlink_dir":
        (workspace / "sub").symlink_to(tmp_path, target_is_directory=True)
    elif attack == "hardlink":
        os.link(outside, workspace / "source.py")
    elif attack == "fifo":
        os.mkfifo(workspace / "pipe")
    else:
        (workspace / ".env").write_text("not a real credential")
    with pytest.raises((Denied, OSError)):
        read_source_tree(workspace, os.getuid())


def test_symlink_ancestor_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        read_source_tree(alias, os.getuid())


def test_wrong_owner_rejected(tmp_path):
    with pytest.raises(Denied):
        read_source_tree(tmp_path, os.getuid() + 1)


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(Denied):
        read_source_tree(Path(str(tmp_path) + "/../escape"), os.getuid())


def test_environment_example_is_not_opened_or_copied(tmp_path):
    (tmp_path / ".env.example").symlink_to("/must/not/be/opened")
    (tmp_path / "source.py").write_text("value = 1\n")
    files, _ = read_source_tree(tmp_path, os.getuid())
    assert files == {"source.py": b"value = 1\n"}
