"""Real Git regression, in disposable repositories only. No root required."""

import hashlib
import os
import subprocess

import pytest

from tools import autonomy_broker_runtime as runtime
from tools.autonomy_broker import Denied
from tools.autonomy_broker_host import SAFE_ENV


@pytest.fixture
def canonical_repo(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()

    def git(*args, env=None, check=True):
        return subprocess.run(
            ["/usr/bin/git", "--no-optional-locks", "-C", str(root), *args],
            env=env or SAFE_ENV,
            capture_output=True,
            check=check,
        )

    git("init", "-b", "jarvis/unified-production")
    (root / "source.py").write_text("value = 1\n")
    git("add", "source.py")
    git(
        "-c",
        "user.name=Broker Test",
        "-c",
        "user.email=broker@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    sha = git("rev-parse", "HEAD").stdout.decode().strip()
    config = root / ".git/config"
    policy = {
        "operator_uid": os.getuid(),
        "git_metadata_hashes": {str(config): hashlib.sha256(config.read_bytes()).hexdigest()},
    }
    monkeypatch.setattr(runtime, "CANONICAL", root)
    # Git's own test switch exercises the root/foreign-owner trust rejection
    # while the test itself remains entirely unprivileged.
    foreign = {**SAFE_ENV, "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
    monkeypatch.setattr(runtime, "SAFE_ENV", foreign)
    return root, git, sha, policy, foreign


def snapshot(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_foreign_owner_reproduction_and_exact_path_fix_is_read_only(canonical_repo):
    root, git, sha, policy, foreign = canonical_repo
    refused = git("rev-parse", "HEAD", env=foreign, check=False)
    assert refused.returncode == 128 and b"dubious ownership" in refused.stderr
    before = snapshot(root)
    runtime.canonical_clean(policy, sha)
    assert snapshot(root) == before
    assert b"safe.directory" not in (root / ".git/config").read_bytes()


@pytest.mark.parametrize("dirty", ["tracked", "staged", "untracked"])
def test_real_dirty_tree_is_rejected_and_preserved(canonical_repo, dirty):
    root, git, sha, policy, _ = canonical_repo
    if dirty == "untracked":
        (root / "unexpected.py").write_text("value = 2\n")
    else:
        (root / "source.py").write_text("value = 2\n")
        if dirty == "staged":
            git("add", "source.py")
    before = snapshot(root)
    with pytest.raises(Denied, match="canonical_worktree_not_clean_current"):
        runtime.canonical_clean(policy, sha)
    assert snapshot(root) == before


def test_wrong_sha_and_branch_are_rejected(canonical_repo):
    _, git, sha, policy, _ = canonical_repo
    with pytest.raises(Denied, match="canonical_worktree_not_clean_current"):
        runtime.canonical_clean(policy, "0" * 40)
    git("branch", "-m", "unrelated")
    with pytest.raises(Denied, match="canonical_worktree_not_clean_current"):
        runtime.canonical_clean(policy, sha)


def test_wrong_owner_rejected_before_git(canonical_repo):
    _, _, sha, policy, _ = canonical_repo
    policy["operator_uid"] += 1
    with pytest.raises(Denied, match="canonical_worktree_owner_changed"):
        runtime.canonical_clean(policy, sha)


def test_git_execution_error_is_not_misreported_as_dirty(canonical_repo, monkeypatch):
    from types import SimpleNamespace

    _, _, sha, policy, _ = canonical_repo
    monkeypatch.setattr(
        runtime.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=128, stdout=b"")
    )
    with pytest.raises(Denied, match="canonical_git_query_failed"):
        runtime.canonical_clean(policy, sha)


def test_runtime_and_validation_artifacts_stay_outside_canonical(canonical_repo):
    from tools import autonomy_broker_install as installer
    from tools.autonomy_broker_release import LIVE_ROOT

    root, _, sha, policy, _ = canonical_repo
    for path in (runtime.BASE, runtime.JOBS, runtime.LIB, LIVE_ROOT, installer.POLICY):
        assert not path.is_relative_to(root)
        assert not path.is_relative_to(installer.CANONICAL)
    before = snapshot(root)
    installer.payload()  # Reads feature source; never generates canonical files.
    runtime.canonical_clean(policy, sha)
    assert snapshot(root) == before
