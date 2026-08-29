"""Durable, idempotent same-conversation follow-ups for Jarvis Core."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Protocol

from app.connectors.credentials import redact_secrets, redact_text


MIN_EXTERNAL_INTERVAL_SECONDS = 10
MAX_EXTERNAL_INTERVAL_SECONDS = 30 * 86400
VALID_EXTERNAL_COMPARISONS = {
    "changed",
    "equals",
    "not_equals",
    "decreased",
    "increased",
    "less_than",
    "greater_than",
    "contains",
    "truthy",
}
VALID_FOLLOWUP_KINDS = {
    "time",
    "scheduled",
    "condition",
    "periodic",
    "completion",
    "external_monitor",
}
VALID_FOLLOWUP_STATUSES = {
    "pending",
    "executing",
    "delivery_pending",
    "delivering",
    "completed",
    "failed",
    "cancelled",
    "expired",
}
EXPLICIT_TARGET_COMPARISONS = {
    "equals",
    "not_equals",
    "less_than",
    "greater_than",
    "contains",
}
MAX_EXTERNAL_POLLS = 1_000_000


class ConversationWriter(Protocol):
    async def add_assistant_message(
        self,
        conversation_id: str,
        content: str,
        *,
        delivery_key: str | None = None,
    ) -> dict[str, Any]: ...


class StateReader(Protocol):
    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ExternalMonitorEvaluation:
    """Verified observation returned by an injected external evaluator."""

    verified: bool
    value: Any = None
    message: str | None = None
    provider_reference: str | None = None
    observed_at: str | None = None


class ExternalMonitorEvaluator(Protocol):
    async def evaluate_external_monitor(
        self, monitor: Mapping[str, Any]
    ) -> ExternalMonitorEvaluation | Mapping[str, Any]: ...


ExternalEvaluatorCallback = Callable[
    [Mapping[str, Any]],
    Awaitable[ExternalMonitorEvaluation | Mapping[str, Any]]
    | ExternalMonitorEvaluation
    | Mapping[str, Any],
]


class FollowUpEngine:
    """SQLite-backed worker with verified, exactly-once chat delivery."""

    def __init__(
        self,
        database_path: str,
        conversations: ConversationWriter,
        states: StateReader,
        poll_seconds: int = 2,
        external_evaluator: ExternalMonitorEvaluator | ExternalEvaluatorCallback | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations, self.states = conversations, states
        self.external_evaluator = external_evaluator
        self.poll_seconds = max(1, min(poll_seconds, 60))
        self.max_attempts = max(1, min(int(max_attempts), 10))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._init()

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self._db() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS followup_jobs (
                  job_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                  kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                  status TEXT NOT NULL, created_at TEXT NOT NULL,
                  next_run_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 3, delivered_at TEXT,
                  result_json TEXT, idempotency_key TEXT NOT NULL UNIQUE,
                  delivery_attempts INTEGER NOT NULL DEFAULT 0,
                  delivery_state TEXT NOT NULL DEFAULT 'pending',
                  delivery_message TEXT, completion_status TEXT, cancelled_at TEXT,
                  request_fingerprint TEXT, poll_count INTEGER NOT NULL DEFAULT 0,
                  max_polls INTEGER, expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_followup_due
                  ON followup_jobs(status, next_run_at);
                """
            )
            columns = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(followup_jobs)").fetchall()
            }
            migrations = {
                "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
                "delivery_state": "TEXT NOT NULL DEFAULT 'pending'",
                "delivery_message": "TEXT",
                "completion_status": "TEXT",
                "cancelled_at": "TEXT",
                "request_fingerprint": "TEXT",
                "poll_count": "INTEGER NOT NULL DEFAULT 0",
                "max_polls": "INTEGER",
                "expires_at": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    con.execute(f"ALTER TABLE followup_jobs ADD COLUMN {column} {definition}")

            # Evaluation is safe to repeat. Delivery reuses a stable conversation
            # key, closing the crash window after a message commit.
            con.execute(
                "UPDATE followup_jobs SET status='pending',delivery_state='pending' "
                "WHERE status='executing'"
            )
            con.execute(
                "UPDATE followup_jobs SET status='delivery_pending',"
                "delivery_state='pending' WHERE status='delivering'"
            )
            con.execute(
                "UPDATE followup_jobs SET delivery_state='delivered' "
                "WHERE delivered_at IS NOT NULL "
                "AND status IN ('completed','failed','expired') "
                "AND delivery_state='pending'"
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    async def create(
        self,
        *,
        conversation_id: str,
        kind: str,
        payload: dict[str, Any],
        due_at: datetime,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(conversation_id).strip()
        if not conversation_id:
            raise ValueError("A conversation is required for a follow-up")
        if kind not in VALID_FOLLOWUP_KINDS:
            raise ValueError("Unsupported follow-up type")
        if not isinstance(payload, dict):
            raise ValueError("Follow-up payload must be an object")

        resolved_payload = dict(payload)
        maximum = self.max_attempts
        if kind == "external_monitor":
            if self.external_evaluator is None:
                raise RuntimeError("External monitor evaluator is not configured")
            resolved_payload, maximum = self._normalise_external_payload(
                conversation_id, resolved_payload
            )
        try:
            payload_json = json.dumps(resolved_payload, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Follow-up payload must be JSON serializable") from exc

        key = str(idempotency_key or uuid.uuid4()).strip()
        if not key or len(key) > 255:
            raise ValueError("Follow-up idempotency key is invalid")
        if redact_text(key, max_length=255) != key:
            raise ValueError("Follow-up idempotency keys may not contain secrets")
        fingerprint = self._request_fingerprint(conversation_id, kind, resolved_payload)
        max_polls = (
            int(resolved_payload["max_polls"])
            if kind == "external_monitor" and resolved_payload.get("max_polls") is not None
            else None
        )
        expires_at = (
            str(resolved_payload["expires_at"])
            if kind == "external_monitor" and resolved_payload.get("expires_at") is not None
            else None
        )
        job_id, now = str(uuid.uuid4()), self._iso(self._now())
        try:
            with self._db() as con:
                existing = con.execute(
                    "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
                if existing is not None:
                    self._assert_idempotent_match(existing, conversation_id, kind, fingerprint)
                    return self._row(existing)
                con.execute(
                    """
                    INSERT INTO followup_jobs(
                      job_id,conversation_id,kind,payload_json,status,created_at,
                      next_run_at,idempotency_key,max_attempts,delivery_state,
                      request_fingerprint,poll_count,max_polls,expires_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job_id,
                        conversation_id,
                        kind,
                        payload_json,
                        "pending",
                        now,
                        self._iso(due_at),
                        key,
                        maximum,
                        "pending",
                        fingerprint,
                        0,
                        max_polls,
                        expires_at,
                    ),
                )
        except sqlite3.IntegrityError:
            with self._db() as con:
                existing = con.execute(
                    "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
            if existing is None:
                raise
            self._assert_idempotent_match(existing, conversation_id, kind, fingerprint)
            return self._row(existing)
        return await self.get(job_id) or {}

    @staticmethod
    def _request_fingerprint(
        conversation_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> str:
        fingerprint_payload = dict(payload)
        if kind == "external_monitor":
            # The live baseline is captured before the durable store is called.
            # It may legitimately differ on an exact transport retry, while the
            # requested monitor definition remains the same.
            fingerprint_payload.pop("baseline", None)
        material = json.dumps(
            {
                "conversation_id": conversation_id,
                "kind": kind,
                "payload": fingerprint_payload,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _assert_idempotent_match(
        self,
        row: sqlite3.Row,
        conversation_id: str,
        kind: str,
        fingerprint: str,
    ) -> None:
        existing_fingerprint = str(row["request_fingerprint"] or "")
        if not existing_fingerprint:
            existing_fingerprint = self._request_fingerprint(
                str(row["conversation_id"]),
                str(row["kind"]),
                json.loads(str(row["payload_json"])),
            )
        if (
            str(row["conversation_id"]) != conversation_id
            or str(row["kind"]) != kind
            or existing_fingerprint != fingerprint
        ):
            raise ValueError("Follow-up idempotency key was already used for a different request")

    def _normalise_external_payload(
        self, conversation_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        provider = str(payload.get("provider") or payload.get("provider_id") or "").strip()
        capability_id = str(payload.get("capability_id") or "").strip()
        if not provider:
            raise ValueError("External monitor provider is required")
        if not capability_id:
            raise ValueError("External monitor capability_id is required")
        has_operation = "operation" in payload and self._has_monitor_value(payload.get("operation"))
        arguments = payload.get("arguments")
        if isinstance(arguments, Mapping) and arguments:
            has_operation = True
        has_query = "query" in payload and self._has_monitor_value(payload.get("query"))
        if not has_operation and not has_query:
            raise ValueError("External monitor operation or query is required")
        if "baseline" not in payload:
            raise ValueError("External monitor baseline is required")
        if "comparison" not in payload:
            raise ValueError("External monitor comparison is required")
        if (
            redact_secrets(payload["baseline"]) != payload["baseline"]
            or redact_secrets(payload["comparison"]) != payload["comparison"]
        ):
            raise ValueError("External monitor definitions may not contain credentials or secrets")
        self._validate_external_comparison(payload["baseline"], payload["comparison"])

        supplied_conversation = str(payload.get("conversation_id") or "").strip()
        if supplied_conversation and supplied_conversation != conversation_id:
            raise ValueError("External monitor payload conversation does not match its job")
        interval = payload.get("polling_interval_seconds", payload.get("interval_seconds"))
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise ValueError("External monitor polling_interval_seconds must be an integer")
        if not MIN_EXTERNAL_INTERVAL_SECONDS <= interval <= MAX_EXTERNAL_INTERVAL_SECONDS:
            raise ValueError(
                "External monitor polling interval must be between "
                f"{MIN_EXTERNAL_INTERVAL_SECONDS} and "
                f"{MAX_EXTERNAL_INTERVAL_SECONDS} seconds"
            )
        maximum = payload.get("max_attempts", self.max_attempts)
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise ValueError("External monitor max_attempts must be an integer")
        if not 1 <= maximum <= 10:
            raise ValueError("External monitor max_attempts must be between 1 and 10")
        if payload.get("poll_count") not in (None, 0):
            raise ValueError("External monitor poll_count is managed by Jarvis")
        payload.pop("poll_count", None)
        max_polls = payload.get("max_polls")
        if max_polls is not None:
            if isinstance(max_polls, bool) or not isinstance(max_polls, int):
                raise ValueError("External monitor max_polls must be an integer")
            if not 1 <= max_polls <= MAX_EXTERNAL_POLLS:
                raise ValueError(
                    f"External monitor max_polls must be between 1 and {MAX_EXTERNAL_POLLS}"
                )
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            parsed_expiry = self._parse_expiry(expires_at)
            if parsed_expiry <= self._now():
                raise ValueError("External monitor expires_at must be in the future")
            payload["expires_at"] = self._iso(parsed_expiry)
        notify_if_unchanged = payload.get("notify_if_unchanged", False)
        if not isinstance(notify_if_unchanged, bool):
            raise ValueError("External monitor notify_if_unchanged must be a boolean")
        if notify_if_unchanged and expires_at is None:
            raise ValueError("A no-change reminder requires an expiry deadline")
        payload["notify_if_unchanged"] = notify_if_unchanged
        # Legacy callers could supply arbitrary notification wording.  Monitor
        # delivery is generated from verified evidence, so that text is neither
        # persisted nor used.
        payload.pop("message", None)
        if redact_secrets(payload) != payload:
            raise ValueError("External monitor definitions may not contain credentials or secrets")

        payload.update(
            provider=provider,
            capability_id=capability_id,
            conversation_id=conversation_id,
            polling_interval_seconds=interval,
            max_attempts=maximum,
            max_polls=max_polls,
        )
        return payload, maximum

    @staticmethod
    def _parse_expiry(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            rendered = value.strip()
            if rendered.endswith("Z"):
                rendered = rendered[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(rendered)
            except ValueError as exc:
                raise ValueError(
                    "External monitor expires_at must be an ISO-8601 timestamp"
                ) from exc
        else:
            raise ValueError("External monitor expires_at must be an ISO-8601 timestamp")
        if parsed.tzinfo is None:
            raise ValueError("External monitor expires_at must include a timezone")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _has_monitor_value(value: Any) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    async def get(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["job_id=?"]
        values: list[Any] = [job_id]
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            clauses.append("kind=?")
            values.append(kind)
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM followup_jobs WHERE " + " AND ".join(clauses),
                values,
            ).fetchone()
        return self._row(row) if row else None

    async def get_by_idempotency_key(
        self,
        conversation_id: str,
        idempotency_key: str,
        *,
        kind: str = "external_monitor",
    ) -> dict[str, Any] | None:
        scoped_conversation = str(conversation_id).strip()
        if not scoped_conversation:
            raise ValueError("A conversation is required for follow-up lookup")
        if kind not in VALID_FOLLOWUP_KINDS:
            raise ValueError("Unsupported follow-up type")
        clauses = ["conversation_id=?", "idempotency_key=?", "kind=?"]
        values: list[Any] = [
            scoped_conversation,
            str(idempotency_key),
            kind,
        ]
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM followup_jobs WHERE " + " AND ".join(clauses),
                values,
            ).fetchone()
        return self._row(row) if row else None

    async def status(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.get(job_id, kind=kind, conversation_id=conversation_id)

    async def list(
        self,
        *,
        conversation_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> List[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            clauses.append("kind=?")
            values.append(kind)
        if status is not None:
            if status not in VALID_FOLLOWUP_STATUSES:
                raise ValueError("Unsupported follow-up status")
            clauses.append("status=?")
            values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 500)))
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM followup_jobs" + where + " ORDER BY created_at DESC LIMIT ?", values
            ).fetchall()
        return [self._row(row) for row in rows]

    async def list_jobs(
        self,
        *,
        conversation_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> List[dict[str, Any]]:
        return await self.list(
            conversation_id=conversation_id,
            kind=kind,
            status=status,
            limit=limit,
        )

    async def active_for_conversation(self, conversation_id: str) -> List[dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs WHERE conversation_id=?
                AND status IN ('pending','executing','delivery_pending','delivering')
                ORDER BY created_at DESC
                """,
                (conversation_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    async def cancel(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Cancel work that has not atomically claimed its chat delivery."""

        async with self._operation_lock:
            return await self._cancel_locked(
                job_id,
                kind=kind,
                conversation_id=conversation_id,
            )

    async def _cancel_locked(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        filters = ["job_id=?"]
        values: list[Any] = [job_id]
        if kind is not None:
            if kind not in VALID_FOLLOWUP_KINDS:
                raise ValueError("Unsupported follow-up type")
            filters.append("kind=?")
            values.append(kind)
        if conversation_id is not None:
            filters.append("conversation_id=?")
            values.append(conversation_id)
        predicate = " AND ".join(filters)
        with self._db() as con:
            con.execute(
                f"""
                UPDATE followup_jobs SET status='cancelled',cancelled_at=?,
                delivery_state='cancelled' WHERE {predicate}
                AND status IN ('pending','executing','delivery_pending')
                """,
                [self._iso(self._now()), *values],
            )
            row = con.execute("SELECT * FROM followup_jobs WHERE " + predicate, values).fetchone()
        return self._row(row) if row else None

    async def cancel_for_conversation(self, conversation_id: str) -> int:
        """Cancel all runnable jobs before their conversation is removed."""

        async with self._operation_lock:
            with self._db() as con:
                rows = con.execute(
                    """
                    SELECT job_id FROM followup_jobs
                    WHERE conversation_id=?
                    AND status IN ('pending','executing','delivery_pending')
                    """,
                    (conversation_id,),
                ).fetchall()
                if rows:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='cancelled',cancelled_at=?,
                        delivery_state='cancelled',result_json=?
                        WHERE conversation_id=?
                        AND status IN ('pending','executing','delivery_pending')
                        """,
                        (
                            self._iso(self._now()),
                            json.dumps(
                                {
                                    "cancelled": True,
                                    "reason": "conversation_deleted",
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            conversation_id,
                        ),
                    )
            return len(rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        data["result"] = json.loads(data.pop("result_json") or "{}")
        return data

    async def start(self) -> None:
        if self._task is not None and self._task.done():
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="jarvis-followups")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def _database_is_healthy(self) -> bool:
        """Probe the durable store without reading user or job data."""

        try:
            with self._db() as con:
                table = con.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='followup_jobs'
                    """
                ).fetchone()
                integrity = con.execute("PRAGMA quick_check(1)").fetchone()
            return bool(
                table is not None and integrity is not None and str(integrity[0]).lower() == "ok"
            )
        except Exception:
            return False

    async def health_snapshot(self) -> dict[str, Any]:
        """Return redacted worker and durable-store health derived at runtime."""

        task = self._task
        if task is None:
            worker_state = "stopped"
        elif not task.done():
            worker_state = "running"
        elif task.cancelled():
            worker_state = "stopped"
        else:
            worker_state = "failed"
        worker_running = worker_state == "running"
        database_healthy = await asyncio.to_thread(self._database_is_healthy)
        healthy = worker_running and database_healthy

        if healthy:
            reason = None
        elif not database_healthy and not worker_running:
            reason = "Follow-up worker is stopped and durable storage is unavailable."
        elif not database_healthy:
            reason = "Durable follow-up storage is unavailable."
        elif worker_state == "failed":
            reason = "Follow-up worker stopped unexpectedly."
        else:
            reason = "Follow-up worker is not running."
        return {
            "healthy": healthy,
            "status": "healthy" if healthy else "degraded",
            "worker_running": worker_running,
            "worker_state": worker_state,
            "database_healthy": database_healthy,
            "worker": {
                "running": worker_running,
                "state": worker_state,
            },
            "database": {"healthy": database_healthy},
            "reason": reason,
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            await asyncio.sleep(self.poll_seconds)

    async def run_once(self) -> None:
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs
                WHERE status IN ('pending','delivery_pending') AND next_run_at<=?
                ORDER BY next_run_at LIMIT 20
                """,
                (self._iso(self._now()),),
            ).fetchall()
        for row in rows:
            await self._execute(self._row(row))

    async def _execute(self, job: dict[str, Any]) -> None:
        async with self._operation_lock:
            await self._execute_locked(job)

    async def _execute_locked(self, job: dict[str, Any]) -> None:
        if job["status"] == "delivery_pending":
            await self._deliver_pending(job)
            return
        with self._db() as con:
            claimed = con.execute(
                "UPDATE followup_jobs SET status='executing' WHERE job_id=? AND status='pending'",
                (job["job_id"],),
            ).rowcount
        if not claimed:
            return
        if job["kind"] == "external_monitor":
            terminal_reason = self._external_monitor_terminal_reason(job)
            if terminal_reason is not None:
                if (
                    terminal_reason == "expires_at"
                    and job["payload"].get("notify_if_unchanged") is True
                ):
                    try:
                        done, message, result = await self._evaluate(job)
                    except Exception as exc:
                        await self._handle_evaluation_failure(job, exc)
                        return
                    if done:
                        queued = self._queue_delivery(
                            job["job_id"],
                            message,
                            result,
                            completion_status="completed",
                        )
                        if queued is not None:
                            await self._deliver_pending(queued)
                    else:
                        await self._expire_external_monitor(
                            job,
                            terminal_reason,
                            result=result,
                        )
                else:
                    await self._expire_external_monitor(job, terminal_reason)
                return
            with self._db() as con:
                incremented = con.execute(
                    """
                    UPDATE followup_jobs SET poll_count=poll_count+1
                    WHERE job_id=? AND status='executing'
                    """,
                    (job["job_id"],),
                ).rowcount
            if not incremented:
                return
            job = dict(job)
            job["poll_count"] = int(job.get("poll_count") or 0) + 1
        try:
            done, message, result = await self._evaluate(job)
        except Exception as exc:
            await self._handle_evaluation_failure(job, exc)
            return

        if not done:
            if job["kind"] == "external_monitor":
                terminal_reason = self._external_monitor_terminal_reason(job)
                if terminal_reason is not None:
                    await self._expire_external_monitor(job, terminal_reason, result=result)
                    return
            with self._db() as con:
                con.execute(
                    """
                    UPDATE followup_jobs SET status='pending',next_run_at=?,
                    result_json=?,attempts=0 WHERE job_id=? AND status='executing'
                    """,
                    (
                        self._iso(self._now() + timedelta(seconds=self._next_interval(job))),
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                        job["job_id"],
                    ),
                )
            return
        queued = self._queue_delivery(job["job_id"], message, result, completion_status="completed")
        if queued is not None:
            await self._deliver_pending(queued)

    def _external_monitor_terminal_reason(self, job: Mapping[str, Any]) -> str | None:
        expires_at = job.get("expires_at")
        if expires_at:
            try:
                expired = self._parse_expiry(str(expires_at)) <= self._now()
            except ValueError:
                expired = True
            if expired:
                return "expires_at"
        max_polls = job.get("max_polls")
        if max_polls is not None and int(job.get("poll_count") or 0) >= int(max_polls):
            return "max_polls"
        return None

    async def _expire_external_monitor(
        self,
        job: Mapping[str, Any],
        reason: str,
        *,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        capability_id = str(job["payload"].get("capability_id") or "monitor")
        if reason == "expires_at" and job["payload"].get("notify_if_unchanged") is True:
            message = (
                f"I did not observe a verified change for {capability_id} before "
                "the requested deadline."
            )
        elif reason == "max_polls":
            message = (
                f"I stopped monitoring {capability_id} after its configured poll "
                "limit was reached without a verified change."
            )
        else:
            message = (
                f"I stopped monitoring {capability_id} because its configured "
                "expiry time was reached without a verified change."
            )
        terminal_result = dict(result or {})
        terminal_result.update(
            {
                "verified": bool(result is not None and result.get("verified") is True),
                "changed": False,
                "terminal_reason": reason,
                "poll_count": int(job.get("poll_count") or 0),
                "max_polls": job.get("max_polls"),
                "expires_at": job.get("expires_at"),
            }
        )
        queued = self._queue_delivery(
            str(job["job_id"]),
            message,
            terminal_result,
            completion_status="expired",
        )
        if queued is not None:
            await self._deliver_pending(queued)

    def _next_interval(self, job: Mapping[str, Any]) -> int:
        payload = job["payload"]
        if job["kind"] == "external_monitor":
            return int(payload["polling_interval_seconds"])
        if job["kind"] == "periodic":
            return max(10, min(int(payload.get("interval_seconds") or 60), 86400))
        return min(60, self.poll_seconds * 2)

    async def _handle_evaluation_failure(self, job: dict[str, Any], exc: Exception) -> None:
        attempts = int(job["attempts"]) + 1
        maximum = int(job.get("max_attempts") or self.max_attempts)
        result = {
            "error": "Follow-up evaluation failed",
            "error_type": type(exc).__name__,
            "verified": False,
        }
        if job["kind"] == "external_monitor":
            result.update(
                error="External monitor evaluation failed",
                provider=job["payload"].get("provider"),
                capability_id=job["payload"].get("capability_id"),
                changed=False,
            )
        if attempts < maximum:
            with self._db() as con:
                con.execute(
                    """
                    UPDATE followup_jobs SET status='pending',attempts=?,
                    next_run_at=?,result_json=? WHERE job_id=? AND status='executing'
                    """,
                    (
                        attempts,
                        self._iso(
                            self._now()
                            + timedelta(seconds=min(3600, 2**attempts * self.poll_seconds))
                        ),
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                        job["job_id"],
                    ),
                )
            return

        message = (
            "I stopped that external monitor after repeated evaluation failures. "
            "I did not verify a change."
            if job["kind"] == "external_monitor"
            else "I couldn't complete that follow-up because the required service was unavailable."
        )
        with self._db() as con:
            updated = con.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',attempts=?,
                result_json=?,delivery_message=?,completion_status='failed',
                delivery_state='pending',next_run_at=?
                WHERE job_id=? AND status='executing'
                """,
                (
                    attempts,
                    json.dumps(result, separators=(",", ":"), sort_keys=True),
                    message,
                    self._iso(self._now()),
                    job["job_id"],
                ),
            ).rowcount
        if updated:
            pending = await self.get(job["job_id"])
            if pending is not None:
                await self._deliver_pending(pending)

    def _queue_delivery(
        self,
        job_id: str,
        message: str,
        result: Mapping[str, Any],
        *,
        completion_status: str,
    ) -> dict[str, Any] | None:
        if completion_status not in {"completed", "failed", "expired"}:
            raise ValueError("Invalid follow-up completion status")
        message = str(message).strip()
        if not message:
            raise ValueError("Follow-up delivery message cannot be empty")
        result_json = json.dumps(dict(result), separators=(",", ":"), sort_keys=True)
        with self._db() as con:
            updated = con.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',result_json=?,
                delivery_message=?,completion_status=?,delivery_state='pending',
                next_run_at=? WHERE job_id=? AND status='executing'
                """,
                (
                    result_json,
                    message,
                    completion_status,
                    self._iso(self._now()),
                    job_id,
                ),
            ).rowcount
            row = (
                con.execute("SELECT * FROM followup_jobs WHERE job_id=?", (job_id,)).fetchone()
                if updated
                else None
            )
        return self._row(row) if row is not None else None

    async def _deliver_pending(self, job: dict[str, Any]) -> None:
        with self._db() as con:
            claimed = con.execute(
                "UPDATE followup_jobs SET status='delivering',"
                "delivery_state='delivering' WHERE job_id=? "
                "AND status='delivery_pending'",
                (job["job_id"],),
            ).rowcount
        if not claimed:
            return
        try:
            await self.conversations.add_assistant_message(
                str(job["conversation_id"]),
                str(job.get("delivery_message") or ""),
                delivery_key=f"followup:{job['job_id']}:delivery",
            )
        except Exception as exc:
            attempts = int(job.get("delivery_attempts") or 0) + 1
            maximum = int(job.get("max_attempts") or self.max_attempts)
            result = dict(job.get("result") or {})
            result["delivery_error"] = "Conversation delivery failed"
            result["delivery_error_type"] = type(exc).__name__
            with self._db() as con:
                if attempts >= maximum:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='failed',
                        delivery_state='failed',delivery_attempts=?,result_json=?
                        WHERE job_id=? AND status='delivering'
                        """,
                        (
                            attempts,
                            json.dumps(result, separators=(",", ":"), sort_keys=True),
                            job["job_id"],
                        ),
                    )
                else:
                    con.execute(
                        """
                        UPDATE followup_jobs SET status='delivery_pending',
                        delivery_state='pending',delivery_attempts=?,result_json=?,
                        next_run_at=? WHERE job_id=? AND status='delivering'
                        """,
                        (
                            attempts,
                            json.dumps(result, separators=(",", ":"), sort_keys=True),
                            self._iso(
                                self._now()
                                + timedelta(seconds=min(3600, 2**attempts * self.poll_seconds))
                            ),
                            job["job_id"],
                        ),
                    )
            return

        completion = str(job.get("completion_status") or "failed")
        if completion not in {"completed", "failed", "expired"}:
            completion = "failed"
        with self._db() as con:
            con.execute(
                """
                UPDATE followup_jobs SET status=?,delivered_at=?,
                delivery_state='delivered' WHERE job_id=? AND status='delivering'
                """,
                (completion, self._iso(self._now()), job["job_id"]),
            )

    async def _evaluate(self, job: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        payload, kind = job["payload"], job["kind"]
        if kind == "external_monitor":
            return await self._evaluate_external_monitor(job)
        if kind in {"time", "scheduled"}:
            return (
                True,
                str(payload.get("message") or "Your requested follow-up is due."),
                {"verified": True},
            )
        if kind == "completion" and payload.get("source_type") == "followup_job":
            source = await self.get(str(payload.get("source_job_id") or ""))
            if source is None:
                raise RuntimeError("The referenced job no longer exists")
            status = str(source.get("status") or "unknown")
            if status == "failed":
                return (
                    True,
                    "The job I was watching failed, so it did not complete successfully.",
                    {
                        "source_job_id": source["job_id"],
                        "state": status,
                        "verified": True,
                    },
                )
            if status in {"cancelled", "expired"}:
                return (
                    True,
                    "The job I was watching ended without completing successfully.",
                    {
                        "source_job_id": source["job_id"],
                        "state": status,
                        "verified": True,
                    },
                )
            return (
                status == "completed",
                str(payload.get("message") or "The job I was watching has finished."),
                {
                    "source_job_id": source["job_id"],
                    "state": status,
                    "verified": status == "completed",
                },
            )

        entity_id = str(payload.get("entity_id") or "")
        states = {
            str(item.get("entity_id")): item
            for item in await self.states.readable_entity_states(refresh=True)
        }
        entity = states.get(entity_id)
        if entity is None:
            raise RuntimeError("The monitored Home Assistant entity is no longer available")
        state = str(entity.get("state") or "unknown")
        if kind in {"condition", "completion"}:
            if payload.get("comparison") == "changed":
                changed = state != str(payload.get("baseline") or "")
                return (
                    changed,
                    str(
                        payload.get("message")
                        or f"{entity.get('name') or entity_id} changed to {state}."
                    ),
                    {"entity_id": entity_id, "state": state, "verified": changed},
                )
            wanted = str(payload.get("state") or "on")
            return (
                state == wanted,
                str(payload.get("message") or f"{entity.get('name') or entity_id} is now {state}."),
                {
                    "entity_id": entity_id,
                    "state": state,
                    "verified": state == wanted,
                },
            )
        baseline = str(payload.get("baseline") or "")
        return (
            state != baseline,
            str(payload.get("message") or f"{entity.get('name') or entity_id} changed to {state}."),
            {
                "entity_id": entity_id,
                "state": state,
                "verified": state != baseline,
            },
        )

    async def _evaluate_external_monitor(
        self, job: Mapping[str, Any]
    ) -> tuple[bool, str, dict[str, Any]]:
        evaluator = self.external_evaluator
        if evaluator is None:
            raise RuntimeError("External monitor evaluator is not configured")
        monitor = dict(job["payload"])
        monitor.update(
            job_id=job["job_id"],
            attempt=int(job.get("attempts") or 0) + 1,
        )
        callback: Callable[[Mapping[str, Any]], Any]
        if hasattr(evaluator, "evaluate_external_monitor"):
            callback = evaluator.evaluate_external_monitor  # type: ignore[union-attr]
        elif hasattr(evaluator, "evaluate"):
            callback = evaluator.evaluate  # type: ignore[union-attr]
        elif callable(evaluator):
            callback = evaluator
        else:
            raise RuntimeError("External monitor evaluator is invalid")

        evaluation = callback(monitor)
        if inspect.isawaitable(evaluation):
            evaluation = await evaluation
        if isinstance(evaluation, ExternalMonitorEvaluation):
            data = asdict(evaluation)
        elif isinstance(evaluation, Mapping):
            data = dict(evaluation)
        else:
            raise RuntimeError("External monitor returned a malformed evaluation")
        if data.get("verified") is not True:
            raise RuntimeError("External monitor result was not verified")

        if data.get("changed") is not None:
            raise RuntimeError(
                "External monitor evaluators must return observations, not decisions"
            )
        value_present = any(key in data for key in ("value", "current", "observation"))
        value = data.get("value", data.get("current", data.get("observation")))
        if not value_present:
            raise RuntimeError("External monitor must return an observed value")
        if redact_secrets(value) != value:
            raise RuntimeError("External monitor observation contained credential material")
        changed = self._compare_external_values(
            job["payload"]["baseline"],
            value,
            job["payload"]["comparison"],
        )

        observed_at = data.get("observed_at")
        if observed_at is None:
            evaluated_at = self._iso(self._now())
        else:
            try:
                evaluated_at = self._iso(self._parse_expiry(observed_at))
            except ValueError as exc:
                raise RuntimeError(
                    "External monitor returned a malformed observation timestamp"
                ) from exc

        result: dict[str, Any] = {
            "provider": job["payload"]["provider"],
            "capability_id": job["payload"]["capability_id"],
            "verified": True,
            "changed": changed,
            "comparison_operator": self._comparison_operator(job["payload"]["comparison"]),
            "baseline": job["payload"]["baseline"],
            "poll_count": int(job.get("poll_count") or 0),
            "evaluated_at": evaluated_at,
        }
        if value_present:
            result["value"] = value
        if data.get("provider_reference") is not None:
            result["provider_reference"] = redact_text(data["provider_reference"], max_length=500)
        try:
            json.dumps(result, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("External monitor returned a non-serializable result") from exc

        # Evaluators supply observations, never user-facing claims.  The worker
        # binds its message to the values that its own comparison verified.
        message = self._external_monitor_change_message(
            job["payload"]["baseline"],
            value,
            job["payload"]["comparison"],
        )
        return changed, message, result

    @classmethod
    def _comparison_operator(cls, comparison: Any) -> str:
        if isinstance(comparison, str):
            operator = comparison.strip().lower()
        elif isinstance(comparison, Mapping):
            if (
                comparison.get("operator") is not None
                and comparison.get("type") is not None
                and str(comparison["operator"]).strip().lower()
                != str(comparison["type"]).strip().lower()
            ):
                raise ValueError("External monitor comparison operators conflict")
            operator = (
                str(comparison.get("operator") or comparison.get("type") or "").strip().lower()
            )
        else:
            raise ValueError("External monitor comparison is malformed")
        if operator not in VALID_EXTERNAL_COMPARISONS:
            raise ValueError(f"Unsupported external monitor comparison: {operator or 'missing'}")
        return operator

    @staticmethod
    def _is_number(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            or isinstance(value, float)
            and math.isfinite(value)
        )

    @classmethod
    def _comparison_target(cls, comparison: Any) -> Any:
        if not isinstance(comparison, Mapping):
            raise ValueError("External monitor comparison requires an explicit target")
        target_keys = [key for key in ("target", "expected", "value") if key in comparison]
        if len(target_keys) != 1:
            raise ValueError("External monitor comparison requires exactly one explicit target")
        return comparison[target_keys[0]]

    @classmethod
    def _validate_external_comparison(cls, baseline: Any, comparison: Any) -> None:
        operator = cls._comparison_operator(comparison)
        if isinstance(comparison, Mapping):
            allowed = {"operator", "type", "target", "expected", "value"}
            if any(key not in allowed for key in comparison):
                raise ValueError("External monitor comparison has unsupported fields")
            target_keys = {key for key in ("target", "expected", "value") if key in comparison}
        else:
            target_keys = set()

        if operator in EXPLICIT_TARGET_COMPARISONS:
            target = cls._comparison_target(comparison)
            if operator in {"less_than", "greater_than"} and not (
                cls._is_number(baseline) and cls._is_number(target)
            ):
                raise ValueError("Ordered external monitor comparisons require numeric values")
            try:
                already_satisfied = cls._compare_external_values(baseline, baseline, comparison)
            except RuntimeError as exc:
                raise ValueError(
                    "External monitor baseline is incompatible with its comparison"
                ) from exc
            if already_satisfied:
                raise ValueError("External monitor baseline already satisfies its comparison")
            return

        if target_keys:
            raise ValueError(f"External monitor comparison '{operator}' does not accept a target")
        if operator in {"decreased", "increased"} and not cls._is_number(baseline):
            raise ValueError("Ordered external monitors require a numeric baseline")
        if operator == "truthy" and bool(baseline):
            raise ValueError("External monitor baseline already satisfies its comparison")

    @staticmethod
    def _render_monitor_value(value: Any) -> str:
        rendered = json.dumps(
            redact_secrets(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        return redact_text(rendered, max_length=240)

    @classmethod
    def _external_monitor_change_message(cls, baseline: Any, current: Any, comparison: Any) -> str:
        operator = cls._comparison_operator(comparison)
        if operator == "changed" and any(
            isinstance(value, Mapping) and str(value.get("kind") or "") == "content_fingerprint"
            for value in (baseline, current)
        ):
            return "The monitored page or live result changed."

        numeric_evidence = cls._is_number(baseline) and cls._is_number(current)
        if operator in EXPLICIT_TARGET_COMPARISONS:
            target_value = cls._comparison_target(comparison)
            numeric_evidence = numeric_evidence and cls._is_number(target_value)
        if not numeric_evidence:
            neutral_messages = {
                "changed": "The monitored external value changed.",
                "equals": ("The monitored external value reached the configured target."),
                "not_equals": (
                    "The monitored external value no longer matches the configured target."
                ),
                "contains": ("The monitored external value now contains the configured target."),
                "truthy": "The monitored external condition became true.",
            }
            if operator in neutral_messages:
                return neutral_messages[operator]

        before = cls._render_monitor_value(baseline)
        after = cls._render_monitor_value(current)
        if operator == "changed":
            return f"The monitored value changed from {before} to {after}."
        if operator == "decreased":
            return f"The monitored value decreased from {before} to {after}."
        if operator == "increased":
            return f"The monitored value increased from {before} to {after}."
        if operator == "truthy":
            return f"The monitored value became truthy, changing from {before} to {after}."

        target = cls._render_monitor_value(cls._comparison_target(comparison))
        if operator == "equals":
            return f"The monitored value reached {after}, matching target {target}."
        if operator == "not_equals":
            return (
                f"The monitored value changed from {before} to {after}, which no "
                f"longer matches target {target}."
            )
        if operator == "less_than":
            return f"The monitored value is now {after}, below target {target} (baseline {before})."
        if operator == "greater_than":
            return f"The monitored value is now {after}, above target {target} (baseline {before})."
        if operator == "contains":
            return f"The monitored value now contains target {target}: {after}."
        raise RuntimeError("External monitor comparison could not be described")

    @classmethod
    def _compare_external_values(cls, baseline: Any, current: Any, comparison: Any) -> bool:
        operator = cls._comparison_operator(comparison)
        if operator == "changed":
            return current != baseline
        if operator in EXPLICIT_TARGET_COMPARISONS:
            expected = cls._comparison_target(comparison)
        else:
            expected = baseline
        if operator == "equals":
            return current == expected
        if operator == "not_equals":
            return current != expected
        if operator in {"decreased", "less_than"}:
            if not (cls._is_number(current) and cls._is_number(expected)):
                raise RuntimeError("External monitor values cannot be ordered")
            return current < expected
        if operator in {"increased", "greater_than"}:
            if not (cls._is_number(current) and cls._is_number(expected)):
                raise RuntimeError("External monitor values cannot be ordered")
            return current > expected
        if operator == "contains":
            try:
                return expected in current
            except TypeError as exc:
                raise RuntimeError("External monitor value does not support containment") from exc
        if operator == "truthy":
            return bool(current)
        raise RuntimeError("External monitor comparison could not be evaluated")
