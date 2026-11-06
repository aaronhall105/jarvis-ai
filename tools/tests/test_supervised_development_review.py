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


def test_review_runner_verifies_real_diff(tmp_path):
    base, digest = _repo(tmp_path)
    review = TrustedReviewRunner(tmp_path, base_sha=base, allowed_paths=("diagnostic.py",))
    result = review.review(object(), _evidence(digest))
    assert result["verified"] is True
    assert result["diff_sha256"] == digest


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
