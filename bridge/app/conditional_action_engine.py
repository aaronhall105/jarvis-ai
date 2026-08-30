from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.task_engine import ActionPlan, TaskCommandResult

logger = logging.getLogger("jarvis-core.conditions")


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


class ToolProtocol(Protocol):
    async def readable_entity_states(
        self,
        *,
        refresh: bool = True,
    ) -> list[dict[str, Any]]: ...

    async def search_entity_states(
        self,
        query: str,
        *,
        domain: str | None = None,
        area_id: str | None = None,
        state_filter: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]: ...

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    trigger_type: str
    entity_id: str
    entity_name: str
    payload: dict[str, Any]
    summary: str


@dataclass(frozen=True, slots=True)
class ParsedRule:
    trigger_text: str
    action_text: str
    one_shot: bool
    cooldown_seconds: int
    debounce_seconds: int
    window_start_minute: int | None
    window_end_minute: int | None


_SHOW_RULES_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me|what(?:'s| is| are)|which)\s+(?:me\s+)?"
    r"(?:my\s+)?(?:conditional\s+)?(?:rules?|conditions?|triggers?)\s*[.!?]*\s*$|"
    r"^\s*what\s+(?:conditional\s+)?rules?\s+do\s+i\s+have\s*[.!?]*\s*$",
    re.I,
)

_RULE_STATUS_PATTERN = re.compile(
    r"^\s*(?:show|check|tell me about|what(?:'s| is) the status of)\s+"
    r"(?:conditional\s+)?(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_RULE_HISTORY_PATTERN = re.compile(
    r"^\s*(?:show|list|tell me)\s+(?:the\s+)?(?:run\s+)?history\s+(?:for\s+)?"
    r"(?:conditional\s+)?(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s*[.!?]*\s*$|"
    r"^\s*(?:show|list)\s+(?:conditional\s+)?(?:rule|condition|trigger)\s*#?"
    r"(?P<rule_id_alt>\d+)\s+(?:run\s+)?history\s*[.!?]*\s*$",
    re.I,
)

_PAUSE_RULE_PATTERN = re.compile(
    r"^\s*pause\s+(?:my\s+)?(?:conditional\s+)?(?:rule|condition|trigger)\s*#?"
    r"(?P<rule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_RESUME_RULE_PATTERN = re.compile(
    r"^\s*(?:resume|restart|unpause)\s+(?:my\s+)?(?:conditional\s+)?"
    r"(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_CANCEL_RULE_PATTERN = re.compile(
    r"^\s*(?:cancel|delete|remove)\s+(?:my\s+)?(?:conditional\s+)?"
    r"(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s*[.!?]*\s*$",
    re.I,
)

_CHANGE_COOLDOWN_PATTERN = re.compile(
    r"^\s*(?:change|set|update)\s+(?:my\s+)?(?:conditional\s+)?"
    r"(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s+(?:the\s+)?cooldown\s+"
    r"(?:to|for)\s+(?P<amount>\d{1,5})\s*(?P<unit>seconds?|minutes?|hours?)\s*[.!?]*\s*$",
    re.I,
)

_CHANGE_WINDOW_PATTERN = re.compile(
    r"^\s*(?:change|set|update)\s+(?:my\s+)?(?:conditional\s+)?"
    r"(?:rule|condition|trigger)\s*#?(?P<rule_id>\d+)\s+(?:the\s+)?"
    r"(?:time\s+)?window\s+to\s+(?P<window>.+?)\s*[.!?]*\s*$",
    re.I,
)

_RULE_PREFIX_PATTERN = re.compile(r"^\s*(?P<prefix>when|if|next\s+time)\s+", re.I)

_TIMED_CONDITION_PATTERN = re.compile(
    r"^\s*at\s+(?P<clock>\d{1,2}(?:(?::|\.)\d{2})?\s*(?:am|pm)?)\s*,?\s*"
    r"(?P<action>.+?)\s+(?:only\s+)?if\s+(?P<condition>.+?)\s*[.!?]*\s*$",
    re.I,
)

_COOLDOWN_PATTERN = re.compile(
    r"\s+(?:with\s+)?(?:a\s+)?(?P<amount>\d{1,5})\s*"
    r"(?P<unit>seconds?|minutes?|hours?)\s+cooldown\b",
    re.I,
)

_DEBOUNCE_PATTERN = re.compile(
    r"\s+for\s+(?P<amount>\d{1,5})\s*(?P<unit>seconds?|minutes?)\s*$",
    re.I,
)

