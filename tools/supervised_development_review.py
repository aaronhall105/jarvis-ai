"""Read-only verifier for supervised-development proposal evidence.

The runner is deliberately not an authority.  It can produce a verification
record for the lifecycle controller, but it cannot admit execution, merge code,
or deploy anything.  All source and diff claims are recomputed locally.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_scope import editable_path
from tools.supervised_development_worker import CHECK_NAMES, verification_receipt


class TrustedReviewRunner:
    """Verify a proposal against a fixed checkout and immutable task scope."""

    def __init__(
        self,
        workspace: Path,
        *,
        base_sha: str,
        allowed_paths: tuple[str, ...],
        expected_receipt_uid: int | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.base_sha = base_sha
        self.allowed_paths = frozenset(allowed_paths)
        # The integration service supplies its fixed protected worker UID from
        # trusted policy, never an owner/task/model-supplied receipt identity.
        self.expected_receipt_uid = (
            os.getuid() if expected_receipt_uid is None else expected_receipt_uid
        )
        if type(self.expected_receipt_uid) is not int or self.expected_receipt_uid < 0:
            raise LifecycleError("review_receipt_identity_invalid")
        if (
            not self.workspace.is_dir()
            or not self.allowed_paths
            or not re.fullmatch(r"[0-9a-f]{40,64}", base_sha)
            or any(not editable_path(path) for path in self.allowed_paths)
        ):
            raise LifecycleError("review_workspace_invalid")

    def _git(self, *args: str) -> bytes:
        try:
            if args and args[0] == "diff":
                args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
            result = subprocess.run(
                [
                    "/usr/bin/git",
                    "--no-optional-locks",
                    "-c",
                    "safe.directory=" + str(self.workspace),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.attributesFile=/dev/null",
                    *args,
                ],
                cwd=self.workspace,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/nonexistent",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_TERMINAL_PROMPT": "0",
                },
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
        if self._git("diff", "--cached", "--name-only").strip():
            raise LifecycleError("review_staged_git_changes")
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

    def _execution_receipt(self, evidence: dict[str, Any], names: list[str], digest: str) -> None:
        """Read coordinator-written records outside generated-code mounts.

        This is an execution provenance boundary, not a signature or protection
        against a compromised coordinator UID. Generated tests see only the
        read-only /workspace mount and cannot manufacture this sibling file.
        """
        path = verification_receipt(self.workspace)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != self.expected_receipt_uid
                    or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o640}
                    or metadata.st_nlink != 1
                    or metadata.st_size > 1_048_576
                ):
                    raise LifecycleError("review_execution_record_untrusted")
                encoded = stream.read()
            receipt = json.loads(encoded)
        except (OSError, ValueError) as exc:
            raise LifecycleError("review_execution_record_missing") from exc
        if hashlib.sha256(encoded).hexdigest() != evidence.get(
            "verification_receipt_sha256"
        ) or not isinstance(receipt, dict):
            raise LifecycleError("review_execution_record_mismatch")
        if (
            receipt.get("version") != 1
            or receipt.get("base_sha") != self.base_sha
            or receipt.get("diff_sha256") != digest
            or receipt.get("changed_files") != names
            or receipt.get("sandbox") != "bubblewrap-read-only-v1"
        ):
            raise LifecycleError("review_execution_record_mismatch")
        checks = receipt.get("checks")
        if (
            not isinstance(checks, list)
            or [check.get("name") for check in checks if isinstance(check, dict)]
            != list(CHECK_NAMES)
            or evidence["tests"].get("checks") != checks
        ):
            raise LifecycleError("review_execution_checks_missing")
        for check in checks:
            if (
                check.get("returncode") != 0
                or type(check.get("duration_ms")) is not int
                or check["duration_ms"] < 0
                or not isinstance(check.get("command"), list)
                or not check["command"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(check.get("output_sha256", "")))
            ):
                raise LifecycleError("review_execution_checks_invalid")
        counts = checks[0].get("test_counts")
        if (
            not isinstance(counts, dict)
            or type(counts.get("executed")) is not int
            or counts["executed"] <= 0
            or counts.get("failed") != 0
            or type(counts.get("collected")) is not int
            or counts["collected"] < counts["executed"]
        ):
            raise LifecycleError("review_executed_tests_unverified")

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
        self._execution_receipt(evidence, names, expected)
        return {
            "verified": True,
            "checks": [
                "source_provenance",
                "diff_scope",
                "test_results",
                "security_results",
                "isolated_execution_receipt",
            ],
            "changed_files": names,
            "diff_sha256": expected,
        }
