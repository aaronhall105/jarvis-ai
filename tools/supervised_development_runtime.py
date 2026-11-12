"""Single production entrypoint for one owner-gated development task.

The runtime is deliberately a coordinator, not a second worker or scheduler.
It reads the existing Governor improvement database read-only, persists the
task in the supervised controller, waits for the already-installed owner and
Guardian approvals, and then invokes the bounded worker exactly once.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from tools.autonomy_broker_host import CANONICAL, SAFE_ENV
from tools.autonomy_guardian import identity as worker_identity
from tools.autonomy_broker_runtime import emergency_stop, guardian_status, installed_policy
from tools.supervised_development_controller import (
    APPROVAL_REQUESTED,
    FAILED,
    LifecycleError,
    REVIEW_PENDING,
    REVIEW_REQUIRED,
    RECONCILIATION_REQUIRED,
    RUNNING,
    SupervisedDevelopmentController,
    CONTROLLER_DATABASE,
)
from tools.supervised_development_pipeline import (
    ExistingSelfDevelopmentWorker,
    GovernorAdapter,
    GovernorTask,
    SupervisedDevelopmentPipeline,
)
from tools.supervised_development_review import TrustedReviewRunner
from tools.supervised_development_scope import editable_path
from tools.supervised_development_environment import (
    verify_worker_environment,
    worker_python_arguments,
)


# Core's authoritative /app/data is the protected host bind mount below.  The
# repository has no trusted Governor database and must never be used as a
# substitute or populated copy.
GOVERNOR_DATABASE = Path("/home/aaron/.local/share/jarvis-runtime/data/jarvis_improvement.db")
WORKTREE_ROOT = Path("/var/lib/jarvis-autonomy-broker/worktrees")
PROTECTED_SCOPE_PREFIXES = (
    "tools/autonomy_",
    "tools/supervised_development_",
    "tools/deploy_",
    "systemd/",
    ".github/",
)
OWNER_ID = "enrolled-operator"
POLL_SECONDS = 2
WORKER_FENCE_SECONDS = 0.25
WORKER_ENTRY = "/usr/local/libexec/jarvis-autonomy-broker/entry.py"


def _configured_call_limit() -> int:
    """Protected service configuration narrows the reviewed ceiling; task signs it.

    Defaults to the original single-call trial. The gateway, candidate JSON and
    model cannot choose this value or modify the service environment.
    """
    value = os.environ.get("JARVIS_SUPERVISED_MAX_CALLS", "1")
    if value not in {"1", "2", "3", "4"}:
        raise LifecycleError("governor_call_policy_invalid")
    return int(value)


_PROVIDER_CONFIGURATION_KEYS = (
    "JARVIS_SUPERVISED_MODEL",
    "JARVIS_SUPERVISED_PRICING_MODEL",
    "JARVIS_SUPERVISED_INPUT_USD_PER_MILLION",
    "JARVIS_SUPERVISED_OUTPUT_USD_PER_MILLION",
    "JARVIS_GPT53_INPUT_USD_PER_MILLION",
    "JARVIS_GPT53_OUTPUT_USD_PER_MILLION",
    "JARVIS_SUPERVISED_MAX_INPUT_TOKENS",
    "JARVIS_SUPERVISED_MAX_OUTPUT_TOKENS",
)


def _provider_configuration() -> dict[str, str]:
    """Only protected service configuration chooses a model and verified rates."""
    return {name: os.environ[name] for name in _PROVIDER_CONFIGURATION_KEYS if name in os.environ}


def _trusted_source_git(source_root: Path, *arguments: str) -> str:
    """Read the fixed source without trusting the process user's Git config.

    Production supplies the protected CANONICAL path. Root lacks the owner's
    repository safe.directory trust; grant only this exact checkout for this
    invocation, matching the existing protected broker inspection pattern.
    """
    root = Path(source_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise LifecycleError("trusted_source_snapshot_unavailable")
    command = [
        "/usr/bin/git",
        "--no-optional-locks",
        "-c",
        "safe.directory=" + str(root),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        "-C",
        str(root),
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if result.returncode != 0 or len(result.stdout) > 65536:
            raise LifecycleError("trusted_source_snapshot_unavailable")
        return result.stdout.decode("utf-8", "strict").strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise LifecycleError("trusted_source_snapshot_unavailable") from exc


class GovernorCandidateSource:
    """Read one evidence-backed queued candidate without modifying Governor DB."""

    def __init__(self, database: Path = GOVERNOR_DATABASE, source_root: Path | None = None):
        self.database = Path(database)
        self.source_root = Path(source_root) if source_root is not None else None
        if self.database.is_symlink() or not self.database.is_file():
            raise LifecycleError("governor_database_unavailable")
        self.db = sqlite3.connect(f"file:{self.database.resolve()}?mode=ro", uri=True, timeout=5)
        self.db.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"improvement_candidates", "improvement_failures"}.issubset(tables):
            self.db.close()
            raise LifecycleError("governor_schema_invalid")

    def close(self) -> None:
        self.db.close()

    def _current_source_sha(self) -> str | None:
        if self.source_root is None:
            return None
        status = _trusted_source_git(self.source_root, "status", "--porcelain")
        source_sha = _trusted_source_git(self.source_root, "rev-parse", "HEAD")
        if status or not re.fullmatch(r"[0-9a-f]{40,64}", source_sha):
            raise LifecycleError("trusted_source_snapshot_unavailable")
        return source_sha

    @staticmethod
    def _is_scope_eligible(summary: str, evidence: object) -> bool:
        """Validate explicit canonical application paths, never infer them from text."""
        if not isinstance(evidence, dict) or evidence.get("scope") != "application":
            return False
        paths = evidence.get("affected_files")
        if not isinstance(paths, list) or not paths:
            return False
        if not all(editable_path(path) for path in paths):
            return False
        for path in paths:
            if (
                not isinstance(path, str)
                or not path
                or "\\" in path
                or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", path)
                or path.startswith(("/", ".git", "developer_gateway/"))
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or path.startswith(PROTECTED_SCOPE_PREFIXES)
                or Path(path).suffix
                not in {".py", ".pyi", ".toml", ".yaml", ".yml", ".json", ".md", ".sh"}
            ):
                return False
        return len(set(paths)) == len(paths) and len(summary.strip()) >= 3

    def review_queue(self) -> list[dict[str, Any]]:
        """Read-only classification; historical rows and evidence are never repaired in place.

        This is also the admission decision used by next(), so the review report
        cannot disagree with execution eligibility. No database migrations.
        """
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(improvement_candidates)")}
        scope_column = "c.development_scope_json" if "development_scope_json" in columns else "'{}'"
        archived_column = "c.archived_at" if "archived_at" in columns else "NULL"
        rows = self.db.execute(
            f"SELECT c.candidate_id,c.failure_id,c.status,c.summary,{scope_column} AS scope_json,"
            f"{archived_column} AS archived_at,f.summary AS failure_summary,f.evidence_json,"
            "f.status AS failure_status FROM improvement_candidates c "
            "LEFT JOIN improvement_failures f ON f.failure_id=c.failure_id "
            "WHERE c.status='queued' ORDER BY c.candidate_id"
        ).fetchall()
        try:
            current_sha = self._current_source_sha()
        except LifecycleError:
            current_sha = None
        reports = []
        for row in rows:
            report: dict[str, Any] = {
                "candidate_id": int(row["candidate_id"]),
                "failure_id": int(row["failure_id"]),
                "status": row["status"],
                "summary": str(row["summary"] or row["failure_summary"] or "").strip(),
                "eligible": False,
                "classification": "review_required",
                "current_source_sha": current_sha,
            }
            try:
                evidence = json.loads(row["evidence_json"])
                scope = json.loads(row["scope_json"] or "{}")
            except (TypeError, ValueError):
                report["classification"] = "malformed_evidence"
                reports.append(report)
                continue
            reason = self._classification(row, report["summary"], evidence, scope, current_sha)
            report["classification"] = reason
            if isinstance(scope, dict):
                report["bound_source_sha"] = scope.get("source_sha")
            if reason == "eligible":
                report.update(
                    eligible=True,
                    evidence={**evidence, **scope},
                    allowed_paths=tuple(scope["affected_files"]),
                )
            reports.append(report)
        return reports

    def _classification(self, row, summary, evidence, scope, current_sha) -> str:
        if row["archived_at"] or row["failure_status"] not in {"queued", "recorded"}:
            return "inactive_failure_or_candidate"
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("source"), dict)
            or not evidence["source"]
        ):
            return "missing_source_evidence"
        if not isinstance(scope, dict) or not scope:
            return "unscoped_review_required"
        if not self._is_scope_eligible(summary, scope):
            return "invalid_application_scope"
        binding = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            type(scope.get("evidence_failure_id")) is not int
            or scope["evidence_failure_id"] != row["failure_id"]
            or scope.get("evidence_sha256") != binding
        ):
            return "stale_failure_evidence"
        source_sha = scope.get("source_sha")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            return "missing_source_binding"
        if current_sha is None:
            return "source_snapshot_unavailable"
        if source_sha != current_sha:
            return "historical_source_review_required"
        inventory: dict[str, str] = {}
        if self.source_root is not None:
            try:
                for line in _trusted_source_git(
                    self.source_root, "ls-tree", "-r", current_sha, "--", *scope["affected_files"]
                ).splitlines():
                    metadata, name = line.split("\t", 1)
                    inventory[name] = metadata.split()[0]
            except (LifecycleError, ValueError):
                return "source_snapshot_unavailable"
        if any(inventory.get(path) not in {"100644", "100755"} for path in scope["affected_files"]):
            return "untracked_or_nonregular_scope"
        review = scope.get("review")
        if not isinstance(review, dict):
            return "scope_review_required"
        findings = review.get("file_findings")
        if (
            not isinstance(review.get("reviewed_by"), str)
            or not review["reviewed_by"].strip()
            or review.get("source_sha") != source_sha
            or review.get("evidence_sha256") != binding
            or not isinstance(review.get("reproduction"), str)
            or len(review["reproduction"].strip()) < 10
            or not isinstance(findings, dict)
            or set(findings) != set(scope["affected_files"])
            or any(
                not isinstance(value, str) or len(value.strip()) < 10 for value in findings.values()
            )
        ):
            return "scope_review_required"
        return "eligible"

    def next(self, excluded: set[int] | None = None) -> dict[str, Any] | None:
        excluded = excluded or set()
        for report in self.review_queue():
            if report["eligible"] and report["candidate_id"] not in excluded:
                return report
        return None


class RuntimeReview:
    def review(self, task: GovernorTask, evidence: dict[str, Any]) -> dict[str, Any]:
        workspace = evidence.get("worktree")
        if not isinstance(workspace, str):
            raise LifecycleError("review_workspace_missing")
        return TrustedReviewRunner(
            Path(workspace), base_sha=task.source_sha, allowed_paths=task.allowed_paths
        ).review(task, evidence)


class _SubmitOnlyWorker:
    def run(self, task: GovernorTask, *, call_ledger: Any) -> dict[str, Any]:
        raise LifecycleError("worker_not_initialized")


class SupervisedDevelopmentRuntime:
    """Coordinate exactly one durable task, then stop at REVIEW_REQUIRED."""

    def __init__(
        self,
        source: GovernorCandidateSource,
        controller: SupervisedDevelopmentController,
        *,
        source_root: Path = CANONICAL,
        worktree_root: Path = WORKTREE_ROOT,
        guardian: Callable[[], dict[str, Any]] = guardian_status,
        stop: Callable[[], None] = emergency_stop,
        sleep: Callable[[float], None] = time.sleep,
        worker: Any | None = None,
        reviewer: Any | None = None,
    ):
        self.source = source
        self.controller = controller
        self.source_root = Path(source_root)
        self.worktree_root = Path(worktree_root)
        self.guardian = guardian
        self.stop = stop
        self.sleep = sleep
        self.worker = worker
        self.reviewer = reviewer or RuntimeReview()

    def _source_sha(self) -> str:
        return _trusted_source_git(self.source_root, "rev-parse", "HEAD")

    def _claimed_candidates(self) -> set[int]:
        claimed: set[int] = set()
        for row in self.controller.db.execute("SELECT parameters FROM tasks").fetchall():
            try:
                candidate_id = json.loads(row[0]).get("candidate_id")
            except (TypeError, json.JSONDecodeError):
                continue
            if type(candidate_id) is int:
                claimed.add(candidate_id)
        return claimed

    def _existing_task(self) -> tuple[str, GovernorTask] | None:
        row = self.controller.db.execute(
            "SELECT id,state,parameters,approval FROM tasks "
            "WHERE state IN (?,?,?,?) ORDER BY created_at,id LIMIT 1",
            (APPROVAL_REQUESTED, RUNNING, REVIEW_PENDING, RECONCILIATION_REQUIRED),
        ).fetchone()
        if row is None:
            return None
        params = json.loads(row["parameters"])
        approval = json.loads(row["approval"] or "{}")
        return row["id"], GovernorTask(
            objective=params["goal"],
            source_sha=params["base_sha"],
            allowed_paths=tuple(params["allowed_paths"]),
            provider=params["provider"],
            model=params["model"],
            max_calls=params["max_calls"],
            max_cost_usd=params["max_cost_usd"],
            deadline_seconds=params["deadline_seconds"],
            owner_id=approval.get("owner_id", OWNER_ID),
            request_ref=approval.get("request_ref", ""),
            candidate_id=params.get("candidate_id"),
            guardian_generation=approval.get("guardian_generation", ""),
            expires_at=approval.get("expires_at", 0),
        )

    def _run_worker_subprocess(self, task_id: str, task: GovernorTask) -> dict[str, Any]:
        """Delegate execution to the policy-pinned unprivileged worker account.

        The root controller never calls the admission socket itself.  Only this
        child may perform development execution, so SO_PEERCRED observes the
        configured autonomy UID and rejects a root-originated admission.
        """
        generation = self._worker_generation(task)
        policy = installed_policy()
        worker_uid = int(policy["autonomy_uid"])
        worker_gid = int(policy.get("autonomy_gid", worker_uid))
        if worker_uid <= 0 or worker_uid == 0:
            raise LifecycleError("worker_identity_invalid")
        # Read/hash the fixed root-owned environment before dropping identity;
        # never import SDK/package code into the privileged broker interpreter.
        worker_python = verify_worker_environment()

        def drop_privileges() -> None:
            os.setgroups([])
            os.setgid(worker_gid)
            os.setuid(worker_uid)

        # Explicit allow-list: never inherit GitHub, Docker, other provider,
        # operator or signing credentials from the privileged coordinator.
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
        for name in ("OPENAI_API_KEY", *_PROVIDER_CONFIGURATION_KEYS):
            if name in os.environ:
                environment[name] = os.environ[name]
        command = [
            str(worker_python),
            *worker_python_arguments(worker_python),
            WORKER_ENTRY,
            "development-worker",
            task_id,
        ]
        if self._worker_generation(task) != generation:
            raise LifecycleError("development_worker_generation_changed")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
            preexec_fn=drop_privileges,
        )
        # The execution budget is not extended by the service's cleanup grace.
        # Kill a blocked SDK/validation child at the signed wall-clock ceiling;
        # authoritative STOP and reap happen afterward, without another call.
        timeout = max(1, int(task.deadline_seconds))
        deadline = time.monotonic() + timeout
        try:
            while True:
                # The independent Guardian also terminates the protected UID.
                # This fence additionally catches STOP/untrusted observation or
                # a new activation cycle while an SDK request is blocked.
                if self._worker_generation(task) != generation:
                    raise LifecycleError("development_worker_generation_changed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, _stderr = process.communicate(
                        timeout=min(WORKER_FENCE_SECONDS, remaining)
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException as exc:
            # Cancellation/service shutdown must not leave the paid worker
            # alive while the coordinator records STOP. Nested verification
            # namespaces also use die-with-parent; never retry this execution.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The coordinator deliberately lacks CAP_KILL. Signal only
                # this unreaped child group from the already-authorised worker
                # identity, not by broadening the root service capabilities.
                try:
                    subprocess.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-c",
                            "import os,signal,sys\n"
                            "try: os.killpg(int(sys.argv[1]),signal.SIGKILL)\n"
                            "except ProcessLookupError: pass\n",
                            str(process.pid),
                        ],
                        preexec_fn=drop_privileges,
                        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=True,
                    )
                except (OSError, subprocess.SubprocessError) as signal_error:
                    raise LifecycleError("development_worker_cleanup_unconfirmed") from signal_error
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired as cleanup_error:
                raise LifecycleError("development_worker_cleanup_unconfirmed") from cleanup_error
            if isinstance(exc, subprocess.TimeoutExpired):
                raise LifecycleError("development_worker_timeout") from exc
            raise
        if process.returncode != 0:
            raise LifecycleError("development_worker_failed")
        try:
            result = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LifecycleError("development_worker_result_invalid") from exc
        if not isinstance(result, dict) or result.get("state") not in {
            REVIEW_REQUIRED,
            FAILED,
            RECONCILIATION_REQUIRED,
        }:
            raise LifecycleError("development_worker_result_invalid")
        return result

    def _worker_generation(self, task: GovernorTask) -> str:
        """Observation can only deny; signed broker admission is still required."""
        try:
            status = self.guardian()
            generation = status["generation"]
            if (
                status.get("state") != "ACTIVE"
                or status.get("trusted") is not True
                or not isinstance(generation, str)
                or str(uuid.UUID(generation)) != generation
                or task.guardian_generation not in (generation, status.get("previous_generation"))
            ):
                raise ValueError("inactive or unrelated Guardian cycle")
            return generation
        except Exception as exc:
            raise LifecycleError("development_worker_guardian_denied") from exc

    def _confirm_stop(self) -> None:
        self.stop()
        status = self.guardian()
        if status.get("state") != "STOPPED" or status.get("trusted") is not True:
            raise LifecycleError("emergency_stop_unconfirmed")

    def run_once(self) -> dict[str, Any]:
        execution_active = False
        stop_attempted = False
        try:
            status = self.guardian()
            if status.get("state") != "STOPPED" or status.get("trusted") is not True:
                raise LifecycleError("guardian_must_be_stopped_before_task_selection")
            self.controller.expire_stale_approval_requests()
            existing = self._existing_task()
            if existing is None:
                candidate = self.source.next(self._claimed_candidates())
                if candidate is None:
                    return {"status": "no_eligible_candidate"}
                source_sha = self._source_sha()
                bound_source_sha = str(candidate["evidence"].get("source_sha") or "")
                if bound_source_sha and bound_source_sha != source_sha:
                    return {"status": "no_eligible_candidate", "reason": "source_binding_mismatch"}
                task = GovernorAdapter.from_candidate(
                    candidate,
                    owner_id=OWNER_ID,
                    request_ref=str(uuid.uuid4()),
                    source_sha=source_sha,
                    allowed_paths=tuple(candidate["allowed_paths"]),
                    guardian_generation=str(status["generation"]),
                    expires_at=int(time.time()) + 90,
                    max_calls=_configured_call_limit(),
                    model=os.environ.get("JARVIS_SUPERVISED_MODEL", "gpt-5.3-codex"),
                )
                task_id = SupervisedDevelopmentPipeline(
                    self.controller,
                    self.worker or _SubmitOnlyWorker(),
                    self.reviewer or RuntimeReview(),
                ).submit(task)
            else:
                task_id, task = existing
                if self.controller.snapshot(task_id)["state"] in {RUNNING, REVIEW_PENDING}:
                    self.controller.recover_after_restart(task_id)
                    return {"status": "reconciliation_required", "task_id": task_id}
            pipeline = SupervisedDevelopmentPipeline(
                self.controller,
                self.worker
                or ExistingSelfDevelopmentWorker(
                    object(),
                    {
                        "JARVIS_AUTHORITATIVE_WORKSPACE": str(self.source_root),
                        "JARVIS_SUPERVISED_WORKTREE_ROOT": str(self.worktree_root),
                        **_provider_configuration(),
                    },
                ),
                self.reviewer,
            )
            while int(time.time()) < task.expires_at:
                current = self.controller.snapshot(task_id)
                if current["state"] in {REVIEW_REQUIRED, FAILED, RECONCILIATION_REQUIRED}:
                    return current
                current_status = self.guardian()
                if (
                    current_status.get("state") == "ACTIVE"
                    and current_status.get("trusted") is True
                    and (
                        current_status.get("generation") == task.guardian_generation
                        or current_status.get("previous_generation") == task.guardian_generation
                    )
                ):
                    execution_active = True
                    try:
                        if self.worker is not None:
                            result = pipeline.execute(
                                task_id, task, current_base_sha=self._source_sha()
                            )
                        else:
                            result = self._run_worker_subprocess(task_id, task)
                    finally:
                        stop_attempted = True
                        self._confirm_stop()
                        execution_active = False
                    return result
                self.sleep(POLL_SECONDS)
            if self.guardian().get("state") == "ACTIVE":
                self._confirm_stop()
            self.controller.expire_stale_approval_requests()
            return self.controller.snapshot(task_id)
        except BaseException:
            if not stop_attempted:
                try:
                    final_status = self.guardian()
                except BaseException:
                    final_status = {}
                # Activation can occur while waiting, before execution_active
                # is set. An observation/crash in that interval must not leave
                # the host ACTIVE or silently claim a safe shutdown.
                if (
                    execution_active
                    or final_status.get("state") != "STOPPED"
                    or final_status.get("trusted") is not True
                ):
                    stop_attempted = True
                    self._confirm_stop()
            raise


def worker_main(task_id: str) -> int:
    """Run one persisted task as the policy-pinned unprivileged worker."""
    # A direct invocation cannot select a different Python/package environment
    # or start development while the independent Guardian is stopped/untrusted.
    try:
        if os.getuid() == 0 or os.getuid() != worker_identity():
            return 1
        status = guardian_status()
        generation = status.get("generation")
        if (
            status.get("state") != "ACTIVE"
            or status.get("trusted") is not True
            or not isinstance(generation, str)
            or str(uuid.UUID(generation)) != generation
            or Path(sys.executable) != verify_worker_environment()
            or not sys.dont_write_bytecode
            or sys.pycache_prefix != str(Path(sys.executable).parent.parent / "bytecode-disabled")
        ):
            return 1
    except Exception:
        return 1
    controller = SupervisedDevelopmentController(CONTROLLER_DATABASE, database_mode=0o660)
    source = GovernorCandidateSource(GOVERNOR_DATABASE, source_root=CANONICAL)
    try:
        runtime = SupervisedDevelopmentRuntime(
            source,
            controller,
            source_root=CANONICAL,
            worktree_root=WORKTREE_ROOT,
        )
        existing = runtime._existing_task()
        if existing is None or existing[0] != task_id:
            raise LifecycleError("development_task_missing")
        task = existing[1]
        worker = ExistingSelfDevelopmentWorker(
            object(),
            {
                "JARVIS_AUTHORITATIVE_WORKSPACE": str(CANONICAL),
                "JARVIS_SUPERVISED_WORKTREE_ROOT": str(WORKTREE_ROOT),
                **_provider_configuration(),
            },
        )
        pipeline = SupervisedDevelopmentPipeline(controller, worker, RuntimeReview())
        result = pipeline.execute(task_id, task, current_base_sha=runtime._source_sha())
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException as exc:
        print(json.dumps({"error": type(exc).__name__}), flush=True)
        return 1
    finally:
        controller.close()
        source.close()


def initialize_controller_database(policy: dict[str, Any]) -> None:
    """Create only the empty protected controller schema, never a task.

    This is an explicit owner-authenticated preparation operation.  It refuses
    to replace an existing database so recovery cannot erase lifecycle state.
    """
    if os.geteuid() != 0 or os.getenv("SUDO_USER") != "aaron":
        raise LifecycleError("owner_authenticated_initialization_required")
    if os.getenv("SUDO_UID") != str(policy["operator_uid"]):
        raise LifecycleError("owner_authenticated_initialization_required")
    guardian = guardian_status()
    if guardian.get("state") != "STOPPED" or guardian.get("trusted") is not True:
        raise LifecycleError("guardian_must_be_trusted_stopped")
    if CONTROLLER_DATABASE.is_symlink() or CONTROLLER_DATABASE.exists():
        raise LifecycleError("controller_database_already_exists")
    parent = CONTROLLER_DATABASE.parent
    if parent.is_symlink() or not parent.is_dir():
        raise LifecycleError("controller_database_parent_invalid")
    controller = SupervisedDevelopmentController(CONTROLLER_DATABASE, database_mode=0o660)
    try:
        if controller.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] != 0:
            raise LifecycleError("controller_database_not_empty")
    finally:
        controller.close()
    os.chown(CONTROLLER_DATABASE, int(policy["autonomy_uid"]), int(policy["autonomy_gid"]))
    CONTROLLER_DATABASE.chmod(0o660)


def main() -> int:
    source = GovernorCandidateSource(source_root=CANONICAL)
    controller = SupervisedDevelopmentController(CONTROLLER_DATABASE, database_mode=0o660)

    def _shutdown(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("controller_shutdown")

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        result = SupervisedDevelopmentRuntime(source, controller).run_once()
        print(json.dumps({"status": result.get("status", result.get("state", "unknown"))}))
        return 0
    finally:
        controller.close()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
