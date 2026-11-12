from __future__ import annotations

import hashlib
import subprocess

import pytest

from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_review import TrustedReviewRunner


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "diagnostic.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "diagnostic.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    (tmp_path / "diagnostic.py").write_text("VALUE = 2\n")
    diff = subprocess.check_output(["git", "-C", str(tmp_path), "diff", "--binary", base])
    return base, hashlib.sha256(diff).hexdigest()


def _evidence(diff_sha):
    return {
        "proposal": "Update the diagnostic value.",
        "diff_sha256": diff_sha,
        "tests": {"passed": True},
        "security": {"passed": True},
        "changed_files": ["diagnostic.py"],
    }


def test_review_runner_rejects_boolean_claims_without_execution_record(tmp_path):
    base, digest = _repo(tmp_path)
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    with pytest.raises(LifecycleError, match="review_execution_record_missing"):
        review.review(object(), _evidence(digest))


def test_review_runner_rejects_scope_and_claimed_results(tmp_path):
    base, digest = _repo(tmp_path)
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("other.py",))
    with pytest.raises(LifecycleError, match="review_scope_violation"):
        review.review(object(), _evidence(digest))


def test_review_runner_rejects_failed_checks_and_diff_tampering(tmp_path):
    base, digest = _repo(tmp_path)
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    evidence = _evidence(digest)
    evidence["tests"] = {"passed": False}
    with pytest.raises(LifecycleError, match="review_checks_failed"):
        review.review(object(), evidence)
    evidence["tests"] = {"passed": True}
    evidence["diff_sha256"] = "0" * 64
    with pytest.raises(LifecycleError, match="review_diff_mismatch"):
        review.review(object(), evidence)


def test_review_runner_rejects_source_drift(tmp_path):
    base, digest = _repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "drift"], check=True
    )
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    with pytest.raises(LifecycleError, match="review_source_drift"):
        review.review(object(), _evidence(digest))


def test_review_runner_requires_actual_unmodified_private_execution_receipt(tmp_path):
    from pathlib import Path
    from tools.supervised_development_worker import verification_receipt
    from tools.tests.test_supervised_development_worker import (
        RecordingLedger,
        _repo as source_repo,
        _worker,
        _task,
        _reply,
        _edit,
    )

    root, sha = source_repo(tmp_path)
    worker = _worker(
        tmp_path,
        root,
        lambda *args: _reply(
            _edit("diagnostic.py", "VALUE = 1", "VALUE = 2"),
            _edit("test_diagnostic.py", "assert VALUE == 1", "assert VALUE == 2"),
        ),
    )
    task = _task(sha)
    evidence = worker.run(task, call_ledger=RecordingLedger())
    workspace = Path(evidence["worktree"])
    reviewer = TrustedReviewRunner(workspace, base_sha=sha, allowed_paths=task.allowed_paths)
    assert reviewer.review(task, evidence)["verified"]
    receipt = verification_receipt(workspace)
    original = receipt.read_bytes()
    receipt.write_bytes(original + b" ")
    with pytest.raises(LifecycleError, match="record_mismatch"):
        reviewer.review(task, evidence)
    receipt.write_bytes(original)
    receipt.chmod(0o666)
    with pytest.raises(LifecycleError, match="record_untrusted"):
        reviewer.review(task, evidence)
    receipt.chmod(0o600)
    evidence["tests"]["checks"] = []
    with pytest.raises(LifecycleError, match="checks_missing"):
        reviewer.review(task, evidence)


def test_review_runner_rejects_staged_changes_without_a_commit(tmp_path):
    base, digest = _repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "diagnostic.py"], check=True)
    reviewer = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    with pytest.raises(LifecycleError, match="staged_git_changes"):
        reviewer.review(object(), _evidence(digest))


def test_review_git_cannot_inherit_external_diff_or_fsmonitor(tmp_path, monkeypatch):
    base, digest = _repo(tmp_path)
    marker = tmp_path.parent / "unexpected-git-command"
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", f"touch {marker}")
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.fsmonitor", f"touch {marker}"], check=True
    )
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    names, diff = review._scope()
    assert names == ["diagnostic.py"]
    assert hashlib.sha256(diff).hexdigest() == digest
    assert not marker.exists()
