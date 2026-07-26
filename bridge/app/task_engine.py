from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("jarvis-core.tasks")


class ActorProtocol(Protocol):
    user_key: str
    display_name: str


class RegistryProtocol(Protocol):
    async def areas(self) -> list[dict[str, Any]]: ...


class ToolProtocol(Protocol):
    registry: RegistryProtocol

    async def controllable_devices(self) -> list[dict[str, Any]]: ...

    async def control_area_lights(
        self,
        area_id: str,
        turn_on: bool,
    ) -> dict[str, Any]: ...

    async def control_device(
        self,
        entity_id: str,
        turn_on: bool,
    ) -> dict[str, Any]: ...

    async def run_media_shortcut(self, shortcut: str) -> dict[str, Any]: ...

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TaskCommandResult:
    handled: bool
    success: bool = True
    response: str = ""
    intent: str = "task"
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ActionPlan:
    action_type: str
    payload: dict[str, Any]
    summary: str


_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "sixty": 60,
}

_RELATIVE_PATTERN = re.compile(
    r"\b(?:in|after)\s+(?P<amount>\d{1,4}|an?|one|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|"
    r"thirty|forty|forty-five|sixty)\s*"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b",
    re.I,
)
_HALF_HOUR_PATTERN = re.compile(r"\b(?:in|after)\s+half\s+(?:an?\s+)?hour\b", re.I)
_QUARTER_HOUR_PATTERN = re.compile(
    r"\b(?:in|after)\s+(?:a\s+)?quarter(?:\s+of\s+an\s+hour|\s+hour)?\b",
    re.I,
)
_TOMORROW_TIME_PATTERN = re.compile(
    r"\btomorrow(?:\s+at)?\s+(?P<hour>\d{1,2})"
    r"(?:(?::|\.)(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b",
    re.I,
)
_AT_TIME_PATTERN = re.compile(
    r"\bat\s+(?P<hour>\d{1,2})(?:(?::|\.)(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>am|pm)?(?P<tomorrow>\s+tomorrow)?\b",
    re.I,
)

_SHOW_TASKS_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me|what(?:'s| is| are)|which)\s+(?:me\s+)?"
    r"(?:my\s+)?(?:scheduled|pending|future)?\s*(?:tasks?|actions?|commands?)\s*[.!?]*\s*$|"
    r"^\s*what\s+(?:have|did)\s+i\s+schedule(?:d)?\s*[.!?]*\s*$",
    re.I,
)
_SHOW_HISTORY_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me|what(?:'s| is| are))\s+(?:me\s+)?"
    r"(?:my\s+)?(?:task|scheduled action)\s+history\s*[.!?]*\s*$|"
    r"^\s*(?:show|list)\s+(?:my\s+)?(?:completed|failed|cancelled|past)\s+"
    r"(?:tasks?|actions?)\s*[.!?]*\s*$",
    re.I,
)
_TASK_STATUS_PATTERN = re.compile(
    r"^\s*(?:what happened to|show|check|tell me about|what(?:'s| is) the status of)\s+"
    r"(?:scheduled\s+)?(?:task|action)\s*#?(?P<task_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_REPEAT_TASK_PATTERN = re.compile(
    r"^\s*(?:repeat|run again|schedule again|do again)\s+"
    r"(?:scheduled\s+)?(?:task|action)\s*#?(?P<task_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_CLEAR_HISTORY_PATTERN = re.compile(
    r"^\s*(?:clear|delete|remove)\s+(?:my\s+)?"
    r"(?P<completed>completed\s+)?(?:task|scheduled action)\s+history\s*[.!?]*\s*$",
    re.I,
)
_AMBIGUOUS_CLEAR_HISTORY_PATTERN = re.compile(
    r"^\s*(?:clear|delete|remove)\s+(?:my\s+)?history\s*[.!?]*\s*$",
    re.I,
)
_CONFIRM_YES_PATTERN = re.compile(
    r"^\s*(?:yes|yeah|yep|correct|that(?:'s| is) right|do it|go ahead)\s*[.!?]*\s*$",
    re.I,
)
_CONFIRM_NO_PATTERN = re.compile(
    r"^\s*(?:no|nope|cancel|never mind|nevermind|don['’]?t)\s*[.!?]*\s*$",
    re.I,
)

_CANCEL_TASK_ID_PATTERN = re.compile(
    r"^\s*(?:cancel|delete|remove)\s+(?:scheduled\s+)?(?:task|action)\s*#?"
    r"(?P<task_id>\d+)\s*[.!?]*\s*$",
    re.I,
)
_CANCEL_LAST_PATTERN = re.compile(
    r"^\s*(?:cancel|delete|remove)\s+(?:my\s+)?(?:last|latest|most recent)\s+"
    r"(?:scheduled\s+)?(?:task|action|command)\s*[.!?]*\s*$",
    re.I,
)
_CANCEL_ALL_PATTERN = re.compile(
    r"^\s*(?:cancel|delete|remove)\s+all\s+(?:my\s+)?(?:scheduled|pending)\s+"
    r"(?:tasks?|actions?|commands?)\s*[.!?]*\s*$",
    re.I,
)