_BETWEEN_WINDOW_PATTERN = re.compile(
    r"\bbetween\s+(?P<start>\d{1,2}(?:(?::|\.)\d{2})?\s*(?:am|pm)?)\s+"
    r"and\s+(?P<end>\d{1,2}(?:(?::|\.)\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

_AFTER_WINDOW_PATTERN = re.compile(
    r"\bafter\s+(?P<start>\d{1,2}(?:(?::|\.)\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

_BEFORE_WINDOW_PATTERN = re.compile(
    r"\bbefore\s+(?P<end>\d{1,2}(?:(?::|\.)\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

_NUMERIC_PATTERN = re.compile(
    r"^(?P<entity>.+?)\s+(?:drops?|falls?|goes?|rises?|climbs?|is|becomes?)\s+"
    r"(?P<direction>below|under|above|over)\s+(?P<value>-?\d+(?:\.\d+)?)"
    r"(?:\s*(?P<unit>%|degrees?|°c|°f|c|f|[a-zA-Z/]+))?\s*$",
    re.I,
)

_PRESENCE_LEAVE_PATTERN = re.compile(
    r"^(?P<entity>.+?)\s+(?:leaves?|left)\s+(?:home|the\s+house)\s*$",
    re.I,
)

_PRESENCE_ARRIVE_PATTERN = re.compile(
    r"^(?P<entity>.+?)\s+(?:arrives?|gets|comes)\s+(?:home|back\s+home)\s*$",
    re.I,
)

_STATE_VERB_PATTERN = re.compile(
    r"^(?P<entity>.+?)\s+(?P<verb>turns?\s+on|turns?\s+off|opens?|closes?|"
    r"starts?|stops?|finishes?|completes?)\s*$",
    re.I,
)

_STATE_EQUALS_PATTERN = re.compile(
    r"^(?P<entity>.+?)\s+(?:is|becomes?|changes?\s+to)\s+(?P<state>[a-zA-Z0-9_ -]+)\s*$",
    re.I,
)

_NOTIFY_ACTION_PATTERN = re.compile(
    r"^(?:notify|alert)\s+(?P<recipient>me|aaron|amber)(?:\s+(?:that|saying)\s+(?P<message>.+))?$|"
    r"^send\s+(?P<recipient_alt>me|aaron|amber)\s+(?:a\s+)?notification"
    r"(?:\s+(?:that|saying)\s+(?P<message_alt>.+))?$",
    re.I,
)

_ACTION_START_PATTERN = re.compile(
    r"\s+(?=(?:notify\b|alert\b|send\s+(?:me|aaron|amber)\b|"
    r"turn\b|switch\b|power\b|open\s+(?:netflix|youtube|bbc|prime)\b|"
    r"launch\s+(?:netflix|youtube|bbc|prime)\b))",
    re.I,
)


class ConditionalActionEngine:
    """Persistent edge-triggered Home Assistant rules for Jarvis v16.2.0."""

    ACTIVE_STATUSES = {"active"}
    VISIBLE_STATUSES = {"active", "paused", "cancelled", "completed"}

    def __init__(
        self,
        *,
        tools: ToolProtocol,
        action_engine: ActionEngineProtocol,
        database_path: str,
        enabled: bool = True,
        timezone_name: str = "Europe/London",
        poll_seconds: int = 2,
        default_cooldown_seconds: int = 300,
        default_debounce_seconds: int = 2,
        notify_failures: bool = True,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.tools = tools
        self.action_engine = action_engine
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.poll_seconds = max(1, min(int(poll_seconds), 60))
        self.default_cooldown_seconds = max(0, min(int(default_cooldown_seconds), 86400))
        self.default_debounce_seconds = max(0, min(int(default_debounce_seconds), 3600))
        self.notify_failures = bool(notify_failures)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        try:
            self._timezone: tzinfo = ZoneInfo(timezone_name)
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
        value = re.sub(r"[^a-z0-9%°\s'.-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean(value: str) -> str:
        value = re.sub(r"\b(?:please|could you|can you)\b", " ", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip(" ,.!?")

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
                CREATE TABLE IF NOT EXISTS conditional_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_entity_id TEXT NOT NULL,
                    trigger_entity_name TEXT NOT NULL,
                    trigger_payload_json TEXT NOT NULL,
                    trigger_summary TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    one_shot INTEGER NOT NULL DEFAULT 0,
                    cooldown_seconds INTEGER NOT NULL,
                    debounce_seconds INTEGER NOT NULL,
                    window_start_minute INTEGER,
                    window_end_minute INTEGER,
                    timezone_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_state_json TEXT NOT NULL DEFAULT '{}',
                    candidate_state_json TEXT,
                    candidate_since TEXT,
                    last_triggered_at TEXT,
                    trigger_count INTEGER NOT NULL DEFAULT 0,
                    execution_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_conditional_rules_active
                ON conditional_rules(status, trigger_entity_id, rule_id);

                CREATE INDEX IF NOT EXISTS idx_conditional_rules_owner
                ON conditional_rules(owner_key, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS conditional_rule_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL,
                    owner_key TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    previous_state_json TEXT NOT NULL,
                    current_state_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    FOREIGN KEY(rule_id) REFERENCES conditional_rules(rule_id)
                );

                CREATE INDEX IF NOT EXISTS idx_conditional_runs_rule
                ON conditional_rule_runs(rule_id, triggered_at DESC, run_id DESC);

                CREATE TABLE IF NOT EXISTS conditional_rule_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(rule_id) REFERENCES conditional_rules(rule_id)
                );
                """
            )

    def _recover_interrupted_runs(self) -> None:
        now = self._iso(self._utc_now())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conditional_rule_runs
                SET status='failed', completed_at=?,
                    error=COALESCE(NULLIF(error, ''), 'Recovered after restart.')
                WHERE status='executing'
                """,
                (now,),
            )

    def _audit(
        self,
        *,
        rule_id: int | None,
        actor: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conditional_rule_audit(
                    rule_id, created_at, actor, action, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    self._iso(self._utc_now()),
                    actor,
                    action,
                    json.dumps(details or {}, ensure_ascii=False, default=str),
                ),
            )

    @staticmethod
    def _decode_json(value: Any, default: Any) -> Any:
        try:
            return json.loads(str(value))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _row_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["trigger_payload"] = cls._decode_json(item.pop("trigger_payload_json"), {})
        item["action_payload"] = cls._decode_json(item.pop("action_payload_json"), {})
        item["last_state"] = cls._decode_json(item.pop("last_state_json"), {})
        candidate = item.pop("candidate_state_json", None)
        item["candidate_state"] = cls._decode_json(candidate, None) if candidate else None
        item["one_shot"] = bool(item.get("one_shot"))
        return item

    @classmethod
    def _run_row_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["previous_state"] = cls._decode_json(item.pop("previous_state_json"), {})
        item["current_state"] = cls._decode_json(item.pop("current_state_json"), {})
        item["result"] = cls._decode_json(item.pop("result_json"), {})
        return item

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._stop_event = asyncio.Event()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="jarvis-conditional-actions")
        logger.info("Conditional Action Engine started")

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
        logger.info("Conditional Action Engine stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive loop guard
                self._last_error = str(exc)
                logger.exception("Conditional action cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def status(self) -> dict[str, Any]:
        with self._connection() as connection:
            rule_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM conditional_rules GROUP BY status"
            ).fetchall()
            run_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM conditional_rule_runs GROUP BY status"
            ).fetchall()
        return {
            "version": "16.2.0",
            "enabled": self.enabled,
            "running": self._running,
            "timezone": self.timezone_name,
            "poll_seconds": self.poll_seconds,
            "default_cooldown_seconds": self.default_cooldown_seconds,
            "default_debounce_seconds": self.default_debounce_seconds,
            "notify_failures": self.notify_failures,
            "last_cycle_at": self._last_cycle_at,
            "last_error": self._last_error,
            "rule_counts": {str(row["status"]): int(row["count"]) for row in rule_rows},
            "run_counts": {str(row["status"]): int(row["count"]) for row in run_rows},
        }

    async def list_rules(
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
                SELECT * FROM conditional_rules
                {where}
                ORDER BY created_at DESC, rule_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    async def get_rule(self, rule_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM conditional_rules WHERE rule_id = ?",
                (int(rule_id),),
            ).fetchone()
        return self._row_dict(row) if row else None

    async def get_owned_rule(
        self,
        rule_id: int,
        *,
        owner_key: str,
    ) -> dict[str, Any] | None:
        item = await self.get_rule(rule_id)
        if item is None or str(item.get("owner_key")) != owner_key:
            return None
        return item

    async def list_runs(
        self,
        rule_id: int,
        *,
        owner_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["r.rule_id = ?"]
        values: list[Any] = [int(rule_id)]
        if owner_key:
            clauses.append("r.owner_key = ?")
            values.append(owner_key)
        values.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT r.* FROM conditional_rule_runs AS r
                WHERE {" AND ".join(clauses)}
                ORDER BY r.triggered_at DESC, r.run_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._run_row_dict(row) for row in rows]

    async def pause_rule(
        self,
        rule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        return self._set_rule_status(
            rule_id,
            from_statuses={"active"},
            to_status="paused",
            owner_key=owner_key,
            actor=actor,
        )

    async def resume_rule(
        self,
        rule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        updated = self._set_rule_status(
            rule_id,
            from_statuses={"paused"},
            to_status="active",
            owner_key=owner_key,
            actor=actor,
        )
        if updated:
            await self._refresh_rule_baseline(rule_id)
        return updated

    async def cancel_rule(
        self,
        rule_id: int,
        *,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        return self._set_rule_status(
            rule_id,
            from_statuses={"active", "paused"},
            to_status="cancelled",
            owner_key=owner_key,
            actor=actor,
        )

    def _set_rule_status(
        self,
        rule_id: int,
        *,
        from_statuses: set[str],
        to_status: str,
        owner_key: str | None,
        actor: str,
    ) -> bool:
        placeholders = ",".join("?" for _ in from_statuses)
        query = (
            "UPDATE conditional_rules SET status=?, updated_at=?, candidate_state_json=NULL, "
            "candidate_since=NULL WHERE rule_id=? "
            f"AND status IN ({placeholders})"
        )
        values: list[Any] = [
            to_status,
            self._iso(self._utc_now()),
            int(rule_id),
            *sorted(from_statuses),
        ]
        if owner_key:
            query += " AND owner_key=?"
            values.append(owner_key)
        with self._connection() as connection:
            cursor = connection.execute(query, values)
            updated = cursor.rowcount > 0
        if updated:
            self._audit(rule_id=rule_id, actor=actor, action=to_status)
        return updated

    async def update_cooldown(
        self,
        rule_id: int,
        *,
        owner_key: str,
        seconds: int,
        actor: str,
    ) -> bool:
        seconds = max(0, min(int(seconds), 86400))
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE conditional_rules
                SET cooldown_seconds=?, updated_at=?
                WHERE rule_id=? AND owner_key=? AND status IN ('active', 'paused')
                """,
                (seconds, self._iso(self._utc_now()), int(rule_id), owner_key),
            )
            updated = cursor.rowcount > 0
        if updated:
            self._audit(
                rule_id=rule_id,
                actor=actor,
                action="cooldown_updated",
                details={"cooldown_seconds": seconds},
            )
        return updated

    async def update_window(
        self,
        rule_id: int,
        *,
        owner_key: str,
        start_minute: int | None,
        end_minute: int | None,
        actor: str,
    ) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE conditional_rules
                SET window_start_minute=?, window_end_minute=?, updated_at=?
                WHERE rule_id=? AND owner_key=? AND status IN ('active', 'paused')
                """,
                (
                    start_minute,
                    end_minute,
                    self._iso(self._utc_now()),
                    int(rule_id),
                    owner_key,
                ),
            )
            updated = cursor.rowcount > 0
        if updated:
            self._audit(
                rule_id=rule_id,
                actor=actor,
                action="window_updated",
                details={"start_minute": start_minute, "end_minute": end_minute},
            )
        return updated

    async def _refresh_rule_baseline(self, rule_id: int) -> None:
        rule = await self.get_rule(rule_id)
        if rule is None:
            return
        states = await self.tools.readable_entity_states(refresh=True)
        current = next(
            (item for item in states if item.get("entity_id") == rule["trigger_entity_id"]),
            None,
        )
        if current is None:
            return
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conditional_rules
                SET last_state_json=?, candidate_state_json=NULL,
                    candidate_since=NULL, updated_at=?
                WHERE rule_id=?
                """,
                (
                    json.dumps(current, ensure_ascii=False, default=str),
                    self._iso(self._utc_now()),
                    int(rule_id),
                ),
            )

    async def create_rule(
        self,
        *,
        actor: ActorProtocol,
        source_text: str,
        trigger: TriggerSpec,
        plan: ActionPlan,
        one_shot: bool,
        cooldown_seconds: int,
        debounce_seconds: int,
        window_start_minute: int | None,
        window_end_minute: int | None,
        baseline: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        now_text = self._iso(self._utc_now())
        trigger_json = json.dumps(trigger.payload, sort_keys=True, ensure_ascii=False, default=str)
        action_json = json.dumps(plan.payload, sort_keys=True, ensure_ascii=False, default=str)
        with self._connection() as connection:
            duplicate = connection.execute(
                """
                SELECT * FROM conditional_rules
                WHERE owner_key=? AND trigger_type=? AND trigger_entity_id=?
                  AND trigger_payload_json=? AND action_type=? AND action_payload_json=?
                  AND one_shot=? AND cooldown_seconds=? AND debounce_seconds=?
                  AND COALESCE(window_start_minute, -1)=COALESCE(?, -1)
                  AND COALESCE(window_end_minute, -1)=COALESCE(?, -1)
                  AND status IN ('active', 'paused')
                ORDER BY rule_id DESC LIMIT 1
                """,
                (
                    actor.user_key,
                    trigger.trigger_type,
                    trigger.entity_id,
                    trigger_json,
                    plan.action_type,
                    action_json,
                    int(one_shot),
                    int(cooldown_seconds),
                    int(debounce_seconds),
                    window_start_minute,
                    window_end_minute,
                ),
            ).fetchone()
            if duplicate is not None:
                return self._row_dict(duplicate), False

            cursor = connection.execute(
                """
                INSERT INTO conditional_rules(
                    owner_key, owner_name, source_text,
                    trigger_type, trigger_entity_id, trigger_entity_name,
                    trigger_payload_json, trigger_summary,
                    action_type, action_payload_json, action_summary,
                    status, one_shot, cooldown_seconds, debounce_seconds,
                    window_start_minute, window_end_minute, timezone_name,
                    created_at, updated_at, last_state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor.user_key,
                    actor.display_name,
                    source_text,
                    trigger.trigger_type,
                    trigger.entity_id,
                    trigger.entity_name,
                    trigger_json,
                    trigger.summary,
                    plan.action_type,
                    action_json,
                    plan.summary,
                    int(one_shot),
                    int(cooldown_seconds),
                    int(debounce_seconds),
                    window_start_minute,
                    window_end_minute,
                    self.timezone_name,
                    now_text,
                    now_text,
                    json.dumps(baseline, ensure_ascii=False, default=str),
                ),
            )
            rule_id = int(cursor.lastrowid)
        self._audit(
            rule_id=rule_id,
            actor=actor.user_key,
            action="created",
            details={
                "trigger": asdict(trigger),
                "action": asdict(plan),
                "one_shot": one_shot,
            },
        )
        rule = await self.get_rule(rule_id)
        assert rule is not None
        return rule, True

    async def process_once(self) -> int:
        if not self.enabled:
            return 0
        async with self._lock:
            now = self._utc_now()
            self._last_cycle_at = self._iso(now)
            rules = await self.list_rules(statuses={"active"}, limit=200)
            if not rules:
                return 0
            states = await self.tools.readable_entity_states(refresh=True)
            lookup = {str(item.get("entity_id")): item for item in states}
            processed = 0
            for rule in rules:
                current = lookup.get(str(rule["trigger_entity_id"]))
                if current is None:
                    self._clear_candidate_and_set_error(
                        int(rule["rule_id"]),
                        "Trigger entity is no longer readable.",
                    )
                    continue
                fired = await self._evaluate_rule(rule, current, now)
                processed += int(fired)
            return processed

    def _clear_candidate_and_set_error(self, rule_id: int, error: str | None) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conditional_rules
                SET candidate_state_json=NULL, candidate_since=NULL,
                    last_error=?, updated_at=?
                WHERE rule_id=?
                """,
                (error, self._iso(self._utc_now()), int(rule_id)),
            )

    async def _evaluate_rule(
        self,
        rule: dict[str, Any],
        current: dict[str, Any],
        now: datetime,
    ) -> bool:
        rule_id = int(rule["rule_id"])
        previous = dict(rule.get("last_state") or {})
        if str(rule.get("trigger_type")) == "time_state":
            due_at = self._parse_iso(str(rule["trigger_payload"]["due_at"]))
            if now < due_at:
                return False
            if not current.get("available", True):
                self._complete_without_action(
                    rule,
                    previous,
                    current,
                    now,
                    "Trigger entity was unavailable at the scheduled time.",
                )
                return False
            if self._condition_holds(rule, current):
                return await self._trigger_rule(rule, previous, current, now)
            self._complete_without_action(
                rule,
                previous,
                current,
                now,
                "The state condition was false at the scheduled time.",
            )
            return False
        if not current.get("available", True):
            self._clear_candidate_and_set_error(rule_id, "Trigger entity is unavailable.")
            return False

        if not self._inside_window(rule, now):
            self._update_baseline(rule_id, current, clear_candidate=True, error=None)
            return False

        candidate = rule.get("candidate_state")
        candidate_since_text = rule.get("candidate_since")
        if candidate and candidate_since_text:
            if not self._condition_holds(rule, current):
                self._update_baseline(rule_id, current, clear_candidate=True, error=None)
                return False
            candidate_since = self._parse_iso(str(candidate_since_text))
            debounce = int(rule.get("debounce_seconds") or 0)
            if now - candidate_since < timedelta(seconds=debounce):
                return False
            return await self._trigger_rule(rule, previous, current, now)

        if not self._edge_matches(rule, previous, current):
            self._update_baseline(rule_id, current, clear_candidate=True, error=None)
            return False

        cooldown = int(rule.get("cooldown_seconds") or 0)
        last_triggered = rule.get("last_triggered_at")
        if last_triggered:
            elapsed = now - self._parse_iso(str(last_triggered))
            if elapsed < timedelta(seconds=cooldown):
                self._record_skipped_run(
                    rule,
                    previous,
                    current,
                    now,
                    "Cooldown is still active.",
                )
                self._update_baseline(rule_id, current, clear_candidate=True, error=None)
                return False

        debounce = int(rule.get("debounce_seconds") or 0)
        if debounce > 0:
            with self._connection() as connection:
                connection.execute(
                    """
                    UPDATE conditional_rules
                    SET candidate_state_json=?, candidate_since=?, updated_at=?, last_error=NULL
                    WHERE rule_id=? AND status='active'
                    """,
                    (
                        json.dumps(current, ensure_ascii=False, default=str),
                        self._iso(now),
                        self._iso(now),
                        rule_id,
                    ),
                )
            return False

        return await self._trigger_rule(rule, previous, current, now)

    def _inside_window(self, rule: dict[str, Any], now: datetime) -> bool:
        start = rule.get("window_start_minute")
        end = rule.get("window_end_minute")
        if start is None and end is None:
            return True
        local = now.astimezone(self._timezone)
        minute = local.hour * 60 + local.minute
        if start is not None and end is not None:
            start_i = int(start)
            end_i = int(end)
            if start_i == end_i:
                return True
            if start_i < end_i:
                return start_i <= minute < end_i
            return minute >= start_i or minute < end_i
        if start is not None:
            return minute >= int(start)
        assert end is not None
        return minute < int(end)

    @classmethod
    def _state_value(cls, entity: dict[str, Any]) -> str:
        return cls._normalise(str(entity.get("state") or ""))

    @staticmethod
    def _numeric_value(entity: dict[str, Any]) -> float | None:
        raw = entity.get("state")
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return value

    def _condition_holds(self, rule: dict[str, Any], current: dict[str, Any]) -> bool:
        trigger_type = str(rule["trigger_type"])
        payload = dict(rule.get("trigger_payload") or {})
        state = self._state_value(current)
        if trigger_type == "state_in":
            return state in {self._normalise(item) for item in payload.get("states", [])}
        if trigger_type == "state_equals":
            return state == self._normalise(str(payload.get("state") or ""))
        if trigger_type == "presence_leave":
            return state != "home"
        if trigger_type == "presence_arrive":
            return state == "home"
        if trigger_type == "time_state":
            return state == self._normalise(str(payload.get("state") or ""))
        if trigger_type in {"numeric_below", "numeric_above"}:
            value = self._numeric_value(current)
            if value is None:
                return False
            threshold = float(payload["threshold"])
            return value < threshold if trigger_type == "numeric_below" else value > threshold
        return False

    def _edge_matches(
        self,
        rule: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> bool:
        if not previous:
            return False
        trigger_type = str(rule["trigger_type"])
        payload = dict(rule.get("trigger_payload") or {})
        previous_state = self._state_value(previous)
        current_state = self._state_value(current)
        if trigger_type == "state_in":
            targets = {self._normalise(item) for item in payload.get("states", [])}
            return previous_state not in targets and current_state in targets
        if trigger_type == "state_equals":
            target = self._normalise(str(payload.get("state") or ""))
            return previous_state != target and current_state == target
        if trigger_type == "presence_leave":
            return previous_state == "home" and current_state != "home"
        if trigger_type == "presence_arrive":
            return previous_state != "home" and current_state == "home"
        if trigger_type in {"numeric_below", "numeric_above"}:
            previous_value = self._numeric_value(previous)
            current_value = self._numeric_value(current)
            if previous_value is None or current_value is None:
                return False
            threshold = float(payload["threshold"])
            if trigger_type == "numeric_below":
                return previous_value >= threshold and current_value < threshold
            return previous_value <= threshold and current_value > threshold
        return False

    def _update_baseline(
        self,
        rule_id: int,
        current: dict[str, Any],
        *,
        clear_candidate: bool,
        error: str | None,
    ) -> None:
        candidate_sql = (
            ", candidate_state_json=NULL, candidate_since=NULL" if clear_candidate else ""
        )
        with self._connection() as connection:
            connection.execute(
                f"""
                UPDATE conditional_rules
                SET last_state_json=?, last_error=?, updated_at=?{candidate_sql}
                WHERE rule_id=?
                """,
                (
                    json.dumps(current, ensure_ascii=False, default=str),
                    error,
                    self._iso(self._utc_now()),
                    int(rule_id),
                ),
            )

    def _record_skipped_run(
        self,
        rule: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
        now: datetime,
        reason: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conditional_rule_runs(
                    rule_id, owner_key, triggered_at,
                    previous_state_json, current_state_json,
                    status, completed_at, error
                ) VALUES (?, ?, ?, ?, ?, 'skipped', ?, ?)
                """,
                (
                    int(rule["rule_id"]),
                    str(rule["owner_key"]),
                    self._iso(now),
                    json.dumps(previous, ensure_ascii=False, default=str),
                    json.dumps(current, ensure_ascii=False, default=str),
                    self._iso(now),
                    reason,
                ),
            )

    def _complete_without_action(
        self,
        rule: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
        now: datetime,
        reason: str,
    ) -> None:
        self._record_skipped_run(rule, previous, current, now, reason)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conditional_rules
                SET status='completed', last_state_json=?, updated_at=?,
                    last_error=?, candidate_state_json=NULL, candidate_since=NULL
                WHERE rule_id=? AND status='active'
                """,
                (
                    json.dumps(current, ensure_ascii=False, default=str),
                    self._iso(now),
                    reason,
                    int(rule["rule_id"]),
                ),
            )
        self._audit(
            rule_id=int(rule["rule_id"]),
            actor="conditional-engine",
            action="condition_false",
            details={"reason": reason},
        )

    async def _trigger_rule(
        self,
        rule: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
        now: datetime,
    ) -> bool:
        rule_id = int(rule["rule_id"])
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conditional_rule_runs(
                    rule_id, owner_key, triggered_at,
                    previous_state_json, current_state_json, status
                ) VALUES (?, ?, ?, ?, ?, 'executing')
                """,
                (
                    rule_id,
                    str(rule["owner_key"]),
                    self._iso(now),
                    json.dumps(previous, ensure_ascii=False, default=str),
                    json.dumps(current, ensure_ascii=False, default=str),
                ),
            )
            run_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE conditional_rules
                SET trigger_count=trigger_count+1, last_triggered_at=?,
                    candidate_state_json=NULL, candidate_since=NULL, updated_at=?
                WHERE rule_id=? AND status='active'
                """,
                (self._iso(now), self._iso(now), rule_id),
            )

        try:
            result = await self._execute_rule_action(rule)
            success = result.get("success") is True
            verified = result.get("verified")
            if verified is False and not result.get("command_accepted"):
                success = False
            status = "completed" if success else "failed"
            error = (
                None
                if success
                else str(
                    result.get("response_message")
                    or result.get("message")
                    or result.get("error")
                    or "Conditional action failed."
                )
            )
        except Exception as exc:  # pragma: no cover - defensive execution guard
            logger.exception("Conditional rule %s failed", rule_id)
            result = {"success": False, "error": str(exc)}
            status = "failed"
            error = str(exc)

        completed_at = self._iso(self._utc_now())
        final_rule_status = (
            "completed" if status == "completed" and rule.get("one_shot") else "active"
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE conditional_rule_runs
                SET status=?, completed_at=?, result_json=?, error=?
                WHERE run_id=? AND status='executing'
                """,
                (
                    status,
                    completed_at,
                    json.dumps(result, ensure_ascii=False, default=str),
                    error,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE conditional_rules
                SET status=?, execution_count=execution_count+1,
                    last_state_json=?, updated_at=?, last_error=?
                WHERE rule_id=?
                """,
                (
                    final_rule_status,
                    json.dumps(current, ensure_ascii=False, default=str),
                    completed_at,
                    error,
                    rule_id,
                ),
            )
        self._audit(
            rule_id=rule_id,
            actor="conditional-engine",
            action=status,
            details={"run_id": run_id, "result": result, "error": error},
        )
        if status == "failed":
            await self._notify_failure(rule, error or "Conditional action failed.")
        return True

    async def _execute_rule_action(self, rule: dict[str, Any]) -> dict[str, Any]:
        action_type = str(rule["action_type"])
        payload = dict(rule.get("action_payload") or {})
        if action_type == "notify_owner":
            recipient = str(payload.get("recipient") or rule["owner_key"]).casefold()
            message = str(payload.get("message") or "").strip()
            if not message:
                message = f"Condition triggered: {rule['trigger_summary']}."
            notification = await self.tools.send_mobile_notification(
                recipient=recipient,
                title="Jarvis conditional action",
                message=message,
            )
            return {
                "success": notification.get("success", True),
                "verified": True,
                "notification": notification,
                "response_message": message,
            }
        return await self.action_engine._execute_action(action_type, payload)

    async def _notify_failure(self, rule: dict[str, Any], error: str) -> None:
        if not self.notify_failures:
            return
        recipient = str(rule.get("owner_key") or "").casefold()
        if recipient not in {"aaron", "amber"}:
            return
        try:
            await self.tools.send_mobile_notification(
                recipient=recipient,
                title="Jarvis conditional action failed",
                message=f"Rule {rule['rule_id']} failed: {rule['action_summary']}. {error}",
            )
        except Exception:
            logger.exception("Could not send failure notification for rule %s", rule["rule_id"])

    def _next_clock_due(self, clock_text: str) -> datetime | None:
        minute = self._clock_minute(clock_text)
        if minute is None:
            return None
        local_now = self._utc_now().astimezone(self._timezone)
        hour, minute_value = divmod(minute, 60)
        due_local = local_now.replace(
            hour=hour,
            minute=minute_value,
            second=0,
            microsecond=0,
        )
        if due_local <= local_now + timedelta(seconds=1):
            due_local += timedelta(days=1)
        return due_local.astimezone(timezone.utc)

    async def _parse_timed_condition(
        self,
        text: str,
        actor: ActorProtocol,
    ) -> tuple[TriggerSpec, ActionPlan, dict[str, Any]] | str | None:
        match = _TIMED_CONDITION_PATTERN.match(text)
        if not match:
            return None
        due_at = self._next_clock_due(match.group("clock"))
        if due_at is None:
            return "I couldn’t understand that clock time."
        action_text = self._clean(match.group("action"))
        plan = await self.action_engine._resolve_action(
            action_text,
            actor_key=actor.user_key,
        )
        if isinstance(plan, str):
            return plan

        condition_text = self._normalise(match.group("condition"))
        state_match = re.search(
            r"\b(?:is|are|stays?|still)\s+(?:still\s+)?(?P<state>on|off|open|closed|home|not home)\b",
            condition_text,
        )
        if not state_match:
            return "The timed condition must name an exact state, such as ‘if it is still on’."
        target_state = state_match.group("state")
        target_state = {"open": "on", "closed": "off", "not home": "not_home"}.get(
            target_state, target_state
        )

        entity_id: str | None = None
        entity_name: str | None = None
        if plan.action_type == "device_control":
            entity_id = str(plan.payload.get("entity_id") or "")
        elif plan.action_type == "media_shortcut":
            shortcut = str(plan.payload.get("shortcut") or "")
            shortcuts = getattr(self.tools, "MEDIA_SHORTCUTS", {})
            if isinstance(shortcuts, dict):
                item = shortcuts.get(shortcut) or {}
                entity_id = str(item.get("state_entity_id") or "")
        if entity_id:
            exact = await self.tools.search_entity_states(entity_id, limit=2)
            entity = next(
                (item for item in exact.get("entities", []) if item.get("entity_id") == entity_id),
                None,
            )
        else:
            condition_query = re.sub(
                r"\b(?:it|they|is|are|stays?|still|on|off|open|closed|home|not home)\b",
                " ",
                condition_text,
            )
            condition_query = self._clean(condition_query)
            if not condition_query:
                condition_query = re.sub(
                    r"\b(?:turn|switch|power|on|off|the|my)\b",
                    " ",
                    action_text,
                    flags=re.I,
                )
            resolved = await self._resolve_entity(condition_query)
            if isinstance(resolved, str):
                return resolved
            entity = resolved
            entity_id = str(entity["entity_id"])
        if entity is None:
            return "I couldn’t identify the exact entity to check at that time."
        entity_name = str(entity.get("name") or entity_id)
        trigger = TriggerSpec(
            trigger_type="time_state",
            entity_id=entity_id,
            entity_name=entity_name,
            payload={"due_at": self._iso(due_at), "state": target_state},
            summary=(
                f"at {match.group('clock').strip()} while {entity_name} is "
                f"{target_state.replace('_', ' ')}"
            ),
        )
        return trigger, plan, entity

    async def _parse_rule(self, text: str) -> ParsedRule | str | None:
        prefix_match = _RULE_PREFIX_PATTERN.match(text)
        if not prefix_match:
            return None
        one_shot = prefix_match.group("prefix").casefold().startswith("next")
        remainder = text[prefix_match.end() :].strip()

        cooldown_seconds = self.default_cooldown_seconds
        cooldown_match = _COOLDOWN_PATTERN.search(remainder)
        if cooldown_match:
            cooldown_seconds = self._duration_seconds(
                cooldown_match.group("amount"), cooldown_match.group("unit")
            )
            remainder = self._clean(
                remainder[: cooldown_match.start()] + " " + remainder[cooldown_match.end() :]
            )

        split = self._split_trigger_action(remainder)
        if split is None:
            return "Please separate the condition and action with ‘then’, for example: when the door opens, then notify me."
        trigger_text, action_text = split

        debounce_seconds = self.default_debounce_seconds
        debounce_match = _DEBOUNCE_PATTERN.search(trigger_text)
        if debounce_match:
            debounce_seconds = self._duration_seconds(
                debounce_match.group("amount"), debounce_match.group("unit")
            )
            trigger_text = self._clean(trigger_text[: debounce_match.start()])

        start_minute, end_minute, trigger_text = self._extract_window(trigger_text)
        return ParsedRule(
            trigger_text=trigger_text,
            action_text=action_text,
            one_shot=one_shot,
            cooldown_seconds=cooldown_seconds,
            debounce_seconds=debounce_seconds,
            window_start_minute=start_minute,
            window_end_minute=end_minute,
        )

    def _split_trigger_action(self, value: str) -> tuple[str, str] | None:
        for separator in (", then ", " then ", ","):
            index = value.casefold().find(separator)
            if index > 0:
                left = self._clean(value[:index])
                right = self._clean(value[index + len(separator) :])
                if left and right:
                    return left, right
        matches = list(_ACTION_START_PATTERN.finditer(value))
        if matches:
            match = matches[-1]
            left = self._clean(value[: match.start()])
            right = self._clean(value[match.end() :])
            if left and right:
                return left, right
        return None

    @staticmethod
    def _duration_seconds(amount_text: str, unit_text: str) -> int:
        amount = int(amount_text)
        unit = unit_text.casefold()
        if unit.startswith("hour"):
            return amount * 3600
        if unit.startswith("minute"):
            return amount * 60
        return amount

    def _extract_window(self, trigger_text: str) -> tuple[int | None, int | None, str]:
        match = _BETWEEN_WINDOW_PATTERN.search(trigger_text)
        if match:
            start = self._clock_minute(match.group("start"))
            end = self._clock_minute(match.group("end"))
            if start is None or end is None:
                return None, None, trigger_text
            cleaned = self._clean(trigger_text[: match.start()] + " " + trigger_text[match.end() :])
            return start, end, cleaned
        match = _AFTER_WINDOW_PATTERN.search(trigger_text)
        if match:
            start = self._clock_minute(match.group("start"))
            if start is not None:
                cleaned = self._clean(
                    trigger_text[: match.start()] + " " + trigger_text[match.end() :]
                )
                return start, None, cleaned
        match = _BEFORE_WINDOW_PATTERN.search(trigger_text)
        if match:
            end = self._clock_minute(match.group("end"))
            if end is not None:
                cleaned = self._clean(
                    trigger_text[: match.start()] + " " + trigger_text[match.end() :]
                )
                return None, end, cleaned
        return None, None, trigger_text

    @staticmethod
    def _clock_minute(value: str) -> int | None:
        match = re.fullmatch(
            r"\s*(?P<hour>\d{1,2})(?:(?::|\.)(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\s*",
            value,
            re.I,
        )
        if not match:
            return None
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        if minute > 59:
            return None
        ampm = match.group("ampm")
        if ampm:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if ampm.casefold() == "pm" else 0)
        elif not 0 <= hour <= 23:
            return None
        return hour * 60 + minute

    async def _resolve_trigger(self, text: str) -> TriggerSpec | str:
        value = self._clean(text)
        match = _PRESENCE_LEAVE_PATTERN.match(value)
        if match:
            entity = await self._resolve_entity(match.group("entity"), domain="person")
            if isinstance(entity, str):
                return entity
            return TriggerSpec(
                trigger_type="presence_leave",
                entity_id=str(entity["entity_id"]),
                entity_name=str(entity["name"]),
                payload={},
                summary=f"{entity['name']} leaves home",
            )

        match = _PRESENCE_ARRIVE_PATTERN.match(value)
        if match:
            entity = await self._resolve_entity(match.group("entity"), domain="person")
            if isinstance(entity, str):
                return entity
            return TriggerSpec(
                trigger_type="presence_arrive",
                entity_id=str(entity["entity_id"]),
                entity_name=str(entity["name"]),
                payload={},
                summary=f"{entity['name']} arrives home",
            )

        match = _NUMERIC_PATTERN.match(value)
        if match:
            entity = await self._resolve_entity(match.group("entity"), domain="sensor")
            if isinstance(entity, str):
                return entity
            direction = match.group("direction").casefold()
            threshold = float(match.group("value"))
            trigger_type = "numeric_below" if direction in {"below", "under"} else "numeric_above"
            word = "below" if trigger_type == "numeric_below" else "above"
            unit = match.group("unit") or entity.get("unit") or ""
            rendered = (
                f"{threshold:g}{unit}"
                if unit in {"%", "°C", "°F"}
                else f"{threshold:g}{(' ' + unit) if unit else ''}"
            )
            return TriggerSpec(
                trigger_type=trigger_type,
                entity_id=str(entity["entity_id"]),
                entity_name=str(entity["name"]),
                payload={"threshold": threshold, "unit": unit},
                summary=f"{entity['name']} goes {word} {rendered}",
            )

        match = _STATE_VERB_PATTERN.match(value)
        if match:
            verb = self._normalise(match.group("verb"))
            domain = "binary_sensor" if verb in {"open", "opens", "close", "closes"} else None
            entity = await self._resolve_entity(match.group("entity"), domain=domain)
            if isinstance(entity, str):
                return entity
            if verb in {"turn on", "turns on", "open", "opens", "start", "starts"}:
                states = ["on"]
                display = (
                    "turns on" if "turn" in verb else ("opens" if "open" in verb else "starts")
                )
            elif verb in {"finish", "finishes", "complete", "completes"}:
                states = ["finished", "complete", "completed", "idle", "off"]
                display = "finishes"
            else:
                states = ["off"]
                display = (
                    "turns off" if "turn" in verb else ("closes" if "close" in verb else "stops")
                )
            return TriggerSpec(
                trigger_type="state_in",
                entity_id=str(entity["entity_id"]),
                entity_name=str(entity["name"]),
                payload={"states": states},
                summary=f"{entity['name']} {display}",
            )

        match = _STATE_EQUALS_PATTERN.match(value)
        if match:
            entity = await self._resolve_entity(match.group("entity"))
            if isinstance(entity, str):
                return entity
            target = self._normalise(match.group("state"))
            return TriggerSpec(
                trigger_type="state_equals",
                entity_id=str(entity["entity_id"]),
                entity_name=str(entity["name"]),
                payload={"state": target},
                summary=f"{entity['name']} becomes {target}",
            )

        return (
            "I can create conditions for exact entity states, doors opening or closing, "
            "people arriving or leaving, and numeric values crossing a threshold."
        )

    async def _resolve_entity(
        self,
        query: str,
        *,
        domain: str | None = None,
    ) -> dict[str, Any] | str:
        cleaned_query = re.sub(r"^(?:the|my)\s+", "", self._clean(query), flags=re.I)
        query_key = self._normalise(cleaned_query)
        result = await self.tools.search_entity_states(
            cleaned_query,
            domain=domain,
            limit=8,
        )
        entities = list(result.get("entities") or [])
        exact = [
            item
            for item in entities
            if query_key
            in {
                self._normalise(str(item.get("name") or "")),
                self._normalise(str(item.get("entity_id") or "")),
            }
        ]
        matches = exact if exact else entities
        unique = {str(item.get("entity_id")): item for item in matches if item.get("entity_id")}
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            names = [
                str(item.get("name") or item.get("entity_id")) for item in list(unique.values())[:4]
            ]
            return (
                "I found more than one matching trigger entity — "
                + ", ".join(names)
                + ". Please name the exact one."
            )
        return f"I couldn’t find one readable Home Assistant entity matching “{cleaned_query}”."

    async def _resolve_action(
        self,
        action_text: str,
        actor: ActorProtocol,
        trigger: TriggerSpec,
    ) -> ActionPlan | str:
        match = _NOTIFY_ACTION_PATTERN.match(self._clean(action_text))
        if match:
            recipient_text = (
                match.group("recipient") or match.group("recipient_alt") or "me"
            ).casefold()
            recipient = actor.user_key if recipient_text == "me" else recipient_text
            if recipient != actor.user_key:
                return "For privacy, a voice-created conditional rule can only notify its owner."
            message = (match.group("message") or match.group("message_alt") or "").strip()
            if not message:
                message = f"Condition triggered: {trigger.summary}."
            return ActionPlan(
                action_type="notify_owner",
                payload={"recipient": recipient, "message": message},
                summary=f"notify {actor.display_name}",
            )
        return await self.action_engine._resolve_action(
            action_text,
            actor_key=actor.user_key,
        )

    @staticmethod
    def _window_description(start: int | None, end: int | None) -> str:
        if start is None and end is None:
            return ""

        def render(value: int) -> str:
            hour, minute = divmod(value, 60)
            suffix = "am" if hour < 12 else "pm"
            hour12 = hour % 12 or 12
            return f"{hour12}:{minute:02d} {suffix}" if minute else f"{hour12} {suffix}"

        if start is not None and end is not None:
            return f" between {render(start)} and {render(end)}"
        if start is not None:
            return f" after {render(start)}"
        assert end is not None
        return f" before {render(end)}"

    def _describe_rule(self, rule: dict[str, Any]) -> str:
        rule_id = int(rule["rule_id"])
        status = str(rule.get("status") or "unknown")
        mode = "one time" if rule.get("one_shot") else "persistent"
        window = self._window_description(
            rule.get("window_start_minute"),
            rule.get("window_end_minute"),
        )
        return (
            f"Rule {rule_id} is {status}: when {rule['trigger_summary']}{window}, "
            f"{rule['action_summary']} ({mode})."
        )

    async def handle_command(
        self,
        text: str,
        actor: ActorProtocol,
    ) -> TaskCommandResult:
        value = self._clean(text)

        if _SHOW_RULES_PATTERN.match(value):
            rules = await self.list_rules(
                owner_key=actor.user_key,
                statuses=self.VISIBLE_STATUSES,
                limit=10,
            )
            if not rules:
                return TaskCommandResult(
                    handled=True,
                    response="You have no conditional rules.",
                    intent="condition_list",
                    details={"rules": []},
                )
            descriptions = [self._describe_rule(rule).rstrip(".") for rule in rules[:5]]
            return TaskCommandResult(
                handled=True,
                response=f"You have {len(rules)} conditional rule{'s' if len(rules) != 1 else ''}. "
                + "; ".join(descriptions)
                + ".",
                intent="condition_list",
                details={"rules": rules},
            )

        match = _RULE_STATUS_PATTERN.match(value)
        if match:
            rule_id = int(match.group("rule_id"))
            rule = await self.get_owned_rule(rule_id, owner_key=actor.user_key)
            if rule is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find rule {rule_id} in your conditional rules.",
                    intent="condition_status",
                )
            return TaskCommandResult(
                handled=True,
                response=self._describe_rule(rule),
                intent="condition_status",
                details={"rule": rule},
            )

        match = _RULE_HISTORY_PATTERN.match(value)
        if match:
            rule_id = int(match.group("rule_id") or match.group("rule_id_alt"))
            rule = await self.get_owned_rule(rule_id, owner_key=actor.user_key)
            if rule is None:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t find rule {rule_id} in your conditional rules.",
                    intent="condition_history",
                )
            runs = await self.list_runs(rule_id, owner_key=actor.user_key, limit=10)
            if not runs:
                response = f"Rule {rule_id} has not triggered yet."
            else:
                latest = runs[0]
                response = (
                    f"Rule {rule_id} has {len(runs)} recorded run{'s' if len(runs) != 1 else ''}. "
                    f"The latest was {latest['status']}."
                )
            return TaskCommandResult(
                handled=True,
                response=response,
                intent="condition_history",
                details={"rule": rule, "runs": runs},
            )

        for pattern, operation, verb in (
            (_PAUSE_RULE_PATTERN, self.pause_rule, "Paused"),
            (_RESUME_RULE_PATTERN, self.resume_rule, "Resumed"),
            (_CANCEL_RULE_PATTERN, self.cancel_rule, "Cancelled"),
        ):
            match = pattern.match(value)
            if match:
                rule_id = int(match.group("rule_id"))
                updated = await operation(
                    rule_id,
                    owner_key=actor.user_key,
                    actor=actor.user_key,
                )
                if not updated:
                    return TaskCommandResult(
                        handled=True,
                        success=False,
                        response=f"I couldn’t {verb.casefold()} rule {rule_id}.",
                        intent="condition_manage",
                    )
                managed_rule = await self.get_rule(rule_id)
                if managed_rule is None:
                    return TaskCommandResult(
                        handled=True,
                        success=False,
                        response=f"Rule {rule_id} was updated but is no longer available.",
                        intent="condition_manage",
                    )
                return TaskCommandResult(
                    handled=True,
                    response=f"{verb} rule {rule_id}: {managed_rule['trigger_summary']}.",
                    intent="condition_manage",
                    details={"rule": managed_rule},
                )

        match = _CHANGE_COOLDOWN_PATTERN.match(value)
        if match:
            rule_id = int(match.group("rule_id"))
            seconds = self._duration_seconds(match.group("amount"), match.group("unit"))
            updated = await self.update_cooldown(
                rule_id,
                owner_key=actor.user_key,
                seconds=seconds,
                actor=actor.user_key,
            )
            if not updated:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t update rule {rule_id}.",
                    intent="condition_manage",
                )
            return TaskCommandResult(
                handled=True,
                response=f"Rule {rule_id} now has a {seconds}-second cooldown.",
                intent="condition_manage",
                details={"rule": await self.get_rule(rule_id)},
            )

        match = _CHANGE_WINDOW_PATTERN.match(value)
        if match:
            rule_id = int(match.group("rule_id"))
            start, end, remainder = self._extract_window(match.group("window"))
            if remainder or (start is None and end is None):
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="Use a window such as between 10 pm and 7 am, after 11 pm, or before 6 am.",
                    intent="condition_manage",
                )
            updated = await self.update_window(
                rule_id,
                owner_key=actor.user_key,
                start_minute=start,
                end_minute=end,
                actor=actor.user_key,
            )
            if not updated:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response=f"I couldn’t update rule {rule_id}.",
                    intent="condition_manage",
                )
            return TaskCommandResult(
                handled=True,
                response=f"Updated rule {rule_id}{self._window_description(start, end)}.",
                intent="condition_manage",
                details={"rule": await self.get_rule(rule_id)},
            )

        timed = await self._parse_timed_condition(value, actor)
        if isinstance(timed, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=timed,
                intent="condition_create",
            )
        if timed is not None:
            if not self.enabled:
                return TaskCommandResult(
                    handled=True,
                    success=False,
                    response="Conditional actions are currently disabled.",
                    intent="condition_create",
                )
            trigger, plan, baseline = timed
            rule, created = await self.create_rule(
                actor=actor,
                source_text=text,
                trigger=trigger,
                plan=plan,
                one_shot=True,
                cooldown_seconds=0,
                debounce_seconds=0,
                window_start_minute=None,
                window_end_minute=None,
                baseline=baseline,
            )
            if not created:
                return TaskCommandResult(
                    handled=True,
                    response=f"That conditional rule already exists as rule {rule['rule_id']}.",
                    intent="condition_create",
                    details={"rule": rule, "duplicate": True},
                )
            return TaskCommandResult(
                handled=True,
                response=(
                    f"Okay. Rule {rule['rule_id']} will {plan.summary} {trigger.summary}, "
                    "but only if that state is still true."
                ),
                intent="condition_create",
                details={"rule": rule},
            )

        parsed = await self._parse_rule(value)
        if parsed is None:
            return TaskCommandResult(handled=False)
        if isinstance(parsed, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=parsed,
                intent="condition_create",
            )
        if not self.enabled:
            return TaskCommandResult(
                handled=True,
                success=False,
                response="Conditional actions are currently disabled.",
                intent="condition_create",
            )

        resolved_trigger = await self._resolve_trigger(parsed.trigger_text)
        if isinstance(resolved_trigger, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=resolved_trigger,
                intent="condition_create",
            )
        resolved_plan = await self._resolve_action(parsed.action_text, actor, resolved_trigger)
        if isinstance(resolved_plan, str):
            return TaskCommandResult(
                handled=True,
                success=False,
                response=resolved_plan,
                intent="condition_create",
            )

        current_result = await self.tools.search_entity_states(
            resolved_trigger.entity_id,
            limit=2,
        )
        resolved_baseline: dict[str, Any] | None = next(
            (
                item
                for item in current_result.get("entities", [])
                if item.get("entity_id") == resolved_trigger.entity_id
            ),
            None,
        )
        if resolved_baseline is None:
            return TaskCommandResult(
                handled=True,
                success=False,
                response=(
                    f"{resolved_trigger.entity_name} is not currently readable, "
                    "so I didn’t create the rule."
                ),
                intent="condition_create",
            )

        rule, created = await self.create_rule(
            actor=actor,
            source_text=text,
            trigger=resolved_trigger,
            plan=resolved_plan,
            one_shot=parsed.one_shot,
            cooldown_seconds=parsed.cooldown_seconds,
            debounce_seconds=parsed.debounce_seconds,
            window_start_minute=parsed.window_start_minute,
            window_end_minute=parsed.window_end_minute,
            baseline=resolved_baseline,
        )
        if not created:
            return TaskCommandResult(
                handled=True,
                response=f"That conditional rule already exists as rule {rule['rule_id']}.",
                intent="condition_create",
                details={"rule": rule, "duplicate": True},
            )

        window = self._window_description(
            parsed.window_start_minute,
            parsed.window_end_minute,
        )
        mode = "once" if parsed.one_shot else "until you pause or cancel it"
        return TaskCommandResult(
            handled=True,
            response=(
                f"Okay. Rule {rule['rule_id']} will {resolved_plan.summary} when "
                f"{resolved_trigger.summary}{window}, {mode}."
            ),
            intent="condition_create",
            details={"rule": rule},
        )
