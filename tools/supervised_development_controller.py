"""Durable owner-gated lifecycle for a supervised development candidate.

This module orchestrates already-certified editing and validation components; it
does not call a provider, create Git commits, push branches, or deploy anything.
Its SQLite file is supplied by the caller and is intended to be disposable for
tests and separate from production state.
"""

from __future__ import annotations

import hashlib
import math
import json
import re
import sqlite3
import socket
import stat
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from tools.supervised_development_scope import editable_path


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
_MAX_ADMISSION_RESPONSE = 16_384
ADMISSION_SOCKET = "/run/jarvis-autonomy-broker/development-admission.sock"
# One protected database is shared by the root coordinator, the unprivileged
# worker, and the read-only publication service.  Callers cannot substitute a
# path for production execution or publication.
CONTROLLER_DATABASE = Path("/var/lib/jarvis-autonomy-broker/work/supervised-development.sqlite3")
_TRANSITIONS = {
    PROPOSED: {APPROVAL_REQUESTED, FAILED},
    APPROVAL_REQUESTED: {RUNNING, FAILED},
    RUNNING: {REVIEW_PENDING, FAILED, RECONCILIATION_REQUIRED},
    REVIEW_PENDING: {REVIEW_REQUIRED, FAILED, RECONCILIATION_REQUIRED},
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


def _is_uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class SupervisedDevelopmentController:
    """State machine for one owner-approved, bounded development task."""

    def __init__(
        self,
        database: Path,
        *,
        read_only: bool = False,
        database_mode: int = 0o600,
    ) -> None:
        self.database = Path(database)
        if read_only:
            if self.database.is_symlink() or not self.database.is_file():
                raise LifecycleError("controller_database_unavailable")
            if any(parent.is_symlink() for parent in self.database.parents):
                raise LifecycleError("controller_database_path_untrusted")
            self.db = sqlite3.connect(
                f"file:{self.database.resolve()}?mode=ro", uri=True, timeout=5
            )
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(tasks)").fetchall()}
            if not {"id", "state", "parameters", "approval"}.issubset(columns):
                self.db.close()
                raise LifecycleError("controller_schema_invalid")
            return
        if self.database.exists():
            # The production coordinator runs without CAP_FOWNER while the
            # database is owned by the unprivileged autonomy account.  Do not
            # attempt to chmod an existing file: verify its exact required
            # mode and fail closed if an operator must repair it instead.
            if self.database.is_symlink() or not self.database.is_file():
                raise LifecycleError("controller_database_path_untrusted")
            if stat.S_IMODE(self.database.stat().st_mode) != database_mode:
                raise LifecycleError("controller_database_mode_invalid")
            database_exists = True
        else:
            database_exists = False

        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        if database_mode not in {0o600, 0o660}:
            raise LifecycleError("controller_database_mode_invalid")
        if not database_exists:
            self.database.chmod(database_mode)
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
        call_columns = {row[1] for row in self.db.execute("PRAGMA table_info(calls)")}
        for column in ("reserved_cost_usd", "actual_cost_usd"):
            if column not in call_columns:
                self.db.execute(f"ALTER TABLE calls ADD COLUMN {column} REAL")

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
        candidate_id: int | None = None,
    ) -> str:
        if not _SHA.fullmatch(base_sha):
            raise LifecycleError("invalid_base_sha")
        if not allowed_paths or not all(editable_path(p) for p in allowed_paths):
            raise LifecycleError("invalid_allow_list")
        if (
            provider != "openai"
            or not model
            or type(max_calls) is not int
            or not 1 <= max_calls <= 4
        ):
            raise LifecycleError("provider_policy_violation")
        if (
            type(max_cost_usd) not in {int, float}
            or not math.isfinite(max_cost_usd)
            or not 0 < max_cost_usd <= 1
            or type(deadline_seconds) is not int
            or not 0 < deadline_seconds <= 120
        ):
            raise LifecycleError("execution_bound_violation")
        if not goal or len(goal) > 4_000:
            raise LifecycleError("invalid_goal")
        if candidate_id is not None and (type(candidate_id) is not int or candidate_id <= 0):
            raise LifecycleError("invalid_candidate_id")
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
        if candidate_id is not None:
            parameters["candidate_id"] = candidate_id
        stamp = _now()
        self.db.execute(
            "INSERT INTO tasks(id,state,parameters,created_at,updated_at) VALUES(?,?,?,?,?)",
            (task_id, PROPOSED, _json(parameters), stamp, stamp),
        )
        return task_id

    def record_approval_request(
        self,
        task_id: str,
        *,
        owner_id: str,
        parameters_sha256: str,
        request_ref: str,
        guardian_generation: str = "",
        expires_at: int | None = None,
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
                "guardian_generation": guardian_generation,
                "expires_at": expires_at or _now() + 90,
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

    def pending_owner_task(self) -> dict[str, Any] | None:
        """Return only the one current, unexpired approval envelope.

        This is the sole publication source for the owner UI; observer plans
        and caller-provided JSON never enter this method.
        """
        row = self.db.execute(
            "SELECT * FROM tasks WHERE state=? ORDER BY created_at,id LIMIT 1",
            (APPROVAL_REQUESTED,),
        ).fetchone()
        if row is None:
            return None
        task = json.loads(row["parameters"])
        approval = json.loads(row["approval"] or "{}")
        expires = approval.get("expires_at")
        if (
            approval.get("task_id") != row["id"]
            or not isinstance(approval.get("request_ref"), str)
            or not isinstance(expires, int)
            or expires <= _now()
            or not isinstance(approval.get("guardian_generation"), str)
            or not _is_uuid(approval["guardian_generation"])
        ):
            return None
        expected = hashlib.sha256(str(row["parameters"]).encode()).hexdigest()
        if approval.get("parameters_sha256") != expected:
            return None
        return {
            "version": 1,
            "action": "approve_development",
            "goal": task["goal"],
            "task_id": row["id"],
            "request_ref": approval["request_ref"],
            "source_sha": task["base_sha"],
            "parameters_sha256": expected,
            "allowed_paths": task["allowed_paths"],
            "provider": task["provider"],
            "model": task["model"],
            "max_calls": task["max_calls"],
            "max_cost_usd": task["max_cost_usd"],
            "deadline_seconds": task["deadline_seconds"],
            "expires_at": expires,
            "guardian_generation": approval["guardian_generation"],
        }

    def expire_stale_approval_requests(self, *, now: int | None = None) -> list[str]:
        """Terminally retire expired approval requests without deleting them."""
        cutoff = _now() if now is None else int(now)
        expired: list[str] = []
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute(
                "SELECT id,approval,evidence FROM tasks WHERE state=?",
                (APPROVAL_REQUESTED,),
            ).fetchall()
            for row in rows:
                try:
                    approval = json.loads(row["approval"] or "")
                    expires_at = approval.get("expires_at")
                except (TypeError, json.JSONDecodeError):
                    expires_at = cutoff
                if not isinstance(expires_at, int) or expires_at > cutoff:
                    continue
                try:
                    existing_evidence = json.loads(row["evidence"] or "")
                except (TypeError, json.JSONDecodeError):
                    existing_evidence = None
                preserved_evidence = (
                    existing_evidence if isinstance(existing_evidence, dict) else {}
                )
                preserved_evidence["approval_expired"] = {
                    "expired_at": expires_at,
                }
                self.db.execute(
                    "UPDATE tasks SET state=?, evidence=?, updated_at=? WHERE id=? AND state=?",
                    (
                        FAILED,
                        _json(preserved_evidence),
                        cutoff,
                        row["id"],
                        APPROVAL_REQUESTED,
                    ),
                )
                if self.db.execute("SELECT changes()").fetchone()[0] == 1:
                    expired.append(str(row["id"]))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return expired

    def start(self, task_id: str, *, current_base_sha: str) -> None:
        """Admit execution only after the installed protected broker attests it.

        The socket path is intentionally fixed.  Callers cannot provide a
        verifier, authority object, or alternate endpoint.  The production
        broker endpoint is a separately installed protected capability; until
        it exists this method fails closed.
        """
        row = self._task(task_id)
        if row["state"] != APPROVAL_REQUESTED:
            raise LifecycleError("execution_admission_denied")
        parameters = json.loads(row["parameters"])
        try:
            approval = json.loads(row["approval"] or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise LifecycleError("malformed_approval") from exc
        if not isinstance(approval, dict) or not isinstance(approval.get("request_ref"), str):
            raise LifecycleError("malformed_approval")
        request = {
            "task_id": task_id,
            "approval_id": approval["request_ref"],
            "source_sha": parameters["base_sha"],
            "parameters_sha256": hashlib.sha256(str(row["parameters"]).encode()).hexdigest(),
            "allowed_paths": parameters["allowed_paths"],
            "provider": parameters["provider"],
            "model": parameters["model"],
            "max_calls": parameters["max_calls"],
            "max_cost_usd": parameters["max_cost_usd"],
            "deadline_seconds": parameters["deadline_seconds"],
        }
        if current_base_sha != request["source_sha"]:
            raise LifecycleError("source_drift_before_admission")
        try:
            response = _request_protected_admission(request)
        except LifecycleError:
            raise
        except Exception as exc:
            raise LifecycleError("trusted_execution_admission_unavailable") from exc
        _validate_admission_response(response, request)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            current = self._task(task_id)
            if current["state"] != APPROVAL_REQUESTED:
                raise LifecycleError("execution_admission_race")
            if (
                current["parameters"] != row["parameters"]
                or current["approval"] != row["approval"]
                or hashlib.sha256(str(current["parameters"]).encode()).hexdigest()
                != request["parameters_sha256"]
            ):
                raise LifecycleError("execution_parameters_changed")
            self.db.execute(
                "UPDATE tasks SET state=?,started_at=?,updated_at=? WHERE id=? AND state=?",
                (RUNNING, _now(), _now(), task_id, APPROVAL_REQUESTED),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise LifecycleError("execution_admission_race")
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _check_deadline(self, task_id: str) -> None:
        row = self._task(task_id)
        if row["state"] != RUNNING or row["started_at"] is None:
            return
        params = json.loads(row["parameters"])
        if _now() - int(row["started_at"]) >= int(params["deadline_seconds"]):
            self._transition(task_id, FAILED)
            raise LifecycleError("deadline_expired")

    def reserve_provider_call(
        self, task_id: str, *, call_id: str, reserved_cost_usd: float = 0
    ) -> bool:
        if (
            not call_id
            or type(reserved_cost_usd) not in {int, float}
            or not math.isfinite(reserved_cost_usd)
            or reserved_cost_usd < 0
        ):
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
            prior = self.db.execute(
                "SELECT outcome,reserved_cost_usd,actual_cost_usd FROM calls WHERE task_id=?",
                (task_id,),
            ).fetchall()
            if any(call["outcome"] != "completed" for call in prior):
                raise LifecycleError("provider_effect_requires_reconciliation")
            if any(call["actual_cost_usd"] is None for call in prior):
                raise LifecycleError("provider_cost_unverified")
            spent = sum(float(call["actual_cost_usd"]) for call in prior)
            if spent + reserved_cost_usd > float(params["max_cost_usd"]) + 1e-12:
                raise LifecycleError("provider_spending_budget_exhausted")
            if int(row["provider_calls"]) >= int(params["max_calls"]):
                self.db.execute("ROLLBACK")
                raise LifecycleError("provider_budget_exhausted")
            self.db.execute(
                "INSERT INTO calls(task_id,call_id,outcome,created_at,reserved_cost_usd) VALUES(?,?,?,?,?)",
                (task_id, call_id, "reserved", _now(), reserved_cost_usd),
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

    def finish_provider_call(
        self, task_id: str, *, call_id: str, outcome: str, actual_cost_usd: float | None = None
    ) -> None:
        if outcome not in {"completed", "failed", "uncertain"}:
            raise LifecycleError("invalid_call_outcome")
        if actual_cost_usd is not None and (
            type(actual_cost_usd) not in {int, float}
            or not math.isfinite(actual_cost_usd)
            or actual_cost_usd < 0
        ):
            raise LifecycleError("provider_cost_invalid")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            changed = self.db.execute(
                "UPDATE calls SET outcome=?,actual_cost_usd=? WHERE task_id=? AND call_id=? AND outcome='reserved'",
                (outcome, actual_cost_usd, task_id, call_id),
            ).rowcount
            if not changed:
                raise LifecycleError("call_not_reserved")
            row = self._task(task_id)
            params = json.loads(row["parameters"])
            cost = (
                self.db.execute(
                    "SELECT SUM(actual_cost_usd) FROM calls WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                or 0
            )
            if outcome == "uncertain" or cost > params["max_cost_usd"]:
                if row["state"] == RUNNING:
                    self._transition(task_id, RECONCILIATION_REQUIRED)
            self.db.execute("COMMIT")
            if cost > params["max_cost_usd"]:
                raise LifecycleError("provider_actual_cost_exceeded")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

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

    def record_review(self, task_id: str, *, review: dict[str, Any]) -> None:
        row = self._task(task_id)
        if row["state"] != REVIEW_PENDING:
            raise LifecycleError("review_not_allowed")
        if review.get("verified") is not True or not review.get("checks"):
            raise LifecycleError("independent_review_unverified")
        current = json.loads(row["evidence"] or "{}")
        current["independent_review"] = review
        self.db.execute(
            "UPDATE tasks SET evidence=?,updated_at=? WHERE id=?",
            (_json(current), _now(), task_id),
        )
        self._transition(task_id, REVIEW_REQUIRED)

    def recover_after_restart(self, task_id: str) -> str:
        row = self._task(task_id)
        if row["state"] in {RUNNING, REVIEW_PENDING}:
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


def _request_protected_admission(request: dict[str, Any]) -> dict[str, Any]:
    """Exchange a bounded request with the fixed root-owned broker endpoint."""
    raw = _json(request).encode("ascii")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(ADMISSION_SOCKET)
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", credentials)
        if uid != 0:
            raise LifecycleError("untrusted_admission_peer")
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        header = connection.recv(4)
        if len(header) != 4:
            raise LifecycleError("malformed_admission_response")
        size = struct.unpack("!I", header)[0]
        if size <= 0 or size > _MAX_ADMISSION_RESPONSE:
            raise LifecycleError("admission_response_size")
        payload = b""
        while len(payload) < size:
            chunk = connection.recv(size - len(payload))
            if not chunk:
                raise LifecycleError("truncated_admission_response")
            payload += chunk
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecycleError("malformed_admission_response") from exc
    if not isinstance(value, dict):
        raise LifecycleError("malformed_admission_response")
    return value


def _validate_admission_response(response: dict[str, Any], request: dict[str, Any]) -> None:
    required = {
        "status",
        "authority",
        "task_id",
        "approval_id",
        "source_sha",
        "parameters_sha256",
        "allowed_paths",
        "provider",
        "model",
        "max_calls",
        "max_cost_usd",
        "deadline_seconds",
        "expires_at",
        "guardian_generation",
    }
    if set(response) != required or response.get("status") != "approved":
        raise LifecycleError("invalid_admission_attestation")
    if response.get("authority") != "jarvis-autonomy-broker":
        raise LifecycleError("untrusted_admission_authority")
    for key, value in request.items():
        if response.get(key) != value:
            raise LifecycleError("admission_parameters_mismatch")
    if type(response["expires_at"]) is not int or response["expires_at"] <= _now():
        raise LifecycleError("expired_admission_attestation")
    try:
        uuid.UUID(str(response["guardian_generation"]))
    except (ValueError, AttributeError):
        raise LifecycleError("invalid_admission_generation") from None
