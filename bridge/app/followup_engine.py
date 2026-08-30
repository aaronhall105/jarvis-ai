"""Durable, idempotent same-conversation follow-ups for Jarvis Core."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from app.capability_registry import ActionReceiptStore

logger = logging.getLogger("jarvis-core.followups")


class ConversationWriter(Protocol):
    async def add_assistant_message(self, conversation_id: str, content: str) -> dict[str, Any]: ...

    async def add_assistant_message_once(
        self, conversation_id: str, content: str, idempotency_key: str
    ) -> dict[str, Any]: ...


class StateReader(Protocol):
    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]: ...


class FollowUpEngine:
    """SQLite-backed worker; delivery is committed before a job becomes final."""

    def __init__(
        self,
        database_path: str,
        conversations: ConversationWriter,
        states: StateReader,
        poll_seconds: int = 2,
        receipts: ActionReceiptStore | None = None,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations, self.states = conversations, states
        self.receipts = receipts
        self.poll_seconds = max(1, min(poll_seconds, 60))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._receipt_blocked_jobs: dict[str, str] = {}
        # Conversation deletion and delivery must not race inside the worker
        # process. A cancelled job may never append to a conversation after the
        # delete path has checked it.
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
            con.executescript("""
            CREATE TABLE IF NOT EXISTS followup_jobs (
              job_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL,
              payload_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
              next_run_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              delivered_at TEXT, result_json TEXT, idempotency_key TEXT NOT NULL UNIQUE,
              action_id TEXT, actor_key TEXT,
              receipt_finalized INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_followup_due ON followup_jobs(status, next_run_at);
            """)
            columns = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(followup_jobs)").fetchall()
            }
            if "action_id" not in columns:
                con.execute("ALTER TABLE followup_jobs ADD COLUMN action_id TEXT")
            if "actor_key" not in columns:
                con.execute("ALTER TABLE followup_jobs ADD COLUMN actor_key TEXT")
            if "receipt_finalized" not in columns:
                con.execute(
                    "ALTER TABLE followup_jobs ADD COLUMN "
                    "receipt_finalized INTEGER NOT NULL DEFAULT 0"
                )
            con.execute("UPDATE followup_jobs SET status='pending' WHERE status='executing'")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    async def create(
        self,
        *,
        conversation_id: str,
        kind: str,
        payload: dict[str, Any],
        due_at: datetime,
        idempotency_key: str | None = None,
        actor_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"time", "scheduled", "condition", "periodic", "completion"}:
            raise ValueError("Unsupported follow-up type")
        key = idempotency_key or str(uuid.uuid4())
        job_id, now, due = str(uuid.uuid4()), self._iso(self._now()), self._iso(due_at)
        inserted = False
        try:
            with self._db() as con:
                con.execute(
                    "INSERT INTO followup_jobs(job_id,conversation_id,kind,payload_json,status,created_at,next_run_at,idempotency_key,actor_key) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        conversation_id,
                        kind,
                        json.dumps(payload, separators=(",", ":")),
                        "pending",
                        now,
                        due,
                        key,
                        actor_key,
                    ),
                )
                inserted = True
        except sqlite3.IntegrityError:
            with self._db() as con:
                existing = con.execute(
                    "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
            if existing is None:
                raise
            existing_job = self._row(existing)
            if self.receipts is not None and not existing_job.get("action_id"):
                await self._ensure_receipt(existing_job)
                return await self.get(existing_job["job_id"]) or existing_job
            return existing_job
        if inserted and self.receipts is not None:
            receipt: dict[str, Any] | None = None
            try:
                receipt = await self.receipts.begin(
                    capability_id="core.followup.schedule",
                    provider="followup",
                    tool_name=f"followup.{kind}",
                    requested_action="schedule_followup",
                    target={
                        "job_id": job_id,
                        "kind": kind,
                        "due_at": due,
                        "entity_id": payload.get("entity_id"),
                        "source_job_id": payload.get("source_job_id"),
                    },
                    conversation_id=conversation_id,
                    actor_key=actor_key,
                    status="scheduled",
                    action_id=f"followup:{job_id}",
                )
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET action_id=? WHERE job_id=?",
                        (receipt["action_id"], job_id),
                    )
            except Exception as exc:
                # A promised job without its audit record is not production-safe.
                with self._db() as con:
                    con.execute("DELETE FROM followup_jobs WHERE job_id=?", (job_id,))
                if receipt and receipt.get("action_id"):
                    try:
                        await self.receipts.complete(
                            str(receipt["action_id"]),
                            {"success": False, "job_id": job_id},
                            status="failed",
                            verified=False,
                            error=f"Could not link follow-up job to receipt: {exc}",
                        )
                    except Exception:
                        logger.exception(
                            "Could not finalize unlinked follow-up receipt job=%s",
                            job_id,
                        )
                raise
        return await self.get(job_id) or {}

    async def get(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as con:
            row = con.execute("SELECT * FROM followup_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    async def _ensure_receipt(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if self.receipts is None:
            return None
        action_id = str(job.get("action_id") or f"followup:{job['job_id']}")
        receipt_getter = getattr(self.receipts, "get", None)
        if job.get("action_id") and receipt_getter is not None:
            existing = await receipt_getter(action_id)
            if existing is not None:
                return existing
        payload = job.get("payload") or {}
        receipt = await self.receipts.begin(
            capability_id="core.followup.schedule",
            provider="followup",
            tool_name=f"followup.{job['kind']}",
            requested_action="schedule_followup",
            target={
                "job_id": job["job_id"],
                "kind": job["kind"],
                "due_at": job["next_run_at"],
                "entity_id": payload.get("entity_id"),
                "source_job_id": payload.get("source_job_id"),
            },
            conversation_id=job["conversation_id"],
            actor_key=job.get("actor_key"),
            status="scheduled",
            action_id=action_id,
        )
        if not job.get("action_id"):
            with self._db() as con:
                con.execute(
                    "UPDATE followup_jobs SET action_id=? WHERE job_id=? AND action_id IS NULL",
                    (action_id, job["job_id"]),
                )
            job["action_id"] = action_id
        return receipt

    async def _reconcile_receipts(self) -> None:
        """Close cross-database crash windows before the worker can execute."""
        if self.receipts is None:
            return
        with self._db() as con:
            rows = con.execute(
                """
                SELECT * FROM followup_jobs
                WHERE action_id IS NULL
                   OR (
                       status IN ('completed','failed','cancelled')
                       AND receipt_finalized=0
                   )
                ORDER BY created_at
                """
            ).fetchall()
        for row in rows:
            job = self._row(row)
            try:
                await self._ensure_receipt(job)
                status = str(job.get("status") or "")
                result = dict(job.get("result") or {})
                action_id = str(job.get("action_id") or "")
                if status == "completed":
                    await self.receipts.complete(
                        action_id,
                        {**result, "success": True, "job_id": job["job_id"]},
                        status="verified" if result.get("verified") else "completed",
                        verified=bool(result.get("verified")),
                    )
                    self._mark_receipt_finalized(job["job_id"])
                elif status in {"failed", "cancelled"}:
                    await self.receipts.complete(
                        action_id,
                        {**result, "success": False, "job_id": job["job_id"]},
                        status=status,
                        verified=False,
                        error=str(result.get("error") or status),
                    )
                    self._mark_receipt_finalized(job["job_id"])
                self._receipt_blocked_jobs.pop(job["job_id"], None)
            except Exception as exc:
                self._receipt_blocked_jobs[job["job_id"]] = str(exc)[:500]
                logger.exception("Could not reconcile follow-up receipt job=%s", job["job_id"])

    async def active_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM followup_jobs WHERE conversation_id=? AND status IN ('pending','executing') ORDER BY created_at DESC",
                (conversation_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        data["result"] = json.loads(data.pop("result_json") or "{}")
        data["receipt_finalized"] = bool(data.get("receipt_finalized"))
        return data

    def _mark_receipt_finalized(self, job_id: str) -> None:
        with self._db() as con:
            con.execute(
                "UPDATE followup_jobs SET receipt_finalized=1 WHERE job_id=?",
                (job_id,),
            )
        self._receipt_blocked_jobs.pop(job_id, None)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            await self._reconcile_receipts()
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="jarvis-followups")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Follow-up worker cycle failed; retrying")
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='pending' WHERE status='executing'"
                    )
            await asyncio.sleep(self.poll_seconds)

    async def run_once(self) -> None:
        now = self._iso(self._now())
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM followup_jobs WHERE status='pending' AND next_run_at<=? ORDER BY next_run_at LIMIT 20",
                (now,),
            ).fetchall()
        for row in rows:
            try:
                await self._execute(self._row(row))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Follow-up execution escaped job=%s", row["job_id"])
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='pending' WHERE job_id=? AND status='executing'",
                        (row["job_id"],),
                    )

    async def _execute(self, job: dict[str, Any]) -> None:
        async with self._operation_lock:
            await self._execute_locked(job)

    async def _execute_locked(self, job: dict[str, Any]) -> None:
        if self.receipts is not None:
            try:
                action_id = str(job.get("action_id") or "").strip()
                receipt_getter = getattr(self.receipts, "get", None)
                existing_receipt = (
                    await receipt_getter(action_id)
                    if action_id and receipt_getter is not None
                    else None
                )
                if existing_receipt is None:
                    existing_receipt = await self._ensure_receipt(job)
                if existing_receipt is None or any(
                    (
                        existing_receipt.get("capability_id")
                        != "core.followup.schedule",
                        existing_receipt.get("provider") != "followup",
                        existing_receipt.get("tool_name")
                        != f"followup.{job['kind']}",
                        existing_receipt.get("conversation_id")
                        != job["conversation_id"],
                        (existing_receipt.get("target") or {}).get("job_id")
                        != job["job_id"],
                    )
                ):
                    raise RuntimeError("Follow-up receipt does not match the persisted job")
                self._receipt_blocked_jobs.pop(job["job_id"], None)
            except Exception as exc:
                self._receipt_blocked_jobs[job["job_id"]] = str(exc)[:500]
                logger.exception(
                    "Refusing to execute follow-up without durable receipt job=%s",
                    job["job_id"],
                )
                blocked_result = dict(job.get("result") or {})
                blocked_result.update(
                    {
                        "audit_blocked": True,
                        "audit_error": str(exc)[:500],
                    }
                )
                with self._db() as con:
                    con.execute(
                        """
                        UPDATE followup_jobs
                        SET next_run_at=?, result_json=?
                        WHERE job_id=? AND status='pending'
                        """,
                        (
                            self._iso(
                                self._now()
                                + timedelta(seconds=max(30, self.poll_seconds * 10))
                            ),
                            json.dumps(blocked_result),
                            job["job_id"],
                        ),
                    )
                return
        # Claim atomically; restart/retry cannot produce two deliveries.
        with self._db() as con:
            changed = con.execute(
                "UPDATE followup_jobs SET status='executing' WHERE job_id=? AND status='pending'",
                (job["job_id"],),
            ).rowcount
        if not changed:
            return
        try:
            done, message, result = await self._evaluate(job)
            if not done:
                interval = (
                    max(10, min(int(job["payload"].get("interval_seconds") or 60), 86400))
                    if job["kind"] == "periodic"
                    else min(60, self.poll_seconds * 2)
                )
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='pending',next_run_at=?,result_json=? WHERE job_id=?",
                        (
                            self._iso(self._now() + timedelta(seconds=interval)),
                            json.dumps(result),
                            job["job_id"],
                        ),
                    )
                return
            delivery_key = f"followup:{job['job_id']}"
            deliver_once = getattr(self.conversations, "add_assistant_message_once", None)
            if deliver_once is None:
                await self.conversations.add_assistant_message(job["conversation_id"], message)
            else:
                await deliver_once(job["conversation_id"], message, delivery_key)
            with self._db() as con:
                con.execute(
                    "UPDATE followup_jobs SET status='completed',delivered_at=?,result_json=? WHERE job_id=?",
                    (self._iso(self._now()), json.dumps(result), job["job_id"]),
                )
        except Exception as exc:
            attempts = int(job["attempts"]) + 1
            if attempts >= 3:
                message = "I couldn't complete that follow-up because the required service was unavailable."
                delivery_key = f"followup:{job['job_id']}:failed"
                deliver_once = getattr(self.conversations, "add_assistant_message_once", None)
                delivery_error: Exception | None = None
                try:
                    if deliver_once is None:
                        await self.conversations.add_assistant_message(
                            job["conversation_id"], message
                        )
                    else:
                        await deliver_once(job["conversation_id"], message, delivery_key)
                except Exception as delivery_exc:
                    delivery_error = delivery_exc
                    logger.exception(
                        "Could not deliver terminal follow-up failure message job=%s",
                        job["job_id"],
                    )
                failure_result = {
                    "error": str(exc),
                    "notification_delivered": delivery_error is None,
                }
                if delivery_error is not None:
                    failure_result["notification_error"] = str(delivery_error)
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='failed',attempts=?,delivered_at=?,result_json=? WHERE job_id=?",
                        (
                            attempts,
                            self._iso(self._now()) if delivery_error is None else None,
                            json.dumps(failure_result),
                            job["job_id"],
                        ),
                    )
                if self.receipts is not None and job.get("action_id"):
                    try:
                        await self.receipts.complete(
                            str(job["action_id"]),
                            {
                                "success": False,
                                "job_id": job["job_id"],
                                **failure_result,
                            },
                            status="failed",
                            verified=False,
                            error=str(exc),
                        )
                        self._mark_receipt_finalized(job["job_id"])
                    except Exception as receipt_exc:
                        self._receipt_blocked_jobs[job["job_id"]] = str(
                            receipt_exc
                        )[:500]
                        logger.exception(
                            "Could not finalize failed follow-up receipt job=%s action=%s",
                            job["job_id"],
                            job["action_id"],
                        )
            else:
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='pending',attempts=?,next_run_at=? WHERE job_id=?",
                        (
                            attempts,
                            self._iso(
                                self._now() + timedelta(seconds=2**attempts * self.poll_seconds)
                            ),
                            job["job_id"],
                        ),
                    )
        else:
            if self.receipts is not None and job.get("action_id"):
                try:
                    await self.receipts.complete(
                        str(job["action_id"]),
                        {**result, "success": True, "job_id": job["job_id"]},
                        status="verified" if result.get("verified") else "completed",
                        verified=bool(result.get("verified")),
                    )
                    self._mark_receipt_finalized(job["job_id"])
                except Exception as exc:
                    self._receipt_blocked_jobs[job["job_id"]] = str(exc)[:500]
                    logger.exception(
                        "Could not finalize completed follow-up receipt job=%s action=%s",
                        job["job_id"],
                        job["action_id"],
                    )

    async def _evaluate(self, job: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        payload, kind = job["payload"], job["kind"]
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
            if status in {"failed", "cancelled"}:
                outcome = "failed" if status == "failed" else "was cancelled"
                return (
                    True,
                    f"The job I was watching {outcome}, so it did not complete successfully.",
                    {"source_job_id": source["job_id"], "state": status, "verified": True},
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
                {"entity_id": entity_id, "state": state, "verified": state == wanted},
            )
        baseline = str(payload.get("baseline") or "")
        return (
            state != baseline,
            str(payload.get("message") or f"{entity.get('name') or entity_id} changed to {state}."),
            {"entity_id": entity_id, "state": state, "verified": state != baseline},
        )

    async def status(self) -> dict[str, Any]:
        with self._db() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS count FROM followup_jobs GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "worker_running": self._task is not None and not self._task.done(),
            "receipt_healthy": not self._receipt_blocked_jobs,
            "receipt_blocked_jobs": len(self._receipt_blocked_jobs),
            "last_receipt_error": next(
                reversed(self._receipt_blocked_jobs.values()),
                None,
            ),
            "poll_seconds": self.poll_seconds,
            "total": sum(counts.values()),
            "statuses": counts,
        }

    async def _finalize_cancelled_receipts(
        self,
        jobs: list[dict[str, Any]],
        *,
        reason: str,
    ) -> None:
        if self.receipts is None:
            return
        for job in jobs:
            action_id = str(job.get("action_id") or "").strip()
            if not action_id:
                continue
            try:
                await self.receipts.complete(
                    action_id,
                    {
                        "success": False,
                        "cancelled": True,
                        "job_id": job["job_id"],
                        "error": reason,
                    },
                    status="cancelled",
                    verified=False,
                    error=reason,
                )
                self._mark_receipt_finalized(job["job_id"])
            except Exception as exc:
                self._receipt_blocked_jobs[job["job_id"]] = str(exc)[:500]
                # Cancellation is still authoritative. A receipt-store outage
                # must not let a deleted conversation's job run later.
                logger.exception(
                    "Could not finalize cancelled follow-up receipt job=%s action=%s",
                    job["job_id"],
                    action_id,
                )

    async def cancel(self, job_id: str) -> bool:
        reason = "cancelled"
        async with self._operation_lock:
            with self._db() as con:
                rows = con.execute(
                    "SELECT * FROM followup_jobs WHERE job_id=? AND status='pending'",
                    (job_id,),
                ).fetchall()
                if rows:
                    con.execute(
                        """
                        UPDATE followup_jobs
                        SET status='cancelled', result_json=?
                        WHERE job_id=? AND status='pending'
                        """,
                        (json.dumps({"cancelled": True, "reason": reason}), job_id),
                    )
        jobs = [self._row(row) for row in rows]
        await self._finalize_cancelled_receipts(jobs, reason=reason)
        return bool(jobs)

    async def cancel_for_conversation(self, conversation_id: str) -> int:
        """Cancel every runnable job before its conversation is deleted."""

        reason = "conversation_deleted"
        async with self._operation_lock:
            with self._db() as con:
                rows = con.execute(
                    """
                    SELECT * FROM followup_jobs
                    WHERE conversation_id=? AND status='pending'
                    ORDER BY created_at
                    """,
                    (conversation_id,),
                ).fetchall()
                if rows:
                    con.execute(
                        """
                        UPDATE followup_jobs
                        SET status='cancelled', result_json=?
                        WHERE conversation_id=? AND status='pending'
                        """,
                        (
                            json.dumps({"cancelled": True, "reason": reason}),
                            conversation_id,
                        ),
                    )
        jobs = [self._row(row) for row in rows]
        await self._finalize_cancelled_receipts(jobs, reason=reason)
        return len(jobs)
