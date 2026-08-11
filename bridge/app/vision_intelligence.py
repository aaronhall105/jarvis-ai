from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.proactive_intelligence import Candidate
from app.proactive_intelligence import engine as proactive_engine


logger = logging.getLogger("jarvis-core.vision")
router = APIRouter(prefix="/api/vision", tags=["vision"])
LONDON = ZoneInfo("Europe/London")

DEFAULT_CAMERA_MAP: dict[str, dict[str, str]] = {
    "front_door": {
        "area": "Front Door",
        "entity_id": "camera.front_door_clear",
    },
    "hallway": {
        "area": "Hallway",
        "entity_id": "camera.hallway_clear",
    },
    "living_room": {
        "area": "Living Room",
        "entity_id": "camera.living_room_clear",
    },
    "bedroom": {
        "area": "Bedroom",
        "entity_id": "camera.bedroom_clear",
    },
}
DEFAULT_LABELS = {
    "person",
    "package",
    "car",
    "truck",
    "motorcycle",
    "bicycle",
}
VISION_QUERY = re.compile(
    r"\b(?:camera|cameras|frigate|snapshot|visitor|package|"
    r"person detected|detection|who was|who is|what happened|"
    r"anyone|someone|movement|motion|recording)\b",
    re.IGNORECASE,
)
DESCRIBE_QUERY = re.compile(
    r"\b(?:who|what happened|describe|see|look|wearing|doing|"
    r"carrying|holding|package|vehicle|car)\b",
    re.IGNORECASE,
)


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


