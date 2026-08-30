from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .proactive_policy import (
    battery_transition,
    is_real_oven_entity,
    proactive_notification_tag,
    proactive_speech_allowed,
    safety_kind,
)


logger = logging.getLogger("jarvis-core.proactive")
router = APIRouter(prefix="/api/proactive", tags=["proactive"])
LONDON = ZoneInfo("Europe/London")

CATEGORIES = (
    "security",
    "cameras",
    "appliances",
    "energy",
    "batteries",
    "presence",
    "system",
)
SAFE_TURN_OFF = {"light", "switch", "fan", "media_player"}
BLOCKED_CONTROL = {"lock", "alarm_control_panel", "cover", "siren"}


def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def friendly(state: dict[str, Any]) -> str:
    attributes = state.get("attributes") or {}
    name = str(attributes.get("friendly_name") or "").strip()
    if name:
        return name
    return str(state.get("entity_id") or "Unknown").split(".", 1)[-1].replace("_", " ").title()


def number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalise_user(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    return cleaned if cleaned in {"aaron", "amber", "all"} else "aaron"


@dataclass(frozen=True)
class Candidate:
    category: str
    kind: str
    entity_id: str
    title: str
    message: str
    reason: str
    importance: int
    target_user: str = "all"
    actions: tuple[str, ...] = ("dismiss", "remind_later")
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()
    room: str = ""

    @property
    def fingerprint(self) -> str:
        raw = f"{self.category}|{self.kind}|{self.entity_id}|{self.target_user}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Rules:
    def __init__(
        self,
        door_seconds: int = 600,
        oven_seconds: int = 1800,
        high_power_w: float = 3000.0,
    ) -> None:
        self.door_seconds = max(60, door_seconds)
        self.oven_seconds = max(300, oven_seconds)
        self.high_power_w = max(250.0, high_power_w)
        self.battery_low_percent = float(
            env(
                "JARVIS_PROACTIVE_BATTERY_LOW_PERCENT",
                default="15",
            )
        )
        self.battery_critical_percent = float(
            env(
                "JARVIS_PROACTIVE_BATTERY_CRITICAL_PERCENT",
                default="5",
            )
        )
        self.oven_entities = {
            item.strip()
            for item in env(
                "JARVIS_PROACTIVE_OVEN_ENTITIES",
                default="",
            ).split(",")
            if item.strip()
        }

    def evaluate(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        *,
        first_seen: int,
        now: int,
        presence: dict[str, str],
    ) -> list[Candidate]:
        entity_id = str(current.get("entity_id") or "")
        if not entity_id:
            return []
        entity_domain = domain(entity_id)
        state = str(current.get("state") or "").strip().lower()
        old = str((previous or {}).get("state") or "").strip().lower()
        name = friendly(current)
        lowered = f"{entity_id} {name}".lower()
        age = max(0, now - first_seen)
        away = all(value != "home" for value in presence.values())
        result: list[Candidate] = []

        safety = safety_kind(previous, current)
        if safety:
            title, message = {
                "smoke_detected": (
                    "Smoke detected",
                    f"{name} reports smoke.",
                ),
                "carbon_monoxide_detected": (
                    "Carbon monoxide detected",
                    f"{name} reports carbon monoxide.",
                ),
                "gas_detected": (
                    "Gas detected",
                    f"{name} reports gas.",
                ),
                "water_leak": (
                    "Water leak detected",
                    f"{name} reports moisture or a leak.",
                ),
            }[safety]
            result.append(
                Candidate(
                    "security",
                    safety,
                    entity_id,
                    title,
                    message,
                    f"{entity_id} changed from {old or 'unknown'} to {state}",
                    100,
                    "all",
                    ("dismiss",),
                )
            )

        if entity_domain == "person":
            if state == "home" and old not in {"", "home"}:
                result.append(
                    Candidate(
                        "presence",
                        "arrival",
                        entity_id,
                        "Arrival detected",
                        f"{name} has arrived home.",
                        f"{entity_id} changed from {old or 'unknown'} to home",
                        72,
                        "all",
                        ("dismiss",),
                    )
                )
            return result

        door = entity_domain == "binary_sensor" and any(
            word in lowered for word in ("door", "window", "contact", "opening")
        )
        if door and state in {"on", "open", "true"} and age >= self.door_seconds:
            result.append(
                Candidate(
                    "security",
                    "door_open",
                    entity_id,
                    "Door or window left open",
                    f"{name} has been open for {max(1, age // 60)} minutes.",
                    f"{entity_id} remained {state} for {age} seconds",
                    90 if away else 86,
                )
            )

        person_detected = any(word in lowered for word in ("person", "occupancy")) and any(
            word in lowered for word in ("camera", "front door", "front_door", "frigate", "motion")
        )
        if (
            person_detected
            and state in {"on", "person", "detected", "true"}
            and old not in {"on", "person", "detected", "true"}
        ):
            result.append(
                Candidate(
                    "cameras",
                    "person_detected",
                    entity_id,
                    "Person detected",
                    f"{name} detected a person"
                    + (" while nobody appears to be home." if away else "."),
                    f"{entity_id} changed from {old or 'unknown'} to {state}; everyone_away={away}",
                    98 if away else 82,
                    "all",
                    ("view_camera", "dismiss", "remind_later"),
                )
            )

        appliance = any(
            word in lowered
            for word in ("washing machine", "washing_machine", "washer", "dryer", "dishwasher")
        )
        running = {"on", "running", "washing", "drying", "cleaning"}
        finished = {"off", "idle", "standby", "done", "finished", "complete"}
        if appliance and old in running and state in finished:
            label = (
                "washing machine"
                if "wash" in lowered
                else "dryer"
                if "dryer" in lowered
                else "dishwasher"
            )
            result.append(
                Candidate(
                    "appliances",
                    "cycle_finished",
                    entity_id,
                    "Appliance finished",
                    f"The {label} has finished.",
                    f"{entity_id} changed from {old} to {state}",
                    82,
                )
            )

        oven = is_real_oven_entity(
            current,
            explicit_entities=self.oven_entities,
        )
        if oven and state in {"on", "heating", "preheating"} and age >= self.oven_seconds:
            actions = (
                ("turn_off", "remind_later", "dismiss")
                if entity_domain in SAFE_TURN_OFF
                else ("remind_later", "dismiss")
            )
            result.append(
                Candidate(
                    "appliances",
                    "oven_left_on",
                    entity_id,
                    "Oven still on",
                    f"{name} has remained on for {max(1, age // 60)} minutes.",
                    f"{entity_id} remained {state} for {age} seconds",
                    94,
                    "all",
                    actions,
                )
            )

        battery = battery_transition(
            previous,
            current,
            low_percent=self.battery_low_percent,
            critical_percent=self.battery_critical_percent,
        )
        if battery:
            kind, importance, level = battery
            target = "amber" if "amber" in lowered else "aaron" if "aaron" in lowered else "all"
            result.append(
                Candidate(
                    "batteries",
                    kind,
                    entity_id,
                    ("Battery critically low" if kind == "battery_critical" else "Battery low"),
                    f"{name} is at {int(level)}%.",
                    f"{entity_id} crossed the {int(level)}% battery threshold",
                    importance,
                    target,
                )
            )

        power = entity_domain == "sensor" and any(
            word in lowered for word in ("power", "current consumption", "current_consumption")
        )
        if power:
            watts = number(current.get("state"))
            unit = str((current.get("attributes") or {}).get("unit_of_measurement") or "").lower()
            if watts is not None:
                if unit == "kw":
                    watts *= 1000
                if watts >= self.high_power_w:
                    result.append(
                        Candidate(
                            "energy",
                            "high_power",
                            entity_id,
                            "High energy use",
                            f"{name} is using about {round(watts)} watts.",
                            f"{entity_id} exceeded {self.high_power_w:.0f} W",
                            84,
                        )
                    )

        if (
            state == "unavailable"
            and old not in {"", "unavailable"}
            and entity_domain
            in {"camera", "binary_sensor", "climate", "lock", "alarm_control_panel"}
        ):
            result.append(
                Candidate(
                    "system",
                    "critical_unavailable",
                    entity_id,
                    "Device unavailable",
                    f"{name} has become unavailable.",
                    f"{entity_id} changed from {old} to unavailable",
                    85,
                )
            )
        return result


class SettingsModel(BaseModel):
    user_id: str = "aaron"
    enabled: bool = True
    min_importance: int = Field(80, ge=0, le=100)
    notify_enabled: bool = True
    speak_enabled: bool = False
    quiet_start_hour: int = Field(22, ge=0, le=23)
    quiet_end_hour: int = Field(7, ge=0, le=23)
    categories: dict[str, bool] = Field(default_factory=dict)


class EvaluateModel(BaseModel):
    previous: dict[str, Any] | None = None
    current: dict[str, Any]
    first_seen: int | None = None


class ActionModel(BaseModel):
    action: str
    minutes: int = Field(15, ge=5, le=240)


class ProactiveEngine:
    def __init__(
        self,
        database_path: str,
        *,
        ha_url: str = "",
        ha_token: str = "",
        enabled: bool = True,
        min_importance: int = 80,
        cooldown: int = 300,
        poll_seconds: int = 15,
        speaker_entity: str = "",
    ) -> None:
        self.database_path = Path(database_path)
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.enabled = enabled
        self.min_importance = max(0, min(100, min_importance))
        self.cooldown = max(30, cooldown)
        self.poll_seconds = max(5, poll_seconds)
        self.speaker_entity = speaker_entity.strip()
        self.reply_window_seconds = max(
            5, min(60, int(env("JARVIS_PROACTIVE_REPLY_WINDOW_SECONDS", default="12")))
        )
        self.daily_speech_budget = max(
            1, min(50, int(env("JARVIS_PROACTIVE_DAILY_SPEECH_BUDGET", default="8")))
        )
        self.learning_threshold = max(
            3, min(30, int(env("JARVIS_PROACTIVE_LEARNING_THRESHOLD", default="5")))
        )
        self.speaker_map = self._speaker_map()
        self.rules = Rules(
            int(env("JARVIS_PROACTIVE_DOOR_OPEN_SECONDS", default="600")),
            int(env("JARVIS_PROACTIVE_OVEN_ON_SECONDS", default="1800")),
            float(env("JARVIS_PROACTIVE_HIGH_POWER_W", default="3000")),
        )
        self.targets = {
            "aaron": env(
                "JARVIS_PROACTIVE_NOTIFY_AARON",
                default="notify.mobile_app_aaron_s_phone",
            ),
            "amber": env(
                "JARVIS_PROACTIVE_NOTIFY_AMBER",
                default="notify.mobile_app_amber_phone",
            ),
        }
        self.states: dict[str, dict[str, Any]] = {}
        self.first_seen: dict[str, int] = {}
        self.presence = {"aaron": "unknown", "amber": "unknown"}
        self.task: asyncio.Task | None = None
        self.initialised = False
        self.state_provider: Any = None

    @classmethod
    def from_env(cls) -> "ProactiveEngine":
        return cls(
            env("JARVIS_PROACTIVE_DB_PATH", default="/app/data/jarvis_proactive.db"),
            ha_url=env(
                "HOME_ASSISTANT_URL",
                "HA_BASE_URL",
                "JARVIS_HOME_ASSISTANT_URL",
                "HOME_ASSISTANT_BASE_URL",
            ),
            ha_token=env(
                "HOME_ASSISTANT_TOKEN",
                "HA_TOKEN",
                "JARVIS_HOME_ASSISTANT_TOKEN",
            ),
            enabled=env_bool("JARVIS_PROACTIVE_ENABLED", True),
            min_importance=int(env("JARVIS_PROACTIVE_MIN_IMPORTANCE", default="80")),
            cooldown=int(env("JARVIS_PROACTIVE_COOLDOWN_SECONDS", default="300")),
            poll_seconds=int(env("JARVIS_PROACTIVE_POLL_SECONDS", default="15")),
            speaker_entity=env("JARVIS_PROACTIVE_SPEAKER_ENTITY"),
        )

    def connection(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _speaker_map(self) -> dict[str, str]:
        raw = env("JARVIS_PROACTIVE_ROOM_SPEAKERS", default="")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid JARVIS_PROACTIVE_ROOM_SPEAKERS")
            return {}
        return (
            {
                str(key).strip().lower().replace(" ", "_"): str(value).strip()
                for key, value in parsed.items()
                if str(key).strip() and str(value).strip()
            }
            if isinstance(parsed, dict)
            else {}
        )

    def initialise(self) -> None:
        if self.initialised:
            return
        schema = (
            "PRAGMA journal_mode=WAL;\n"
            "CREATE TABLE IF NOT EXISTS proactive_events (\n"
            " id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,\n"
            " category TEXT NOT NULL, kind TEXT NOT NULL,\n"
            " entity_id TEXT NOT NULL, title TEXT NOT NULL,\n"
            " message TEXT NOT NULL, reason TEXT NOT NULL,\n"
            " importance INTEGER NOT NULL, target_user TEXT NOT NULL,\n"
            " actions_json TEXT NOT NULL, status TEXT NOT NULL,\n"
            " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,\n"
            " notified_at INTEGER, spoken_at INTEGER, snoozed_until INTEGER\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_proactive_events_created "
            "ON proactive_events(created_at DESC);\n"
            "CREATE INDEX IF NOT EXISTS idx_proactive_events_fingerprint "
            "ON proactive_events(fingerprint, created_at DESC);\n"
            "CREATE TABLE IF NOT EXISTS proactive_settings (\n"
            " user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL,\n"
            " min_importance INTEGER NOT NULL, notify_enabled INTEGER NOT NULL,\n"
            " speak_enabled INTEGER NOT NULL, quiet_start_hour INTEGER NOT NULL,\n"
            " quiet_end_hour INTEGER NOT NULL, categories_json TEXT NOT NULL,\n"
            " updated_at INTEGER NOT NULL\n"
            ");\n"
        )
        with self.connection() as connection:
            connection.executescript(schema)
            existing = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(proactive_events)").fetchall()
            }
            additions = {
                "confidence": "REAL NOT NULL DEFAULT 1.0",
                "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
                "decision_json": "TEXT NOT NULL DEFAULT '{}'",
                "room": "TEXT NOT NULL DEFAULT ''",
                "reply_until": "INTEGER",
            }
            for column, definition in additions.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE proactive_events ADD COLUMN {column} {definition}"
                    )
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS proactive_feedback ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,"
                " user_id TEXT NOT NULL, feedback TEXT NOT NULL, created_at INTEGER NOT NULL);"
                "CREATE TABLE IF NOT EXISTS proactive_proposals ("
                " id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,"
                " title TEXT NOT NULL, reason TEXT NOT NULL, evidence_count INTEGER NOT NULL,"
                " confidence REAL NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,"
                " updated_at INTEGER NOT NULL);"
                "CREATE TABLE IF NOT EXISTS initiative_suppressions ("
                " fingerprint TEXT PRIMARY KEY, reason TEXT NOT NULL,"
                " created_at INTEGER NOT NULL);"
            )
        self.initialised = True

    async def start(self) -> None:
        self.initialise()
        if self.task and not self.task.done():
            return
        if not self.enabled or not self.ha_url or not self.ha_token:
            logger.info(
                "Proactive engine ready without poller: enabled=%s ha=%s token=%s",
                self.enabled,
                bool(self.ha_url),
                bool(self.ha_token),
            )
            return
        self.task = asyncio.create_task(self.poll_loop())

    async def stop(self) -> None:
        if not self.task:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    def default_settings(self, user: str) -> dict[str, Any]:
        return {
            "user_id": normalise_user(user),
            "enabled": self.enabled,
            "min_importance": self.min_importance,
            "notify_enabled": True,
            "speak_enabled": False,
            "quiet_start_hour": 22,
            "quiet_end_hour": 7,
            "categories": {category: True for category in CATEGORIES},
        }

    def settings(self, user: str) -> dict[str, Any]:
        self.initialise()
        user = normalise_user(user)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_settings WHERE user_id = ?",
                (user,),
            ).fetchone()
        if row is None:
            return self.default_settings(user)
        categories = self.default_settings(user)["categories"]
        try:
            stored_categories = json.loads(row["categories_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Ignoring invalid proactive categories for %s: %s",
                user,
                exc,
            )
        else:
            if isinstance(stored_categories, dict):
                categories.update(
                    {
                        key: bool(value)
                        for key, value in stored_categories.items()
                        if key in CATEGORIES
                    }
                )
            else:
                logger.warning(
                    "Ignoring non-object proactive categories for %s",
                    user,
                )
        return {
            "user_id": user,
            "enabled": bool(row["enabled"]),
            "min_importance": int(row["min_importance"]),
            "notify_enabled": bool(row["notify_enabled"]),
            "speak_enabled": bool(row["speak_enabled"]),
            "quiet_start_hour": int(row["quiet_start_hour"]),
            "quiet_end_hour": int(row["quiet_end_hour"]),
            "categories": categories,
        }

    def save_settings(self, model: SettingsModel) -> dict[str, Any]:
        self.initialise()
        user = normalise_user(model.user_id)
        categories = self.default_settings(user)["categories"]
        for key, value in model.categories.items():
            if key in CATEGORIES:
                categories[key] = bool(value)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO proactive_settings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "enabled=excluded.enabled, min_importance=excluded.min_importance, "
                "notify_enabled=excluded.notify_enabled, "
                "speak_enabled=excluded.speak_enabled, "
                "quiet_start_hour=excluded.quiet_start_hour, "
                "quiet_end_hour=excluded.quiet_end_hour, "
                "categories_json=excluded.categories_json, "
                "updated_at=excluded.updated_at",
                (
                    user,
                    int(model.enabled),
                    model.min_importance,
                    int(model.notify_enabled),
                    int(model.speak_enabled),
                    model.quiet_start_hour,
                    model.quiet_end_hour,
                    json.dumps(categories, sort_keys=True),
                    int(time.time()),
                ),
            )
        return self.settings(user)

    @staticmethod
    def quiet(settings: dict[str, Any], now: datetime | None = None) -> bool:
        hour = (now or datetime.now(LONDON)).hour
        start = int(settings["quiet_start_hour"])
        end = int(settings["quiet_end_hour"])
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    async def ingest(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        first_seen: int | None = None,
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        entity_id = str(current.get("entity_id") or "")
        if entity_id.startswith("person."):
            owner = "amber" if "amber" in entity_id.lower() else "aaron"
            self.presence[owner] = str(current.get("state") or "unknown").lower()
        candidates = self.rules.evaluate(
            previous,
            current,
            first_seen=first_seen or now,
            now=now,
            presence=self.presence,
        )
        created = []
        for candidate in candidates:
            event = await self.record(candidate)
            if event:
                created.append(event)
        return created

    async def record(self, candidate: Candidate) -> dict[str, Any] | None:
        self.initialise()
        now = int(time.time())
        with self.connection() as connection:
            duplicate = connection.execute(
                "SELECT created_at FROM proactive_events "
                "WHERE fingerprint = ? ORDER BY created_at DESC LIMIT 1",
                (candidate.fingerprint,),
            ).fetchone()
            if duplicate and now - int(duplicate["created_at"]) < self.cooldown:
                return None
            event_id = str(uuid.uuid4())
            confidence = max(0.0, min(1.0, float(candidate.confidence)))
            room = candidate.room.strip().lower().replace(" ", "_")
            decision = self._decision(candidate, confidence, room, now, connection)
            connection.execute(
                "INSERT INTO proactive_events ("
                "id, fingerprint, category, kind, entity_id, title, message, "
                "reason, importance, target_user, actions_json, status, "
                "created_at, updated_at, confidence, evidence_json, decision_json, room) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    candidate.fingerprint,
                    candidate.category,
                    candidate.kind,
                    candidate.entity_id,
                    candidate.title,
                    candidate.message,
                    candidate.reason,
                    candidate.importance,
                    candidate.target_user,
                    json.dumps(list(candidate.actions)),
                    now,
                    now,
                    confidence,
                    json.dumps(list(candidate.evidence), separators=(",", ":")),
                    json.dumps(decision, separators=(",", ":")),
                    room,
                ),
            )
            self._consider_learning(connection, candidate, now)
        event = self.get_event(event_id)
        await self.deliver(event)
        return self.get_event(event_id)

    def _decision(
        self,
        candidate: Candidate,
        confidence: float,
        room: str,
        now: int,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        critical = candidate.importance >= 95 and candidate.category in {"security", "cameras"}
        day_start = now - (now % 86400)
        spoken_today = int(
            connection.execute(
                "SELECT COUNT(*) FROM proactive_events WHERE spoken_at >= ?",
                (day_start,),
            ).fetchone()[0]
        )
        suppressed_reason = ""
        suppressed = connection.execute(
            "SELECT reason FROM initiative_suppressions WHERE fingerprint = ?",
            (candidate.fingerprint,),
        ).fetchone()
        if suppressed is not None and not critical:
            suppressed_reason = "disabled_by_user_feedback"
        elif confidence < 0.65 and not critical:
            suppressed_reason = "confidence_below_announcement_threshold"
        elif spoken_today >= self.daily_speech_budget and not critical:
            suppressed_reason = "daily_attention_budget_exhausted"
        speaker = self.speaker_map.get(room) or self.speaker_entity
        return {
            "critical": critical,
            "confidence": confidence,
            "room": room or "unknown",
            "speaker": speaker,
            "spoken_today": spoken_today,
            "daily_budget": self.daily_speech_budget,
            "suppress_speech": bool(suppressed_reason),
            "suppressed_reason": suppressed_reason,
            "why": candidate.reason,
        }

    def _consider_learning(
        self, connection: sqlite3.Connection, candidate: Candidate, now: int
    ) -> None:
        count = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM proactive_events WHERE fingerprint = ? AND created_at >= ?",
                    (candidate.fingerprint, now - 30 * 86400),
                ).fetchone()[0]
            )
            + 1
        )
        if count < self.learning_threshold:
            return
        confidence = min(0.95, 0.55 + count * 0.05)
        connection.execute(
            """INSERT INTO proactive_proposals
               (id, fingerprint, title, reason, evidence_count, confidence,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                 evidence_count=excluded.evidence_count,
                 confidence=excluded.confidence, updated_at=excluded.updated_at""",
            (
                "proposal-" + candidate.fingerprint,
                candidate.fingerprint,
                f"Learn a preference for {candidate.title.lower()}",
                "Repeated verified events suggest a routine or notification preference; "
                "Jarvis will not automate it until approved.",
                count,
                confidence,
                now,
                now,
            ),
        )

    def get_event(self, event_id: str) -> dict[str, Any]:
        self.initialise()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self.row(row)

    def feed(self, user: str, limit: int = 100) -> list[dict[str, Any]]:
        self.initialise()
        user = normalise_user(user)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_events "
                "WHERE target_user IN (?, 'all') "
                "ORDER BY created_at DESC LIMIT ?",
                (user, max(1, min(250, limit))),
            ).fetchall()
        return [self.row(row) for row in rows]

    async def action(
        self,
        event_id: str,
        model: ActionModel,
    ) -> dict[str, Any]:
        event = self.get_event(event_id)
        action = model.action.strip().lower()
        if action not in event["actions"]:
            raise ValueError("Action is not available for this event")
        now = int(time.time())
        if action == "dismiss":
            self.update(event_id, status="dismissed", updated_at=now)
        elif action == "remind_later":
            self.update(
                event_id,
                status="snoozed",
                snoozed_until=now + model.minutes * 60,
                updated_at=now,
            )
        elif action == "view_camera":
            self.update(event_id, status="viewed", updated_at=now)
        elif action == "turn_off":
            entity_id = event["entity_id"]
            entity_domain = domain(entity_id)
            if entity_domain in BLOCKED_CONTROL:
                raise ValueError("Sensitive security devices are blocked")
            if entity_domain not in SAFE_TURN_OFF:
                raise ValueError("This entity is not in the safe turn-off list")
            await self.ha_service(
                entity_domain,
                "turn_off",
                {"entity_id": entity_id},
            )
            self.update(event_id, status="actioned", updated_at=now)
        return self.get_event(event_id)

    async def deliver(self, event: dict[str, Any]) -> None:
        users = (
            ["aaron", "amber"]
            if event["target_user"] == "all"
            else [normalise_user(event["target_user"])]
        )
        notified = False
        speak = False
        for user in users:
            settings = self.settings(user)
            if not settings["enabled"]:
                continue
            if not settings["categories"].get(event["category"], True):
                continue
            if event["importance"] < settings["min_importance"]:
                continue
            critical = event["category"] in {"security", "cameras"} and event["importance"] >= 95
            if settings["notify_enabled"] and (not self.quiet(settings) or critical):
                target = self.targets.get(user, "")
                if target.startswith("notify."):
                    try:
                        await self.mobile_notify(target, event)
                        notified = True
                    except Exception:
                        logger.exception("Mobile proactive notification failed")
            if (
                settings["speak_enabled"]
                and not event["decision"].get("suppress_speech", False)
                and proactive_speech_allowed(
                    event,
                    quiet=self.quiet(settings),
                )
            ):
                speak = True

        spoken = False
        speaker = str(event["decision"].get("speaker") or "").strip()
        if speak and speaker:
            try:
                if speaker.startswith("script."):
                    await self.ha_service(
                        "script",
                        "turn_on",
                        {
                            "entity_id": speaker,
                            "variables": {"message": event["message"]},
                        },
                    )
                else:
                    await self.ha_service(
                        "assist_satellite",
                        "start_conversation",
                        {
                            "entity_id": speaker,
                            "start_message": event["message"],
                            "preannounce": False,
                            "extra_system_prompt": (
                                "This is a Jarvis proactive household event. "
                                "Accept a short natural reply such as yes, no, "
                                "thanks, show me, remind me later, or stop telling "
                                "me that. Do not invent devices or observations."
                            ),
                        },
                    )
                spoken = True
            except Exception:
                logger.exception("Proactive announcement failed")

        fields = {"updated_at": int(time.time())}
        if notified:
            fields["notified_at"] = int(time.time())
        if spoken:
            fields["spoken_at"] = int(time.time())
            fields["reply_until"] = int(time.time()) + self.reply_window_seconds
        self.update(event["id"], **fields)

    def active_reply_event(self, user: str, now: int | None = None) -> dict[str, Any] | None:
        self.initialise()
        current = int(now or time.time())
        requester = normalise_user(user)
        with self.connection() as connection:
            row = connection.execute(
                """SELECT * FROM proactive_events
                   WHERE reply_until >= ? AND status = 'active'
                     AND target_user IN (?, 'all')
                   ORDER BY spoken_at DESC LIMIT 1""",
                (current, requester),
            ).fetchone()
        return self.row(row) if row is not None else None

    async def handle_reply(self, text: str, user: str) -> dict[str, Any] | None:
        event = self.active_reply_event(user)
        if event is None:
            return None
        cleaned = " ".join(text.lower().strip(" .!?'").split())
        feedback = ""
        response = ""
        if cleaned in {"thanks", "thank you", "i know", "okay", "ok", "no"}:
            self.update(event["id"], status="dismissed", updated_at=int(time.time()))
            feedback, response = "dismissed", "Understood."
        elif (
            cleaned in {"yes", "show me", "show it", "open it"}
            and "view_camera" in event["actions"]
        ):
            self.update(event["id"], status="viewed", updated_at=int(time.time()))
            feedback, response = "viewed", "I've opened the camera event in Jarvis."
        elif cleaned in {"that was useful", "useful", "good alert"}:
            feedback, response = "useful", "Noted. I'll keep that kind of alert useful and concise."
        elif cleaned in {
            "don't announce that again",
            "dont announce that again",
            "stop telling me that",
        }:
            self.update(event["id"], status="dismissed", updated_at=int(time.time()))
            with self.connection() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO initiative_suppressions(fingerprint,reason,created_at) VALUES(?,?,?)",
                    (event["fingerprint"], "explicit_user_feedback", int(time.time())),
                )
            feedback, response = (
                "suppress_kind",
                "Understood. I won't announce that kind of event again.",
            )
        elif cleaned.startswith("remind me"):
            self.update(
                event["id"],
                status="snoozed",
                snoozed_until=int(time.time()) + 15 * 60,
                updated_at=int(time.time()),
            )
            feedback, response = "snoozed_15m", "I'll remind you in fifteen minutes."
        else:
            return None
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO proactive_feedback(event_id,user_id,feedback,created_at) VALUES(?,?,?,?)",
                (event["id"], normalise_user(user), feedback, int(time.time())),
            )
        return {"handled": True, "response": response, "event": self.get_event(event["id"])}

    def proposals(self, limit: int = 50) -> list[dict[str, Any]]:
        self.initialise()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_proposals ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(250, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def proposal_action(self, proposal_id: str, action: str) -> dict[str, Any]:
        resolved = action.strip().lower()
        if resolved not in {"approve", "reject"}:
            raise ValueError("Proposal action must be approve or reject")
        status = "approved" if resolved == "approve" else "rejected"
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE proactive_proposals SET status=?, updated_at=? WHERE id=?",
                (status, int(time.time()), proposal_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(proposal_id)
        return next(item for item in self.proposals(250) if item["id"] == proposal_id)

    async def mobile_notify(self, target: str, event: dict[str, Any]) -> None:
        service = target.split(".", 1)[1]
        channel = {
            "security": "Jarvis Security",
            "cameras": "Jarvis Security",
            "batteries": "Jarvis Battery",
            "presence": "Jarvis Presence",
            "appliances": "Jarvis Appliances",
            "energy": "Jarvis Energy",
            "system": "Jarvis System",
        }.get(event["category"], "Jarvis")
        await self.ha_service(
            "notify",
            service,
            {
                "title": "Jarvis",
                "message": event["message"],
                "data": {
                    "channel": channel,
                    "tag": proactive_notification_tag(event),
                    "group": "jarvis_" + event["category"],
                    "alert_once": event["importance"] < 95,
                    "importance": "high" if event["importance"] >= 90 else "default",
                    "priority": "high" if event["importance"] >= 90 else "normal",
                    "clickAction": "jarvis://proactive",
                    "actions": [
                        {
                            "action": "URI",
                            "title": "Open Jarvis",
                            "uri": "jarvis://proactive",
                        }
                    ],
                },
            },
        )

    async def ha_service(
        self,
        service_domain: str,
        service: str,
        payload: dict[str, Any],
    ) -> Any:
        if not self.ha_url or not self.ha_token:
            raise RuntimeError("Home Assistant URL/token is not configured")
        return await asyncio.to_thread(
            self.request_json,
            f"{self.ha_url}/api/services/{service_domain}/{service}",
            "POST",
            payload,
        )

    def request_json(
        self,
        url: str,
        method: str,
        payload: dict[str, Any] | None,
    ) -> Any:
        try:
            response = httpx.request(
                method=method,
                url=url,
                json=payload,
                headers={
                    "Authorization": "Bearer " + self.ha_token,
                    "Content-Type": "application/json",
                },
                timeout=12.0,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise RuntimeError(
                f"Home Assistant HTTP {exc.response.status_code}: {detail[:250]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Home Assistant request failed: {exc}") from exc

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Home Assistant returned invalid JSON") from exc

    def set_state_provider(self, provider: Any) -> None:
        """Use Jarvis's shared live Home Assistant state cache."""
        self.state_provider = provider

    async def fetch_states(self) -> list[dict[str, Any]]:
        if self.state_provider is not None:
            states = self.state_provider()
            if isinstance(states, (list, tuple)):
                return [item for item in states if isinstance(item, dict)]

        # Compatibility fallback for standalone use when the shared
        # House Awareness cache hasn't been wired.
        return await asyncio.to_thread(
            self.request_json,
            f"{self.ha_url}/api/states",
            "GET",
            None,
        )

    async def poll_loop(self) -> None:
        while True:
            try:
                states = await self.fetch_states()
                await self.process_states(states)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Proactive Home Assistant poll failed")
            await asyncio.sleep(self.poll_seconds)

    async def process_states(self, states: list[dict[str, Any]]) -> None:
        now = int(time.time())
        current = {
            str(item.get("entity_id")): item
            for item in states
            if isinstance(item, dict) and item.get("entity_id")
        }
        for entity_id, item in current.items():
            if entity_id.startswith("person."):
                owner = "amber" if "amber" in entity_id.lower() else "aaron"
                self.presence[owner] = str(item.get("state") or "unknown").lower()
        if not self.states:
            self.states = current
            self.first_seen = {key: now for key in current}
            logger.info("Proactive baseline loaded: %s states", len(current))
            return
        for index, (entity_id, item) in enumerate(
            current.items(),
            start=1,
        ):
            previous = self.states.get(entity_id)
            state = str(item.get("state") or "")
            old = str((previous or {}).get("state") or "")
            if state != old:
                self.first_seen[entity_id] = now
            await self.ingest(
                previous,
                item,
                self.first_seen.get(entity_id, now),
            )

            # Most rule evaluations complete synchronously because no
            # candidate is produced. Yield between small batches so a
            # large Home Assistant registry cannot monopolise Uvicorn's
            # asyncio event loop.
            if index % 32 == 0:
                await asyncio.sleep(0)
        self.states = current

    def update(self, event_id: str, **fields: Any) -> None:
        statements = {
            "status": ("UPDATE proactive_events SET status = ? WHERE id = ?"),
            "updated_at": ("UPDATE proactive_events SET updated_at = ? WHERE id = ?"),
            "notified_at": ("UPDATE proactive_events SET notified_at = ? WHERE id = ?"),
            "spoken_at": ("UPDATE proactive_events SET spoken_at = ? WHERE id = ?"),
            "snoozed_until": ("UPDATE proactive_events SET snoozed_until = ? WHERE id = ?"),
            "reply_until": ("UPDATE proactive_events SET reply_until = ? WHERE id = ?"),
        }
        safe = {key: value for key, value in fields.items() if key in statements}
        if not safe:
            return
        with self.connection() as connection:
            for key, value in safe.items():
                connection.execute(
                    statements[key],
                    (value, event_id),
                )

    @staticmethod
    def row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "fingerprint": row["fingerprint"],
            "category": row["category"],
            "kind": row["kind"],
            "entity_id": row["entity_id"],
            "title": row["title"],
            "message": row["message"],
            "reason": row["reason"],
            "importance": int(row["importance"]),
            "target_user": row["target_user"],
            "actions": json.loads(row["actions_json"]),
            "status": row["status"],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "notified_at": row["notified_at"],
            "spoken_at": row["spoken_at"],
            "snoozed_until": row["snoozed_until"],
            "reply_until": row["reply_until"],
            "confidence": float(row["confidence"]),
            "evidence": json.loads(row["evidence_json"]),
            "decision": json.loads(row["decision_json"]),
            "room": row["room"],
        }


engine = ProactiveEngine.from_env()


async def authorise(request: Request) -> None:
    expected = env(
        "JARVIS_MOBILE_VOICE_TOKEN",
        "MOBILE_VOICE_TOKEN",
        "JARVIS_MOBILE_TOKEN",
    )
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if expected:
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(401, "Invalid Jarvis mobile token")
        return
    client = request.client.host if request.client else ""
    try:
        if ipaddress.ip_address(client).is_global:
            raise HTTPException(
                403,
                "A mobile token is required for non-private clients",
            )
    except ValueError as exc:
        raise HTTPException(403, "Unable to validate client") from exc


@router.get("/status")
async def status(_: None = Depends(authorise)) -> dict[str, Any]:
    engine.initialise()
    return {
        "ready": True,
        "release": "19.0.0-alpha9",
        "poller_running": bool(engine.task and not engine.task.done()),
        "home_assistant_configured": bool(engine.ha_url and engine.ha_token),
        "min_importance": engine.min_importance,
        "cooldown_seconds": engine.cooldown,
        "speaker_configured": bool(engine.speaker_entity),
        "room_speakers": sorted(engine.speaker_map),
        "reply_window_seconds": engine.reply_window_seconds,
        "daily_speech_budget": engine.daily_speech_budget,
        "learning_threshold": engine.learning_threshold,
    }


@router.get("/feed")
async def feed(
    user_value: str = Query("aaron", alias="user_id"),
    limit: int = Query(100, ge=1, le=250),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    return {
        "events": engine.feed(user_value, limit),
        "settings": engine.settings(user_value),
    }


@router.get("/events/{event_id}/explain")
async def explain_event(
    event_id: str,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    try:
        event = engine.get_event(event_id)
    except KeyError as exc:
        raise HTTPException(404, "Proactive event not found") from exc
    return {
        "event_id": event_id,
        "why": event["reason"],
        "confidence": event["confidence"],
        "evidence": event["evidence"],
        "decision": event["decision"],
        "room": event["room"],
    }


@router.get("/proposals")
async def proposals(
    limit: int = Query(50, ge=1, le=250),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    values = engine.proposals(limit)
    return {"count": len(values), "proposals": values}


@router.post("/proposals/{proposal_id}/{action}")
async def proposal_action(
    proposal_id: str,
    action: str,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    try:
        return engine.proposal_action(proposal_id, action)
    except KeyError as exc:
        raise HTTPException(404, "Learning proposal not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/settings")
async def get_settings(
    user_value: str = Query("aaron", alias="user_id"),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    return engine.settings(user_value)


@router.put("/settings")
async def put_settings(
    model: SettingsModel,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    return engine.save_settings(model)


@router.post("/evaluate")
async def evaluate(
    model: EvaluateModel,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    created = await engine.ingest(
        model.previous,
        model.current,
        model.first_seen,
    )
    return {"created": created, "count": len(created)}


@router.post("/events/{event_id}/action")
async def event_action(
    event_id: str,
    model: ActionModel,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    try:
        return await engine.action(event_id, model)
    except KeyError as exc:
        raise HTTPException(404, "Proactive event not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
