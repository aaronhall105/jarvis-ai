from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.task_engine import ActionPlan, TaskCommandResult

logger = logging.getLogger("jarvis-core.schedules")


class ActorProtocol(Protocol):
    user_key: str
    display_name: str


class ActionEngineProtocol(Protocol):
    async def _resolve_action(
        self,
        text: str,
        actor_key: str | None = None,
    ) -> ActionPlan | str: ...

    async def _execute_action(
        self,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


class RegistryProtocol(Protocol):
    async def areas(self) -> list[dict[str, Any]]: ...


class ToolProtocol(Protocol):
    registry: RegistryProtocol

    async def controllable_devices(self) -> list[dict[str, Any]]: ...

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RecurrenceSpec:
    recurrence_type: str
    description: str
    weekdays: tuple[int, ...] = ()
    local_hour: int | None = None
    local_minute: int | None = None
    interval_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedRecurrence:
    spec: RecurrenceSpec
    action_text: str


_NUMBER_WORDS = {
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

_WEEKDAY_NAMES = {
    "monday": 0,
    "mondays": 0,
    "tuesday": 1,
    "tuesdays": 1,
    "wednesday": 2,
    "wednesdays": 2,
    "thursday": 3,
    "thursdays": 3,
    "friday": 4,
    "fridays": 4,
    "saturday": 5,
    "saturdays": 5,
    "sunday": 6,
    "sundays": 6,
}

_CLOCK_FRAGMENT = (
    r"(?P<hour>\d{1,2})(?:(?::|\.)(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>am|pm)?"
)

_INTERVAL_PATTERN = re.compile(
    r"\bevery\s+(?P<amount>\d{1,4}|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|"
    r"forty-five|sixty)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?)\b",
    re.I,
)

_DAILY_PATTERN = re.compile(
    rf"\b(?:every\s+(?:day|night|morning|evening)|daily|each\s+day)\s+at\s+{_CLOCK_FRAGMENT}\b",
    re.I,
)

_WEEKDAY_PATTERN = re.compile(
    rf"\b(?:every\s+weekday|weekdays|monday\s+to\s+friday)\s+at\s+{_CLOCK_FRAGMENT}\b",
    re.I,
)

_WEEKEND_PATTERN = re.compile(
    rf"\b(?:every\s+weekend|weekends)\s+at\s+{_CLOCK_FRAGMENT}\b",
    re.I,
)

_NAMED_DAYS_PATTERN = re.compile(
    rf"\b(?:every|each)(?:\s+week\s+on)?\s+"
    rf"(?P<days>(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
    rf"(?:\s*(?:,|and)\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?)*)"
    rf"\s+at\s+{_CLOCK_FRAGMENT}\b",
    re.I,
)

_SHOW_SCHEDULES_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me|what(?:'s| is| are)|which)\s+(?:me\s+)?"
    r"(?:my\s+)?(?:recurring\s+)?schedules?\s*[.!?]*\s*$|"
    r"^\s*what\s+(?:recurring\s+)?schedules?\s+do\s+i\s+have\s*[.!?]*\s*$",
    re.I,
)

_NEXT_SCHEDULE_PATTERN = re.compile(
    r"^\s*(?:when\s+(?:will|does)\s+my\s+next\s+schedule\s+(?:run|happen)|"
    r"what(?:'s| is)\s+my\s+next\s+schedule|next\s+schedule)\s*[.!?]*\s*$",
    re.I,
)

_SCHEDULE_STATUS_PATTERN = re.compile(
    r"^\s*(?:show|check|tell me about|what(?:'s| is) the status of|when\s+(?:will|does))\s+"
    r"(?:recurring\s+)?schedule\s*#?(?P<schedule_id>\d+)"
    r"(?:\s+(?:run|happen))?\s*[.!?]*\s*$",
    re.I,
)

_SCHEDULE_HISTORY_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me)\s+(?:the\s+)?(?:run\s+)?history\s+(?:for\s+)?"
    r"(?:recurring\s+)?schedule\s*#?(?P<schedule_id>\d+)\s*[.!?]*\s*$|"
    r"^\s*(?:show|list)\s+(?:recurring\s+)?schedule\s*#?(?P<schedule_id_alt>\d+)\s+"
    r"(?:run\s+)?history\s*[.!?]*\s*$",
    re.I,
)

_PAUSE_SCHEDULE_PATTERN = re.compile(
    r"^\s*pause\s+(?:my\s+)?(?:recurring\s+)?schedule\s*#?(?P<schedule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_RESUME_SCHEDULE_PATTERN = re.compile(
    r"^\s*(?:resume|restart|unpause)\s+(?:my\s+)?(?:recurring\s+)?schedule\s*#?"
    r"(?P<schedule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_CANCEL_SCHEDULE_PATTERN = re.compile(
    r"^\s*(?:cancel|delete|remove)\s+(?:my\s+)?(?:recurring\s+)?schedule\s*#?"
    r"(?P<schedule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_CHANGE_TIME_PATTERN = re.compile(
    rf"^\s*(?:change|move|set|update)\s+(?:my\s+)?(?:recurring\s+)?schedule\s*#?"
    rf"(?P<schedule_id>\d+)\s+(?:time\s+)?(?:to|for)\s+{_CLOCK_FRAGMENT}\s*[.!?]*\s*$",
    re.I,
)

