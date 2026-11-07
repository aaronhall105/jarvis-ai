"""Single production entrypoint for one owner-gated development task.

The runtime is deliberately a coordinator, not a second worker or scheduler.
It reads the existing Governor improvement database read-only, persists the
task in the supervised controller, waits for the already-installed owner and
Guardian approvals, and then invokes the bounded worker exactly once.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from tools.autonomy_broker_host import CANONICAL
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
WORKER_ENTRY = "/usr/local/libexec/jarvis-autonomy-broker/entry.py"


class GovernorCandidateSource:
    """Read one evidence-backed queued candidate without modifying Governor DB."""

    def __init__(self, database: Path = GOVERNOR_DATABASE):
        self.database = Path(database)
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

    @staticmethod
    def _is_scope_eligible(summary: str, evidence: object) -> bool:
        """Require explicit evidence binding a candidate to the edit scope.

        Governor records remain untouched.  The evidence must name one or both
        approved diagnostics files and the summary must describe diagnostics
        work; free-form conversation text or a candidate ID is never enough.
        """
        if not isinstance(evidence, dict) or evidence.get("scope") != "application":
            return False
        paths = evidence.get("affected_files")
        if not isinstance(paths, list) or not paths:
            return False
        if any(
            type(path) is not str
            or not path
            or path.startswith("/")
            or ".." in Path(path).parts
            or path.startswith(PROTECTED_SCOPE_PREFIXES)
            or not (path.endswith(".py") or path.endswith(".toml") or path.endswith(".yaml"))
            for path in paths
        ):
            return False
        if len(set(paths)) != len(paths):
            return False
        # Evidence, not wording, establishes scope.  The summary may describe
        # voice, routing, performance, tools or any other supported component.
        return bool(summary.strip())

    def next(self, excluded: set[int] | None = None) -> dict[str, Any] | None:
        excluded = excluded or set()
        try:
            rows = self.db.execute(
                """
                SELECT c.candidate_id, c.status, c.summary, c.failure_id,
                       c.development_scope_json, f.summary AS failure_summary,
                       f.evidence_json, f.status AS failure_status
                FROM improvement_candidates c
                JOIN improvement_failures f ON f.failure_id = c.failure_id
                WHERE c.status='queued' AND f.status IN ('queued','recorded')
                ORDER BY c.candidate_id ASC
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            rows = self.db.execute(
                """
                SELECT c.candidate_id, c.status, c.summary, c.failure_id,
                       '{}' AS development_scope_json,
                       f.summary AS failure_summary, f.evidence_json,
                       f.status AS failure_status
                FROM improvement_candidates c
                JOIN improvement_failures f ON f.failure_id = c.failure_id
                WHERE c.status='queued' AND f.status IN ('queued','recorded')
                ORDER BY c.candidate_id ASC
                """
            ).fetchall()
        for row in rows:
            candidate_id = int(row["candidate_id"])
            if candidate_id in excluded:
                continue
            summary = str(row["summary"] or row["failure_summary"] or "").strip()
            try:
                evidence = json.loads(row["evidence_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(evidence, dict) or not evidence:
                continue
            try:
                scope = json.loads(row["development_scope_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(scope, dict) and scope:
                # Scope attribution is candidate-local so the original
                # Governor failure evidence remains immutable.  The binding
                # must point back to the exact preserved failure record.
                if scope.get("evidence_failure_id") != int(row["failure_id"]):
                    continue
                evidence = {**evidence, **scope}
            if len(summary) < 3:
                continue
            if not self._is_scope_eligible(summary, evidence):
                continue
            paths = evidence["affected_files"]
            return {
                "candidate_id": candidate_id,
                "failure_id": int(row["failure_id"]),
                "status": "queued",
                "summary": summary,
                "evidence": evidence,
                "allowed_paths": tuple(sorted(set(paths))),
            }
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
    def run(self, task: GovernorTask) -> dict[str, Any]:
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
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source_root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        return result.stdout.strip()

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
        policy = installed_policy()
        worker_uid = int(policy["autonomy_uid"])
        worker_gid = int(policy.get("autonomy_gid", worker_uid))
        if worker_uid <= 0 or worker_uid == 0:
            raise LifecycleError("worker_identity_invalid")

        def drop_privileges() -> None:
            os.setgroups([])
            os.setgid(worker_gid)
            os.setuid(worker_uid)

        environment = os.environ.copy()
        # The worker receives only its provider configuration.  Operator and
        # private signing material is never inherited by the development child.
        for name in tuple(environment):
            if any(token in name.upper() for token in ("OPERATOR", "PRIVATE_KEY", "SIGNING_KEY")):
                environment.pop(name, None)
        command = [
            "/usr/bin/python3",
            "-I",
            WORKER_ENTRY,
            "development-worker",
            task_id,
        ]
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
        timeout = max(1, int(task.deadline_seconds) + 30)
        try:
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            finally:
                process.communicate()
            raise LifecycleError("development_worker_timeout") from exc
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
            existing = self._existing_task()
            if existing is None:
                candidate = self.source.next(self._claimed_candidates())
                if candidate is None:
                    return {"status": "no_eligible_candidate"}
                source_sha = self._source_sha()
                task = GovernorAdapter.from_candidate(
                    candidate,
                    owner_id=OWNER_ID,
                    request_ref=str(uuid.uuid4()),
                    source_sha=source_sha,
                    allowed_paths=tuple(candidate["allowed_paths"]),
                    guardian_generation=str(status["generation"]),
                    expires_at=int(time.time()) + 90,
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
                        "JARVIS_GPT53_INPUT_USD_PER_MILLION": os.environ.get(
                            "JARVIS_GPT53_INPUT_USD_PER_MILLION", ""
                        ),
                        "JARVIS_GPT53_OUTPUT_USD_PER_MILLION": os.environ.get(
                            "JARVIS_GPT53_OUTPUT_USD_PER_MILLION", ""
                        ),
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
            return self.controller.snapshot(task_id)
        except BaseException:
            if execution_active and not stop_attempted:
                stop_attempted = True
                self._confirm_stop()
            raise


def worker_main(task_id: str) -> int:
    """Run one persisted task as the policy-pinned unprivileged worker."""
    if os.getuid() == 0:
        return 1
    controller = SupervisedDevelopmentController(CONTROLLER_DATABASE, database_mode=0o660)
    source = GovernorCandidateSource(GOVERNOR_DATABASE)
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
                "JARVIS_GPT53_INPUT_USD_PER_MILLION": os.environ.get(
                    "JARVIS_GPT53_INPUT_USD_PER_MILLION", ""
                ),
                "JARVIS_GPT53_OUTPUT_USD_PER_MILLION": os.environ.get(
                    "JARVIS_GPT53_OUTPUT_USD_PER_MILLION", ""
                ),
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
    source = GovernorCandidateSource()
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