_APP_NAMES = {
    "netflix": ("netflix", "Netflix"),
    "youtube": ("youtube", "YouTube"),
    "bbc iplayer": ("bbc_iplayer", "BBC iPlayer"),
    "bbciplayer": ("bbc_iplayer", "BBC iPlayer"),
    "prime video": ("prime_video", "Prime Video"),
}


class TemporalActionEngine:
    """Restart-safe one-off Home Assistant action scheduler.

    v16.0.3 supports a narrow allow-list: lights, switches, TV power
    and configured TV app shortcuts. Targets are resolved when the task is
    created, so a later registry change cannot silently redirect an action.
    """

    ACTIVE_STATUSES = {"pending", "executing"}
    FINAL_STATUSES = {"completed", "cancelled", "failed", "expired"}

    def __init__(
        self,
        *,
        tools: ToolProtocol,
        database_path: str,
        enabled: bool = True,
        timezone_name: str = "Europe/London",
        poll_seconds: int = 1,
        max_future_days: int = 365,
        notify_completion: bool = True,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.tools = tools
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.poll_seconds = max(1, min(int(poll_seconds), 60))
        self.max_future_days = max(1, min(int(max_future_days), 3650))
        self.notify_completion = bool(notify_completion)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        try:
            self._timezone = ZoneInfo(timezone_name)
            self.timezone_name = timezone_name
        except ZoneInfoNotFoundError:
            self._timezone = timezone.utc
            self.timezone_name = "UTC"

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._running = False
        self._last_error: str | None = None
        self._last_cycle_at: str | None = None
        self._initialise_database()
        self._recover_interrupted_tasks()

    def _utc_now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalise(value: str) -> str:
        value = str(value or "").casefold().replace("_", " ")
        value = re.sub(r"[^a-z0-9\s'-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_action_text(value: str) -> str:
        value = re.sub(r"\b(?:please|could you|can you)\b", " ", value, flags=re.I)
        value = re.sub(r"\s+", " ", value).strip(" ,.!?")
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialise_database(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    timezone_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    executed_at TEXT,
                    cancelled_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
                ON scheduled_tasks(status, due_at, task_id);

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_owner
                ON scheduled_tasks(owner_key, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS task_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES scheduled_tasks(task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_task_audit_time
                ON task_audit(created_at DESC, audit_id DESC);

                CREATE TABLE IF NOT EXISTS task_dialogue (
                    owner_key TEXT PRIMARY KEY,
                    pending_action TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )

    def _recover_interrupted_tasks(self) -> None:
        now = self._iso(self._utc_now())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending', updated_at = ?,
                    error = CASE
                        WHEN error IS NULL OR error = '' THEN 'Recovered after restart.'
                        ELSE error
                    END
                WHERE status = 'executing'
                """,
                (now,),
            )

    def _audit_sync(
        self,
        *,
        task_id: int | None,
        actor: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO task_audit(task_id, created_at, actor, action, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    self._iso(self._utc_now()),
                    actor,
                    action,
                    json.dumps(details or {}, ensure_ascii=False, default=str),
                ),
            )

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["action_payload"] = json.loads(item.pop("action_payload_json"))
        except (TypeError, ValueError):
            item["action_payload"] = {}
            item.pop("action_payload_json", None)
        try:
            item["result"] = json.loads(item.pop("result_json"))
        except (TypeError, ValueError):
            item["result"] = {}
            item.pop("result_json", None)
        return item

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._stop_event = asyncio.Event()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="jarvis-temporal-actions")
        logger.info("Temporal Action Engine started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._running = False
        logger.info("Temporal Action Engine stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive loop guard
                self._last_error = str(exc)
                logger.exception("Temporal action cycle failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except TimeoutError:
                pass

    async def status(self) -> dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM scheduled_tasks GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "version": "16.0.3",
            "enabled": self.enabled,
            "notify_completion": self.notify_completion,
            "running": self._running,
            "timezone": self.timezone_name,
            "poll_seconds": self.poll_seconds,
            "last_cycle_at": self._last_cycle_at,
            "last_error": self._last_error,
            "counts": counts,
        }

    async def list_tasks(
        self,
        *,
        owner_key: str | None = None,
        statuses: set[str] | None = None,
        limit: int = 50,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if owner_key:
            clauses.append("owner_key = ?")
            values.append(owner_key)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(sorted(statuses))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 200)))
        order_by = (
            "updated_at DESC, task_id DESC"
            if newest_first
            else "due_at ASC, task_id ASC"
        )
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM scheduled_tasks
                {where}
                ORDER BY {order_by}
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    async def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id = ?",
                (int(task_id),),
            ).fetchone()
        return self._row_dict(row) if row else None

    async def get_owned_task(
        self,
        task_id: int,
        *,
        owner_key: str,
    ) -> dict[str, Any] | None:
        task = await self.get_task(task_id)
        if task is None or str(task.get("owner_key")) != owner_key:
            return None
        return task

    def _set_pending_confirmation(
        self,
        *,
        owner_key: str,
        pending_action: str,
        ttl_seconds: int = 120,
    ) -> None:
        now = self._utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO task_dialogue(owner_key, pending_action, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_key) DO UPDATE SET
                    pending_action=excluded.pending_action,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    owner_key,
                    pending_action,
                    self._iso(now),
                    self._iso(now + timedelta(seconds=max(15, ttl_seconds))),
                ),
            )

    def _pending_confirmation(self, owner_key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT pending_action, expires_at FROM task_dialogue WHERE owner_key = ?",
                (owner_key,),
            ).fetchone()
            if row is None:
                return None
            if self._parse_iso(str(row["expires_at"])) <= self._utc_now():
                connection.execute(
                    "DELETE FROM task_dialogue WHERE owner_key = ?",
                    (owner_key,),
                )
                return None
            return str(row["pending_action"])

    def _clear_pending_confirmation(self, owner_key: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM task_dialogue WHERE owner_key = ?",
                (owner_key,),
            )

    async def delete_history(
        self,
        *,
        owner_key: str,
        actor: str,
        completed_only: bool = False,
    ) -> int:
        statuses = {"completed"} if completed_only else set(self.FINAL_STATUSES)
        placeholders = ",".join("?" for _ in statuses)
        values: list[Any] = [owner_key, *sorted(statuses)]
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT task_id FROM scheduled_tasks
                WHERE owner_key = ? AND status IN ({placeholders})
                """,
                values,
            ).fetchall()
            task_ids = [int(row["task_id"]) for row in rows]
            if task_ids:
                id_placeholders = ",".join("?" for _ in task_ids)
                connection.execute(
                    f"DELETE FROM task_audit WHERE task_id IN ({id_placeholders})",
                    task_ids,
                )
                connection.execute(
                    f"DELETE FROM scheduled_tasks WHERE task_id IN ({id_placeholders})",
                    task_ids,
                )
        self._audit_sync(
            task_id=None,
            actor=actor,
            action="history_deleted",
            details={
                "owner_key": owner_key,
                "completed_only": completed_only,
                "deleted_count": len(task_ids),
            },
        )
        return len(task_ids)

    async def repeat_task(
        self,
        task_id: int,
        *,
        actor: ActorProtocol,
    ) -> dict[str, Any] | None:
        original = await self.get_owned_task(task_id, owner_key=actor.user_key)
        if original is None:
            return None
        created_at = self._parse_iso(str(original["created_at"]))
        due_at = self._parse_iso(str(original["due_at"]))
        original_delay = max(due_at - created_at, timedelta(seconds=2))
        plan = ActionPlan(
            action_type=str(original["action_type"]),
            payload=dict(original.get("action_payload") or {}),
            summary=str(original["action_summary"]),
        )
        return await self.create_task(
            actor=actor,
            source_text=f"Repeat task {task_id}",
            plan=plan,
            due_at=self._utc_now() + original_delay,
        )

    async def create_task(
        self,
        *,
        actor: ActorProtocol,
        source_text: str,
        plan: ActionPlan,
        due_at: datetime,
    ) -> dict[str, Any]:
        now = self._utc_now()
        due_utc = due_at.astimezone(timezone.utc)
        if due_utc <= now + timedelta(seconds=1):
            raise ValueError("Scheduled time must be at least two seconds in the future.")
        if due_utc > now + timedelta(days=self.max_future_days):
            raise ValueError(
                f"Scheduled time cannot be more than {self.max_future_days} days away."
            )

        now_text = self._iso(now)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scheduled_tasks(
                    owner_key, owner_name, source_text, action_type,
                    action_payload_json, action_summary, due_at, timezone_name,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    actor.user_key,
                    actor.display_name,
                    source_text,
                    plan.action_type,
                    json.dumps(plan.payload, ensure_ascii=False, default=str),
                    plan.summary,
                    self._iso(due_utc),
                    self.timezone_name,
                    now_text,
                    now_text,
                ),
            )
            task_id = int(cursor.lastrowid)
        self._audit_sync(
            task_id=task_id,
            actor=actor.user_key,
            action="created",
            details={"summary": plan.summary, "due_at": self._iso(due_utc)},
        )
        task = await self.get_task(task_id)
        assert task is not None
        return task

    async def cancel_task(
        self,
        task_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        now = self._iso(self._utc_now())
        query = (
            "UPDATE scheduled_tasks SET status='cancelled', cancelled_at=?, updated_at=? "
            "WHERE task_id=? AND status='pending'"
        )
        values: list[Any] = [now, now, int(task_id)]
        if owner_key:
            query += " AND owner_key=?"
            values.append(owner_key)
        with self._connection() as connection:
            cursor = connection.execute(query, values)
            updated = cursor.rowcount > 0
        if updated:
            self._audit_sync(
                task_id=int(task_id),
                actor=actor,
                action="cancelled",
            )
        return updated

    async def cancel_last(self, *, owner_key: str, actor: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT task_id FROM scheduled_tasks
                WHERE owner_key = ? AND status = 'pending'
                ORDER BY created_at DESC, task_id DESC
                LIMIT 1
                """,
                (owner_key,),
            ).fetchone()
        if row is None:
            return None
        task_id = int(row["task_id"])
        updated = await self.cancel_task(
            task_id,
            owner_key=owner_key,
            actor=actor,
        )
        return await self.get_task(task_id) if updated else None

    async def cancel_all(self, *, owner_key: str, actor: str) -> int:
        now = self._iso(self._utc_now())
        with self._connection() as connection:
            task_rows = connection.execute(
                """
                SELECT task_id FROM scheduled_tasks
                WHERE owner_key = ? AND status = 'pending'
                """,
                (owner_key,),
            ).fetchall()
            connection.execute(
                """
                UPDATE scheduled_tasks
                SET status='cancelled', cancelled_at=?, updated_at=?
                WHERE owner_key=? AND status='pending'
                """,
                (now, now, owner_key),
            )
        for row in task_rows:
            self._audit_sync(
                task_id=int(row["task_id"]),
                actor=actor,
                action="cancelled",
                details={"scope": "all"},
            )
        return len(task_rows)

    async def process_once(self) -> int:
        if not self.enabled:
            return 0
        async with self._lock:
            now = self._utc_now()
            now_text = self._iso(now)
            self._last_cycle_at = now_text
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT task_id FROM scheduled_tasks
                    WHERE status='pending' AND due_at <= ?
                    ORDER BY due_at ASC, task_id ASC
                    LIMIT 20
                    """,
                    (now_text,),
                ).fetchall()
            processed = 0
            for row in rows:
                task_id = int(row["task_id"])
                claimed = self._claim_task(task_id, now_text)
                if not claimed:
                    continue
                task = await self.get_task(task_id)
                if task is None:
                    continue
                await self._execute_claimed_task(task)
                processed += 1
            return processed

    def _claim_task(self, task_id: int, now_text: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE scheduled_tasks
                SET status='executing', updated_at=?, attempts=attempts+1
                WHERE task_id=? AND status='pending'
                """,
                (now_text, task_id),
            )
            return cursor.rowcount > 0

    async def _execute_claimed_task(self, task: dict[str, Any]) -> None:
        task_id = int(task["task_id"])
        try:
            result = await self._execute_action(
                str(task["action_type"]),
                dict(task.get("action_payload") or {}),
            )
            success = result.get("success") is True
            verified = result.get("verified")
            if verified is False:
                success = False
            status = "completed" if success else "failed"
            error = None if success else str(
                result.get("response_message")
                or result.get("message")
                or "The scheduled action could not be verified."
            )
        except Exception as exc:  # pragma: no cover - exercised through failure result
            logger.exception("Scheduled task %s failed", task_id)
            result = {"success": False, "error": str(exc)}
            status = "failed"
            error = str(exc)

        now_text = self._iso(self._utc_now())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE scheduled_tasks
                SET status=?, executed_at=?, updated_at=?, result_json=?, error=?
                WHERE task_id=? AND status='executing'
                """,
                (
                    status,
                    now_text,
                    now_text,
                    json.dumps(result, ensure_ascii=False, default=str),
                    error,
                    task_id,
                ),
            )
        self._audit_sync(
            task_id=task_id,
            actor="task-engine",
            action=status,
            details={"result": result, "error": error},
        )
        await self._notify_task_result(
            task=task,
            status=status,
            result=result,
            error=error,
        )

    async def _notify_task_result(
        self,
        *,
        task: dict[str, Any],
        status: str,
        result: dict[str, Any],
        error: str | None,
    ) -> None:
        if not self.notify_completion:
            return
        recipient = str(task.get("owner_key") or "").strip().casefold()
        if recipient not in {"aaron", "amber"}:
            return
        task_id = int(task["task_id"])
        summary = str(task.get("action_summary") or "scheduled action")
        result_message = str(
            result.get("response_message")
            or result.get("message")
            or error
            or ""
        ).strip()
        if status == "completed":
            message = f"Task {task_id} completed: {summary}."
        else:
            message = f"Task {task_id} failed: {summary}."
        if result_message:
            message += f" {result_message}"
        try:
            notification = await self.tools.send_mobile_notification(
                recipient=recipient,
                title="Jarvis scheduled action",
                message=message,
            )
            self._audit_sync(
                task_id=task_id,
                actor="task-engine",
                action="completion_notification",
                details={"recipient": recipient, "result": notification},
            )
        except Exception as exc:  # notification failure must not change task status
            logger.exception("Could not notify %s about task %s", recipient, task_id)
            self._audit_sync(
                task_id=task_id,
                actor="task-engine",
                action="completion_notification_failed",
                details={"recipient": recipient, "error": str(exc)},
            )

    async def _execute_action(
        self,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action_type == "area_lights":
            return await self.tools.control_area_lights(
                str(payload["area_id"]),
                bool(payload["turn_on"]),
            )
        if action_type == "device_control":
            return await self.tools.control_device(
                str(payload["entity_id"]),
                bool(payload["turn_on"]),
            )
        if action_type == "media_shortcut":
            return await self.tools.run_media_shortcut(str(payload["shortcut"]))
        raise ValueError(f"Unsupported scheduled action type: {action_type}")

    @staticmethod
    def _amount(value: str) -> int:
        normalised = value.casefold().strip()
        if normalised in {"a", "an"}:
            return 1
        if normalised.isdigit():
            return int(normalised)
        return _NUMBER_WORDS.get(normalised, -1)

    def _parse_due(
        self,
        text: str,
        now_utc: datetime,
    ) -> tuple[datetime, str, str] | None:
        local_now = now_utc.astimezone(self._timezone)

        match = _HALF_HOUR_PATTERN.search(text)
        if match:
            return (
                now_utc + timedelta(minutes=30),
                self._clean_action_text(text[: match.start()] + " " + text[match.end() :]),
                "in 30 minutes",
            )

        match = _QUARTER_HOUR_PATTERN.search(text)
        if match:
            return (
                now_utc + timedelta(minutes=15),
                self._clean_action_text(text[: match.start()] + " " + text[match.end() :]),
                "in 15 minutes",
            )

        match = _RELATIVE_PATTERN.search(text)
        if match:
            amount = self._amount(match.group("amount"))
            if amount <= 0:
                return None
            unit = match.group("unit").casefold()
            if unit.startswith(("sec", "secs")):
                delta = timedelta(seconds=amount)
                unit_text = "second" if amount == 1 else "seconds"
            elif unit.startswith(("min", "mins")):
                delta = timedelta(minutes=amount)
                unit_text = "minute" if amount == 1 else "minutes"
            elif unit.startswith(("hr", "hour")):
                delta = timedelta(hours=amount)
                unit_text = "hour" if amount == 1 else "hours"
            else:
                delta = timedelta(days=amount)
                unit_text = "day" if amount == 1 else "days"
            return (
                now_utc + delta,
                self._clean_action_text(text[: match.start()] + " " + text[match.end() :]),
                f"in {amount} {unit_text}",
            )

        match = _TOMORROW_TIME_PATTERN.search(text)
        if match:
            due_local = self._clock_time(
                local_now + timedelta(days=1),
                match.group("hour"),
                match.group("minute"),
                match.group("ampm"),
            )
            if due_local is None:
                return None
            return (
                due_local.astimezone(timezone.utc),
                self._clean_action_text(text[: match.start()] + " " + text[match.end() :]),
                f"tomorrow {self._spoken_clock(due_local)}",
            )

        match = _AT_TIME_PATTERN.search(text)
        if match:
            tomorrow = bool(match.group("tomorrow"))
            base_date = local_now + timedelta(days=1 if tomorrow else 0)
            due_local = self._clock_time(
                base_date,
                match.group("hour"),
                match.group("minute"),
                match.group("ampm"),
            )
            if due_local is None:
                return None
            if not tomorrow and due_local <= local_now + timedelta(seconds=1):
                due_local += timedelta(days=1)
            timing = (
                f"tomorrow {self._spoken_clock(due_local)}"
                if due_local.date() != local_now.date()
                else self._spoken_clock(due_local)
            )
            return (
                due_local.astimezone(timezone.utc),
                self._clean_action_text(text[: match.start()] + " " + text[match.end() :]),
                timing,
            )

        return None

    @staticmethod
    def _clock_time(
        base: datetime,
        hour_text: str,
        minute_text: str | None,
        ampm: str | None,
    ) -> datetime | None:
        hour = int(hour_text)
        minute = int(minute_text or 0)
        if minute > 59:
            return None
        if ampm:
            if hour < 1 or hour > 12:
                return None
            hour = hour % 12 + (12 if ampm.casefold() == "pm" else 0)
        elif hour > 23:
            return None
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def _spoken_clock(value: datetime) -> str:
        rendered = value.strftime("%I:%M %p").lower().lstrip("0")
        if rendered.endswith(":00 am"):
            rendered = rendered.replace(":00 am", " am")
        elif rendered.endswith(":00 pm"):
            rendered = rendered.replace(":00 pm", " pm")
        return f"at {rendered}"

    async def _resolve_action(self, text: str) -> ActionPlan | str:
        value = self._clean_action_text(text)
        normalised = self._normalise(value)

        app_match = re.search(
            r"\b(?:open|launch|start|watch)\s+(netflix|youtube|bbc\s*i?player|prime\s*video)\b",
            normalised,
            re.I,
        )
        if app_match:
            key = self._normalise(app_match.group(1))
            shortcut, display = _APP_NAMES[key]
            return ActionPlan(
                action_type="media_shortcut",
                payload={"shortcut": shortcut},
                summary=f"open {display}",
            )

        action, target = self._extract_on_off_target(normalised)
        if action is None or target is None:
            return (
                "I can currently schedule lights, switches, TV power and configured "
                "TV apps."
            )
        turn_on = action == "on"

        if re.search(r"\b(?:tv|television)\b", target):
            return ActionPlan(
                action_type="media_shortcut",
                payload={"shortcut": "tv_on" if turn_on else "tv_off"},
                summary=f"turn the TV {action}",
            )

        area_query = re.sub(r"\blights?\b", " ", target)
        area_query = self._normalise(re.sub(r"\bthe\b", " ", area_query))
        if re.search(r"\blights\b", target):
            area = await self._resolve_area(area_query)
            if isinstance(area, str):
                return area
            return ActionPlan(
                action_type="area_lights",
                payload={
                    "area_id": str(area["area_id"]),
                    "turn_on": turn_on,
                },
                summary=f"turn the {area['name']} lights {action}",
            )

        device = await self._resolve_device(target)
        if isinstance(device, str):
            return device
        return ActionPlan(
            action_type="device_control",
            payload={
                "entity_id": str(device["entity_id"]),
                "turn_on": turn_on,
            },
            summary=f"turn {device['name']} {action}",
        )

    @staticmethod
    def _extract_on_off_target(value: str) -> tuple[str | None, str | None]:
        patterns = (
            re.compile(
                r"\b(?:turn|switch|power)\s+(?P<action>on|off)\s+(?:the\s+)?(?P<target>.+)$",
                re.I,
            ),
            re.compile(
                r"\b(?:turn|switch|power)\s+(?:the\s+)?(?P<target>.+?)\s+(?P<action>on|off)$",
                re.I,
            ),
        )
        for pattern in patterns:
            match = pattern.search(value)
            if match:
                target = re.sub(r"\bplease\b", " ", match.group("target"), flags=re.I)
                target = re.sub(r"\s+", " ", target).strip()
                return match.group("action").casefold(), target
        return None, None

    async def _resolve_area(self, query: str) -> dict[str, Any] | str:
        areas = await self.tools.registry.areas()
        query_key = self._normalise(query)
        exact = [
            area
            for area in areas
            if self._normalise(str(area.get("name") or "")) == query_key
        ]
        if len(exact) == 1:
            area = exact[0]
            return {
                "area_id": str(area.get("area_id") or area.get("id")),
                "name": str(area.get("name") or query),
            }
        if not exact:
            candidates = [
                area
                for area in areas
                if query_key
                and query_key in self._normalise(str(area.get("name") or ""))
            ]
            if len(candidates) == 1:
                area = candidates[0]
                return {
                    "area_id": str(area.get("area_id") or area.get("id")),
                    "name": str(area.get("name") or query),
                }
        if len(exact) > 1:
            return "I found more than one matching room, so I haven’t scheduled it."
        return f"I couldn’t match “{query}” to one Home Assistant room."

    async def _resolve_device(self, query: str) -> dict[str, Any] | str:
        query_key = self._normalise(re.sub(r"\bthe\b", " ", query))
        devices = await self.tools.controllable_devices()
        exact: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        query_terms = set(query_key.split())

        for device in devices:
            name = str(device.get("name") or device.get("entity_id") or "")
            area_name = str(device.get("area_name") or "")
            aliases = {
                self._normalise(name),
                self._normalise(str(device.get("entity_id") or "")),
                self._normalise(f"{area_name} {name}"),
            }
            if query_key in aliases:
                exact.append(device)
                continue
            combined = self._normalise(" ".join(aliases))
            if query_terms and query_terms.issubset(set(combined.split())):
                candidates.append(device)

        matches = exact if exact else candidates
        unique = {
            str(device.get("entity_id")): device
            for device in matches
            if device.get("entity_id")
        }
        if len(unique) == 1:
            device = next(iter(unique.values()))
            return {
                "entity_id": str(device["entity_id"]),
                "name": str(device.get("name") or device["entity_id"]),
            }
        if len(unique) > 1:
            names = sorted(
                str(device.get("name") or device.get("entity_id"))
                for device in unique.values()
            )[:3]
            return (
                "I found more than one matching device — "
                + ", ".join(names)
                + ". Please name the exact one."
            )
        return f"I couldn’t find one controllable device matching “{query}”."

    def _task_due_phrase(self, task: dict[str, Any]) -> str:
        due = self._parse_iso(str(task["due_at"])).astimezone(self._timezone)
        local_now = self._utc_now().astimezone(self._timezone)
        if due.date() == local_now.date():
            return self._spoken_clock(due)
        if due.date() == (local_now + timedelta(days=1)).date():
            return f"tomorrow {self._spoken_clock(due)}"
        return due.strftime("on %A %-d %B at %-I:%M %p").lower()

    @staticmethod
    def _task_result_message(task: dict[str, Any]) -> str:
        result = task.get("result") or {}
        return str(
            result.get("response_message")
            or result.get("message")
            or task.get("error")
            or ""
        ).strip()

    def _describe_task_status(self, task: dict[str, Any]) -> str:
        task_id = int(task["task_id"])
        summary = str(task["action_summary"])
        status = str(task.get("status") or "unknown")
        if status in self.ACTIVE_STATUSES:
            return f"Task {task_id} is pending: {summary} {self._task_due_phrase(task)}."
        result_message = self._task_result_message(task)
        if status == "completed":
            response = f"Task {task_id} completed: {summary}."
        elif status == "failed":
            response = f"Task {task_id} failed: {summary}."
        elif status == "cancelled":
            response = f"Task {task_id} was cancelled: {summary}."
        elif status == "expired":
            response = f"Task {task_id} expired without running: {summary}."
        else:
            response = f"Task {task_id} has status {status}: {summary}."
        if result_message:
            response += f" {result_message}"
        return response

    async def handle_command(
        self,
        text: str,
        actor: ActorProtocol,
    ) -> TaskCommandResult:
        value = self._clean_action_text(text)

        pending_confirmation = self._pending_confirmation(actor.user_key)
        if pending_confirmation == "delete_completed_history":
            if _CONFIRM_YES_PATTERN.match(value):
                self._clear_pending_confirmation(actor.user_key)
                count = await self.delete_history(
                    owner_key=actor.user_key,
                    actor=actor.user_key,
                    completed_only=True,
                )
                if count == 0:
                    response = "You have no completed task history to delete."
                else:
                    response = (
                        f"Deleted {count} completed task history "
                        f"record{'s' if count != 1 else ''}."
                    )
                return TaskCommandResult(
                    handled=True,
                    response=response,
                    details={
                        "deleted_count": count,
                        "completed_only": True,
                        "confirmed": True,
                    },
                )
            if _CONFIRM_NO_PATTERN.match(value):
                self._clear_pending_confirmation(actor.user_key)
                return TaskCommandResult(
                    handled=True,
                    response="Okay, I won’t delete your task history.",
                    details={"confirmed": False},
                )

        if _AMBIGUOUS_CLEAR_HISTORY_PATTERN.match(value):
            self._set_pending_confirmation(
                owner_key=actor.user_key,
                pending_action="delete_completed_history",
            )
            return TaskCommandResult(
                handled=True,
                response="Do you mean your completed task history?",
                details={
                    "confirmation_required": True,
                    "pending_action": "delete_completed_history",
                },
            )

        if _SHOW_TASKS_PATTERN.match(value):
            tasks = await self.list_tasks(
                owner_key=actor.user_key,
                statuses={"pending", "executing"},
                limit=10,
            )
            if not tasks:
                return TaskCommandResult(
                    handled=True,
                    response="You have no pending scheduled actions.",
                    details={"tasks": []},
                )
            descriptions = [
                f"Task {task['task_id']}: {task['action_summary']} {self._task_due_phrase(task)}"
                for task in tasks[:5]
            ]
            response = "You have " + str(len(tasks)) + " pending scheduled action"
            response += "s. " if len(tasks) != 1 else ". "
            response += "; ".join(descriptions) + "."
            return TaskCommandResult(
                handled=True,
                response=response,
                details={"tasks": tasks},
            )

        if _SHOW_HISTORY_PATTERN.match(value):
            history = await self.list_tasks(
                owner_key=actor.user_key,
                statuses=set(self.FINAL_STATUSES),
                limit=10,
                newest_first=True,
            )
            if not history:
                return TaskCommandResult(
                    handled=True,
                    response="You have no completed, failed or cancelled task history.",
                    details={"tasks": []},
                )
            descriptions = [self._describe_task_status(task).rstrip(".") for task in history[:5]]
            return TaskCommandResult(
                handled=True,
                response="Your latest task history: " + "; ".join(descriptions) + ".",
                details={"tasks": history},
            )

        match = _TASK_STATUS_PATTERN.match(value)
        if match:
            task_id = int(match.group("task_id"))
            task = await self.get_owned_task(task_id, owner_key=actor.user_key)
            if task is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find task {task_id} in your task history.",
                )
            return TaskCommandResult(
                handled=True,
                response=self._describe_task_status(task),
                details={"task": task},
            )

        match = _REPEAT_TASK_PATTERN.match(value)
        if match:
            task_id = int(match.group("task_id"))
            try:
                repeated = await self.repeat_task(task_id, actor=actor)
            except ValueError as exc:
                return TaskCommandResult(handled=True, success=False, response=str(exc))
            if repeated is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find task {task_id} in your task history.",
                )
            return TaskCommandResult(
                handled=True,
                response=(
                    f"Okay. I’ll repeat task {task_id}: {repeated['action_summary']} "
                    f"{self._task_due_phrase(repeated)}. Task {repeated['task_id']}."
                ),
                details={"task": repeated, "repeated_task_id": task_id},
            )

        match = _CLEAR_HISTORY_PATTERN.match(value)
        if match:
            self._clear_pending_confirmation(actor.user_key)
            completed_only = bool(match.group("completed"))
            count = await self.delete_history(
                owner_key=actor.user_key,
                actor=actor.user_key,
                completed_only=completed_only,
            )
            if count == 0:
                response = (
                    "You have no completed task history to delete."
                    if completed_only
                    else "You have no finished task history to delete."
                )
            else:
                scope = "completed " if completed_only else ""
                response = f"Deleted {count} {scope}task history record{'s' if count != 1 else ''}."
            return TaskCommandResult(
                handled=True,
                response=response,
                details={"deleted_count": count, "completed_only": completed_only},
            )

        match = _CANCEL_TASK_ID_PATTERN.match(value)
        if match:
            task_id = int(match.group("task_id"))
            updated = await self.cancel_task(
                task_id,
                owner_key=actor.user_key,
                actor=actor.user_key,
            )
            if not updated:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=(
                        f"I couldn’t cancel task {task_id}. It may not exist, may belong "
                        "to someone else, or may have already run."
                    ),
                )
            task = await self.get_task(task_id)
            return TaskCommandResult(
                handled=True,
                response=f"Cancelled task {task_id}: {task['action_summary']}.",
                details={"task": task},
            )

        if _CANCEL_LAST_PATTERN.match(value):
            task = await self.cancel_last(
                owner_key=actor.user_key,
                actor=actor.user_key,
            )
            if task is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="You have no pending scheduled action to cancel.",
                )
            return TaskCommandResult(
                handled=True,
                response=f"Cancelled task {task['task_id']}: {task['action_summary']}.",
                details={"task": task},
            )

        if _CANCEL_ALL_PATTERN.match(value):
            count = await self.cancel_all(
                owner_key=actor.user_key,
                actor=actor.user_key,
            )
            if count == 0:
                response = "You have no pending scheduled actions to cancel."
            else:
                response = f"Cancelled {count} scheduled action{'s' if count != 1 else ''}."
            return TaskCommandResult(
                handled=True,
                response=response,
                details={"cancelled_count": count},
            )

        now = self._utc_now()
        parsed = self._parse_due(value, now)
        if parsed is None:
            return TaskCommandResult(handled=False)
        due_at, action_text, timing_text = parsed
        if not self.enabled:
            return TaskCommandResult(
                handled=True,
                success=False,
                response="Scheduled actions are currently disabled.",
            )

        plan = await self._resolve_action(action_text)
        if isinstance(plan, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=plan,
            )
        try:
            task = await self.create_task(
                actor=actor,
                source_text=text,
                plan=plan,
                due_at=due_at,
            )
        except ValueError as exc:
            return TaskCommandResult(
                handled=True,
                success=False,
                response=str(exc),
            )
        return TaskCommandResult(
            handled=True,
            response=f"Okay. I’ll {plan.summary} {timing_text}. Task {task['task_id']}.",
            details={"task": task},
        )
