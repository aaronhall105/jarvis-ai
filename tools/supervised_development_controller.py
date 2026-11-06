"""Durable owner-gated lifecycle for a supervised development candidate.

This module orchestrates already-certified editing and validation components; it
does not call a provider, create Git commits, push branches, or deploy anything.
Its SQLite file is supplied by the caller and is intended to be disposable for
tests and separate from production state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


PROPOSED = "PROPOSED"
APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
RUNNING = "RUNNING"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
REVIEW_PENDING = "REVIEW_PENDING"
FAILED = "FAILED"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
TERMINAL = frozenset({REVIEW_REQUIRED, FAILED, RECONCILIATION_REQUIRED})
_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)[\"']?(?:authorization\s*:\s*bearer|api[_-]?key|password|secret|token|cookie)[\"']?\s*[:=]\s*[\"']?\S+"
)
_MAX_JSON = 64_000
_TRANSITIONS = {
    PROPOSED: {APPROVAL_REQUESTED, FAILED},
    APPROVAL_REQUESTED: {FAILED},
    RUNNING: {REVIEW_PENDING, FAILED, RECONCILIATION_REQUIRED},
    REVIEW_PENDING: {REVIEW_REQUIRED, FAILED},
    REVIEW_REQUIRED: set(),
    FAILED: set(),
    RECONCILIATION_REQUIRED: set(),
}


class LifecycleError(RuntimeError):
    """A fail-closed lifecycle or persistence violation."""


def _json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) > _MAX_JSON:
        raise LifecycleError("evidence_or_parameters_too_large")
    if _SECRET.search(encoded):
        raise LifecycleError("sensitive_value_in_persisted_record")
    return encoded


def _now() -> int:
    return int(time.time())


class SupervisedDevelopmentController:
    """State machine for one owner-approved, bounded development task."""

    def __init__(
        self,
        database: Path,
    ) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        self.database.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                parameters TEXT NOT NULL,
                approval TEXT,
                evidence TEXT,
                provider_calls INTEGER NOT NULL DEFAULT 0,
                started_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}
        if "started_at" not in columns:
            self.db.execute("ALTER TABLE tasks ADD COLUMN started_at INTEGER")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                task_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(task_id, call_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )"""
        )

    def close(self) -> None:
        self.db.close()

    def _task(self, task_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise LifecycleError("task_not_found")
        return row

    def _transition(self, task_id: str, target: str) -> None:
        row = self._task(task_id)
        if target not in _TRANSITIONS.get(str(row["state"]), set()):
            raise LifecycleError(f"invalid_transition:{row['state']}:{target}")
        self.db.execute(
            "UPDATE tasks SET state=?, updated_at=? WHERE id=? AND state=?",
            (target, _now(), task_id, row["state"]),
        )

    def create_task(
        self,
        *,
        base_sha: str,
        allowed_paths: list[str],
        provider: str,
        model: str,
        max_calls: int,
        max_cost_usd: float,
        deadline_seconds: int,
        goal: str,
    ) -> str:
        if not _SHA.fullmatch(base_sha):
            raise LifecycleError("invalid_base_sha")
        if not allowed_paths or any(
            not p or p.startswith("/") or ".." in Path(p).parts for p in allowed_paths
        ):
            raise LifecycleError("invalid_allow_list")
        if provider != "openai" or not model or max_calls < 1 or max_calls > 1:
            raise LifecycleError("provider_policy_violation")
        if max_cost_usd <= 0 or max_cost_usd > 1 or deadline_seconds <= 0 or deadline_seconds > 120:
            raise LifecycleError("execution_bound_violation")
        if not goal or len(goal) > 4_000:
            raise LifecycleError("invalid_goal")
        task_id = str(uuid.uuid4())
        parameters = {
            "base_sha": base_sha,
            "allowed_paths": sorted(set(allowed_paths)),
            "provider": provider,
            "model": model,
            "max_calls": max_calls,
            "max_cost_usd": max_cost_usd,
            "deadline_seconds": deadline_seconds,
            "goal": goal,
        }
        stamp = _now()
        self.db.execute(
            "INSERT INTO tasks(id,state,parameters,created_at,updated_at) VALUES(?,?,?,?,?)",
            (task_id, PROPOSED, _json(parameters), stamp, stamp),
        )
        return task_id

    def record_approval_request(
        self, task_id: str, *, owner_id: str, parameters_sha256: str, request_ref: str
    ) -> None:
        """Persist an untrusted request; this never grants execution authority."""
        if not owner_id or len(owner_id) > 200 or not request_ref or len(request_ref) > 200:
            raise LifecycleError("invalid_approval_request")
        if not re.fullmatch(r"[0-9a-f]{64}", parameters_sha256):
            raise LifecycleError("invalid_parameters_hash")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._task(task_id)
            if row["state"] != PROPOSED:
                raise LifecycleError("approval_request_not_allowed")
            expected_hash = hashlib.sha256(str(row["parameters"]).encode()).hexdigest()
            if parameters_sha256 != expected_hash:
                raise LifecycleError("approval_parameters_mismatch")
            request = {
                "task_id": task_id,
                "owner_id": owner_id,
                "requested_at": _now(),
                "parameters_sha256": expected_hash,
                "request_ref": request_ref,
            }
            encoded = _json(request)
            self._transition(task_id, APPROVAL_REQUESTED)
            self.db.execute(
                "UPDATE tasks SET approval=?, updated_at=? WHERE id=?",
                (encoded, _now(), task_id),
            )
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def start(self, task_id: str, *, current_base_sha: str) -> None:
        del task_id, current_base_sha
        raise LifecycleError("trusted_execution_admission_unavailable")

    def _check_deadline(self, task_id: str) -> None:
        row = self._task(task_id)
        if row["state"] != RUNNING or row["started_at"] is None:
            return
        params = json.loads(row["parameters"])
        if _now() - int(row["started_at"]) >= int(params["deadline_seconds"]):
            self._transition(task_id, FAILED)
            raise LifecycleError("deadline_expired")

    def reserve_provider_call(self, task_id: str, *, call_id: str) -> bool:
        if not call_id:
            raise LifecycleError("invalid_call_id")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._task(task_id)
            if row["state"] != RUNNING:
                raise LifecycleError("task_not_running")
            self._check_deadline(task_id)
            params = json.loads(row["parameters"])
            existing = self.db.execute(
                "SELECT outcome FROM calls WHERE task_id=? AND call_id=?", (task_id, call_id)
            ).fetchone()
            if existing is not None:
                self.db.execute("ROLLBACK")
                return False
            if int(row["provider_calls"]) >= int(params["max_calls"]):
                self.db.execute("ROLLBACK")
                raise LifecycleError("provider_budget_exhausted")
            self.db.execute(
                "INSERT INTO calls(task_id,call_id,outcome,created_at) VALUES(?,?,?,?)",
                (task_id, call_id, "reserved", _now()),
            )
            self.db.execute(
                "UPDATE tasks SET provider_calls=provider_calls+1,updated_at=? WHERE id=?",
                (_now(), task_id),
            )
            self.db.execute("COMMIT")
            return True
        except LifecycleError as exc:
            if str(exc) == "deadline_expired":
                self.db.execute("COMMIT")
            elif self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def finish_provider_call(self, task_id: str, *, call_id: str, outcome: str) -> None:
        if outcome not in {"completed", "failed", "uncertain"}:
            raise LifecycleError("invalid_call_outcome")
        self._check_deadline(task_id)
        changed = self.db.execute(
            "UPDATE calls SET outcome=? WHERE task_id=? AND call_id=? AND outcome='reserved'",
            (outcome, task_id, call_id),
        ).rowcount
        if not changed:
            raise LifecycleError("call_not_reserved")
        if outcome == "uncertain":
            self._transition(task_id, RECONCILIATION_REQUIRED)

    def record_evidence(self, task_id: str, *, evidence: dict[str, Any]) -> None:
        row = self._task(task_id)
        if row["state"] != RUNNING:
            raise LifecycleError("evidence_not_allowed")
        self._check_deadline(task_id)
        required = {"proposal", "diff_sha256", "tests", "security"}
        if not required.issubset(evidence):
            raise LifecycleError("incomplete_evidence")
        self.db.execute(
            "UPDATE tasks SET evidence=?,updated_at=? WHERE id=?",
            (_json(evidence), _now(), task_id),
        )
        self._transition(task_id, REVIEW_PENDING)

    def recover_after_restart(self, task_id: str) -> str:
        row = self._task(task_id)
        if row["state"] == RUNNING:
            self._transition(task_id, RECONCILIATION_REQUIRED)
        return str(self._task(task_id)["state"])

    def snapshot(self, task_id: str) -> dict[str, Any]:
        row = self._task(task_id)
        return {
            "id": row["id"],
            "state": row["state"],
            "parameters": json.loads(row["parameters"]),
            "approval": json.loads(row["approval"]) if row["approval"] else None,
            "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
            "provider_calls": row["provider_calls"],
        }
