from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("jarvis-core.proactive")


class AwarenessProtocol(Protocol):
    async def recent_events(
        self,
        *,
        minutes: int = 60,
        limit: int = 50,
        area_id: str | None = None,
        categories: Any = None,
        min_importance: int = 0,
    ) -> list[Any]: ...

    async def mark_proactive_delivered(self, event_id: int) -> bool: ...

    async def active_devices_summary(self) -> tuple[str, list[dict[str, Any]]]: ...


class ToolProtocol(Protocol):
    async def readable_entity_states(self, refresh: bool = False) -> list[dict[str, Any]]: ...

    async def get_entity_state(self, entity_id: str) -> dict[str, Any]: ...

    async def announce_message(self, target: str, message: str) -> dict[str, Any]: ...

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ProactiveCommandResult:
    handled: bool
    success: bool = True
    response: str = ""
    intent: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    recipient: str | None
    announce: bool
    hold_until: str | None
    reason: str
    severity: str


_ACK_PATTERN = re.compile(
    r"^\s*(?:thanks|thank you|cheers|i know|got it|okay|ok|acknowledged|that'?s fine)\s*[.!?]*\s*$",
    re.I,
)
_SNOOZE_PATTERN = re.compile(
    r"^\s*(?:remind me|tell me|let me know)(?:\s+about\s+that)?\s+again\s+in\s+"
    r"(?P<amount>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*(?P<unit>minutes?|mins?|hours?|hrs?)\s*[.!?]*\s*$",
    re.I,
)
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
}
_SUPPRESS_TONIGHT_PATTERN = re.compile(
    r"^\s*(?:don['’]?t|do not)\s+(?:tell|remind|notify)\s+me\s+about\s+that\s+again\s+tonight\s*[.!?]*\s*$",
    re.I,
)
_FORWARD_PATTERN = re.compile(
    r"^\s*(?:send|forward|tell)\s+that\s+to\s+(?P<recipient>amber|aaron|both|both of us)\s*[.!?]*\s*$",
    re.I,
)
_ACTIVE_ALERTS_PATTERN = re.compile(
    r"^\s*(?:show(?:\s+me)?|tell me|what(?:\s+are)?|which(?:\s+are)?)\s+"
    r"(?:the\s+)?(?:active|current|open)\s+alerts?\s*[.!?]*\s*$",
    re.I,
)


