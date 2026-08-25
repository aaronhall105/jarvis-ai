"""Durable, idempotent same-conversation follow-ups for Jarvis Core."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


class ConversationWriter(Protocol):
    async def add_assistant_message(self, conversation_id: str, content: str) -> dict[str, Any]: ...


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
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations, self.states = conversations, states
        self.poll_seconds = max(1, min(poll_seconds, 60))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
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
              delivered_at TEXT, result_json TEXT, idempotency_key TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_followup_due ON followup_jobs(status, next_run_at);
            """)
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
    ) -> dict[str, Any]:
        if kind not in {"time", "scheduled", "condition", "periodic", "completion"}:
            raise ValueError("Unsupported follow-up type")
        key = idempotency_key or str(uuid.uuid4())
        job_id, now, due = str(uuid.uuid4()), self._iso(self._now()), self._iso(due_at)
        try:
            with self._db() as con:
                con.execute(
                    "INSERT INTO followup_jobs(job_id,conversation_id,kind,payload_json,status,created_at,next_run_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        conversation_id,
                        kind,
                        json.dumps(payload, separators=(",", ":")),
                        "pending",
                        now,
                        due,
                        key,
                    ),
                )
        except sqlite3.IntegrityError:
            with self._db() as con:
                existing = con.execute(
                    "SELECT * FROM followup_jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
            if existing is None:
                raise
            return self._row(existing)
        return await self.get(job_id) or {}

    async def get(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as con:
            row = con.execute("SELECT * FROM followup_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

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
        return data

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="jarvis-followups")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            await asyncio.sleep(self.poll_seconds)

    async def run_once(self) -> None:
        now = self._iso(self._now())
        with self._db() as con:
            rows = con.execute(
                "SELECT * FROM followup_jobs WHERE status='pending' AND next_run_at<=? ORDER BY next_run_at LIMIT 20",
                (now,),
            ).fetchall()
        for row in rows:
            await self._execute(self._row(row))

    async def _execute(self, job: dict[str, Any]) -> None:
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
            await self.conversations.add_assistant_message(job["conversation_id"], message)
            with self._db() as con:
                con.execute(
                    "UPDATE followup_jobs SET status='completed',delivered_at=?,result_json=? WHERE job_id=?",
                    (self._iso(self._now()), json.dumps(result), job["job_id"]),
                )
        except Exception as exc:
            attempts = int(job["attempts"]) + 1
            if attempts >= 3:
                message = "I couldn't complete that follow-up because the required service was unavailable."
                await self.conversations.add_assistant_message(job["conversation_id"], message)
                with self._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET status='failed',attempts=?,delivered_at=?,result_json=? WHERE job_id=?",
                        (
                            attempts,
                            self._iso(self._now()),
                            json.dumps({"error": str(exc)}),
                            job["job_id"],
                        ),
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
            if status == "failed":
                return (
                    True,
                    "The job I was watching failed, so it did not complete successfully.",
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