_EXPLICIT_RECURRENCE_PATTERN = re.compile(
    r"\b(?:every\s+(?:day|night|morning|evening|weekday|weekend|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|\d+|one|two|three|four|five|six|seven|eight|nine|ten)|"
    r"daily\b|weekdays\b|weekends\b|monday\s+to\s+friday\b)",
    re.I,
)


class RecurringScheduleEngine:
    """Restart-safe recurring Home Assistant scheduler for Jarvis v16.1.0."""

    ACTIVE_STATUSES = {"active"}
    VISIBLE_STATUSES = {"active", "paused", "cancelled"}

    def __init__(
        self,
        *,
        tools: ToolProtocol,
        action_engine: ActionEngineProtocol,
        database_path: str,
        enabled: bool = True,
        timezone_name: str = "Europe/London",
        poll_seconds: int = 1,
        misfire_grace_seconds: int = 300,
        notify_completion: bool = True,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.tools = tools
        self.action_engine = action_engine
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.poll_seconds = max(1, min(int(poll_seconds), 60))
        self.misfire_grace_seconds = max(0, min(int(misfire_grace_seconds), 86400))
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
        self._recover_interrupted_runs()

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
        value = re.sub(r"[^a-z0-9\s'.:-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_action_text(value: str) -> str:
        value = re.sub(r"\b(?:please|could you|can you)\b", " ", value, flags=re.I)
        value = re.sub(r"\s+", " ", value)
        return value.strip(" ,.!?")

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
                CREATE TABLE IF NOT EXISTS recurring_schedules (
                    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    recurrence_type TEXT NOT NULL,
                    recurrence_description TEXT NOT NULL,
                    weekdays_json TEXT NOT NULL DEFAULT '[]',
                    local_time TEXT,
                    interval_seconds INTEGER,
                    anchor_at TEXT,
                    timezone_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_recurring_schedule_due
                ON recurring_schedules(status, next_run_at, schedule_id);

                CREATE INDEX IF NOT EXISTS idx_recurring_schedule_owner
                ON recurring_schedules(owner_key, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS schedule_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_for),
                    FOREIGN KEY(schedule_id) REFERENCES recurring_schedules(schedule_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule
                ON schedule_runs(schedule_id, scheduled_for DESC, run_id DESC);

                CREATE TABLE IF NOT EXISTS schedule_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(schedule_id) REFERENCES recurring_schedules(schedule_id)
                        ON DELETE CASCADE
                );
                """
            )

    def _recover_interrupted_runs(self) -> None:
        now_text = self._iso(self._utc_now())
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, schedule_id, scheduled_for
                FROM schedule_runs
                WHERE status = 'executing'
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE schedule_runs
                    SET status='failed', finished_at=?, error=?
                    WHERE run_id=?
                    """,
                    (now_text, "Interrupted by Jarvis restart.", int(row["run_id"])),
                )
                connection.execute(
                    """
                    UPDATE recurring_schedules
                    SET failure_count=failure_count+1, last_error=?, updated_at=?
                    WHERE schedule_id=?
                    """,
                    (
                        "Interrupted by Jarvis restart.",
                        now_text,
                        int(row["schedule_id"]),
                    ),
                )

    def _audit_sync(
        self,
        *,
        schedule_id: int | None,
        actor: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO schedule_audit(
                    schedule_id, created_at, actor, action, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    self._iso(self._utc_now()),
                    actor,
                    action,
                    json.dumps(details or {}, ensure_ascii=False, default=str),
                ),
            )

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source_key, target_key, fallback in (
            ("action_payload_json", "action_payload", {}),
            ("weekdays_json", "weekdays", []),
            ("last_result_json", "last_result", {}),
        ):
            raw = item.pop(source_key, None)
            try:
                item[target_key] = json.loads(raw) if raw is not None else fallback
            except (TypeError, ValueError):
                item[target_key] = fallback
        return item

    @staticmethod
    def _run_row_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        raw = item.pop("result_json", None)
        try:
            item["result"] = json.loads(raw) if raw is not None else {}
        except (TypeError, ValueError):
            item["result"] = {}
        return item

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._stop_event = asyncio.Event()
        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(),
            name="jarvis-recurring-schedules",
        )
        logger.info("Recurring Schedule Engine started")

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
        logger.info("Recurring Schedule Engine stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive loop guard
                self._last_error = str(exc)
                logger.exception("Recurring schedule cycle failed")
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
                "SELECT status, COUNT(*) AS count FROM recurring_schedules GROUP BY status"
            ).fetchall()
            run_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM schedule_runs GROUP BY status"
            ).fetchall()
        return {
            "version": "16.1.0",
            "enabled": self.enabled,
            "running": self._running,
            "timezone": self.timezone_name,
            "poll_seconds": self.poll_seconds,
            "misfire_grace_seconds": self.misfire_grace_seconds,
            "notify_completion": self.notify_completion,
            "last_cycle_at": self._last_cycle_at,
            "last_error": self._last_error,
            "schedule_counts": {
                str(row["status"]): int(row["count"]) for row in rows
            },
            "run_counts": {
                str(row["status"]): int(row["count"]) for row in run_rows
            },
        }

    async def list_schedules(
        self,
        *,
        owner_key: str | None = None,
        statuses: set[str] | None = None,
        limit: int = 50,
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
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM recurring_schedules
                {where}
                ORDER BY
                    CASE WHEN next_run_at IS NULL THEN 1 ELSE 0 END,
                    next_run_at ASC,
                    schedule_id ASC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    async def get_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM recurring_schedules WHERE schedule_id = ?",
                (int(schedule_id),),
            ).fetchone()
        return self._row_dict(row) if row else None

    async def get_owned_schedule(
        self,
        schedule_id: int,
        *,
        owner_key: str,
    ) -> dict[str, Any] | None:
        item = await self.get_schedule(schedule_id)
        if item is None or str(item.get("owner_key")) != owner_key:
            return None
        return item

    async def list_runs(
        self,
        schedule_id: int,
        *,
        owner_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["r.schedule_id = ?"]
        values: list[Any] = [int(schedule_id)]
        if owner_key:
            clauses.append("s.owner_key = ?")
            values.append(owner_key)
        values.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT r.* FROM schedule_runs r
                JOIN recurring_schedules s ON s.schedule_id = r.schedule_id
                WHERE {' AND '.join(clauses)}
                ORDER BY r.scheduled_for DESC, r.run_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._run_row_dict(row) for row in rows]

    @staticmethod
    def _amount(value: str) -> int:
        value = value.casefold().strip()
        if value.isdigit():
            return int(value)
        return _NUMBER_WORDS.get(value, -1)

    @staticmethod
    def _parse_clock_parts(
        hour_text: str,
        minute_text: str | None,
        ampm: str | None,
    ) -> tuple[int, int] | None:
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
        return hour, minute

    @staticmethod
    def _spoken_time(hour: int, minute: int) -> str:
        marker = "am" if hour < 12 else "pm"
        hour12 = hour % 12 or 12
        if minute == 0:
            return f"{hour12} {marker}"
        return f"{hour12}:{minute:02d} {marker}"

    @classmethod
    def _weekday_description(cls, weekdays: Sequence[int]) -> str:
        names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        values = tuple(sorted(set(int(day) for day in weekdays)))
        if values == (0, 1, 2, 3, 4):
            return "every weekday"
        if values == (5, 6):
            return "every weekend"
        if values == tuple(range(7)):
            return "every day"
        selected = [names[day] for day in values]
        if len(selected) == 1:
            return f"every {selected[0]}"
        if len(selected) == 2:
            return f"every {selected[0]} and {selected[1]}"
        return "every " + ", ".join(selected[:-1]) + f", and {selected[-1]}"

    def _parse_recurrence(self, text: str) -> ParsedRecurrence | str | None:
        value = self._clean_action_text(text)

        match = _INTERVAL_PATTERN.search(value)
        if match:
            amount = self._amount(match.group("amount"))
            unit = match.group("unit").casefold()
            if amount <= 0:
                return "Please give a valid recurring interval."
            if unit.startswith(("min", "mins")):
                seconds = amount * 60
                unit_name = "minute" if amount == 1 else "minutes"
            elif unit.startswith(("hr", "hour")):
                seconds = amount * 3600
                unit_name = "hour" if amount == 1 else "hours"
            else:
                seconds = amount * 86400
                unit_name = "day" if amount == 1 else "days"
            if seconds < 60:
                return "Recurring schedules must be at least one minute apart."
            if seconds > 365 * 86400:
                return "Recurring intervals cannot be longer than one year."
            action_text = self._clean_action_text(
                value[: match.start()] + " " + value[match.end() :]
            )
            return ParsedRecurrence(
                RecurrenceSpec(
                    recurrence_type="interval",
                    interval_seconds=seconds,
                    description=f"every {amount} {unit_name}",
                ),
                action_text,
            )

        for pattern, weekdays in (
            (_WEEKDAY_PATTERN, (0, 1, 2, 3, 4)),
            (_WEEKEND_PATTERN, (5, 6)),
            (_DAILY_PATTERN, tuple(range(7))),
        ):
            match = pattern.search(value)
            if not match:
                continue
            clock = self._parse_clock_parts(
                match.group("hour"),
                match.group("minute"),
                match.group("ampm"),
            )
            if clock is None:
                return "Please give a valid time for that schedule."
            hour, minute = clock
            action_text = self._clean_action_text(
                value[: match.start()] + " " + value[match.end() :]
            )
            description = (
                f"{self._weekday_description(weekdays)} at "
                f"{self._spoken_time(hour, minute)}"
            )
            return ParsedRecurrence(
                RecurrenceSpec(
                    recurrence_type="weekly",
                    weekdays=weekdays,
                    local_hour=hour,
                    local_minute=minute,
                    description=description,
                ),
                action_text,
            )

        match = _NAMED_DAYS_PATTERN.search(value)
        if match:
            clock = self._parse_clock_parts(
                match.group("hour"),
                match.group("minute"),
                match.group("ampm"),
            )
            if clock is None:
                return "Please give a valid time for that schedule."
            day_tokens = re.findall(
                r"monday|tuesday|wednesday|thursday|friday|saturday|sunday",
                match.group("days").casefold(),
            )
            weekdays = tuple(sorted({_WEEKDAY_NAMES[token] for token in day_tokens}))
            if not weekdays:
                return "Please name at least one day for that schedule."
            hour, minute = clock
            action_text = self._clean_action_text(
                value[: match.start()] + " " + value[match.end() :]
            )
            description = (
                f"{self._weekday_description(weekdays)} at "
                f"{self._spoken_time(hour, minute)}"
            )
            return ParsedRecurrence(
                RecurrenceSpec(
                    recurrence_type="weekly",
                    weekdays=weekdays,
                    local_hour=hour,
                    local_minute=minute,
                    description=description,
                ),
                action_text,
            )

        if _EXPLICIT_RECURRENCE_PATTERN.search(value):
            return "Please include an exact time or interval for that recurring schedule."
        return None

    def _local_wall_time(self, day: date, hour: int, minute: int) -> datetime:
        """Return a valid zoned wall time, adjusting spring-forward gaps safely."""
        naive = datetime.combine(day, time(hour=hour, minute=minute))
        candidate = naive.replace(tzinfo=self._timezone, fold=0)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(self._timezone)
        if roundtrip.replace(tzinfo=None) != naive:
            # The requested local time does not exist during a DST spring-forward.
            # Use the first valid round-tripped instant instead of silently skipping a day.
            candidate = roundtrip
        return candidate

    def _next_run_after(
        self,
        spec: RecurrenceSpec,
        after_utc: datetime,
        *,
        anchor_utc: datetime | None = None,
    ) -> datetime:
        after = after_utc.astimezone(timezone.utc)
        if spec.recurrence_type == "interval":
            interval = int(spec.interval_seconds or 0)
            if interval <= 0:
                raise ValueError("Invalid recurring interval.")
            anchor = (anchor_utc or after).astimezone(timezone.utc)
            if anchor > after:
                return anchor
            elapsed = max(0.0, (after - anchor).total_seconds())
            steps = math.floor(elapsed / interval) + 1
            return anchor + timedelta(seconds=steps * interval)

        if spec.local_hour is None or spec.local_minute is None:
            raise ValueError("Recurring clock time is missing.")
        weekdays = set(spec.weekdays or tuple(range(7)))
        local_after = after.astimezone(self._timezone)
        for offset in range(0, 15):
            candidate_day = local_after.date() + timedelta(days=offset)
            if candidate_day.weekday() not in weekdays:
                continue
            candidate_local = self._local_wall_time(
                candidate_day,
                spec.local_hour,
                spec.local_minute,
            )
            candidate_utc = candidate_local.astimezone(timezone.utc)
            if candidate_utc > after + timedelta(seconds=1):
                return candidate_utc
        raise ValueError("Could not calculate the next recurring run.")

    @staticmethod
    def _spec_from_schedule(schedule: dict[str, Any]) -> RecurrenceSpec:
        local_time = str(schedule.get("local_time") or "")
        hour: int | None = None
        minute: int | None = None
        if ":" in local_time:
            hour_text, minute_text = local_time.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        return RecurrenceSpec(
            recurrence_type=str(schedule["recurrence_type"]),
            description=str(schedule["recurrence_description"]),
            weekdays=tuple(int(day) for day in schedule.get("weekdays") or []),
            local_hour=hour,
            local_minute=minute,
            interval_seconds=(
                int(schedule["interval_seconds"])
                if schedule.get("interval_seconds") is not None
                else None
            ),
        )

    async def _validate_action_available(
        self,
        action_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str | None]:
        if action_type == "area_lights":
            area_id = str(payload.get("area_id") or "")
            areas = await self.tools.registry.areas()
            valid = {
                str(area.get("area_id") or area.get("id"))
                for area in areas
                if area.get("area_id") or area.get("id")
            }
            if area_id not in valid:
                return False, "The scheduled Home Assistant room no longer exists."
            return True, None

        if action_type == "device_control":
            entity_id = str(payload.get("entity_id") or "")
            devices = {
                str(device.get("entity_id")): device
                for device in await self.tools.controllable_devices()
                if device.get("entity_id")
            }
            device = devices.get(entity_id)
            if device is None:
                return False, "The scheduled Home Assistant device is no longer controllable."
            if device.get("available") is False:
                return False, "The scheduled Home Assistant device is unavailable."
            return True, None

        if action_type == "media_shortcut":
            shortcut = str(payload.get("shortcut") or "")
            shortcuts = getattr(self.tools, "MEDIA_SHORTCUTS", None)
            if isinstance(shortcuts, dict) and shortcut not in shortcuts:
                return False, "The scheduled media shortcut is no longer configured."
            return True, None

        if action_type == "home_routine":
            entity_id = str(payload.get("entity_id") or "")
            try:
                routines = await self.tools.runnable_routines(limit=200)
            except (AttributeError, NotImplementedError):
                routines = []
            if entity_id not in {str(item.get("entity_id")) for item in routines}:
                return False, "The scheduled Home Assistant routine is no longer available."
            return True, None

        if action_type in {"delay", "notify_owner", "announcement"}:
            return True, None

        if action_type == "sequence":
            steps = payload.get("steps")
            if not isinstance(steps, list) or not steps:
                return False, "The recurring multi-step action contains no steps."
            for step in steps:
                if not isinstance(step, dict):
                    return False, "The recurring multi-step action is invalid."
                available, error = await self._validate_action_available(
                    str(step.get("action_type") or ""),
                    dict(step.get("payload") or {}),
                )
                if not available:
                    return False, error
            return True, None

        return False, f"Unsupported recurring action type: {action_type}"

    async def create_schedule(
        self,
        *,
        actor: ActorProtocol,
        source_text: str,
        plan: ActionPlan,
        spec: RecurrenceSpec,
    ) -> tuple[dict[str, Any], bool]:
        now = self._utc_now()
        if spec.recurrence_type == "interval":
            anchor = now + timedelta(seconds=int(spec.interval_seconds or 0))
            next_run = anchor
        else:
            anchor = None
            next_run = self._next_run_after(spec, now)

        payload_json = json.dumps(
            plan.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        weekdays_json = json.dumps(list(spec.weekdays), separators=(",", ":"))
        local_time = (
            f"{spec.local_hour:02d}:{spec.local_minute:02d}"
            if spec.local_hour is not None and spec.local_minute is not None
            else None
        )

        with self._connection() as connection:
            duplicate = connection.execute(
                """
                SELECT * FROM recurring_schedules
                WHERE owner_key=? AND status IN ('active','paused')
                  AND action_type=? AND action_payload_json=?
                  AND recurrence_type=? AND weekdays_json=?
                  AND COALESCE(local_time,'')=COALESCE(?, '')
                  AND COALESCE(interval_seconds,0)=COALESCE(?,0)
                ORDER BY schedule_id ASC LIMIT 1
                """,
                (
                    actor.user_key,
                    plan.action_type,
                    payload_json,
                    spec.recurrence_type,
                    weekdays_json,
                    local_time,
                    spec.interval_seconds,
                ),
            ).fetchone()
            if duplicate is not None:
                return self._row_dict(duplicate), True

            now_text = self._iso(now)
            cursor = connection.execute(
                """
                INSERT INTO recurring_schedules(
                    owner_key, owner_name, source_text, action_type,
                    action_payload_json, action_summary, recurrence_type,
                    recurrence_description, weekdays_json, local_time,
                    interval_seconds, anchor_at, timezone_name, status,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    actor.user_key,
                    actor.display_name,
                    source_text,
                    plan.action_type,
                    payload_json,
                    plan.summary,
                    spec.recurrence_type,
                    spec.description,
                    weekdays_json,
                    local_time,
                    spec.interval_seconds,
                    self._iso(anchor) if anchor else None,
                    self.timezone_name,
                    self._iso(next_run),
                    now_text,
                    now_text,
                ),
            )
            schedule_id = int(cursor.lastrowid)

        self._audit_sync(
            schedule_id=schedule_id,
            actor=actor.user_key,
            action="created",
            details={
                "summary": plan.summary,
                "recurrence": spec.description,
                "next_run_at": self._iso(next_run),
            },
        )
        item = await self.get_schedule(schedule_id)
        assert item is not None
        return item, False

    async def _set_status(
        self,
        schedule_id: int,
        *,
        owner_key: str | None,
        actor: str,
        status: str,
    ) -> bool:
        if status not in {"active", "paused", "cancelled"}:
            raise ValueError("Unsupported schedule status.")
        schedule = await self.get_schedule(schedule_id)
        if schedule is None:
            return False
        if owner_key and str(schedule.get("owner_key")) != owner_key:
            return False
        current = str(schedule.get("status"))
        if current == "cancelled":
            return False
        if status == "active":
            spec = self._spec_from_schedule(schedule)
            anchor = (
                self._parse_iso(str(schedule["anchor_at"]))
                if schedule.get("anchor_at")
                else None
            )
            next_run = self._next_run_after(spec, self._utc_now(), anchor_utc=anchor)
            next_run_text: str | None = self._iso(next_run)
        elif status == "paused":
            next_run_text = str(schedule.get("next_run_at") or "") or None
        else:
            next_run_text = None

        now_text = self._iso(self._utc_now())
        with self._connection() as connection:
            query = (
                "UPDATE recurring_schedules SET status=?, next_run_at=?, updated_at=? "
                "WHERE schedule_id=? AND status!='cancelled'"
            )
            values: list[Any] = [status, next_run_text, now_text, int(schedule_id)]
            if owner_key:
                query += " AND owner_key=?"
                values.append(owner_key)
            cursor = connection.execute(query, values)
            updated = cursor.rowcount > 0
        if updated:
            self._audit_sync(
                schedule_id=int(schedule_id),
                actor=actor,
                action=status,
                details={"previous_status": current, "next_run_at": next_run_text},
            )
        return updated

    async def pause_schedule(
        self,
        schedule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        return await self._set_status(
            schedule_id,
            owner_key=owner_key,
            actor=actor,
            status="paused",
        )

    async def resume_schedule(
        self,
        schedule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        return await self._set_status(
            schedule_id,
            owner_key=owner_key,
            actor=actor,
            status="active",
        )

    async def cancel_schedule(
        self,
        schedule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        return await self._set_status(
            schedule_id,
            owner_key=owner_key,
            actor=actor,
            status="cancelled",
        )

    async def change_schedule_time(
        self,
        schedule_id: int,
        *,
        owner_key: str,
        actor: str,
        hour: int,
        minute: int,
    ) -> dict[str, Any] | None:
        schedule = await self.get_owned_schedule(schedule_id, owner_key=owner_key)
        if schedule is None or str(schedule.get("status")) == "cancelled":
            return None
        if str(schedule.get("recurrence_type")) == "interval":
            raise ValueError(
                "That is an interval schedule. Cancel it and create a new interval instead."
            )
        spec = self._spec_from_schedule(schedule)
        updated_spec = RecurrenceSpec(
            recurrence_type=spec.recurrence_type,
            weekdays=spec.weekdays,
            local_hour=hour,
            local_minute=minute,
            description=(
                f"{self._weekday_description(spec.weekdays)} at "
                f"{self._spoken_time(hour, minute)}"
            ),
        )
        next_run = self._next_run_after(updated_spec, self._utc_now())
        now_text = self._iso(self._utc_now())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE recurring_schedules
                SET local_time=?, recurrence_description=?, next_run_at=?, updated_at=?
                WHERE schedule_id=? AND owner_key=? AND status!='cancelled'
                """,
                (
                    f"{hour:02d}:{minute:02d}",
                    updated_spec.description,
                    self._iso(next_run),
                    now_text,
                    int(schedule_id),
                    owner_key,
                ),
            )
        self._audit_sync(
            schedule_id=int(schedule_id),
            actor=actor,
            action="time_changed",
            details={
                "local_time": f"{hour:02d}:{minute:02d}",
                "next_run_at": self._iso(next_run),
            },
        )
        return await self.get_schedule(schedule_id)

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
                    SELECT schedule_id FROM recurring_schedules
                    WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                    ORDER BY next_run_at ASC, schedule_id ASC
                    LIMIT 20
                    """,
                    (now_text,),
                ).fetchall()
            processed = 0
            for row in rows:
                schedule = await self.get_schedule(int(row["schedule_id"]))
                if schedule is None or str(schedule.get("status")) != "active":
                    continue
                await self._process_due_schedule(schedule, now)
                processed += 1
            return processed

    async def _process_due_schedule(
        self,
        schedule: dict[str, Any],
        now: datetime,
    ) -> None:
        schedule_id = int(schedule["schedule_id"])
        scheduled_for = self._parse_iso(str(schedule["next_run_at"]))
        scheduled_text = self._iso(scheduled_for)
        now_text = self._iso(now)
        lag_seconds = max(0, round((now - scheduled_for).total_seconds()))

        with self._connection() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO schedule_runs(
                        schedule_id, scheduled_for, started_at, status, created_at
                    ) VALUES (?, ?, ?, 'executing', ?)
                    """,
                    (schedule_id, scheduled_text, now_text, now_text),
                )
            except sqlite3.IntegrityError:
                await self._advance_schedule(schedule, after_utc=now)
                return
            run_id = int(cursor.lastrowid)

        if lag_seconds > self.misfire_grace_seconds:
            error = (
                f"Missed by {lag_seconds} seconds, beyond the "
                f"{self.misfire_grace_seconds}-second catch-up window."
            )
            with self._connection() as connection:
                connection.execute(
                    """
                    UPDATE schedule_runs
                    SET status='skipped', finished_at=?, error=?
                    WHERE run_id=?
                    """,
                    (now_text, error, run_id),
                )
                connection.execute(
                    """
                    UPDATE recurring_schedules
                    SET last_run_at=?, last_error=?, updated_at=?
                    WHERE schedule_id=?
                    """,
                    (scheduled_text, error, now_text, schedule_id),
                )
            self._audit_sync(
                schedule_id=schedule_id,
                actor="schedule-engine",
                action="missed_run_skipped",
                details={"scheduled_for": scheduled_text, "lag_seconds": lag_seconds},
            )
            await self._advance_schedule(schedule, after_utc=now)
            return

        available, availability_error = await self._validate_action_available(
            str(schedule["action_type"]),
            dict(schedule.get("action_payload") or {}),
        )
        if not available:
            result = {"success": False, "verified": False, "message": availability_error}
            status = "failed"
            error = availability_error or "The scheduled action is unavailable."
        else:
            try:
                result = await self.action_engine._execute_action(
                    str(schedule["action_type"]),
                    dict(schedule.get("action_payload") or {}),
                )
                success = result.get("success") is True
                if result.get("verified") is False:
                    success = False
                status = "completed" if success else "failed"
                error = None if success else str(
                    result.get("response_message")
                    or result.get("message")
                    or result.get("error")
                    or "The recurring action could not be verified."
                )
            except Exception as exc:  # pragma: no cover - defensive execution guard
                logger.exception("Recurring schedule %s failed", schedule_id)
                result = {"success": False, "error": str(exc)}
                status = "failed"
                error = str(exc)

        finished = self._utc_now()
        finished_text = self._iso(finished)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE schedule_runs
                SET status=?, finished_at=?, result_json=?, error=?
                WHERE run_id=?
                """,
                (
                    status,
                    finished_text,
                    json.dumps(result, ensure_ascii=False, default=str),
                    error,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE recurring_schedules
                SET last_run_at=?, last_result_json=?, last_error=?,
                    run_count=run_count+1,
                    failure_count=failure_count + CASE WHEN ?='failed' THEN 1 ELSE 0 END,
                    updated_at=?
                WHERE schedule_id=?
                """,
                (
                    scheduled_text,
                    json.dumps(result, ensure_ascii=False, default=str),
                    error,
                    status,
                    finished_text,
                    schedule_id,
                ),
            )

        self._audit_sync(
            schedule_id=schedule_id,
            actor="schedule-engine",
            action=status,
            details={
                "run_id": run_id,
                "scheduled_for": scheduled_text,
                "result": result,
                "error": error,
            },
        )
        await self._notify_result(
            schedule=schedule,
            status=status,
            result=result,
            error=error,
            scheduled_for=scheduled_for,
        )
        await self._advance_schedule(schedule, after_utc=finished)

    async def _advance_schedule(
        self,
        schedule: dict[str, Any],
        *,
        after_utc: datetime,
    ) -> None:
        spec = self._spec_from_schedule(schedule)
        anchor = (
            self._parse_iso(str(schedule["anchor_at"]))
            if schedule.get("anchor_at")
            else None
        )
        next_run = self._next_run_after(spec, after_utc, anchor_utc=anchor)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE recurring_schedules
                SET next_run_at=?, updated_at=?
                WHERE schedule_id=? AND status='active'
                """,
                (
                    self._iso(next_run),
                    self._iso(self._utc_now()),
                    int(schedule["schedule_id"]),
                ),
            )

    async def _notify_result(
        self,
        *,
        schedule: dict[str, Any],
        status: str,
        result: dict[str, Any],
        error: str | None,
        scheduled_for: datetime,
    ) -> None:
        if not self.notify_completion:
            return
        recipient = str(schedule.get("owner_key") or "").strip().casefold()
        if recipient not in {"aaron", "amber"}:
            return
        schedule_id = int(schedule["schedule_id"])
        summary = str(schedule.get("action_summary") or "recurring action")
        local_time = scheduled_for.astimezone(self._timezone).strftime(
            "%A %-d %B at %-I:%M %p"
        ).lower()
        if status == "completed":
            message = f"Schedule {schedule_id} ran: {summary} ({local_time})."
        else:
            message = f"Schedule {schedule_id} failed: {summary} ({local_time})."
        result_message = str(
            result.get("response_message")
            or result.get("message")
            or error
            or ""
        ).strip()
        if result_message:
            message += f" {result_message}"
        try:
            notification = await self.tools.send_mobile_notification(
                recipient=recipient,
                title="Jarvis recurring schedule",
                message=message,
            )
            self._audit_sync(
                schedule_id=schedule_id,
                actor="schedule-engine",
                action="run_notification",
                details={"recipient": recipient, "result": notification},
            )
        except Exception as exc:  # notification failure must not alter run status
            logger.exception(
                "Could not notify %s about recurring schedule %s",
                recipient,
                schedule_id,
            )
            self._audit_sync(
                schedule_id=schedule_id,
                actor="schedule-engine",
                action="run_notification_failed",
                details={"recipient": recipient, "error": str(exc)},
            )

    def _next_run_phrase(self, schedule: dict[str, Any]) -> str:
        next_text = schedule.get("next_run_at")
        if not next_text:
            return "with no next run scheduled"
        next_run = self._parse_iso(str(next_text)).astimezone(self._timezone)
        local_now = self._utc_now().astimezone(self._timezone)
        clock = self._spoken_time(next_run.hour, next_run.minute)
        if next_run.date() == local_now.date():
            return f"today at {clock}"
        if next_run.date() == (local_now + timedelta(days=1)).date():
            return f"tomorrow at {clock}"
        return next_run.strftime("on %A %-d %B at %-I:%M %p").lower()

    def _describe_schedule(self, schedule: dict[str, Any]) -> str:
        schedule_id = int(schedule["schedule_id"])
        summary = str(schedule["action_summary"])
        recurrence = str(schedule["recurrence_description"])
        status = str(schedule.get("status") or "unknown")
        if status == "active":
            return (
                f"Schedule {schedule_id}: {summary} {recurrence}; next run "
                f"{self._next_run_phrase(schedule)}."
            )
        if status == "paused":
            return f"Schedule {schedule_id} is paused: {summary} {recurrence}."
        if status == "cancelled":
            return f"Schedule {schedule_id} was cancelled: {summary} {recurrence}."
        return f"Schedule {schedule_id} has status {status}: {summary} {recurrence}."

    async def handle_command(
        self,
        text: str,
        actor: ActorProtocol,
    ) -> TaskCommandResult:
        value = self._clean_action_text(text)

        if _SHOW_SCHEDULES_PATTERN.match(value):
            items = await self.list_schedules(
                owner_key=actor.user_key,
                statuses={"active", "paused"},
                limit=20,
            )
            if not items:
                return TaskCommandResult(
                    handled=True,
                    response="You have no active recurring schedules.",
                    intent="schedule_list",
                    details={"schedules": []},
                )
            descriptions = [self._describe_schedule(item).rstrip(".") for item in items[:5]]
            response = f"You have {len(items)} recurring schedule"
            response += "s. " if len(items) != 1 else ". "
            response += "; ".join(descriptions) + "."
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="schedule_list",
                details={"schedules": items},
            )

        if _NEXT_SCHEDULE_PATTERN.match(value):
            items = await self.list_schedules(
                owner_key=actor.user_key,
                statuses={"active"},
                limit=1,
            )
            if not items:
                return TaskCommandResult(
                    handled=True,
                    response="You have no active recurring schedule.",
                    intent="schedule_next",
                )
            item = items[0]
            return TaskCommandResult(
                handled=True,
                response=(
                    f"Your next recurring schedule is schedule {item['schedule_id']}: "
                    f"{item['action_summary']} {self._next_run_phrase(item)}."
                ),
                intent="schedule_next",
                details={"schedule": item},
            )

        match = _SCHEDULE_STATUS_PATTERN.match(value)
        if match:
            schedule_id = int(match.group("schedule_id"))
            item = await self.get_owned_schedule(schedule_id, owner_key=actor.user_key)
            if item is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find schedule {schedule_id} in your schedules.",
                    intent="schedule_status",
                )
            return TaskCommandResult(
                handled=True,
                response=self._describe_schedule(item),
                intent="schedule_status",
                details={"schedule": item},
            )

        match = _SCHEDULE_HISTORY_PATTERN.match(value)
        if match:
            schedule_id = int(
                match.group("schedule_id") or match.group("schedule_id_alt")
            )
            item = await self.get_owned_schedule(schedule_id, owner_key=actor.user_key)
            if item is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find schedule {schedule_id} in your schedules.",
                    intent="schedule_history",
                )
            runs = await self.list_runs(
                schedule_id,
                owner_key=actor.user_key,
                limit=10,
            )
            if not runs:
                response = f"Schedule {schedule_id} has not run yet."
            else:
                parts = []
                for run in runs[:5]:
                    when = self._parse_iso(str(run["scheduled_for"])).astimezone(
                        self._timezone
                    )
                    parts.append(
                        f"{run['status']} on {when.strftime('%A %-d %B at %-I:%M %p').lower()}"
                    )
                response = f"Schedule {schedule_id} run history: " + "; ".join(parts) + "."
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="schedule_history",
                details={"schedule": item, "runs": runs},
            )

        for pattern, operation, verb, action_word in (
            (_PAUSE_SCHEDULE_PATTERN, self.pause_schedule, "paused", "pause"),
            (_RESUME_SCHEDULE_PATTERN, self.resume_schedule, "resumed", "resume"),
            (_CANCEL_SCHEDULE_PATTERN, self.cancel_schedule, "cancelled", "cancel"),
        ):
            match = pattern.match(value)
            if not match:
                continue
            schedule_id = int(match.group("schedule_id"))
            updated = await operation(
                schedule_id,
                owner_key=actor.user_key,
                actor=actor.user_key,
            )
            if not updated:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=(
                        f"I couldn’t {action_word} schedule {schedule_id}. It may not "
                        "exist, may belong to someone else, or may already be cancelled."
                    ),
                    intent=f"schedule_{verb}",
                )
            item = await self.get_schedule(schedule_id)
            assert item is not None
            extra = (
                f" Its next run is {self._next_run_phrase(item)}."
                if verb == "resumed"
                else ""
            )
            return TaskCommandResult(
                handled=True,
                response=f"{verb.title()} schedule {schedule_id}: {item['action_summary']}.{extra}",
                intent=f"schedule_{verb}",
                details={"schedule": item},
            )

        match = _CHANGE_TIME_PATTERN.match(value)
        if match:
            schedule_id = int(match.group("schedule_id"))
            clock = self._parse_clock_parts(
                match.group("hour"),
                match.group("minute"),
                match.group("ampm"),
            )
            if clock is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="Please give a valid schedule time.",
                    intent="schedule_edit",
                )
            try:
                item = await self.change_schedule_time(
                    schedule_id,
                    owner_key=actor.user_key,
                    actor=actor.user_key,
                    hour=clock[0],
                    minute=clock[1],
                )
            except ValueError as exc:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=str(exc),
                    intent="schedule_edit",
                )
            if item is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find schedule {schedule_id} in your schedules.",
                    intent="schedule_edit",
                )
            return TaskCommandResult(
                handled=True,
                response=(
                    f"Changed schedule {schedule_id} to "
                    f"{self._spoken_time(clock[0], clock[1])}. Its next run is "
                    f"{self._next_run_phrase(item)}."
                ),
                intent="schedule_edit",
                details={"schedule": item},
            )

        parsed = self._parse_recurrence(value)
        if parsed is None:
            return TaskCommandResult(handled=False)
        if isinstance(parsed, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=parsed,
                intent="schedule_create",
            )
        if not self.enabled:
            return TaskCommandResult(
                handled=True,
                success=False,
                response="Recurring schedules are currently disabled.",
                intent="schedule_create",
            )
        if not parsed.action_text:
            return TaskCommandResult(
                handled=True,
                success=False,
                response="Please say what action the recurring schedule should perform.",
                intent="schedule_create",
            )

        plan = await self.action_engine._resolve_action(
            parsed.action_text,
            actor_key=actor.user_key,
        )
        if isinstance(plan, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=plan,
                intent="schedule_create",
            )

        available, availability_error = await self._validate_action_available(
            plan.action_type,
            plan.payload,
        )
        if not available:
            return TaskCommandResult(
                handled=True,
                success=False,
                response=availability_error or "That action is not currently available.",
                intent="schedule_create",
            )

        item, duplicate = await self.create_schedule(
            actor=actor,
            source_text=text,
            plan=plan,
            spec=parsed.spec,
        )
        if duplicate:
            response = (
                f"That schedule already exists as schedule {item['schedule_id']}: "
                f"{item['action_summary']} {item['recurrence_description']}."
            )
        else:
            response = (
                f"Okay. Schedule {item['schedule_id']} will {plan.summary} "
                f"{parsed.spec.description}. Its next run is "
                f"{self._next_run_phrase(item)}."
            )
        return TaskCommandResult(
            handled=True,
            response=response,
            intent="schedule_create",
            details={"schedule": item, "duplicate": duplicate},
        )