def env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def clean(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def camera_key(value: Any) -> str:
    text = clean(value, 120).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def zones_from(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for raw in values:
        item = clean(raw, 80)
        if item and item not in result:
            result.append(item)
    return result[:20]


def load_camera_map() -> dict[str, dict[str, str]]:
    mapping = {
        key: dict(value)
        for key, value in DEFAULT_CAMERA_MAP.items()
    }
    raw = env("JARVIS_VISION_CAMERA_MAP")
    if not raw:
        return mapping
    try:
        supplied = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid JARVIS_VISION_CAMERA_MAP JSON")
        return mapping
    if not isinstance(supplied, dict):
        return mapping
    for raw_key, raw_value in supplied.items():
        key = camera_key(raw_key)
        if not key:
            continue
        if isinstance(raw_value, str):
            mapping[key] = {
                "area": key.replace("_", " ").title(),
                "entity_id": clean(raw_value, 180),
            }
            continue
        if not isinstance(raw_value, dict):
            continue
        entity_id = clean(raw_value.get("entity_id"), 180)
        area = clean(raw_value.get("area"), 100)
        mapping[key] = {
            "area": area or key.replace("_", " ").title(),
            "entity_id": entity_id,
        }
    return mapping


class DescribeModel(BaseModel):
    refresh: bool = False


class FrigateEventModel(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class VisionEngine:
    def __init__(
        self,
        database_path: str,
        *,
        frigate_url: str = "",
        frigate_token: str = "",
        ha_url: str = "",
        ha_token: str = "",
        openai_key: str = "",
        model: str = "gpt-5-mini",
        enabled: bool = True,
        poll_seconds: int = 10,
        duplicate_seconds: int = 120,
        retention_days: int = 30,
        offline_seconds: int = 120,
        auto_describe_queries: bool = True,
    ) -> None:
        self.database_path = Path(database_path)
        self.frigate_url = frigate_url.rstrip("/")
        self.frigate_token = frigate_token
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.openai_key = openai_key
        self.model = model
        self.enabled = enabled
        self.poll_seconds = max(5, poll_seconds)
        self.duplicate_seconds = max(15, duplicate_seconds)
        self.retention_days = max(1, retention_days)
        self.offline_seconds = max(30, offline_seconds)
        self.auto_describe_queries = auto_describe_queries
        self.camera_map = load_camera_map()
        self.labels = {
            item.strip().lower()
            for item in env(
                "JARVIS_VISION_LABELS",
                default="person,package,car,truck,motorcycle,bicycle",
            ).split(",")
            if item.strip()
        } or set(DEFAULT_LABELS)
        self.task: asyncio.Task[None] | None = None
        self.initialised = False
        self.state_provider: Any = None
        self.last_frigate_event_time = 0.0
        self.presence_cache: tuple[float, dict[str, str]] = (
            0.0,
            {"aaron": "unknown", "amber": "unknown"},
        )
        self.unavailable_since: dict[str, int] = {}
        self.health_alerted: set[str] = set()
        self.openai = (
            AsyncOpenAI(api_key=openai_key)
            if openai_key
            else None
        )

    @classmethod
    def from_env(cls) -> VisionEngine:
        return cls(
            env(
                "JARVIS_VISION_DB_PATH",
                default="/app/data/jarvis_vision.db",
            ),
            frigate_url=env("JARVIS_FRIGATE_URL"),
            frigate_token=env("JARVIS_FRIGATE_TOKEN"),
            ha_url=env(
                "HOME_ASSISTANT_URL",
                "HA_BASE_URL",
                "JARVIS_HOME_ASSISTANT_URL",
            ),
            ha_token=env(
                "HOME_ASSISTANT_TOKEN",
                "HA_TOKEN",
                "JARVIS_HOME_ASSISTANT_TOKEN",
            ),
            openai_key=env("OPENAI_API_KEY"),
            model=env(
                "JARVIS_VISION_MODEL",
                "OPENAI_MODEL",
                default="gpt-5-mini",
            ),
            enabled=env_bool("JARVIS_VISION_ENABLED", True),
            poll_seconds=env_int(
                "JARVIS_VISION_POLL_SECONDS",
                10,
                minimum=5,
                maximum=300,
            ),
            duplicate_seconds=env_int(
                "JARVIS_VISION_DUPLICATE_SECONDS",
                120,
                minimum=15,
                maximum=3600,
            ),
            retention_days=env_int(
                "JARVIS_VISION_RETENTION_DAYS",
                30,
                minimum=1,
                maximum=365,
            ),
            offline_seconds=env_int(
                "JARVIS_VISION_CAMERA_OFFLINE_SECONDS",
                120,
                minimum=30,
                maximum=3600,
            ),
            auto_describe_queries=env_bool(
                "JARVIS_VISION_AUTO_DESCRIBE_QUERIES",
                True,
            ),
        )

    def connection(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path,
            timeout=15,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def initialise(self) -> None:
        if self.initialised:
            return
        schema = (
            "PRAGMA journal_mode=WAL;\n"
            "CREATE TABLE IF NOT EXISTS vision_events (\n"
            " id TEXT PRIMARY KEY,\n"
            " source_id TEXT UNIQUE NOT NULL,\n"
            " camera TEXT NOT NULL,\n"
            " area TEXT NOT NULL,\n"
            " camera_entity TEXT NOT NULL,\n"
            " label TEXT NOT NULL,\n"
            " sub_label TEXT NOT NULL,\n"
            " zones_json TEXT NOT NULL,\n"
            " score REAL NOT NULL,\n"
            " start_time REAL NOT NULL,\n"
            " end_time REAL,\n"
            " has_snapshot INTEGER NOT NULL,\n"
            " has_clip INTEGER NOT NULL,\n"
            " description TEXT NOT NULL,\n"
            " description_at INTEGER,\n"
            " importance INTEGER NOT NULL,\n"
            " everyone_away INTEGER NOT NULL,\n"
            " suppressed INTEGER NOT NULL,\n"
            " raw_json TEXT NOT NULL,\n"
            " created_at INTEGER NOT NULL,\n"
            " updated_at INTEGER NOT NULL\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_vision_created "
            "ON vision_events(created_at DESC);\n"
            "CREATE INDEX IF NOT EXISTS idx_vision_camera "
            "ON vision_events(camera, start_time DESC);\n"
            "CREATE INDEX IF NOT EXISTS idx_vision_label "
            "ON vision_events(label, start_time DESC);\n"
        )
        with self.connection() as connection:
            connection.executescript(schema)
        self.initialised = True

    async def start(self) -> None:
        self.initialise()
        if self.task and not self.task.done():
            return
        if not self.enabled:
            logger.info("Vision Intelligence is disabled")
            return
        if not self.frigate_url and not (
            self.ha_url and self.ha_token
        ):
            logger.info(
                "Vision Intelligence ready without poller: "
                "Frigate and Home Assistant are not configured"
            )
            return
        self.task = asyncio.create_task(
            self.poll_loop(),
            name="jarvis_vision_intelligence",
        )
        logger.info(
            "Vision Intelligence started cameras=%s frigate=%s",
            len(self.camera_map),
            bool(self.frigate_url),
        )

    async def stop(self) -> None:
        if not self.task:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    def normalise_event(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        envelope = payload
        body = payload.get("after")
        if not isinstance(body, dict):
            body = payload.get("event")
        if not isinstance(body, dict):
            body = payload
        data = body.get("data")
        if not isinstance(data, dict):
            data = {}

        camera = camera_key(
            body.get("camera")
            or data.get("camera")
        )
        label = clean(
            body.get("label")
            or data.get("label"),
            80,
        ).lower()
        if not camera or not label:
            return None
        if label not in self.labels and label not in {
            "camera_offline",
            "camera_restored",
        }:
            return None

        source_id = clean(
            body.get("id")
            or data.get("id"),
            180,
        )
        start_time = self._number(
            body.get("start_time")
            or data.get("start_time")
        )
        if start_time is None:
            start_time = time.time()
        if not source_id:
            raw = (
                f"{camera}|{label}|{start_time}|"
                f"{body.get('current_zones')}"
            )
            source_id = hashlib.sha256(
                raw.encode("utf-8")
            ).hexdigest()[:32]

        raw_sub_label = (
            body.get("sub_label")
            or data.get("sub_label")
            or ""
        )
        if isinstance(raw_sub_label, list) and raw_sub_label:
            raw_sub_label = raw_sub_label[0]
        zones = zones_from(
            body.get("current_zones")
            or body.get("entered_zones")
            or data.get("zones")
            or data.get("current_zones")
        )
        score = self._number(
            data.get("top_score")
            or body.get("top_score")
            or data.get("score")
            or body.get("score")
        )
        end_time = self._number(
            body.get("end_time")
            or data.get("end_time")
        )
        camera_details = self.camera_map.get(
            camera,
            {
                "area": camera.replace("_", " ").title(),
                "entity_id": "",
            },
        )
        return {
            "source_id": source_id,
            "camera": camera,
            "area": camera_details.get("area", ""),
            "camera_entity": camera_details.get(
                "entity_id",
                "",
            ),
            "label": label,
            "sub_label": clean(raw_sub_label, 100),
            "zones": zones,
            "score": score or 0.0,
            "start_time": float(start_time),
            "end_time": end_time,
            "has_snapshot": bool(
                body.get("has_snapshot", True)
            ),
            "has_clip": bool(body.get("has_clip", False)),
            "raw": envelope,
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def presence(self) -> dict[str, str]:
        cached_at, cached = self.presence_cache
        if time.time() - cached_at < 15:
            return dict(cached)
        result = {"aaron": "unknown", "amber": "unknown"}
        if not self.ha_url or not self.ha_token:
            self.presence_cache = (time.time(), result)
            return result
        try:
            states = await self._ha_json("/api/states")
        except Exception:
            logger.exception("Vision presence lookup failed")
            return result
        if isinstance(states, list):
            for item in states:
                if not isinstance(item, dict):
                    continue
                entity_id = clean(
                    item.get("entity_id"),
                    180,
                ).lower()
                state = clean(item.get("state"), 40).lower()
                if entity_id == "person.aaron":
                    result["aaron"] = state
                elif entity_id in {
                    "person.amber",
                    "person.amber_hall",
                }:
                    result["amber"] = state
        self.presence_cache = (time.time(), result)
        return result

    @staticmethod
    def everyone_away(presence: dict[str, str]) -> bool:
        known = [
            value
            for value in presence.values()
            if value not in {"", "unknown", "unavailable"}
        ]
        return bool(known) and all(
            value != "home"
            for value in known
        )

    def importance(
        self,
        event: dict[str, Any],
        *,
        away: bool,
    ) -> int:
        label = event["label"]
        camera = event["camera"]
        zones = event["zones"]
        if label == "camera_offline":
            return 88
        if label == "camera_restored":
            return 68
        if label == "person":
            if away:
                value = 98
            elif camera == "front_door":
                value = 92
            else:
                value = 84
        elif label == "package":
            value = 91
        elif label in {"car", "truck", "motorcycle"}:
            value = 84 if camera == "front_door" else 76
        else:
            value = 72
        if zones:
            value += 2
        score = float(event.get("score") or 0.0)
        if score and score < 0.55:
            value -= 8
        return max(0, min(100, value))

    def duplicate(
        self,
        event: dict[str, Any],
        *,
        now: int,
    ) -> bool:
        self.initialise()
        threshold = now - self.duplicate_seconds
        zones_json = json.dumps(
            event["zones"],
            separators=(",", ":"),
        )
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id FROM vision_events "
                "WHERE camera = ? AND label = ? "
                "AND zones_json = ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1",
                (
                    event["camera"],
                    event["label"],
                    zones_json,
                    threshold,
                ),
            ).fetchone()
        return row is not None

    async def ingest(
        self,
        payload: dict[str, Any],
        *,
        publish: bool = True,
    ) -> dict[str, Any] | None:
        self.initialise()
        event = self.normalise_event(payload)
        if event is None:
            return None

        existing = self.get_by_source(event["source_id"])
        if existing is not None:
            return existing

        presence = await self.presence()
        away = self.everyone_away(presence)
        now = int(time.time())
        suppressed = self.duplicate(event, now=now)
        importance = self.importance(event, away=away)
        event_id = "vision-" + hashlib.sha256(
            event["source_id"].encode("utf-8")
        ).hexdigest()[:24]
        raw_json = json.dumps(
            event["raw"],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )[:30000]
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO vision_events ("
                "id, source_id, camera, area, camera_entity, "
                "label, sub_label, zones_json, score, "
                "start_time, end_time, has_snapshot, has_clip, "
                "description, description_at, importance, "
                "everyone_away, suppressed, raw_json, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event["source_id"],
                    event["camera"],
                    event["area"],
                    event["camera_entity"],
                    event["label"],
                    event["sub_label"],
                    json.dumps(
                        event["zones"],
                        separators=(",", ":"),
                    ),
                    event["score"],
                    event["start_time"],
                    event["end_time"],
                    int(event["has_snapshot"]),
                    int(event["has_clip"]),
                    "",
                    None,
                    importance,
                    int(away),
                    int(suppressed),
                    raw_json,
                    now,
                    now,
                ),
            )
        stored = self.get_event(event_id)
        if (
            publish
            and stored is not None
            and not stored["suppressed"]
        ):
            await self.publish_proactive(stored)
        return stored

    async def publish_proactive(
        self,
        event: dict[str, Any],
    ) -> None:
        label = event["label"]
        area = event["area"] or event["camera"].replace(
            "_",
            " ",
        ).title()
        if label == "camera_offline":
            title = "Camera offline"
            message = f"The {area} camera is unavailable."
            kind = "vision_camera_offline"
        elif label == "camera_restored":
            title = "Camera restored"
            message = f"The {area} camera is available again."
            kind = "vision_camera_restored"
        elif label == "person":
            title = "Person detected"
            message = f"A person was detected at {area}."
            if event["everyone_away"]:
                message += " Nobody appears to be home."
            kind = "vision_person_detected"
        elif label == "package":
            title = "Package detected"
            message = f"A package was detected at {area}."
            kind = "vision_package_detected"
        else:
            title = "Camera activity"
            message = (
                f"{label.replace('_', ' ').title()} "
                f"was detected at {area}."
            )
            kind = "vision_object_detected"

        entity_id = event["camera_entity"] or (
            "camera." + event["camera"]
        )
        candidate = Candidate(
            "cameras",
            kind,
            entity_id,
            title,
            message,
            (
                f"Frigate event {event['source_id']} "
                f"camera={event['camera']} "
                f"label={event['label']} "
                f"zones={','.join(event['zones']) or 'none'} "
                f"everyone_away={event['everyone_away']}"
            ),
            int(event["importance"]),
            "all",
            ("view_camera", "dismiss", "remind_later"),
        )
        try:
            await proactive_engine.record(candidate)
        except Exception:
            logger.exception(
                "Could not publish vision event to Jarvis activity"
            )

    def get_by_source(
        self,
        source_id: str,
    ) -> dict[str, Any] | None:
        self.initialise()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM vision_events "
                "WHERE source_id = ? LIMIT 1",
                (source_id,),
            ).fetchone()
        return self.row(row) if row else None

    def get_event(
        self,
        event_id: str,
    ) -> dict[str, Any] | None:
        self.initialise()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM vision_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return self.row(row) if row else None

    def recent(
        self,
        *,
        camera: str = "",
        after: float = 0.0,
        before: float | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.initialise()
        safe_limit = max(1, min(250, limit))
        with self.connection() as connection:
            if camera and before is not None:
                rows = connection.execute(
                    "SELECT * FROM vision_events "
                    "WHERE camera = ? AND start_time >= ? "
                    "AND start_time < ? "
                    "ORDER BY start_time DESC LIMIT ?",
                    (camera, after, before, safe_limit),
                ).fetchall()
            elif camera:
                rows = connection.execute(
                    "SELECT * FROM vision_events "
                    "WHERE camera = ? AND start_time >= ? "
                    "ORDER BY start_time DESC LIMIT ?",
                    (camera, after, safe_limit),
                ).fetchall()
            elif before is not None:
                rows = connection.execute(
                    "SELECT * FROM vision_events "
                    "WHERE start_time >= ? AND start_time < ? "
                    "ORDER BY start_time DESC LIMIT ?",
                    (after, before, safe_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM vision_events "
                    "WHERE start_time >= ? "
                    "ORDER BY start_time DESC LIMIT ?",
                    (after, safe_limit),
                ).fetchall()
        return [self.row(row) for row in rows]

    @staticmethod
    def row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "camera": row["camera"],
            "area": row["area"],
            "camera_entity": row["camera_entity"],
            "label": row["label"],
            "sub_label": row["sub_label"],
            "zones": json.loads(row["zones_json"]),
            "score": float(row["score"]),
            "start_time": float(row["start_time"]),
            "end_time": row["end_time"],
            "has_snapshot": bool(row["has_snapshot"]),
            "has_clip": bool(row["has_clip"]),
            "description": row["description"],
            "description_at": row["description_at"],
            "importance": int(row["importance"]),
            "everyone_away": bool(row["everyone_away"]),
            "suppressed": bool(row["suppressed"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def public_event(
        self,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(event)
        event_id = event["id"]
        result["snapshot_path"] = (
            f"/api/vision/events/{event_id}/snapshot"
            if event["has_snapshot"]
            else ""
        )
        result["describe_path"] = (
            f"/api/vision/events/{event_id}/describe"
        )
        entity_id = event["camera_entity"]
        result["home_assistant_path"] = (
            "/config/entities/entity/" + quote(entity_id, safe="")
            if entity_id
            else ""
        )
        return result

    async def snapshot(
        self,
        event: dict[str, Any],
    ) -> tuple[bytes, str]:
        source_id = quote(event["source_id"], safe="")
        if self.frigate_url and event["has_snapshot"]:
            headers = self._frigate_headers()
            url = (
                f"{self.frigate_url}/api/events/"
                f"{source_id}/snapshot.jpg"
            )
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    url,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.content
                if len(data) > 12_000_000:
                    raise RuntimeError(
                        "Frigate snapshot exceeded 12 MB"
                    )
                content_type = response.headers.get(
                    "content-type",
                    "image/jpeg",
                )
                return data, content_type

        entity_id = event["camera_entity"]
        if self.ha_url and self.ha_token and entity_id:
            url = (
                f"{self.ha_url}/api/camera_proxy/"
                f"{quote(entity_id, safe='._')}"
            )
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    url,
                    headers=self._ha_headers(),
                )
                response.raise_for_status()
                data = response.content
                if len(data) > 12_000_000:
                    raise RuntimeError(
                        "Home Assistant snapshot exceeded 12 MB"
                    )
                return (
                    data,
                    response.headers.get(
                        "content-type",
                        "image/jpeg",
                    ),
                )
        raise RuntimeError(
            "No snapshot source is configured for this camera"
        )

    async def describe(
        self,
        event_id: str,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        event = self.get_event(event_id)
        if event is None:
            raise KeyError(event_id)
        if event["description"] and not refresh:
            return self.public_event(event)
        if self.openai is None:
            raise RuntimeError(
                "OPENAI_API_KEY is required for snapshot descriptions"
            )
        data, content_type = await self.snapshot(event)
        encoded = base64.b64encode(data).decode("ascii")
        prompt = (
            "Describe this private home security-camera snapshot for "
            "Jarvis. State only visible observations. Mention the number "
            "of people, clothing, obvious actions, vehicles, packages and "
            "relevant scene changes. Do not identify a person by name, do "
            "not perform face recognition, and do not infer intent. Use "
            "one or two concise British-English sentences. "
            f"Camera area: {event['area']}. "
            f"Detector label: {event['label']}. "
            f"Zones: {', '.join(event['zones']) or 'none'}."
        )
        response = await self.openai.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{content_type};base64,"
                                f"{encoded}"
                            ),
                        },
                    ],
                }
            ],
            max_output_tokens=220,
        )
        description = clean(
            getattr(response, "output_text", ""),
            800,
        )
        if not description:
            raise RuntimeError(
                "Vision model returned an empty description"
            )
        now = int(time.time())
        with self.connection() as connection:
            connection.execute(
                "UPDATE vision_events "
                "SET description = ?, description_at = ?, "
                "updated_at = ? WHERE id = ?",
                (description, now, now, event_id),
            )
        updated = self.get_event(event_id)
        if updated is None:
            raise RuntimeError("Vision event disappeared")
        return self.public_event(updated)


    async def live_person_rooms(
        self,
    ) -> dict[str, Any]:
        if not (
            self.ha_url
            and self.ha_token
            and self.openai is not None
        ):
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": [],
                "available": False,
            }

        cameras = [
            (
                details.get("area", key.replace("_", " ").title()),
                details.get("entity_id", ""),
            )
            for key, details in self.camera_map.items()
            if key != "front_door"
                and details.get("entity_id")
        ]

        if not cameras:
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": [],
                "available": False,
            }

        async def fetch_camera(
            area: str,
            entity_id: str,
        ) -> tuple[str, str, bytes] | None:
            url = (
                f"{self.ha_url}/api/camera_proxy/"
                f"{quote(entity_id, safe='._')}"
            )
            try:
                async with httpx.AsyncClient(
                    timeout=8.0,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(
                        url,
                        headers=self._ha_headers(),
                    )
                    response.raise_for_status()
            except Exception:
                logger.warning(
                    "Live room snapshot failed for %s",
                    entity_id,
                )
                return None

            data = response.content
            if not data or len(data) > 12_000_000:
                return None

            content_type = response.headers.get(
                "content-type",
                "image/jpeg",
            )
            return area, content_type, data

        fetched = await asyncio.gather(
            *(
                fetch_camera(area, entity_id)
                for area, entity_id in cameras
            )
        )
        images = [
            item for item in fetched
            if item is not None
        ]

        if not images:
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": [],
                "available": False,
            }

        allowed = [area for area, _, _ in images]
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "These are private home camera snapshots. "
                    "For each labelled room, state only whether "
                    "at least one person is visibly present. "
                    "Do not identify anyone, compare faces, infer "
                    "identity or infer intent. Return strict JSON "
                    'only: {"rooms":["Room name"],'
                    '"uncertain":false}. Use only these room '
                    f"names: {', '.join(allowed)}."
                ),
            }
        ]

        for area, content_type, data in images:
            encoded = base64.b64encode(data).decode(
                "ascii"
            )
            content.append({
                "type": "input_text",
                "text": f"Camera room: {area}",
            })
            content.append({
                "type": "input_image",
                "image_url": (
                    f"data:{content_type};base64,"
                    f"{encoded}"
                ),
            })

        try:
            response = await self.openai.responses.create(
                model=self.model,
                input=[{
                    "role": "user",
                    "content": content,
                }],
                max_output_tokens=120,
            )
        except Exception:
            logger.exception(
                "Live room camera analysis failed"
            )
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": allowed,
                "available": False,
            }

        raw = clean(
            getattr(response, "output_text", ""),
            1200,
        )
        match = re.search(r"\{.*\}", raw, re.S)

        if not match:
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": allowed,
                "available": True,
            }

        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {
                "source": "live_snapshots",
                "rooms": [],
                "checked": allowed,
                "available": True,
            }

        supplied = payload.get("rooms")
        if not isinstance(supplied, list):
            supplied = []

        room_lookup = {
            area.casefold(): area
            for area in allowed
        }
        rooms = []
        for value in supplied:
            resolved = room_lookup.get(
                str(value).strip().casefold()
            )
            if resolved and resolved not in rooms:
                rooms.append(resolved)

        return {
            "source": "live_snapshots",
            "rooms": rooms,
            "checked": allowed,
            "available": True,
        }

    async def person_room_evidence(
        self,
    ) -> dict[str, Any]:
        live = await self.live_person_rooms()
        if live.get("rooms"):
            live["events"] = []
            live["primary_event"] = None
            return live

        from app.person_room_context import (
            recent_person_rooms,
        )

        recent = recent_person_rooms(
            self.recent(
                after=time.time() - 180,
                limit=20,
            )
        )
        recent["available"] = bool(
            recent.get("events")
        )
        return recent

    def matches_query(self, text: str) -> bool:
        return bool(VISION_QUERY.search(clean(text, 1000)))

    def camera_from_query(self, text: str) -> str:
        lowered = clean(text, 1000).lower()
        aliases = {
            "front door": "front_door",
            "front_door": "front_door",
            "living room": "living_room",
            "living_room": "living_room",
            "hallway": "hallway",
            "bedroom": "bedroom",
        }
        for alias, key in aliases.items():
            if alias in lowered:
                return key
        for key, value in self.camera_map.items():
            area = value.get("area", "").lower()
            if area and area in lowered:
                return key
        return ""

    async def context_for_query(
        self,
        text: str,
    ) -> dict[str, Any]:
        now = time.time()
        lowered = clean(text, 1000).lower()
        camera = self.camera_from_query(lowered)
        before: float | None = None
        if "last hour" in lowered:
            after = now - 3600
        elif "yesterday" in lowered:
            local_now = datetime.now(LONDON)
            today = local_now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ).timestamp()
            after = today - 86400
            before = today
        elif "today" in lowered:
            local_now = datetime.now(LONDON)
            after = local_now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ).timestamp()
        else:
            after = now - 86400

        events = self.recent(
            camera=camera,
            after=after,
            before=before,
            limit=8,
        )
        if (
            events
            and self.auto_describe_queries
            and DESCRIBE_QUERY.search(lowered)
            and events[0]["has_snapshot"]
            and not events[0]["description"]
        ):
            try:
                described = await self.describe(
                    events[0]["id"],
                )
                events[0] = described
            except Exception:
                logger.exception(
                    "Automatic camera description failed"
                )

        if not events:
            prompt = (
                "Jarvis Vision Intelligence found no matching camera "
                "events in the requested time window. Do not claim that "
                "nothing happened outside that recorded window."
            )
            return {
                "prompt": prompt,
                "primary_event": None,
                "events": [],
            }

        lines = [
            "Verified recent camera events:",
        ]
        for event in events:
            local_time = datetime.fromtimestamp(
                event["start_time"],
                LONDON,
            ).strftime("%d %b %Y %H:%M")
            description = event["description"] or (
                event["label"].replace("_", " ").title()
                + " detected"
            )
            lines.append(
                "- "
                f"{local_time}; area={event['area']}; "
                f"camera={event['camera']}; "
                f"label={event['label']}; "
                f"zones={','.join(event['zones']) or 'none'}; "
                f"everyone_away={event['everyone_away']}; "
                f"description={description}"
            )
        lines.extend(
            [
                "Use only these recorded observations.",
                "Do not identify a person by name or infer intent.",
                "The absence of an event does not prove that nothing "
                "happened before monitoring or outside the time window.",
                "Home Assistant remains the authority for live feeds "
                "and recordings.",
            ]
        )
        return {
            "prompt": "\n".join(lines),
            "primary_event": self.public_event(events[0]),
            "events": [
                self.public_event(event)
                for event in events
            ],
        }

    def set_state_provider(self, provider: Any) -> None:
        """Use Jarvis's shared live Home Assistant state cache."""
        self.state_provider = provider

    async def poll_loop(self) -> None:
        cleanup_at = 0.0
        while True:
            try:
                if self.frigate_url:
                    await self.poll_frigate()
                if self.ha_url and self.ha_token:
                    await self.poll_camera_health()
                if time.time() >= cleanup_at:
                    self.cleanup()
                    cleanup_at = time.time() + 3600
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Vision Intelligence poll failed"
                )
            await asyncio.sleep(self.poll_seconds)

    async def poll_frigate(self) -> None:
        params: dict[str, Any] = {"limit": 50}
        if self.last_frigate_event_time:
            params["after"] = max(
                0,
                self.last_frigate_event_time - 5,
            )
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{self.frigate_url}/api/events",
                params=params,
                headers=self._frigate_headers(),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(
                "Frigate events endpoint returned non-list JSON"
            )
        newest = self.last_frigate_event_time
        for item in reversed(payload):
            if not isinstance(item, dict):
                continue
            created = await self.ingest(item)
            if created:
                newest = max(
                    newest,
                    float(created["start_time"]),
                )
        self.last_frigate_event_time = newest

    async def poll_camera_health(self) -> None:
        if self.state_provider is not None:
            states = self.state_provider()
        else:
            # Compatibility fallback for standalone Vision use.
            states = await self._ha_json("/api/states")

        if not isinstance(states, (list, tuple)):
            return
        indexed = {
            clean(item.get("entity_id"), 180): item
            for item in states
            if isinstance(item, dict)
        }
        now = int(time.time())
        for camera, details in self.camera_map.items():
            entity_id = details.get("entity_id", "")
            if not entity_id:
                continue
            item = indexed.get(entity_id)
            state = clean(
                (item or {}).get("state"),
                40,
            ).lower()
            unavailable = state in {
                "",
                "unknown",
                "unavailable",
            }
            if unavailable:
                started = self.unavailable_since.setdefault(
                    camera,
                    now,
                )
                if (
                    now - started >= self.offline_seconds
                    and camera not in self.health_alerted
                ):
                    self.health_alerted.add(camera)
                    await self.ingest(
                        {
                            "id": (
                                f"health-offline-{camera}-"
                                f"{now // self.offline_seconds}"
                            ),
                            "camera": camera,
                            "label": "camera_offline",
                            "start_time": started,
                            "has_snapshot": False,
                        }
                    )
            else:
                was_alerted = camera in self.health_alerted
                self.unavailable_since.pop(camera, None)
                self.health_alerted.discard(camera)
                if was_alerted:
                    await self.ingest(
                        {
                            "id": f"health-restored-{camera}-{now}",
                            "camera": camera,
                            "label": "camera_restored",
                            "start_time": now,
                            "has_snapshot": False,
                        }
                    )

    def cleanup(self) -> None:
        self.initialise()
        threshold = int(
            time.time() - self.retention_days * 86400
        )
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM vision_events "
                "WHERE created_at < ?",
                (threshold,),
            )

    def status(self) -> dict[str, Any]:
        self.initialise()
        with self.connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM vision_events"
            ).fetchone()["count"]
        return {
            "ready": True,
            "release": "19.0.0-alpha12",
            "enabled": self.enabled,
            "frigate_configured": bool(self.frigate_url),
            "home_assistant_configured": bool(
                self.ha_url and self.ha_token
            ),
            "vision_model_configured": bool(
                self.openai_key and self.model
            ),
            "poller_running": bool(
                self.task and not self.task.done()
            ),
            "camera_map": self.camera_map,
            "labels": sorted(self.labels),
            "event_count": int(count),
            "retention_days": self.retention_days,
            "duplicate_seconds": self.duplicate_seconds,
        }

    def _frigate_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.frigate_token:
            headers["Authorization"] = (
                "Bearer " + self.frigate_token
            )
        return headers

    def _ha_headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self.ha_token,
            "Accept": "application/json",
        }

    async def _ha_json(self, path: str) -> Any:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                self.ha_url + path,
                headers=self._ha_headers(),
            )
            response.raise_for_status()
            return response.json()


