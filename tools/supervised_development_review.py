"""Read-only verifier for supervised-development proposal evidence.

The runner is deliberately not an authority.  It can produce a verification
record for the lifecycle controller, but it cannot admit execution, merge code,
or deploy anything.  All source and diff claims are recomputed locally.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from tools.supervised_development_controller import LifecycleError


class TrustedReviewRunner:
    """Verify a proposal against a fixed checkout and immutable task scope."""

    def __init__(self, workspace: Path, *, base_sha: str, allowed_paths: tuple[str, ...]):
        self.workspace = Path(workspace).resolve()
        self.base_sha = base_sha
        self.allowed_paths = frozenset(allowed_paths)
        if not self.workspace.is_dir() or not self.allowed_paths:
            raise LifecycleError("review_workspace_invalid")

    def _git(self, *args: str) -> bytes:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.workspace,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleError("review_git_unavailable") from exc
        return result.stdout

    def _scope(self) -> tuple[list[str], bytes]:
        actual = self._git("rev-parse", "HEAD").decode("ascii", "strict").strip()
        if actual != self.base_sha:
            raise LifecycleError("review_source_drift")
        status = self._git("status", "--porcelain", "--untracked-files=all").decode(
            "utf-8", "strict"
        )
        if any(line.startswith("?? ") for line in status.splitlines()):
            raise LifecycleError("review_untracked_files")
        names = (
            self._git("diff", "--name-only", self.base_sha, "--")
            .decode("utf-8", "strict")
            .splitlines()
        )
        if not names or any(name not in self.allowed_paths for name in names):
            raise LifecycleError("review_scope_violation")
        for name in names:
            raw = self.workspace / name
            path = raw.resolve()
            if raw.is_symlink() or not str(path).startswith(str(self.workspace) + "/"):
                raise LifecycleError("review_path_violation")
        return names, self._git("diff", "--binary", self.base_sha, "--")

    def review(self, task: Any, evidence: dict[str, Any]) -> dict[str, Any]:
        required = {"proposal", "diff_sha256", "tests", "security", "changed_files"}
        if not isinstance(evidence, dict) or not required.issubset(evidence):
            raise LifecycleError("review_evidence_missing")
        if not isinstance(evidence["proposal"], str) or not evidence["proposal"].strip():
            raise LifecycleError("review_proposal_invalid")
        names, diff = self._scope()
        expected = hashlib.sha256(diff).hexdigest()
        if evidence["diff_sha256"] != expected:
            raise LifecycleError("review_diff_mismatch")
        changed = evidence["changed_files"]
        if not isinstance(changed, list) or any(not isinstance(name, str) for name in changed):
            raise LifecycleError("review_changed_files_malformed")
        if sorted(changed) != sorted(names) or any(
            name not in self.allowed_paths for name in changed
        ):
            raise LifecycleError("review_changed_files_mismatch")
        tests = evidence["tests"]
        security = evidence["security"]
        if not isinstance(tests, dict) or not isinstance(security, dict):
            raise LifecycleError("review_results_malformed")
        if tests.get("passed") is not True or security.get("passed") is not True:
            raise LifecycleError("review_checks_failed")
        return {
            "verified": True,
            "checks": ["source_provenance", "diff_scope", "test_results", "security_results"],
            "changed_files": names,
            "diff_sha256": expected,
        }
