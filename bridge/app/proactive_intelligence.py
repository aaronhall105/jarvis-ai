from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import ipaddress
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
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
from .notification_policy import (
    CRITICAL_SAFETY_KINDS,
    NOTIFICATION_MODES,
    decide_notification,
    normalise_mode,
    notification_recipients,
)
from .home_intelligence import (
    DeviceAvailability,
    GroundedHomeEntity,
    roll_up_physical_devices,
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


class NotificationOutcomeUnknown(RuntimeError):
    """The notification request may have reached the provider; never retry blindly."""


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
    device_id: str = ""
    device_name: str = ""
    area_id: str = ""
    previous_state: str = ""
    current_state: str = ""
    observed_at: int = 0
    persistence_seconds: int = 0
    recovery_of: str = ""

    @property
    def fingerprint(self) -> str:
        subject = f"device:{self.device_id}" if self.device_id else self.entity_id
        raw = f"{self.category}|{self.kind}|{subject}|{self.target_user}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Rules:
    def __init__(
        self,
        door_seconds: int = 600,
        oven_seconds: int = 1800,
        high_power_w: float | None = None,
    ) -> None:
        self.door_seconds = max(60, door_seconds)
        self.oven_seconds = max(300, oven_seconds)
        self.high_power_w = max(250.0, high_power_w) if high_power_w is not None else None
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
        away = bool(presence) and all(
            value not in {"", "home", "unknown", "unavailable"} for value in presence.values()
        )
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
            appliance_opening = any(
                word in lowered
                for word in (
                    "washing machine",
                    "washing_machine",
                    "washer",
                    "dryer",
                    "dishwasher",
                )
            )
            result.append(
                Candidate(
                    "appliances" if appliance_opening else "security",
                    "appliance_door_open" if appliance_opening else "door_open",
                    entity_id,
                    "Appliance door open" if appliance_opening else "Door or window left open",
                    f"{name} has been open for {max(1, age // 60)} minutes.",
                    f"{entity_id} remained {state} for {age} seconds",
                    35 if appliance_opening else 90 if away else 86,
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
                if self.high_power_w is not None and watts >= self.high_power_w:
                    result.append(
                        Candidate(
                            "energy",
                            "high_power",
                            entity_id,
                            "High energy use",
                            f"{name} is using about {round(watts)} watts.",
                            f"{entity_id} exceeded the configured {self.high_power_w:.0f} W threshold",
                            84,
                        )
                    )

        return result


class SettingsModel(BaseModel):
    user_id: str = "aaron"
    enabled: bool = True
    min_importance: int = Field(80, ge=0, le=100)
    notify_enabled: bool = True
    speak_enabled: bool = False
    notification_mode: (
        Literal[
            "important_only",
            "all_useful",
            "critical_only",
        ]
        | None
    ) = None
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
        principal_id: str = "aaron",
    ) -> None:
        self.database_path = Path(database_path)
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.enabled = enabled
        self.min_importance = max(0, min(100, min_importance))
        self.cooldown = max(30, cooldown)
        self.camera_incident_cooldown = max(
            self.cooldown,
            int(env("JARVIS_PROACTIVE_CAMERA_INCIDENT_COOLDOWN_SECONDS", default="1800")),
        )
        self.household_incident_cooldown = max(
            self.cooldown,
            int(env("JARVIS_PROACTIVE_INCIDENT_COOLDOWN_SECONDS", default="3600")),
        )
        self.device_unavailable_seconds = max(
            30,
            int(env("JARVIS_PROACTIVE_DEVICE_UNAVAILABLE_SECONDS", default="120")),
        )
        self.poll_seconds = max(5, poll_seconds)
        self.speaker_entity = speaker_entity.strip()
        self.home_principal = normalise_user(principal_id)
        self.reply_window_seconds = max(
            5, min(60, int(env("JARVIS_PROACTIVE_REPLY_WINDOW_SECONDS", default="12")))
        )
        self.daily_speech_budget = max(
            1, min(50, int(env("JARVIS_PROACTIVE_DAILY_SPEECH_BUDGET", default="8")))
        )
        self.learning_threshold = max(
            3, min(30, int(env("JARVIS_PROACTIVE_LEARNING_THRESHOLD", default="5")))
        )
        self.global_notification_limit = max(
            1,
            min(30, int(env("JARVIS_PROACTIVE_MAX_NOTIFICATIONS_5M", default="6"))),
        )
        self.speaker_map = self._speaker_map()
        self.rules = Rules(
            int(env("JARVIS_PROACTIVE_DOOR_OPEN_SECONDS", default="600")),
            int(env("JARVIS_PROACTIVE_OVEN_ON_SECONDS", default="1800")),
            (
                float(env("JARVIS_PROACTIVE_HIGH_POWER_W"))
                if env("JARVIS_PROACTIVE_HIGH_POWER_W")
                else None
            ),
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
        self.pipeline_counts = {
            "raw_observations": 0,
            "normalized_events": 0,
            "semantic_candidates": 0,
            "notifications": 0,
        }
        self.pipeline_timings_ms = {
            "raw_observation_processing": 0.0,
            "device_rollup": 0.0,
            "event_normalization": 0.0,
            "deterministic_prefilter": 0.0,
            "semantic_significance": 0.0,
            "snapshot_update": 0.0,
            "end_to_end_decision": 0.0,
        }

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
            principal_id=env("JARVIS_PROACTIVE_HOME_PRINCIPAL", default="aaron"),
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
            " updated_at INTEGER NOT NULL,\n"
            " notification_mode TEXT NOT NULL DEFAULT 'important_only'\n"
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
            settings_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(proactive_settings)").fetchall()
            }
            if "notification_mode" not in settings_columns:
                connection.execute(
                    "ALTER TABLE proactive_settings ADD COLUMN notification_mode "
                    "TEXT NOT NULL DEFAULT 'important_only'"
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
                "CREATE TABLE IF NOT EXISTS proactive_incidents ("
                " incident_id TEXT PRIMARY KEY, incident_key TEXT NOT NULL,"
                " category TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,"
                " target_user TEXT NOT NULL, status TEXT NOT NULL,"
                " first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,"
                " resolved_at INTEGER, occurrence_count INTEGER NOT NULL DEFAULT 1,"
                " notification_count INTEGER NOT NULL DEFAULT 0,"
                " delivery_attempts INTEGER NOT NULL DEFAULT 0, next_retry_at INTEGER,"
                " last_notified_at INTEGER, cooldown_until INTEGER,"
                " last_event_id TEXT, last_decision_json TEXT NOT NULL DEFAULT '{}');"
                "CREATE INDEX IF NOT EXISTS idx_proactive_incidents_key_status "
                "ON proactive_incidents(incident_key,status,last_seen DESC);"
                "CREATE INDEX IF NOT EXISTS idx_proactive_incidents_entity_status "
                "ON proactive_incidents(entity_id,status,last_seen DESC);"
                "CREATE TABLE IF NOT EXISTS proactive_home_conditions ("
                " condition_key TEXT PRIMARY KEY, principal_id TEXT NOT NULL,"
                " kind TEXT NOT NULL, subject_key TEXT NOT NULL, status TEXT NOT NULL,"
                " first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,"
                " qualified_at INTEGER, recovered_at INTEGER,"
                " suppression_reason TEXT, evidence_json TEXT NOT NULL DEFAULT '{}',"
                " notified_event_id TEXT);"
                "CREATE INDEX IF NOT EXISTS idx_proactive_home_conditions_status "
                "ON proactive_home_conditions(status,last_seen DESC);"
            )
            incident_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(proactive_incidents)").fetchall()
            }
            for column, definition in {
                "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
                "next_retry_at": "INTEGER",
            }.items():
                if column not in incident_columns:
                    connection.execute(
                        f"ALTER TABLE proactive_incidents ADD COLUMN {column} {definition}"
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
            "notification_mode": "important_only",
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
            "notification_mode": normalise_mode(row["notification_mode"]),
            "quiet_start_hour": int(row["quiet_start_hour"]),
            "quiet_end_hour": int(row["quiet_end_hour"]),
            "categories": categories,
        }

    def save_settings(self, model: SettingsModel) -> dict[str, Any]:
        self.initialise()
        user = normalise_user(model.user_id)
        notification_mode = (
            normalise_mode(model.notification_mode)
            if model.notification_mode is not None
            else self.settings(user)["notification_mode"]
        )
        categories = self.default_settings(user)["categories"]
        for key, value in model.categories.items():
            if key in CATEGORIES:
                categories[key] = bool(value)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO proactive_settings ("
                "user_id,enabled,min_importance,notify_enabled,speak_enabled,"
                "quiet_start_hour,quiet_end_hour,categories_json,updated_at,"
                "notification_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "enabled=excluded.enabled, min_importance=excluded.min_importance, "
                "notify_enabled=excluded.notify_enabled, "
                "speak_enabled=excluded.speak_enabled, "
                "quiet_start_hour=excluded.quiet_start_hour, "
                "quiet_end_hour=excluded.quiet_end_hour, "
                "categories_json=excluded.categories_json, "
                "updated_at=excluded.updated_at, "
                "notification_mode=excluded.notification_mode",
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
                    notification_mode,
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

    def conditions(
        self,
        limit: int = 100,
        *,
        principal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialise()
        principal = normalise_user(principal_id or self.home_principal)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_home_conditions WHERE principal_id=? "
                "ORDER BY last_seen DESC LIMIT ?",
                (principal, max(1, min(500, int(limit)))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["evidence"] = json.loads(str(item.pop("evidence_json")))
            except (json.JSONDecodeError, TypeError):
                item["evidence"] = {}
            result.append(item)
        return result

    def pipeline_report(self) -> dict[str, Any]:
        raw = int(self.pipeline_counts["raw_observations"])
        normalized = int(self.pipeline_counts["normalized_events"])
        semantic = int(self.pipeline_counts["semantic_candidates"])
        notifications = int(self.pipeline_counts["notifications"])
        return {
            "counts": dict(self.pipeline_counts),
            "timings_ms": dict(self.pipeline_timings_ms),
            "candidate_reduction": {
                "raw_to_normalized": round(normalized / raw, 6) if raw else 0.0,
                "normalized_to_semantic": round(semantic / normalized, 6) if normalized else 0.0,
                "semantic_to_notification": round(notifications / semantic, 6) if semantic else 0.0,
            },
            "llm_calls_per_raw_observation": 0.0,
        }

    @staticmethod
    def _availability_subject(device: Any) -> str:
        return str(device.device_id or device.device_key)

    @staticmethod
    def _availability_is_user_facing(device: Any) -> bool:
        domains = {item.domain for item in device.member_entities}
        return bool(
            device.device_id
            and domains
            & {
                "alarm_control_panel",
                "camera",
                "climate",
                "cover",
                "fan",
                "humidifier",
                "light",
                "lock",
                "media_player",
                "siren",
                "switch",
                "vacuum",
                "water_heater",
            }
        )

    def _condition_upsert(self, device: Any, now: int) -> tuple[sqlite3.Row, bool]:
        subject = self._availability_subject(device)
        key = f"{self.home_principal}:device_unavailable:{subject}"
        evidence = {
            "device_id": device.device_id,
            "device_name": device.name,
            "area_id": device.area_id,
            "area_name": device.area_name,
            "availability": device.availability.value,
            "unavailable_entity_count": device.unavailable_entity_count,
            "member_entities": [
                {
                    "entity_id": item.entity_id,
                    "domain": item.domain,
                    "state": item.state,
                    "observed_at": item.observed_at,
                }
                for item in device.member_entities
            ],
            "evidence_kind": device.evidence_kind,
        }
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT 1 FROM proactive_home_conditions WHERE condition_key=?",
                (key,),
            ).fetchone()
            connection.execute(
                "INSERT INTO proactive_home_conditions("
                "condition_key,principal_id,kind,subject_key,status,first_seen,last_seen,"
                "evidence_json) VALUES(?,?,'device_unavailable',?,'observed',?,?,?) "
                "ON CONFLICT(condition_key) DO UPDATE SET last_seen=excluded.last_seen,"
                "evidence_json=excluded.evidence_json",
                (
                    key,
                    self.home_principal,
                    subject,
                    now,
                    now,
                    json.dumps(evidence, separators=(",", ":")),
                ),
            )
            row = connection.execute(
                "SELECT * FROM proactive_home_conditions WHERE condition_key=?",
                (key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Proactive condition persistence failed")
        return row, existing is None

    def _condition_transition(
        self,
        condition_key: str,
        *,
        status: str,
        now: int,
        suppression_reason: str | None = None,
        event_id: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE proactive_home_conditions SET status=?,last_seen=?,"
                "qualified_at=CASE WHEN ?='qualified' THEN COALESCE(qualified_at,?) "
                "ELSE qualified_at END,"
                "recovered_at=CASE WHEN ? IN ('recovered','suppressed') THEN ? "
                "ELSE recovered_at END,suppression_reason=?,"
                "notified_event_id=COALESCE(?,notified_event_id) WHERE condition_key=?",
                (
                    status,
                    now,
                    status,
                    now,
                    status,
                    now,
                    suppression_reason,
                    event_id,
                    condition_key,
                ),
            )

    async def _process_device_availability(
        self,
        states: list[dict[str, Any]],
        *,
        now: int,
    ) -> None:
        started = time.monotonic()
        observed_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        raw_started = time.monotonic()
        entities: list[GroundedHomeEntity] = []
        for state in states:
            try:
                entities.append(GroundedHomeEntity.from_state(state, observed_at))
            except ValueError:
                continue
        self.pipeline_timings_ms["raw_observation_processing"] = round(
            (time.monotonic() - raw_started) * 1000,
            3,
        )
        rollup_started = time.monotonic()
        devices = roll_up_physical_devices(entities, observed_at=observed_at)
        self.pipeline_timings_ms["device_rollup"] = round(
            (time.monotonic() - rollup_started) * 1000,
            3,
        )
        normalization_started = time.monotonic()
        by_key = {
            f"{self.home_principal}:device_unavailable:{self._availability_subject(item)}": item
            for item in devices
            if self._availability_is_user_facing(item)
        }
        self.pipeline_timings_ms["event_normalization"] = round(
            (time.monotonic() - normalization_started) * 1000,
            3,
        )
        self.pipeline_counts["raw_observations"] += len(states)
        prefilter_started = time.monotonic()
        for key, device in by_key.items():
            if device.availability is DeviceAvailability.UNAVAILABLE:
                row, created = self._condition_upsert(device, now)
                if created:
                    self.pipeline_counts["normalized_events"] += 1
                if str(row["status"]) == "qualified":
                    continue
                first_seen = int(row["first_seen"])
                elapsed = max(0, now - first_seen)
                if elapsed < self.device_unavailable_seconds:
                    self._condition_transition(
                        key,
                        status="observed",
                        now=now,
                        suppression_reason="persistence_threshold_pending",
                    )
                    continue
                representative = next(
                    (
                        item
                        for item in device.member_entities
                        if item.state == "unavailable" and item.domain == "camera"
                    ),
                    next(item for item in device.member_entities if item.state == "unavailable"),
                )
                category = (
                    "cameras"
                    if any(item.domain == "camera" for item in device.member_entities)
                    else "system"
                )
                event = await self.record(
                    Candidate(
                        category=category,
                        kind="device_unavailable",
                        entity_id=representative.entity_id,
                        title="Device unavailable",
                        message=(
                            f"{device.name} has been unavailable for "
                            f"{max(1, elapsed // 60)} minutes."
                        ),
                        reason=(
                            f"Home Assistant's primary device surface remained unavailable "
                            f"for {elapsed} seconds."
                        ),
                        importance=85,
                        target_user=self.home_principal,
                        confidence=1.0,
                        evidence=tuple(
                            item.entity_id
                            for item in device.member_entities
                            if item.state == "unavailable"
                        ),
                        room=str(device.area_name or ""),
                        device_id=str(device.device_id or ""),
                        device_name=device.name,
                        area_id=str(device.area_id or ""),
                        previous_state="available",
                        current_state="unavailable",
                        observed_at=first_seen,
                        persistence_seconds=elapsed,
                    )
                )
                self.pipeline_counts["semantic_candidates"] += 1
                notified_event_id = (
                    str(event["id"])
                    if event is not None and event.get("notified_at") is not None
                    else None
                )
                self._condition_transition(
                    key,
                    status="qualified",
                    now=now,
                    event_id=notified_event_id,
                )
                continue

            with self.connection() as connection:
                row = connection.execute(
                    "SELECT * FROM proactive_home_conditions WHERE condition_key=? "
                    "AND status IN ('observed','qualified')",
                    (key,),
                ).fetchone()
            if row is None:
                continue
            was_qualified = str(row["status"]) == "qualified"
            was_notified = bool(row["notified_event_id"])
            self._condition_transition(
                key,
                status="recovered" if was_qualified else "suppressed",
                now=now,
                suppression_reason=(None if was_qualified else "recovered_before_threshold"),
            )
            self._resolve_device_incidents(str(device.device_id or ""), now)
            if not was_qualified or not was_notified:
                continue
            representative = device.member_entities[0]
            await self.record(
                Candidate(
                    category=(
                        "cameras"
                        if any(item.domain == "camera" for item in device.member_entities)
                        else "system"
                    ),
                    kind="device_recovered",
                    entity_id=representative.entity_id,
                    title="Device available again",
                    message=f"{device.name} is back online.",
                    reason="Home Assistant's primary device surface is available again.",
                    importance=80,
                    target_user=self.home_principal,
                    actions=("dismiss",),
                    evidence=tuple(item.entity_id for item in device.member_entities),
                    room=str(device.area_name or ""),
                    device_id=str(device.device_id or ""),
                    device_name=device.name,
                    area_id=str(device.area_id or ""),
                    previous_state="unavailable",
                    current_state="available",
                    observed_at=now,
                    recovery_of=str(row["notified_event_id"] or ""),
                )
            )
        self.pipeline_timings_ms["deterministic_prefilter"] = round(
            (time.monotonic() - prefilter_started) * 1000,
            3,
        )
        # The grounded deterministic path deliberately performs no per-state
        # semantic/LLM call. Keep this explicit for production observability.
        self.pipeline_timings_ms["semantic_significance"] = 0.0
        self.pipeline_timings_ms["end_to_end_decision"] = round(
            (time.monotonic() - started) * 1000,
            3,
        )

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
        self._resolve_inactive_incidents(current, now)
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

    @staticmethod
    def _persistent_incident(kind: str) -> bool:
        return kind in {
            *CRITICAL_SAFETY_KINDS,
            "door_open",
            "appliance_door_open",
            "person_detected",
            "oven_left_on",
            "critical_unavailable",
            "device_unavailable",
        }

    @staticmethod
    def _incident_still_active(kind: str, state: str) -> bool:
        active = state.strip().casefold()
        if kind in CRITICAL_SAFETY_KINDS:
            return active in {"on", "true", "detected", "wet"}
        if kind in {"door_open", "appliance_door_open"}:
            return active in {"on", "open", "true"}
        if kind == "person_detected":
            return active in {"on", "person", "detected", "true"}
        if kind == "oven_left_on":
            return active in {"on", "heating", "preheating"}
        if kind == "critical_unavailable":
            return active == "unavailable"
        if kind == "device_unavailable":
            return active == "unavailable"
        return False

    def _resolve_device_incidents(self, device_id: str, now: int) -> None:
        if not device_id:
            return
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT incident_id,last_decision_json FROM proactive_incidents "
                "WHERE kind='device_unavailable' AND status='active' AND target_user=?",
                (self.home_principal,),
            ).fetchall()
            for row in rows:
                try:
                    decision = json.loads(str(row["last_decision_json"] or "{}"))
                except (json.JSONDecodeError, TypeError):
                    continue
                subject = decision.get("subject")
                if (
                    not isinstance(subject, dict)
                    or str(subject.get("device_id") or "") != device_id
                ):
                    continue
                connection.execute(
                    "UPDATE proactive_incidents SET status='resolved',resolved_at=?,last_seen=? "
                    "WHERE incident_id=?",
                    (now, now, str(row["incident_id"])),
                )

    def _resolve_inactive_incidents(self, current: dict[str, Any], now: int) -> None:
        entity_id = str(current.get("entity_id") or "")
        if not entity_id:
            return
        state = str(current.get("state") or "")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT incident_id,kind FROM proactive_incidents "
                "WHERE entity_id=? AND status='active'",
                (entity_id,),
            ).fetchall()
            for row in rows:
                if self._incident_still_active(str(row["kind"]), state):
                    continue
                connection.execute(
                    "UPDATE proactive_incidents SET status='resolved',resolved_at=?,last_seen=? "
                    "WHERE incident_id=?",
                    (now, now, str(row["incident_id"])),
                )

    def _incident_cooldown_seconds(self, kind: str) -> int:
        if kind == "person_detected":
            return self.camera_incident_cooldown
        if kind in CRITICAL_SAFETY_KINDS:
            return self.cooldown
        return self.household_incident_cooldown

    def _begin_incident(
        self,
        connection: sqlite3.Connection,
        candidate: Candidate,
        now: int,
    ) -> tuple[str, bool]:
        incident_key = candidate.fingerprint
        active = connection.execute(
            "SELECT * FROM proactive_incidents WHERE incident_key=? AND status='active' "
            "ORDER BY last_seen DESC LIMIT 1",
            (incident_key,),
        ).fetchone()
        if active is not None:
            try:
                previous_decision = json.loads(str(active["last_decision_json"] or "{}"))
            except (json.JSONDecodeError, TypeError):
                previous_decision = {}
            attempts = int(active["delivery_attempts"] or 0)
            next_retry_at = int(active["next_retry_at"] or 0)
            if (
                previous_decision.get("notification_outcome") == "delivery_failed"
                and attempts < 3
                and next_retry_at <= now
            ):
                connection.execute(
                    "UPDATE proactive_incidents SET last_seen=?,"
                    "occurrence_count=occurrence_count+1 WHERE incident_id=?",
                    (now, str(active["incident_id"])),
                )
                return str(active["incident_id"]), False
            if previous_decision.get("notification_outcome") == "delivery_failed":
                connection.execute(
                    "UPDATE proactive_incidents SET last_seen=?,"
                    "occurrence_count=occurrence_count+1 WHERE incident_id=?",
                    (now, str(active["incident_id"])),
                )
                return str(active["incident_id"]), True
            detail = {
                "notify": False,
                "activity_only": True,
                "reason_code": "incident_already_active",
                "reason": "The same incident is already active and will not interrupt again.",
            }
            connection.execute(
                "UPDATE proactive_incidents SET last_seen=?,occurrence_count=occurrence_count+1,"
                "last_decision_json=? WHERE incident_id=?",
                (
                    now,
                    json.dumps(detail, separators=(",", ":")),
                    str(active["incident_id"]),
                ),
            )
            return str(active["incident_id"]), True

        recent = connection.execute(
            "SELECT * FROM proactive_incidents WHERE incident_key=? "
            "ORDER BY last_seen DESC LIMIT 1",
            (incident_key,),
        ).fetchone()
        if recent is not None and int(recent["cooldown_until"] or 0) > now:
            detail = {
                "notify": False,
                "activity_only": True,
                "reason_code": "incident_cooldown",
                "reason": "The incident recurred within its notification cooldown.",
                "cooldown_until": int(recent["cooldown_until"]),
            }
            connection.execute(
                "UPDATE proactive_incidents SET last_seen=?,occurrence_count=occurrence_count+1,"
                "last_decision_json=? WHERE incident_id=?",
                (
                    now,
                    json.dumps(detail, separators=(",", ":")),
                    str(recent["incident_id"]),
                ),
            )
            return str(recent["incident_id"]), True

        incident_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO proactive_incidents ("
            "incident_id,incident_key,category,kind,entity_id,target_user,status,"
            "first_seen,last_seen) VALUES (?,?,?,?,?,?, 'active',?,?)",
            (
                incident_id,
                incident_key,
                candidate.category,
                candidate.kind,
                candidate.entity_id,
                candidate.target_user,
                now,
                now,
            ),
        )
        return incident_id, False

    async def record(self, candidate: Candidate) -> dict[str, Any] | None:
        self.initialise()
        now = int(time.time())
        with self.connection() as connection:
            incident_id, duplicate = self._begin_incident(connection, candidate, now)
            if duplicate:
                return None
            event_id = str(uuid.uuid4())
            confidence = max(0.0, min(1.0, float(candidate.confidence)))
            room = candidate.room.strip().lower().replace(" ", "_")
            decision = self._decision(
                candidate,
                confidence,
                room,
                now,
                connection,
                incident_id=incident_id,
            )
            event_status = "active" if decision["should_notify"] else "activity"
            connection.execute(
                "INSERT INTO proactive_events ("
                "id, fingerprint, category, kind, entity_id, title, message, "
                "reason, importance, target_user, actions_json, status, "
                "created_at, updated_at, confidence, evidence_json, decision_json, room) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    event_status,
                    now,
                    now,
                    confidence,
                    json.dumps(list(candidate.evidence), separators=(",", ":")),
                    json.dumps(decision, separators=(",", ":")),
                    room,
                ),
            )
            connection.execute(
                "UPDATE proactive_incidents SET last_event_id=?,last_decision_json=? "
                "WHERE incident_id=?",
                (
                    event_id,
                    json.dumps(decision, separators=(",", ":")),
                    incident_id,
                ),
            )
            self._consider_learning(connection, candidate, now)
        event = self.get_event(event_id)
        await self.deliver(event)
        if not self._persistent_incident(candidate.kind):
            resolved_at = int(time.time())
            with self.connection() as connection:
                connection.execute(
                    "UPDATE proactive_incidents SET status='resolved',resolved_at=?,last_seen=?,"
                    "cooldown_until=COALESCE(cooldown_until,?) "
                    "WHERE incident_id=?",
                    (
                        resolved_at,
                        resolved_at,
                        resolved_at + self.cooldown,
                        incident_id,
                    ),
                )
        return self.get_event(event_id)

    def _decision(
        self,
        candidate: Candidate,
        confidence: float,
        room: str,
        now: int,
        connection: sqlite3.Connection,
        *,
        incident_id: str,
    ) -> dict[str, Any]:
        critical = candidate.kind in CRITICAL_SAFETY_KINDS
        day_start = now - (now % 86400)
        spoken_today = int(
            connection.execute(
                "SELECT COUNT(*) FROM proactive_events WHERE spoken_at >= ?",
                (day_start,),
            ).fetchone()[0]
        )
        suppressed_reason = ""
        recent_notifications = int(
            connection.execute(
                "SELECT COUNT(*) FROM proactive_events WHERE notified_at >= ?",
                (now - 300,),
            ).fetchone()[0]
        )
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
        elif recent_notifications >= self.global_notification_limit and not critical:
            suppressed_reason = "global_notification_rate_limit"
        speaker = self.speaker_map.get(room) or self.speaker_entity
        event = {
            "category": candidate.category,
            "kind": candidate.kind,
            "entity_id": candidate.entity_id,
            "title": candidate.title,
            "message": candidate.message,
            "reason": candidate.reason,
            "importance": candidate.importance,
            "target_user": candidate.target_user,
            "device_id": candidate.device_id,
            "persistence_seconds": candidate.persistence_seconds,
        }
        recipient_decisions: dict[str, dict[str, Any]] = {}
        for recipient in notification_recipients(event):
            settings = self.settings(recipient)
            resolved = decide_notification(
                event,
                settings=settings,
                presence=self.presence,
                recipient=recipient,
                quiet_hours=self.quiet(settings),
            ).as_dict()
            if (
                suppressed_reason
                in {
                    "disabled_by_user_feedback",
                    "confidence_below_announcement_threshold",
                    "global_notification_rate_limit",
                }
                and not critical
            ):
                resolved.update(
                    {
                        "notify": False,
                        "activity_only": True,
                        "reason_code": suppressed_reason,
                        "reason": (
                            "This event type was disabled by explicit user feedback."
                            if suppressed_reason == "disabled_by_user_feedback"
                            else (
                                "This event does not have enough confidence to interrupt."
                                if suppressed_reason == "confidence_below_announcement_threshold"
                                else "The household notification rate limit is active."
                            )
                        ),
                    }
                )
            recipient_decisions[recipient] = resolved
        should_notify = any(bool(value.get("notify")) for value in recipient_decisions.values())
        persistence_modifier = min(10, max(0, candidate.persistence_seconds // 60))
        recurrence_penalty = 0
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
            "policy": "contextual_interruption_v1",
            "incident_id": incident_id,
            "notification_mode_options": list(NOTIFICATION_MODES),
            "recipient_decisions": recipient_decisions,
            "should_notify": should_notify,
            "notification_outcome": "eligible" if should_notify else "activity_only",
            "subject": {
                "entity_id": candidate.entity_id,
                "device_id": candidate.device_id or None,
                "device_name": candidate.device_name or None,
                "area_id": candidate.area_id or None,
            },
            "transition": {
                "previous_state": candidate.previous_state or None,
                "current_state": candidate.current_state or None,
                "observed_at": candidate.observed_at or now,
                "persistence_seconds": candidate.persistence_seconds,
                "recovery_of": candidate.recovery_of or None,
            },
            "significance": {
                "base_importance": candidate.importance,
                "persistence_modifier": persistence_modifier,
                "novelty_modifier": 0,
                "user_relevance_modifier": 0,
                "recurrence_penalty": recurrence_penalty,
                "cooldown_penalty": 0,
                "bounded_score": max(
                    0,
                    min(
                        100,
                        candidate.importance + persistence_modifier - recurrence_penalty,
                    ),
                ),
                "deterministic": True,
            },
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

    def incidents(self, limit: int = 100) -> list[dict[str, Any]]:
        self.initialise()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_incidents ORDER BY last_seen DESC LIMIT ?",
                (max(1, min(250, int(limit))),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["last_decision"] = json.loads(item.pop("last_decision_json"))
            except (json.JSONDecodeError, TypeError):
                item["last_decision"] = {}
            result.append(item)
        return result

    def active_incidents(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return current incidents without letting recent recoveries displace them."""

        self.initialise()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_incidents WHERE status='active' "
                "ORDER BY last_seen DESC LIMIT ?",
                (max(1, min(250, int(limit))),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["last_decision"] = json.loads(item.pop("last_decision_json"))
            except (json.JSONDecodeError, TypeError):
                item["last_decision"] = {}
            result.append(item)
        return result

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        self.initialise()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["last_decision"] = json.loads(item.pop("last_decision_json"))
        except (json.JSONDecodeError, TypeError):
            item["last_decision"] = {}
        return item

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
        decision = dict(event.get("decision") or {})
        recipient_decisions = decision.get("recipient_decisions")
        if not isinstance(recipient_decisions, dict):
            recipient_decisions = {}
        notified = False
        speak = False
        delivery_results: dict[str, dict[str, Any]] = {}
        for user, user_decision in recipient_decisions.items():
            if not isinstance(user_decision, dict) or not bool(user_decision.get("notify")):
                continue
            settings = self.settings(user)
            target = self.targets.get(user, "")
            if target.startswith("notify."):
                try:
                    receipt = await self.mobile_notify(target, event)
                    notified = True
                    delivery_results[user] = {
                        "accepted": True,
                        "verified_delivered": False,
                        "outcome": "accepted",
                        "target": target,
                        "provider_receipt": (
                            receipt if isinstance(receipt, (dict, list)) else None
                        ),
                    }
                except NotificationOutcomeUnknown as exc:
                    delivery_results[user] = {
                        "accepted": False,
                        "verified_delivered": False,
                        "outcome": "unknown",
                        "error": type(exc).__name__,
                    }
                    logger.warning("Mobile proactive notification outcome is unknown")
                except Exception as exc:
                    delivery_results[user] = {
                        "accepted": False,
                        "verified_delivered": False,
                        "outcome": "failed",
                        "error": type(exc).__name__,
                    }
                    logger.exception("Mobile proactive notification failed")
            else:
                delivery_results[user] = {
                    "accepted": False,
                    "verified_delivered": False,
                    "outcome": "failed",
                    "error": "notification_target_unavailable",
                }
            if (
                settings["speak_enabled"]
                and not decision.get("suppress_speech", False)
                and (
                    bool(decision.get("critical"))
                    or any(value == "home" for value in self.presence.values())
                )
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
        now = int(time.time())
        if notified:
            fields["notified_at"] = now
        if spoken:
            fields["spoken_at"] = now
            fields["reply_until"] = now + self.reply_window_seconds
        self.update(event["id"], **fields)
        decision["delivery_results"] = delivery_results
        outcome_unknown = any(
            value.get("outcome") == "unknown" for value in delivery_results.values()
        )
        decision["notification_outcome"] = (
            "accepted"
            if notified
            else "outcome_unknown"
            if outcome_unknown
            else "delivery_failed"
            if delivery_results
            else "activity_only"
        )
        if notified:
            self.pipeline_counts["notifications"] += 1
        incident_id = str(decision.get("incident_id") or "")
        with self.connection() as connection:
            connection.execute(
                "UPDATE proactive_events SET decision_json=?,updated_at=? WHERE id=?",
                (json.dumps(decision, separators=(",", ":")), now, event["id"]),
            )
            if incident_id:
                if notified:
                    cooldown_until = now + self._incident_cooldown_seconds(event["kind"])
                    connection.execute(
                        "UPDATE proactive_incidents SET notification_count=notification_count+1,"
                        "last_notified_at=?,cooldown_until=?,last_decision_json=?,"
                        "delivery_attempts=0,next_retry_at=NULL "
                        "WHERE incident_id=?",
                        (
                            now,
                            cooldown_until,
                            json.dumps(decision, separators=(",", ":")),
                            incident_id,
                        ),
                    )
                elif not outcome_unknown:
                    attempts = 1 if delivery_results else 0
                    next_retry_at = now + 30 if delivery_results else None
                    connection.execute(
                        "UPDATE proactive_incidents SET last_decision_json=?,"
                        "delivery_attempts=delivery_attempts+?,next_retry_at=? "
                        "WHERE incident_id=?",
                        (
                            json.dumps(decision, separators=(",", ":")),
                            attempts,
                            next_retry_at,
                            incident_id,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE proactive_incidents SET last_decision_json=?,"
                        "delivery_attempts=delivery_attempts+1,next_retry_at=NULL "
                        "WHERE incident_id=?",
                        (
                            json.dumps(decision, separators=(",", ":")),
                            incident_id,
                        ),
                    )

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

    def latest_reported_event(self, user: str, now: int | None = None) -> dict[str, Any] | None:
        self.initialise()
        current = int(now or time.time())
        requester = normalise_user(user)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_events WHERE notified_at IS NOT NULL "
                "AND notified_at >= ? AND target_user IN (?, 'all') "
                "ORDER BY notified_at DESC LIMIT 1",
                (current - 86400, requester),
            ).fetchone()
        return self.row(row) if row is not None else None

    def significant_brief(self, user: str) -> str:
        self.initialise()
        requester = normalise_user(user)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT incident_id,last_event_id FROM proactive_incidents "
                "WHERE status='active' AND notification_count > 0 "
                "AND target_user IN (?, 'all') "
                "ORDER BY last_notified_at DESC LIMIT 3",
                (requester,),
            ).fetchall()
            event_ids = [str(row["last_event_id"] or "") for row in rows if row["last_event_id"]]
            events = []
            for event_id in event_ids:
                row = connection.execute(
                    "SELECT * FROM proactive_events WHERE id=? AND target_user IN (?, 'all')",
                    (event_id, requester),
                ).fetchone()
                if row is not None:
                    events.append(self.row(row))
        if not events:
            return "Everything looks normal."
        messages = [str(item["message"]).rstrip(".") for item in events]
        if len(messages) == 1:
            return f"One thing is worth noting: {messages[0]}."
        return (
            f"{len(messages)} things are worth noting: "
            + "; ".join(messages[:-1])
            + f"; and {messages[-1]}."
        )

    async def _grounded_subject_status(self, event: dict[str, Any]) -> str:
        decision = event.get("decision")
        subject = decision.get("subject") if isinstance(decision, dict) else None
        subject = subject if isinstance(subject, dict) else {}
        device_id = str(subject.get("device_id") or "").strip()
        entity_id = str(subject.get("entity_id") or event.get("entity_id") or "").strip()
        name = str(subject.get("device_name") or event.get("title") or "That device").strip()
        states = await self.fetch_states()
        observed_at = datetime.now(timezone.utc).isoformat()
        entities: list[GroundedHomeEntity] = []
        for state in states:
            try:
                entities.append(GroundedHomeEntity.from_state(state, observed_at))
            except ValueError:
                continue
        if device_id:
            device = next(
                (
                    item
                    for item in roll_up_physical_devices(entities, observed_at=observed_at)
                    if item.device_id == device_id
                ),
                None,
            )
            if device is None:
                return f"I can't verify {name}'s current state from Home Assistant."
            if device.availability is DeviceAvailability.AVAILABLE:
                return f"Yes. {device.name} is back online."
            if device.availability is DeviceAvailability.PARTIAL:
                return (
                    f"{device.name} is online, but one or more related features "
                    "are still unavailable."
                )
            if device.availability is DeviceAvailability.UNAVAILABLE:
                return f"No. {device.name} is still unavailable."
            return f"I can't verify {device.name}'s current availability conclusively."
        entity = next((item for item in entities if item.entity_id == entity_id), None)
        if entity is None:
            return f"I can't verify {name}'s current state from Home Assistant."
        if entity.state == "unavailable":
            return f"No. {entity.name} is still unavailable."
        if entity.state in {"unknown", ""}:
            return f"I can't verify {entity.name}'s current availability conclusively."
        return f"Yes. {entity.name} is available."

    async def handle_reply(self, text: str, user: str) -> dict[str, Any] | None:
        cleaned = " ".join(text.lower().strip(" .!?'").split())
        words = set(re.findall(r"[a-z0-9]+", cleaned))
        if "anything" in words and {"need", "know"} <= words:
            return {
                "handled": True,
                "response": self.significant_brief(user),
                "event": self.latest_reported_event(user) or {},
            }
        event = self.active_reply_event(user) or self.latest_reported_event(user)
        if event is None:
            return None
        if words & {"back", "online", "available"} and words & {"it", "device", "yet", "now"}:
            return {
                "handled": True,
                "response": await self._grounded_subject_status(event),
                "event": event,
            }
        if "why" in words and words & {"tell", "told", "notify", "notified", "alert"}:
            evidence = [str(item) for item in event.get("evidence") or () if str(item)]
            evidence_text = (
                f" The grounded evidence includes {len(evidence)} Home Assistant "
                f"entit{'y' if len(evidence) == 1 else 'ies'}."
                if evidence
                else ""
            )
            return {
                "handled": True,
                "response": str(event["reason"]).rstrip(".") + "." + evidence_text,
                "event": event,
            }
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

    async def mobile_notify(self, target: str, event: dict[str, Any]) -> Any:
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
        return await self.ha_service(
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
        except (httpx.ReadTimeout, httpx.WriteError) as exc:
            raise NotificationOutcomeUnknown(
                "Home Assistant notification outcome could not be determined"
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
            if inspect.isawaitable(states):
                states = await states
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
        processing_started = time.monotonic()
        now = int(time.time())
        await self._process_device_availability(states, now=now)
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
            snapshot_started = time.monotonic()
            self.states = current
            self.first_seen = {key: now for key in current}
            self.pipeline_timings_ms["snapshot_update"] = round(
                (time.monotonic() - snapshot_started) * 1000,
                3,
            )
            self.pipeline_timings_ms["end_to_end_decision"] = round(
                (time.monotonic() - processing_started) * 1000,
                3,
            )
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
        snapshot_started = time.monotonic()
        self.states = current
        self.pipeline_timings_ms["snapshot_update"] = round(
            (time.monotonic() - snapshot_started) * 1000,
            3,
        )
        self.pipeline_timings_ms["end_to_end_decision"] = round(
            (time.monotonic() - processing_started) * 1000,
            3,
        )

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
        "device_unavailable_seconds": engine.device_unavailable_seconds,
        "global_notification_limit_5m": engine.global_notification_limit,
        "home_principal": engine.home_principal,
        "pipeline_counts": dict(engine.pipeline_counts),
        "pipeline": engine.pipeline_report(),
        "active_conditions": sum(
            1 for item in engine.conditions(500) if item.get("status") in {"observed", "qualified"}
        ),
        "notification_modes": list(NOTIFICATION_MODES),
        "default_notification_mode": "important_only",
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
    incident_id = str(event["decision"].get("incident_id") or "")
    return {
        "event_id": event_id,
        "why": event["reason"],
        "confidence": event["confidence"],
        "evidence": event["evidence"],
        "decision": event["decision"],
        "room": event["room"],
        "incident": engine.incident(incident_id) if incident_id else None,
    }


@router.get("/incidents")
async def incidents(
    limit: int = Query(100, ge=1, le=250),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    values = engine.incidents(limit)
    return {"count": len(values), "incidents": values}


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