engine = VisionEngine.from_env()


async def authorise(request: Request) -> None:
    expected = env(
        "JARVIS_MOBILE_VOICE_TOKEN",
        "MOBILE_VOICE_TOKEN",
        "JARVIS_MOBILE_TOKEN",
    )
    header = request.headers.get("Authorization", "")
    supplied = (
        header[7:].strip()
        if header.lower().startswith("bearer ")
        else ""
    )
    if expected:
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(
                401,
                "Invalid Jarvis mobile token",
            )
        return
    client = request.client.host if request.client else ""
    try:
        if ipaddress.ip_address(client).is_global:
            raise HTTPException(
                403,
                "A mobile token is required "
                "for non-private clients",
            )
    except ValueError as exc:
        raise HTTPException(
            403,
            "Unable to validate client",
        ) from exc


@router.on_event("startup")
async def startup() -> None:
    await engine.start()


@router.on_event("shutdown")
async def shutdown() -> None:
    await engine.stop()


@router.get("/status")
async def status(
    _: None = Depends(authorise),
) -> dict[str, Any]:
    return engine.status()


@router.get("/events")
async def events(
    camera: str = Query("", max_length=100),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(50, ge=1, le=250),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    items = engine.recent(
        camera=camera_key(camera),
        after=time.time() - hours * 3600,
        limit=limit,
    )
    return {
        "count": len(items),
        "events": [
            engine.public_event(item)
            for item in items
        ],
    }


@router.get("/events/latest")
async def latest(
    camera: str = Query("", max_length=100),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    items = engine.recent(
        camera=camera_key(camera),
        after=0,
        limit=1,
    )
    return {
        "event": (
            engine.public_event(items[0])
            if items
            else None
        )
    }


@router.get("/events/{event_id}")
async def event(
    event_id: str,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    item = engine.get_event(event_id)
    if item is None:
        raise HTTPException(404, "Vision event not found")
    return engine.public_event(item)


@router.get("/events/{event_id}/snapshot")
async def snapshot(
    event_id: str,
    _: None = Depends(authorise),
) -> Response:
    item = engine.get_event(event_id)
    if item is None:
        raise HTTPException(404, "Vision event not found")
    try:
        data, content_type = await engine.snapshot(item)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            "Unable to retrieve camera snapshot",
        ) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=30"},
    )


@router.post("/events/{event_id}/describe")
async def describe(
    event_id: str,
    model: DescribeModel,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    try:
        return await engine.describe(
            event_id,
            refresh=model.refresh,
        )
    except KeyError as exc:
        raise HTTPException(
            404,
            "Vision event not found",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            "Unable to retrieve camera snapshot",
        ) from exc


@router.get("/context")
async def context(
    query: str = Query(..., min_length=1, max_length=1000),
    _: None = Depends(authorise),
) -> dict[str, Any]:
    return await engine.context_for_query(query)


@router.post("/frigate/events")
async def frigate_event(
    model: FrigateEventModel,
    _: None = Depends(authorise),
) -> dict[str, Any]:
    created = await engine.ingest(model.payload)
    return {
        "created": created,
        "accepted": created is not None,
    }