class ProactiveOrchestrator:
    """Presence-aware, quiet-hours-aware delivery for House Awareness events.

    House Awareness remains the authoritative event source. This component owns
    notification policy, timed conditions, duplicate suppression, escalation,
    acknowledgement and an auditable delivery history.
    """

    CRITICAL_EVENT_TYPES = {"safety_alert", "occupancy_while_away"}
    PERSISTENT_EVENT_TYPES = {
        "safety_alert",
        "occupancy_while_away",
        "opening_open_long",
        "camera_offline",
        "battery_low",
    }
    AWAY_OCCUPANCY_EXCLUDED_TOKENS = {
        "all",
        "backpack",
        "cell phone",
        "laptop",
        "remote",
        "suitcase",
        "television",
        "tv",
    }
    INFRASTRUCTURE_DEVICE_PATTERNS = (
        "advanced ssh",
        "mosquitto",
        "openwakeword",
        "piper",
        "samba",
        "studio code server",
        "tailscale",
        "terminal & ssh",
        "whisper",
    )
    HIGH_EVENT_TYPES = {
        "safety_cleared",
        "opening_open_long",
        "camera_offline",
        "devices_left_on",
    }
    IMMEDIATE_EVENT_TYPES = {
        "safety_alert",
        "safety_cleared",
        "washing_finished",
        "battery_low",
        "person_arrived",
        "occupancy_detected",
        "person_left",
        "opening_opened",
        "opening_closed",
    }

    def __init__(
        self,
        *,
        awareness: AwarenessProtocol,
        tools: ToolProtocol,
        database_path: str,
        enabled: bool = True,
        announcement_target: str = "living_room",
        timezone_name: str = "Europe/London",
        quiet_start: str = "22:30",
        quiet_end: str = "07:00",
        poll_seconds: int = 5,
        duplicate_cooldown_seconds: int = 300,
        opening_delay_seconds: int = 300,
        camera_offline_seconds: int = 120,
        camera_scan_seconds: int = 30,
        escalation_seconds: int = 300,
        max_escalations: int = 2,
        process_existing_events: bool = False,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.awareness = awareness
        self.tools = tools
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.announcement_target = announcement_target.strip() or "living_room"
        self.poll_seconds = max(1, min(int(poll_seconds), 300))
        self.duplicate_cooldown_seconds = max(30, min(int(duplicate_cooldown_seconds), 86400))
        self.opening_delay_seconds = max(30, min(int(opening_delay_seconds), 86400))
        self.camera_offline_seconds = max(30, min(int(camera_offline_seconds), 86400))
        self.camera_scan_seconds = max(10, min(int(camera_scan_seconds), 3600))
        self.escalation_seconds = max(30, min(int(escalation_seconds), 86400))
        self.max_escalations = max(0, min(int(max_escalations), 5))
        self.process_existing_events = bool(process_existing_events)
        raw_security_entities = os.getenv(
            "JARVIS_PROACTIVE_SECURITY_OCCUPANCY_ENTITIES",
            "",
        )
        self.security_occupancy_entities = {
            value.strip() for value in raw_security_entities.split(",") if value.strip()
        }
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._quiet_start = self._parse_clock(quiet_start, time(22, 30))
        self._quiet_end = self._parse_clock(quiet_end, time(7, 0))
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
        self._last_event_id = 0
        self._last_camera_scan_at: datetime | None = None
        self._presence_cache_at: datetime | None = None
        self._presence_cache: set[str] = set()
        self._initialise_database()
        stored_cursor = self._meta_get_sync("last_event_id")
        if stored_cursor and stored_cursor.isdigit():
            self._last_event_id = int(stored_cursor)

    @staticmethod
    def _parse_clock(value: str, fallback: time) -> time:
        try:
            hour_text, minute_text = value.strip().split(":", 1)
            return time(hour=int(hour_text), minute=int(minute_text))
        except (AttributeError, TypeError, ValueError):
            return fallback

    def _utc_now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalise(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip().casefold()

    @staticmethod
    def _event_dict(event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            return dict(event)
        method = getattr(event, "as_dict", None)
        if callable(method):
            value = method()
            if isinstance(value, dict):
                return value
        try:
            return asdict(event)
        except (TypeError, ValueError):
            return {
                key: getattr(event, key)
                for key in (
                    "event_id",
                    "occurred_at",
                    "entity_id",
                    "event_type",
                    "summary",
                    "importance",
                    "area_id",
                    "area_name",
                    "person_key",
                    "payload",
                )
                if hasattr(event, key)
            }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS proactive_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proactive_alerts (
                    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    house_event_id INTEGER,
                    event_type TEXT NOT NULL,
                    entity_id TEXT,
                    summary TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    severity TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    recipient TEXT,
                    channels_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT,
                    snoozed_until TEXT,
                    next_escalation_at TEXT,
                    escalation_level INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_proactive_alerts_status
                ON proactive_alerts (status, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_proactive_alerts_dedupe
                ON proactive_alerts (dedupe_key, created_at DESC);

                CREATE TABLE IF NOT EXISTS proactive_conditions (
                    condition_key TEXT PRIMARY KEY,
                    condition_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    source_event_id INTEGER,
                    summary TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proactive_suppressions (
                    scope_key TEXT PRIMARY KEY,
                    until_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proactive_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_proactive_audit_time
                ON proactive_audit (created_at DESC, audit_id DESC);
                """
            )

    def _meta_get_sync(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM proactive_meta WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else None

    def _meta_set_sync(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def _audit_sync(
        self,
        action: str,
        *,
        alert_id: int | None = None,
        actor: str = "orchestrator",
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_audit (
                    alert_id, action, actor, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    action,
                    actor,
                    json.dumps(
                        details or {}, ensure_ascii=False, separators=(",", ":"), default=str
                    ),
                    self._iso(self._utc_now()),
                ),
            )

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        if self._last_event_id == 0 and not self.process_existing_events:
            events = await self.awareness.recent_events(minutes=43200, limit=1)
            if events:
                event_id = self._event_dict(events[0]).get("event_id")
                if isinstance(event_id, int):
                    self._last_event_id = event_id
                    await asyncio.to_thread(
                        self._meta_set_sync,
                        "last_event_id",
                        str(event_id),
                    )
        self._stop_event.clear()
        self._running = True
        self._task = asyncio.create_task(self._run(), name="jarvis_proactive_orchestrator")
        logger.info(
            "Proactive Orchestrator started target=%s quiet=%s-%s timezone=%s",
            self.announcement_target,
            self._quiet_start.strftime("%H:%M"),
            self._quiet_end.strftime("%H:%M"),
            self.timezone_name,
        )

    async def stop(self) -> None:
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
        logger.info("Proactive Orchestrator stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Proactive Orchestrator cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def process_once(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            events = await self.awareness.recent_events(
                minutes=43200,
                limit=500,
                min_importance=0,
            )
            ordered = sorted(
                (self._event_dict(event) for event in events),
                key=lambda item: int(item.get("event_id") or 0),
            )
            for event in ordered:
                event_id = int(event.get("event_id") or 0)
                if event_id <= self._last_event_id:
                    continue
                await self._handle_event(event)
                self._last_event_id = event_id
                await asyncio.to_thread(
                    self._meta_set_sync,
                    "last_event_id",
                    str(event_id),
                )

            await self._process_due_conditions()
            await self._process_snoozed_alerts()
            await self._process_escalations()
            await self._scan_camera_health_if_due()
            self._last_cycle_at = self._iso(self._utc_now())

    async def _presence(self, *, force: bool = False) -> set[str]:
        now = self._utc_now()
        if (
            not force
            and self._presence_cache_at is not None
            and (now - self._presence_cache_at).total_seconds() < 10
        ):
            return set(self._presence_cache)

        states = await self.tools.readable_entity_states(refresh=True)
        home: set[str] = set()
        for entity in states:
            if str(entity.get("domain") or "") != "person":
                continue
            if str(entity.get("state") or "").casefold() != "home":
                continue
            combined = self._normalise(
                " ".join(
                    str(value)
                    for value in (
                        entity.get("entity_id"),
                        entity.get("name"),
                        entity.get("friendly_name"),
                    )
                    if value
                )
            )
            if "amber" in combined:
                home.add("amber")
            elif "aaron" in combined:
                home.add("aaron")
            else:
                home.add(combined or "someone")
        self._presence_cache = home
        self._presence_cache_at = now
        return set(home)

    def _is_quiet(self, now: datetime | None = None) -> bool:
        local_now = (now or self._utc_now()).astimezone(self._timezone)
        current = local_now.time().replace(tzinfo=None)
        if self._quiet_start == self._quiet_end:
            return False
        if self._quiet_start < self._quiet_end:
            return self._quiet_start <= current < self._quiet_end
        return current >= self._quiet_start or current < self._quiet_end

    def _quiet_end_utc(self, now: datetime | None = None) -> datetime:
        local_now = (now or self._utc_now()).astimezone(self._timezone)
        target = local_now.replace(
            hour=self._quiet_end.hour,
            minute=self._quiet_end.minute,
            second=0,
            microsecond=0,
        )
        if target <= local_now:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    @staticmethod
    def _recipient_for_event(event: dict[str, Any]) -> str:
        combined = ProactiveOrchestrator._normalise(
            " ".join(
                str(value)
                for value in (
                    event.get("entity_id"),
                    event.get("summary"),
                    event.get("person_key"),
                    (event.get("payload") or {}).get("name")
                    if isinstance(event.get("payload"), dict)
                    else None,
                )
                if value
            )
        )
        if "amber" in combined:
            return "amber"
        return "aaron"

    async def _route(self, event: dict[str, Any], severity: str) -> RoutingDecision:
        event_type = str(event.get("event_type") or "")
        home = await self._presence()
        quiet = self._is_quiet()
        someone_home = bool(home)

        if severity == "critical":
            return RoutingDecision(
                recipient="both",
                announce=someone_home,
                hold_until=None,
                reason="Critical alerts bypass quiet hours and use every available channel.",
                severity=severity,
            )

        if event_type == "battery_low":
            return RoutingDecision(
                recipient=self._recipient_for_event(event),
                announce=False,
                hold_until=None,
                reason="Low-battery alerts are personal mobile notifications.",
                severity=severity,
            )

        if event_type == "person_arrived":
            if quiet:
                return RoutingDecision(
                    recipient=None,
                    announce=False,
                    hold_until=None,
                    reason="Arrival announcements are suppressed during quiet hours.",
                    severity=severity,
                )
            return RoutingDecision(
                recipient=None,
                announce=someone_home,
                hold_until=None,
                reason="Arrival is announced only when somebody is home to hear it.",
                severity=severity,
            )

        if event_type == "washing_finished":
            if someone_home and not quiet:
                return RoutingDecision(
                    recipient=None,
                    announce=True,
                    hold_until=None,
                    reason="The washing-machine completion can be announced at home.",
                    severity=severity,
                )
            return RoutingDecision(
                recipient="aaron",
                announce=False,
                hold_until=None,
                reason="Nobody can hear the announcement, so Aaron receives a phone notification.",
                severity=severity,
            )

        if severity == "high":
            if quiet and event_type not in {"safety_cleared"}:
                return RoutingDecision(
                    recipient="aaron",
                    announce=False,
                    hold_until=None,
                    reason="High-priority events use mobile delivery during quiet hours.",
                    severity=severity,
                )
            if someone_home:
                return RoutingDecision(
                    recipient=None,
                    announce=True,
                    hold_until=None,
                    reason="A high-priority alert is announced because somebody is home.",
                    severity=severity,
                )
            return RoutingDecision(
                recipient="aaron",
                announce=False,
                hold_until=None,
                reason="A high-priority alert is sent to Aaron because nobody is home.",
                severity=severity,
            )

        if quiet:
            return RoutingDecision(
                recipient=None,
                announce=False,
                hold_until=self._iso(self._quiet_end_utc()),
                reason="Normal-priority alert held until quiet hours end.",
                severity=severity,
            )
        if someone_home:
            return RoutingDecision(
                recipient=None,
                announce=True,
                hold_until=None,
                reason="Normal-priority alert announced because somebody is home.",
                severity=severity,
            )
        return RoutingDecision(
            recipient="aaron",
            announce=False,
            hold_until=None,
            reason="Normal-priority alert sent to Aaron because nobody is home.",
            severity=severity,
        )

    @classmethod
    def _severity(cls, event_type: str, importance: int) -> str:
        if event_type in cls.CRITICAL_EVENT_TYPES or importance >= 95:
            return "critical"
        if event_type in cls.HIGH_EVENT_TYPES or importance >= 80:
            return "high"
        return "normal"

    @classmethod
    def _is_persistent_event(cls, event_type: str) -> bool:
        return event_type in cls.PERSISTENT_EVENT_TYPES

    @classmethod
    def _is_actionable_alert(cls, alert: dict[str, Any]) -> bool:
        status = str(alert.get("status") or "")
        if status in {"pending", "snoozed"}:
            return True
        return (
            status == "delivered"
            and alert.get("acknowledged_at") is None
            and cls._is_persistent_event(str(alert.get("event_type") or ""))
        )

    @classmethod
    def _event_descriptor(cls, event: dict[str, Any]) -> str:
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        return cls._normalise(
            " ".join(
                str(value or "")
                for value in (
                    event.get("entity_id"),
                    event.get("summary"),
                    payload.get("name"),
                    payload.get("device_name"),
                )
            )
        )

    def _trusted_away_occupancy(self, event: dict[str, Any]) -> bool:
        entity_id = str(event.get("entity_id") or "").strip()
        if entity_id and entity_id in self.security_occupancy_entities:
            return True

        descriptor = self._event_descriptor(event)
        if any(
            re.search(rf"\b{re.escape(token)}\b", descriptor)
            for token in self.AWAY_OCCUPANCY_EXCLUDED_TOKENS
        ):
            return False

        # Generic Frigate object/zone occupancy entities are not security alarms.
        # Only explicit person, presence or motion entities are trusted by default.
        return bool(re.search(r"\b(?:person|presence|motion)\b", descriptor))

    @classmethod
    def _clean_devices_left_on_summary(cls, summary: str) -> str | None:
        clean = summary.strip()
        marker = "these are still on:"
        lower = clean.casefold()
        index = lower.find(marker)
        if index < 0:
            return None
        tail = clean[index + len(marker) :].strip().rstrip(".")
        tail = re.sub(r",?\s+and\s+", ", ", tail, flags=re.I)
        names = [item.strip() for item in tail.split(",") if item.strip()]
        filtered = [
            name
            for name in names
            if not any(pattern in name.casefold() for pattern in cls.INFRASTRUCTURE_DEVICE_PATTERNS)
        ]
        if not filtered:
            return None
        if len(filtered) == 1:
            joined = filtered[0]
        elif len(filtered) == 2:
            joined = f"{filtered[0]} and {filtered[1]}"
        else:
            joined = f"{', '.join(filtered[:-1])}, and {filtered[-1]}"
        return f"Nobody is home, but these are still on: {joined}."

    async def _entity_state(self, entity_id: str) -> str | None:
        if not entity_id:
            return None
        try:
            result = await self.tools.get_entity_state(entity_id)
        except Exception:
            logger.exception("Could not verify proactive entity state entity=%s", entity_id)
            return None
        if not result.get("success"):
            return None
        entity = result.get("entity") or {}
        return str(entity.get("state") or "").casefold() or None

    async def _alert_still_active(self, alert: dict[str, Any]) -> bool:
        event_type = str(alert.get("event_type") or "")
        entity_id = str(alert.get("entity_id") or "")
        if event_type == "occupancy_while_away":
            if await self._presence(force=True):
                return False
            state = await self._entity_state(entity_id)
            return state in {"on", "open", "occupied", "detected", "motion"}
        if event_type == "opening_open_long":
            return await self._entity_state(entity_id) in {"on", "open"}
        if event_type == "camera_offline":
            return await self._entity_state(entity_id) in {"unavailable", "unknown", "offline"}
        if event_type == "safety_alert":
            state = await self._entity_state(entity_id)
            if state is None:
                return True
            return state not in {"off", "safe", "clear", "cleared", "normal", "idle"}
        if event_type == "battery_low":
            state = await self._entity_state(entity_id)
            if state is None:
                return True
            try:
                return float(state) <= 20
            except ValueError:
                return state not in {"off", "charging", "normal", "ok"}
        return False

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_id = int(event.get("event_id") or 0)
        event_type = str(event.get("event_type") or "")
        entity_id = str(event.get("entity_id") or "")
        if not event_id or not event_type:
            return

        if event_type == "safety_cleared":
            await asyncio.to_thread(
                self._resolve_alerts_sync,
                f"safety_alert:{entity_id}",
                "The safety condition cleared.",
            )

        if event_type == "person_arrived":
            await asyncio.to_thread(
                self._resolve_event_type_sync,
                "occupancy_while_away",
                "Someone arrived home.",
            )
            await asyncio.to_thread(
                self._resolve_event_type_sync,
                "devices_left_on",
                "Someone arrived home.",
            )

        if event_type == "occupancy_cleared":
            await asyncio.to_thread(
                self._resolve_alerts_sync,
                f"occupancy_while_away:{entity_id}",
                "Occupancy cleared.",
            )
            return

        if event_type == "opening_opened":
            await self._schedule_condition(
                condition_key=f"opening:{entity_id}",
                condition_type="opening_open_long",
                entity_id=entity_id,
                source_event_id=event_id,
                summary=f"{str(event.get('summary') or 'An opening was opened').rstrip('.')} and is still open.",
                due_at=self._utc_now() + timedelta(seconds=self.opening_delay_seconds),
                payload={"event": event},
            )
            return

        if event_type == "opening_closed":
            await asyncio.to_thread(self._delete_condition_sync, f"opening:{entity_id}")
            await asyncio.to_thread(
                self._resolve_alerts_sync,
                f"opening_open_long:{entity_id}",
                "The opening closed.",
            )
            return

        if event_type == "occupancy_detected":
            home = await self._presence(force=True)
            if home:
                return
            if not self._trusted_away_occupancy(event):
                await asyncio.to_thread(
                    self._audit_sync,
                    "untrusted_occupancy_ignored",
                    details={
                        "event_id": event_id,
                        "entity_id": entity_id,
                        "descriptor": self._event_descriptor(event),
                    },
                )
                return
            event = dict(event)
            event["event_type"] = "occupancy_while_away"
            place = event.get("area_name") or event.get("summary") or "the flat"
            event["summary"] = f"Movement was detected in {place} while nobody is home."
            event["importance"] = 100

        if event_type == "person_left":
            home = await self._presence(force=True)
            if not home:
                summary, _calls = await self.awareness.active_devices_summary()
                cleaned_summary = self._clean_devices_left_on_summary(summary)
                if cleaned_summary:
                    synthetic = dict(event)
                    synthetic["event_type"] = "devices_left_on"
                    synthetic["entity_id"] = "household"
                    synthetic["summary"] = cleaned_summary
                    synthetic["importance"] = 85
                    await self._create_and_route_alert(synthetic, source_suffix="devices-left-on")
            return

        if event_type not in self.IMMEDIATE_EVENT_TYPES and event_type not in {
            "occupancy_while_away",
        }:
            return
        await self._create_and_route_alert(event)

    async def _create_and_route_alert(
        self,
        event: dict[str, Any],
        *,
        source_suffix: str = "event",
    ) -> int | None:
        event_id = int(event.get("event_id") or 0)
        event_type = str(event.get("event_type") or "unknown")
        entity_id = str(event.get("entity_id") or "")
        summary = str(event.get("summary") or "Jarvis detected an important change.").strip()
        importance = max(0, min(int(event.get("importance") or 0), 100))
        severity = self._severity(event_type, importance)
        dedupe_key = f"{event_type}:{entity_id}"
        source_key = f"house:{event_id}:{source_suffix}:{event_type}"

        if await asyncio.to_thread(self._active_dedupe_sync, dedupe_key):
            await asyncio.to_thread(
                self._audit_sync,
                "active_duplicate_skipped",
                details={"source_key": source_key, "dedupe_key": dedupe_key},
            )
            return None

        if severity != "critical" and await asyncio.to_thread(
            self._suppressed_sync,
            event_type,
            entity_id,
        ):
            alert_id = await asyncio.to_thread(
                self._insert_alert_sync,
                source_key,
                event_id or None,
                event_type,
                entity_id or None,
                summary,
                importance,
                severity,
                dedupe_key,
                "suppressed",
                None,
                [],
                "Suppressed by an active user rule.",
                event,
            )
            if alert_id:
                await asyncio.to_thread(
                    self._audit_sync,
                    "suppressed",
                    alert_id=alert_id,
                    details={"event_type": event_type, "entity_id": entity_id},
                )
            return alert_id

        duplicate = await asyncio.to_thread(
            self._recent_duplicate_sync,
            dedupe_key,
            self._iso(self._utc_now() - timedelta(seconds=self.duplicate_cooldown_seconds)),
        )
        if duplicate and severity != "critical":
            await asyncio.to_thread(
                self._audit_sync,
                "duplicate_skipped",
                alert_id=int(duplicate["alert_id"]),
                details={"source_key": source_key},
            )
            return None

        decision = await self._route(event, severity)
        initial_status = "snoozed" if decision.hold_until else "pending"
        alert_id = await asyncio.to_thread(
            self._insert_alert_sync,
            source_key,
            event_id or None,
            event_type,
            entity_id or None,
            summary,
            importance,
            severity,
            dedupe_key,
            initial_status,
            decision.recipient,
            [],
            decision.reason,
            event,
            decision.hold_until,
        )
        if not alert_id:
            return None

        await asyncio.to_thread(
            self._audit_sync,
            "alert_created",
            alert_id=alert_id,
            details={"routing": asdict(decision)},
        )
        if decision.hold_until:
            return alert_id
        if not decision.announce and not decision.recipient:
            await asyncio.to_thread(
                self._update_alert_delivery_sync,
                alert_id,
                "suppressed",
                [],
                None,
                None,
                decision.reason,
            )
            return alert_id
        await self._deliver(alert_id, decision)
        return alert_id

    def _insert_alert_sync(
        self,
        source_key: str,
        house_event_id: int | None,
        event_type: str,
        entity_id: str | None,
        summary: str,
        importance: int,
        severity: str,
        dedupe_key: str,
        status: str,
        recipient: str | None,
        channels: list[dict[str, Any]],
        reason: str,
        payload: dict[str, Any],
        snoozed_until: str | None = None,
    ) -> int | None:
        now = self._iso(self._utc_now())
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO proactive_alerts (
                        source_key, house_event_id, event_type, entity_id, summary,
                        importance, severity, dedupe_key, status, recipient,
                        channels_json, reason, created_at, snoozed_until,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_key,
                        house_event_id,
                        event_type,
                        entity_id,
                        summary,
                        importance,
                        severity,
                        dedupe_key,
                        status,
                        recipient,
                        json.dumps(channels, separators=(",", ":"), default=str),
                        reason,
                        now,
                        snoozed_until,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT alert_id FROM proactive_alerts WHERE source_key = ?",
                    (source_key,),
                ).fetchone()
                return int(row["alert_id"]) if row else None
            if cursor.lastrowid is None:
                raise RuntimeError("Proactive alert insert returned no identifier")
            return int(cursor.lastrowid)

    def _get_alert_sync(self, alert_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_alerts WHERE alert_id = ?",
                (int(alert_id),),
            ).fetchone()
        return self._row_to_alert(row) if row else None

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("channels_json", "channels"),
            ("payload_json", "payload"),
        ):
            try:
                item[target] = json.loads(str(item.pop(source)))
            except (json.JSONDecodeError, TypeError):
                item[target] = [] if target == "channels" else {}
        return item

    def _update_alert_delivery_sync(
        self,
        alert_id: int,
        status: str,
        channels: list[dict[str, Any]],
        delivered_at: str | None,
        next_escalation_at: str | None,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE proactive_alerts
                SET status = ?, channels_json = ?, delivered_at = ?,
                    next_escalation_at = ?, snoozed_until = NULL, reason = ?
                WHERE alert_id = ?
                """,
                (
                    status,
                    json.dumps(channels, ensure_ascii=False, separators=(",", ":"), default=str),
                    delivered_at,
                    next_escalation_at,
                    reason,
                    int(alert_id),
                ),
            )

    async def _deliver(self, alert_id: int, decision: RoutingDecision) -> None:
        alert = await asyncio.to_thread(self._get_alert_sync, alert_id)
        if not alert:
            return
        channels: list[dict[str, Any]] = []
        errors: list[str] = []

        if decision.announce:
            try:
                result = await self.tools.announce_message(
                    target=self.announcement_target,
                    message=str(alert["summary"]),
                )
                channels.append({"channel": "announcement", "result": result})
            except Exception as exc:
                errors.append(f"announcement: {exc}")
                logger.exception("Proactive announcement failed alert_id=%s", alert_id)

        if decision.recipient:
            try:
                title = {
                    "critical": "Jarvis — Critical alert",
                    "high": "Jarvis — Important alert",
                    "normal": "Jarvis",
                }.get(str(alert["severity"]), "Jarvis")
                result = await self.tools.send_mobile_notification(
                    recipient=decision.recipient,
                    message=str(alert["summary"]),
                    title=title,
                )
                channels.append({"channel": "mobile", "result": result})
            except Exception as exc:
                errors.append(f"mobile: {exc}")
                logger.exception("Proactive mobile delivery failed alert_id=%s", alert_id)

        delivered = bool(channels)
        now = self._utc_now()
        next_escalation: str | None = None
        if (
            delivered
            and self._is_persistent_event(str(alert.get("event_type") or ""))
            and str(alert["severity"]) in {"high", "critical"}
            and self.max_escalations > 0
        ):
            delay = (
                min(60, self.escalation_seconds)
                if str(alert["severity"]) == "critical"
                else self.escalation_seconds
            )
            next_escalation = self._iso(now + timedelta(seconds=delay))
        status = "delivered" if delivered else "failed"
        reason = str(alert["reason"])
        if errors:
            reason = f"{reason} Delivery errors: {'; '.join(errors)}"
        await asyncio.to_thread(
            self._update_alert_delivery_sync,
            alert_id,
            status,
            channels,
            self._iso(now) if delivered else None,
            next_escalation,
            reason,
        )
        await asyncio.to_thread(
            self._audit_sync,
            "delivered" if delivered else "delivery_failed",
            alert_id=alert_id,
            details={"channels": channels, "errors": errors},
        )
        house_event_id = alert.get("house_event_id")
        if delivered and isinstance(house_event_id, int):
            try:
                await self.awareness.mark_proactive_delivered(house_event_id)
            except Exception:
                logger.exception(
                    "Could not mark House Awareness event delivered id=%s", house_event_id
                )

    async def _schedule_condition(
        self,
        *,
        condition_key: str,
        condition_type: str,
        entity_id: str,
        source_event_id: int | None,
        summary: str,
        due_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._upsert_condition_sync,
            condition_key,
            condition_type,
            entity_id,
            source_event_id,
            summary,
            self._iso(due_at),
            payload,
        )

    def _upsert_condition_sync(
        self,
        condition_key: str,
        condition_type: str,
        entity_id: str,
        source_event_id: int | None,
        summary: str,
        due_at: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_conditions (
                    condition_key, condition_type, entity_id, source_event_id,
                    summary, due_at, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_key) DO UPDATE SET
                    condition_type = excluded.condition_type,
                    source_event_id = excluded.source_event_id,
                    summary = excluded.summary,
                    due_at = CASE
                        WHEN proactive_conditions.condition_type = 'camera_offline'
                        THEN proactive_conditions.due_at
                        ELSE excluded.due_at
                    END,
                    payload_json = excluded.payload_json
                """,
                (
                    condition_key,
                    condition_type,
                    entity_id,
                    source_event_id,
                    summary,
                    due_at,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                    self._iso(self._utc_now()),
                ),
            )

    def _delete_condition_sync(self, condition_key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM proactive_conditions WHERE condition_key = ?",
                (condition_key,),
            )

    def _due_conditions_sync(self, now_iso: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_conditions WHERE due_at <= ? ORDER BY due_at",
                (now_iso,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json")))
            except (json.JSONDecodeError, TypeError):
                item["payload"] = {}
            items.append(item)
        return items

    async def _process_due_conditions(self) -> None:
        conditions = await asyncio.to_thread(
            self._due_conditions_sync,
            self._iso(self._utc_now()),
        )
        for condition in conditions:
            entity_id = str(condition["entity_id"])
            condition_type = str(condition["condition_type"])
            if condition_type in {"opening_open_long", "camera_offline"}:
                try:
                    state_result = await self.tools.get_entity_state(entity_id)
                    entity = state_result.get("entity") or {}
                    state = str(entity.get("state") or "unknown").casefold()
                except Exception:
                    state = "unknown"
                active = (
                    state in {"on", "open", "opening"}
                    if condition_type == "opening_open_long"
                    else state in {"unavailable", "unknown", ""}
                )
                if not active:
                    await asyncio.to_thread(
                        self._delete_condition_sync,
                        str(condition["condition_key"]),
                    )
                    continue

            source_event_id = int(condition.get("source_event_id") or 0)
            synthetic = {
                "event_id": source_event_id,
                "event_type": condition_type,
                "entity_id": entity_id,
                "summary": str(condition["summary"]),
                "importance": 90 if condition_type == "opening_open_long" else 85,
                "payload": condition.get("payload") or {},
            }
            await self._create_and_route_alert(
                synthetic, source_suffix=str(condition["condition_key"])
            )
            await asyncio.to_thread(
                self._delete_condition_sync,
                str(condition["condition_key"]),
            )

    async def _scan_camera_health_if_due(self) -> None:
        now = self._utc_now()
        if (
            self._last_camera_scan_at is not None
            and (now - self._last_camera_scan_at).total_seconds() < self.camera_scan_seconds
        ):
            return
        self._last_camera_scan_at = now
        states = await self.tools.readable_entity_states(refresh=True)
        cameras = [item for item in states if str(item.get("domain") or "") == "camera"]
        for camera in cameras:
            entity_id = str(camera.get("entity_id") or "")
            if not entity_id:
                continue
            state = str(camera.get("state") or "unknown").casefold()
            key = f"camera:{entity_id}"
            name = str(camera.get("name") or camera.get("friendly_name") or entity_id)
            if state in {"unavailable", "unknown", ""}:
                active_alert = await asyncio.to_thread(
                    self._active_dedupe_sync,
                    f"camera_offline:{entity_id}",
                )
                if active_alert:
                    continue
                await self._schedule_condition(
                    condition_key=key,
                    condition_type="camera_offline",
                    entity_id=entity_id,
                    source_event_id=None,
                    summary=f"{name} has been offline for at least {max(1, round(self.camera_offline_seconds / 60))} minutes.",
                    due_at=now + timedelta(seconds=self.camera_offline_seconds),
                    payload={"camera": camera},
                )
            else:
                await asyncio.to_thread(self._delete_condition_sync, key)
                await asyncio.to_thread(
                    self._resolve_alerts_sync,
                    f"camera_offline:{entity_id}",
                    "The camera is available again.",
                )

    def _snoozed_due_sync(self, now_iso: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proactive_alerts
                WHERE status = 'snoozed' AND snoozed_until IS NOT NULL
                  AND snoozed_until <= ?
                ORDER BY snoozed_until
                """,
                (now_iso,),
            ).fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def _process_snoozed_alerts(self) -> None:
        alerts = await asyncio.to_thread(
            self._snoozed_due_sync,
            self._iso(self._utc_now()),
        )
        for alert in alerts:
            event = dict(alert.get("payload") or {})
            event.setdefault("event_type", alert["event_type"])
            event.setdefault("entity_id", alert.get("entity_id"))
            event.setdefault("summary", alert["summary"])
            event.setdefault("importance", alert["importance"])
            decision = await self._route(event, str(alert["severity"]))
            if decision.hold_until:
                await asyncio.to_thread(
                    self._set_snooze_sync,
                    int(alert["alert_id"]),
                    decision.hold_until,
                    "Quiet hours still active.",
                )
                continue
            await self._deliver(int(alert["alert_id"]), decision)

    def _escalations_due_sync(self, now_iso: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proactive_alerts
                WHERE status = 'delivered'
                  AND acknowledged_at IS NULL
                  AND next_escalation_at IS NOT NULL
                  AND next_escalation_at <= ?
                  AND escalation_level < ?
                ORDER BY next_escalation_at
                """,
                (now_iso, self.max_escalations),
            ).fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def _process_escalations(self) -> None:
        alerts = await asyncio.to_thread(
            self._escalations_due_sync,
            self._iso(self._utc_now()),
        )
        for alert in alerts:
            if not await self._alert_still_active(alert):
                await asyncio.to_thread(
                    self._resolve_alerts_sync,
                    str(alert["dedupe_key"]),
                    "The triggering condition is no longer active.",
                )
                continue
            level = int(alert["escalation_level"]) + 1
            recipient = "both" if level >= 2 or str(alert["severity"]) == "critical" else "aaron"
            try:
                result = await self.tools.send_mobile_notification(
                    recipient=recipient,
                    message=f"Still unresolved: {alert['summary']}",
                    title="Jarvis — Alert still active",
                )
                success = bool(result.get("success"))
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
                success = False
                logger.exception("Proactive escalation failed alert_id=%s", alert["alert_id"])
            next_at = None
            if success and level < self.max_escalations:
                next_at = self._iso(self._utc_now() + timedelta(seconds=self.escalation_seconds))
            await asyncio.to_thread(
                self._update_escalation_sync,
                int(alert["alert_id"]),
                level,
                next_at,
                result,
            )

    def _update_escalation_sync(
        self,
        alert_id: int,
        level: int,
        next_at: str | None,
        result: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT channels_json FROM proactive_alerts WHERE alert_id = ?",
                (alert_id,),
            ).fetchone()
            channels: list[Any] = []
            if row:
                try:
                    channels = json.loads(str(row["channels_json"]))
                except (json.JSONDecodeError, TypeError):
                    channels = []
            channels.append({"channel": "escalation", "level": level, "result": result})
            connection.execute(
                """
                UPDATE proactive_alerts
                SET escalation_level = ?, next_escalation_at = ?, channels_json = ?
                WHERE alert_id = ?
                """,
                (
                    level,
                    next_at,
                    json.dumps(channels, ensure_ascii=False, separators=(",", ":"), default=str),
                    alert_id,
                ),
            )
        self._audit_sync(
            "escalated",
            alert_id=alert_id,
            details={"level": level, "next_at": next_at, "result": result},
        )

    def _active_dedupe_sync(self, dedupe_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM proactive_alerts
                WHERE dedupe_key = ?
                  AND status IN ('pending', 'delivered', 'snoozed')
                  AND acknowledged_at IS NULL
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return row is not None

    def _recent_duplicate_sync(self, dedupe_key: str, since: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT alert_id, status, created_at FROM proactive_alerts
                WHERE dedupe_key = ? AND created_at >= ?
                  AND status NOT IN ('resolved', 'suppressed', 'failed')
                ORDER BY created_at DESC LIMIT 1
                """,
                (dedupe_key, since),
            ).fetchone()
        return dict(row) if row else None

    def _suppressed_sync(self, event_type: str, entity_id: str) -> bool:
        now_iso = self._iso(self._utc_now())
        keys = [f"event:{event_type}"]
        if entity_id:
            keys.append(f"entity:{entity_id}")
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM proactive_suppressions WHERE until_at <= ?",
                (now_iso,),
            )
            row = connection.execute(
                f"SELECT 1 FROM proactive_suppressions WHERE scope_key IN ({placeholders}) AND until_at > ? LIMIT 1",
                [*keys, now_iso],
            ).fetchone()
        return row is not None

    def _set_suppression_sync(
        self,
        scope_key: str,
        until_at: str,
        reason: str,
        actor: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_suppressions (
                    scope_key, until_at, reason, actor, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    until_at = excluded.until_at,
                    reason = excluded.reason,
                    actor = excluded.actor,
                    created_at = excluded.created_at
                """,
                (scope_key, until_at, reason, actor, self._iso(self._utc_now())),
            )

    def _resolve_alerts_sync(self, dedupe_key: str, reason: str) -> int:
        now = self._iso(self._utc_now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proactive_alerts
                SET status = 'resolved', acknowledged_at = COALESCE(acknowledged_at, ?),
                    next_escalation_at = NULL, reason = reason || ' ' || ?
                WHERE dedupe_key = ? AND status IN ('pending', 'delivered', 'snoozed')
                """,
                (now, reason, dedupe_key),
            )
            return cursor.rowcount

    def _resolve_event_type_sync(self, event_type: str, reason: str) -> int:
        now = self._iso(self._utc_now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proactive_alerts
                SET status = 'resolved', acknowledged_at = COALESCE(acknowledged_at, ?),
                    next_escalation_at = NULL, reason = reason || ' ' || ?
                WHERE event_type = ? AND status IN ('pending', 'delivered', 'snoozed')
                """,
                (now, reason, event_type),
            )
            return cursor.rowcount

    def _latest_actionable_sync(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM proactive_alerts
                WHERE acknowledged_at IS NULL
                  AND (
                    status IN ('pending', 'snoozed')
                    OR (status = 'delivered' AND event_type IN ('battery_low', 'camera_offline', 'occupancy_while_away', 'opening_open_long', 'safety_alert'))
                  )
                ORDER BY created_at DESC, alert_id DESC LIMIT 1
                """
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def _acknowledge_sync(self, alert_id: int, actor: str) -> bool:
        now = self._iso(self._utc_now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proactive_alerts
                SET status = 'acknowledged', acknowledged_at = ?, acknowledged_by = ?,
                    next_escalation_at = NULL, snoozed_until = NULL
                WHERE alert_id = ? AND acknowledged_at IS NULL
                """,
                (now, actor, int(alert_id)),
            )
        if cursor.rowcount:
            self._audit_sync("acknowledged", alert_id=alert_id, actor=actor)
        return cursor.rowcount > 0

    def _set_snooze_sync(self, alert_id: int, until_at: str, reason: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proactive_alerts
                SET status = 'snoozed', snoozed_until = ?, next_escalation_at = NULL,
                    reason = reason || ' ' || ?
                WHERE alert_id = ?
                """,
                (until_at, reason, int(alert_id)),
            )
        return cursor.rowcount > 0

    async def acknowledge(self, alert_id: int, actor: str) -> bool:
        return await asyncio.to_thread(self._acknowledge_sync, alert_id, actor)

    async def snooze(self, alert_id: int, seconds: int, actor: str) -> bool:
        until = self._iso(self._utc_now() + timedelta(seconds=max(60, seconds)))
        updated = await asyncio.to_thread(
            self._set_snooze_sync,
            alert_id,
            until,
            f"Snoozed by {actor} until {until}.",
        )
        if updated:
            await asyncio.to_thread(
                self._audit_sync,
                "snoozed",
                alert_id=alert_id,
                actor=actor,
                details={"until": until},
            )
        return updated

    async def handle_command(self, text: str, actor: Any) -> ProactiveCommandResult:
        clean = text.strip()
        actor_key = str(
            getattr(actor, "user_key", None) or getattr(actor, "display_name", None) or "user"
        )

        if _ACTIVE_ALERTS_PATTERN.match(clean):
            alerts = [
                alert
                for alert in await self.list_alerts(
                    limit=50,
                    statuses={"pending", "delivered", "snoozed"},
                )
                if self._is_actionable_alert(alert)
            ][:5]
            if not alerts:
                return ProactiveCommandResult(
                    handled=True,
                    response="There are no active proactive alerts.",
                    intent="proactive_status",
                    details={"alerts": []},
                )
            summaries = [str(item["summary"]).rstrip(".") for item in alerts[:3]]
            response = "Active alerts: " + "; ".join(summaries) + "."
            return ProactiveCommandResult(
                handled=True,
                response=response,
                intent="proactive_status",
                details={"alerts": alerts},
            )

        alert = await asyncio.to_thread(self._latest_actionable_sync)
        if alert is None:
            return ProactiveCommandResult(handled=False)

        if _ACK_PATTERN.match(clean):
            await self.acknowledge(int(alert["alert_id"]), actor_key)
            return ProactiveCommandResult(
                handled=True,
                response="Understood. I won’t escalate that alert.",
                intent="proactive_acknowledge",
                details={"alert_id": alert["alert_id"]},
            )

        snooze_match = _SNOOZE_PATTERN.match(clean)
        if snooze_match:
            raw_amount = snooze_match.group("amount").casefold()
            parsed_amount = _NUMBER_WORDS.get(raw_amount)
            if parsed_amount is None:
                parsed_amount = int(raw_amount)
            amount = max(1, min(parsed_amount, 168))
            unit = snooze_match.group("unit").casefold()
            seconds = amount * (3600 if unit.startswith(("hour", "hr")) else 60)
            await self.snooze(int(alert["alert_id"]), seconds, actor_key)
            unit_text = "hour" if seconds >= 3600 and seconds % 3600 == 0 else "minute"
            display_amount = seconds // 3600 if unit_text == "hour" else seconds // 60
            return ProactiveCommandResult(
                handled=True,
                response=f"I’ll remind you again in {display_amount} {unit_text}{'s' if display_amount != 1 else ''}.",
                intent="proactive_snooze",
                details={"alert_id": alert["alert_id"], "seconds": seconds},
            )

        if _SUPPRESS_TONIGHT_PATTERN.match(clean):
            local_now = self._utc_now().astimezone(self._timezone)
            if self._is_quiet():
                until = self._quiet_end_utc()
            else:
                tomorrow = (local_now + timedelta(days=1)).replace(
                    hour=self._quiet_end.hour,
                    minute=self._quiet_end.minute,
                    second=0,
                    microsecond=0,
                )
                until = tomorrow.astimezone(timezone.utc)
            scope = f"event:{alert['event_type']}"
            await asyncio.to_thread(
                self._set_suppression_sync,
                scope,
                self._iso(until),
                "Suppressed for the rest of tonight.",
                actor_key,
            )
            await self.acknowledge(int(alert["alert_id"]), actor_key)
            return ProactiveCommandResult(
                handled=True,
                response="Understood. I won’t repeat that type of alert again tonight.",
                intent="proactive_suppress",
                details={"alert_id": alert["alert_id"], "until": self._iso(until)},
            )

        forward_match = _FORWARD_PATTERN.match(clean)
        if forward_match:
            recipient = forward_match.group("recipient").casefold()
            if recipient == "both of us":
                recipient = "both"
            result = await self.tools.send_mobile_notification(
                recipient=recipient,
                message=str(alert["summary"]),
                title="Jarvis — Forwarded alert",
            )
            await asyncio.to_thread(
                self._audit_sync,
                "forwarded",
                alert_id=int(alert["alert_id"]),
                actor=actor_key,
                details={"recipient": recipient, "result": result},
            )
            recipient_text = (
                "both phones" if recipient == "both" else f"{recipient.title()}’s phone"
            )
            return ProactiveCommandResult(
                handled=True,
                response=f"Sent to {recipient_text}.",
                intent="proactive_forward",
                details={"alert_id": alert["alert_id"], "recipient": recipient},
            )

        return ProactiveCommandResult(handled=False)

    async def status(self) -> dict[str, Any]:
        def counts() -> dict[str, int]:
            with self._connect() as connection:
                result = {
                    "total": int(
                        connection.execute("SELECT COUNT(*) FROM proactive_alerts").fetchone()[0]
                    ),
                    "active": int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM proactive_alerts
                            WHERE acknowledged_at IS NULL
                              AND (
                                status IN ('pending','snoozed')
                                OR (status = 'delivered' AND event_type IN (?,?,?,?,?))
                              )
                            """,
                            tuple(sorted(self.PERSISTENT_EVENT_TYPES)),
                        ).fetchone()[0]
                    ),
                    "conditions": int(
                        connection.execute("SELECT COUNT(*) FROM proactive_conditions").fetchone()[
                            0
                        ]
                    ),
                    "suppressions": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM proactive_suppressions WHERE until_at > ?",
                            (self._iso(self._utc_now()),),
                        ).fetchone()[0]
                    ),
                }
            return result

        values = await asyncio.to_thread(counts)
        return {
            "version": "15.0.1",
            "enabled": self.enabled,
            "running": self._running,
            "last_cycle_at": self._last_cycle_at,
            "last_error": self._last_error,
            "last_event_id": self._last_event_id,
            "announcement_target": self.announcement_target,
            "quiet_hours": {
                "start": self._quiet_start.strftime("%H:%M"),
                "end": self._quiet_end.strftime("%H:%M"),
                "timezone": self.timezone_name,
                "active": self._is_quiet(),
            },
            "poll_seconds": self.poll_seconds,
            "opening_delay_seconds": self.opening_delay_seconds,
            "camera_offline_seconds": self.camera_offline_seconds,
            "escalation_seconds": self.escalation_seconds,
            "max_escalations": self.max_escalations,
            "security_occupancy_allowlist_count": len(self.security_occupancy_entities),
            **values,
            "database": str(self.database_path),
        }

    async def list_alerts(
        self,
        *,
        limit: int = 50,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        def query() -> list[dict[str, Any]]:
            safe_limit = max(1, min(int(limit), 500))
            params: list[Any] = []
            clause = ""
            if statuses:
                values = sorted(str(value) for value in statuses)
                clause = f"WHERE status IN ({','.join('?' for _ in values)})"
                params.extend(values)
            params.append(safe_limit)
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM proactive_alerts {clause} ORDER BY created_at DESC, alert_id DESC LIMIT ?",
                    params,
                ).fetchall()
            return [self._row_to_alert(row) for row in rows]

        return await asyncio.to_thread(query)

    async def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        def query() -> list[dict[str, Any]]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM proactive_audit ORDER BY created_at DESC, audit_id DESC LIMIT ?",
                    (max(1, min(int(limit), 500)),),
                ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    item["details"] = json.loads(str(item.pop("details_json")))
                except (json.JSONDecodeError, TypeError):
                    item["details"] = {}
                items.append(item)
            return items

        return await asyncio.to_thread(query)
