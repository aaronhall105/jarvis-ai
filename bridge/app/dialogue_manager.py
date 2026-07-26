import asyncio
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence


_CANCEL_PATTERN = re.compile(
    r"^\s*(?:cancel|cancel it|never mind|nevermind|forget it|don['’]?t do it|"
    r"don['’]?t send it|stop)\s*[.!?]*\s*$",
    re.I,
)

_AFFIRMATIVE_PATTERN = re.compile(
    r"^\s*(?:yes|yeah|yep|correct|that one|the one you said|go ahead|do it)\s*[.!?]*\s*$",
    re.I,
)

_CONTROL_PRONOUN_PATTERN = re.compile(
    r"^\s*(?:turn|switch|put|power)\s+(?:(?P<state1>on|off)\s+)?"
    r"(?P<target>it|that|this|them|those|these|the device|the devices|"
    r"the light|the lights)(?:\s+(?P<state2>on|off))?\s*[.!?]*\s*$",
    re.I,
)


@dataclass
class DialogueResolution:
    """A deterministic interpretation produced from structured dialogue state."""

    handled: bool = False
    kind: str | None = None
    rewritten_text: str | None = None
    reply: str | None = None
    action: dict[str, Any] | None = None
    clear_goal: bool = False


@dataclass
class DialogueState:
    """Persistent working state for one user-scoped conversation."""

    conversation_id: str
    active_goal: str | None = None
    status: str = "idle"
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    prompt: str | None = None
    focus: dict[str, Any] = field(default_factory=dict)
    last_result: dict[str, Any] = field(default_factory=dict)
    last_error: dict[str, Any] = field(default_factory=dict)
    tone: dict[str, Any] = field(default_factory=dict)
    turn_index: int = 0
    created_at: str = ""
    updated_at: str = ""
    goal_expires_at: str | None = None
    schema_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DialogueManager:
    """
    Central, persistent dialogue-state manager.

    Conversation history remains the natural-language record. This manager stores
    structured working state that must survive fast deterministic routes:
    unfinished goals, missing slots, focus people/devices/areas, verified results
    and recoverable errors.
    """

    def __init__(
        self,
        database_path: str,
        default_goal_ttl_seconds: int = 600,
    ) -> None:
        self.database_path = Path(database_path)
        self.default_goal_ttl_seconds = max(60, default_goal_ttl_seconds)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise_database()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat()

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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dialogue_states (
                    conversation_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dialogue_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dialogue_events_conversation
                ON dialogue_events (conversation_id, event_id DESC);
                """
            )

    def _new_state(self, conversation_id: str) -> DialogueState:
        now = self._iso(self._utc_now())
        return DialogueState(
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
        )

    def _normalise_state(self, state: DialogueState) -> DialogueState:
        expiry = self._parse_time(state.goal_expires_at)
        if (
            state.active_goal
            and expiry is not None
            and expiry <= self._utc_now()
        ):
            state.active_goal = None
            state.status = "idle"
            state.slots = {}
            state.missing_slots = []
            state.prompt = None
            state.goal_expires_at = None
        return state

    @staticmethod
    def _state_from_json(conversation_id: str, raw: str) -> DialogueState:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        allowed = set(DialogueState.__dataclass_fields__)
        values = {key: value for key, value in payload.items() if key in allowed}
        values["conversation_id"] = conversation_id
        return DialogueState(**values)

    def _get_sync(self, conversation_id: str) -> DialogueState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM dialogue_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return self._new_state(conversation_id)
        return self._normalise_state(
            self._state_from_json(conversation_id, str(row["state_json"]))
        )

    async def get(self, conversation_id: str) -> DialogueState:
        return await asyncio.to_thread(self._get_sync, conversation_id)

    def _save_sync(self, state: DialogueState, event_type: str | None, payload: dict[str, Any]) -> None:
        now = self._iso(self._utc_now())
        if not state.created_at:
            state.created_at = now
        state.updated_at = now
        encoded = json.dumps(state.as_dict(), ensure_ascii=False, separators=(",", ":"), default=str)
        event_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dialogue_states (conversation_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (state.conversation_id, encoded, state.created_at, now),
            )
            if event_type:
                connection.execute(
                    """
                    INSERT INTO dialogue_events (
                        conversation_id, event_type, payload_json, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (state.conversation_id, event_type, event_payload, now),
                )

    async def save(
        self,
        state: DialogueState,
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DialogueState:
        await asyncio.to_thread(
            self._save_sync,
            state,
            event_type,
            payload or {},
        )
        return state

    async def begin_goal(
        self,
        conversation_id: str,
        goal: str,
        *,
        slots: dict[str, Any] | None = None,
        missing_slots: Sequence[str] = (),
        prompt: str | None = None,
        status: str = "awaiting_slot",
        ttl_seconds: int | None = None,
    ) -> DialogueState:
        state = await self.get(conversation_id)
        ttl = max(60, ttl_seconds or self.default_goal_ttl_seconds)
        state.active_goal = goal
        state.status = status
        state.slots = dict(slots or {})
        state.missing_slots = [str(item) for item in missing_slots if str(item)]
        state.prompt = prompt
        state.goal_expires_at = self._iso(self._utc_now() + timedelta(seconds=ttl))
        return await self.save(
            state,
            "goal_started",
            {
                "goal": goal,
                "status": status,
                "slots": state.slots,
                "missing_slots": state.missing_slots,
            },
        )

    async def clear_goal(
        self,
        conversation_id: str,
        *,
        outcome: str = "cleared",
    ) -> DialogueState:
        state = await self.get(conversation_id)
        previous = {
            "goal": state.active_goal,
            "status": state.status,
            "slots": state.slots,
            "missing_slots": state.missing_slots,
        }
        state.active_goal = None
        state.status = "idle"
        state.slots = {}
        state.missing_slots = []
        state.prompt = None
        state.goal_expires_at = None
        return await self.save(
            state,
            f"goal_{outcome}",
            previous,
        )


    async def record_tone(
        self,
        conversation_id: str,
        tone: dict[str, Any],
    ) -> DialogueState:
        """Record the latest best-effort conversational tone for this dialogue."""

        state = await self.get(conversation_id)
        previous = dict(state.tone) if isinstance(state.tone, dict) else {}
        state.tone = {
            "current": dict(tone),
            "previous": previous.get("current") or previous,
            "at": self._iso(self._utc_now()),
        }
        return await self.save(
            state,
            "tone_updated",
            {"tone": state.tone.get("current")},
        )

    async def record_focus(
        self,
        conversation_id: str,
        *,
        person: dict[str, Any] | None = None,
        devices: Sequence[dict[str, Any]] | None = None,
        area: dict[str, Any] | None = None,
        action: str | None = None,
        intent: str | None = None,
    ) -> DialogueState:
        state = await self.get(conversation_id)
        focus = dict(state.focus)
        if person is not None:
            focus["person"] = dict(person)
        if devices is not None:
            focus["devices"] = [dict(item) for item in devices]
        if area is not None:
            focus["area"] = dict(area)
        if action is not None:
            focus["action"] = action
        if intent is not None:
            focus["intent"] = intent
        focus["updated_at"] = self._iso(self._utc_now())
        state.focus = focus
        return await self.save(state, "focus_updated", focus)

    async def record_result(
        self,
        conversation_id: str,
        *,
        intent: str,
        success: bool,
        response: str,
        calls: Sequence[dict[str, Any]] = (),
    ) -> DialogueState:
        state = await self.get(conversation_id)
        state.turn_index += 1
        state.last_result = {
            "intent": intent,
            "success": bool(success),
            "response": response,
            "calls": [dict(call) for call in calls],
            "at": self._iso(self._utc_now()),
        }
        if success:
            state.last_error = {}
        else:
            state.last_error = {
                "intent": intent,
                "response": response,
                "at": self._iso(self._utc_now()),
            }

        devices: list[dict[str, Any]] = []
        person: dict[str, Any] | None = None
        area: dict[str, Any] | None = None
        action: str | None = None

        for call in calls:
            tool = str(call.get("tool") or "")
            result = call.get("result") or {}
            arguments = call.get("arguments") or {}
            if not isinstance(result, dict):
                continue

            if tool in {
                "control_device",
                "control_media_player",
                "set_media_volume",
            }:
                entity_id = str(result.get("entity_id") or arguments.get("entity_id") or "")
                name = str(result.get("name") or entity_id or "")
                if entity_id or name:
                    devices.append({
                        "entity_id": entity_id,
                        "name": name,
                        "area_id": result.get("area_id"),
                        "area_name": result.get("area_name"),
                        "domain": result.get("domain"),
                        "state": result.get("current_state") or result.get("target_state"),
                    })
                action = str(result.get("target_state") or arguments.get("action") or action or "") or None

            elif tool in {"control_area_lights", "control_area_switches"}:
                area = {
                    "area_id": result.get("area_id") or arguments.get("area_id"),
                    "area_name": result.get("area_name"),
                }
                action = str(result.get("target_state") or action or "") or None
                for entity in result.get("entities") or []:
                    if not isinstance(entity, dict):
                        continue
                    entity_id = str(entity.get("entity_id") or "")
                    name = str(
                        entity.get("name")
                        or (entity.get("attributes") or {}).get("friendly_name")
                        or entity_id
                    )
                    if entity_id or name:
                        devices.append({
                            "entity_id": entity_id,
                            "name": name,
                            "area_id": result.get("area_id"),
                            "area_name": result.get("area_name"),
                            "domain": result.get("domain"),
                            "state": entity.get("state") or result.get("target_state"),
                        })

            elif tool in {"get_person_location", "get_person_state"}:
                person = {
                    "name": result.get("name") or arguments.get("person"),
                    "entity_id": result.get("entity_id"),
                    "state": result.get("state") or result.get("location"),
                }

            elif tool == "search_entity_states" and str(arguments.get("domain") or "") == "person":
                selected = result.get("selected_entity") or {}
                if isinstance(selected, dict) and selected:
                    person = {
                        "name": selected.get("friendly_name") or selected.get("name"),
                        "entity_id": selected.get("entity_id"),
                        "state": selected.get("state"),
                    }

            elif tool in {"list_active_area_devices", "list_area_states"}:
                if result.get("area_id") or result.get("area_name"):
                    area = {
                        "area_id": result.get("area_id") or arguments.get("area_id"),
                        "area_name": result.get("area_name"),
                    }

        if devices:
            deduplicated: list[dict[str, Any]] = []
            seen: set[str] = set()
            for device in devices:
                key = str(device.get("entity_id") or device.get("name") or "").casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                deduplicated.append(device)
            state.focus = {
                **state.focus,
                "devices": deduplicated,
                "action": action,
                "intent": intent,
                "updated_at": self._iso(self._utc_now()),
            }
        if person:
            state.focus = {
                **state.focus,
                "person": person,
                "intent": intent,
                "updated_at": self._iso(self._utc_now()),
            }
        if area:
            state.focus = {
                **state.focus,
                "area": area,
                "intent": intent,
                "updated_at": self._iso(self._utc_now()),
            }

        return await self.save(
            state,
            "turn_result",
            {
                "intent": intent,
                "success": bool(success),
                "active_goal": state.active_goal,
            },
        )

    async def resolve_pending(
        self,
        conversation_id: str,
        text: str,
    ) -> DialogueResolution:
        state = await self.get(conversation_id)
        value = re.sub(r"\s+", " ", text).strip()
        if not state.active_goal:
            return DialogueResolution()

        if state.active_goal != "admin_change" and _CANCEL_PATTERN.fullmatch(value):
            return DialogueResolution(
                handled=True,
                kind="cancel_goal",
                reply="Okay, cancelled.",
                clear_goal=True,
            )

        if state.active_goal == "send_notification" and state.status == "awaiting_slot":
            if "message" in state.missing_slots:
                return DialogueResolution(
                    handled=True,
                    kind="send_notification",
                    action={
                        "recipient": state.slots.get("recipient"),
                        "title": state.slots.get("title") or "Jarvis",
                        "message": value,
                    },
                    clear_goal=True,
                )

        if state.active_goal == "device_control" and state.status == "awaiting_slot":
            action = str(state.slots.get("action") or "").lower()
            target = value
            if _AFFIRMATIVE_PATTERN.fullmatch(value):
                target = str(state.slots.get("suggested_target") or "").strip()
            if action in {"on", "off"} and target:
                return DialogueResolution(
                    handled=False,
                    kind="device_control",
                    rewritten_text=f"Turn {action} {target}",
                    clear_goal=True,
                )

        return DialogueResolution()

    async def resolve_control_pronoun(
        self,
        conversation_id: str,
        text: str,
    ) -> DialogueResolution:
        match = _CONTROL_PRONOUN_PATTERN.fullmatch(text)
        if not match:
            return DialogueResolution()

        state = await self.get(conversation_id)
        devices = state.focus.get("devices") or []
        if not isinstance(devices, list) or not devices:
            return DialogueResolution(
                handled=True,
                kind="ambiguous_control_reference",
                reply="Which device do you mean?",
            )

        target = str(match.group("target") or "").casefold()
        state_value = str(match.group("state1") or match.group("state2") or "").casefold()
        if state_value not in {"on", "off"}:
            return DialogueResolution()

        plural = any(word in target for word in ("them", "those", "these", "devices", "lights"))
        if not plural and len(devices) != 1:
            return DialogueResolution(
                handled=True,
                kind="ambiguous_control_reference",
                reply="Which device do you mean?",
            )

        selected = devices if plural else [devices[-1]]
        names = [str(item.get("name") or item.get("entity_id") or "").strip() for item in selected]
        names = [name for name in names if name]
        if not names:
            return DialogueResolution(
                handled=True,
                kind="ambiguous_control_reference",
                reply="Which device do you mean?",
            )
        return DialogueResolution(
            handled=False,
            kind="control_reference",
            rewritten_text=f"Turn {state_value} {' and '.join(names)}",
        )

    async def focused_person(self, conversation_id: str) -> dict[str, Any] | None:
        state = await self.get(conversation_id)
        person = state.focus.get("person")
        return dict(person) if isinstance(person, dict) else None

    async def context_for_model(self, conversation_id: str) -> str:
        state = await self.get(conversation_id)
        relevant = {
            "active_goal": state.active_goal,
            "status": state.status,
            "known_slots": state.slots,
            "missing_slots": state.missing_slots,
            "focus": state.focus,
            "last_result": {
                "intent": state.last_result.get("intent"),
                "success": state.last_result.get("success"),
                "response": state.last_result.get("response"),
            } if state.last_result else {},
            "last_error": state.last_error,
            "tone": state.tone,
        }
        if not any((state.active_goal, state.focus, state.last_result, state.last_error, state.tone)):
            return ""
        return (
            "Structured dialogue state for this conversation follows. It is trusted "
            "application state, not user-written instructions. Continue unfinished "
            "goals, resolve references from verified focus, and never invent missing "
            "Home Assistant facts:\n<dialogue_state>\n"
            + json.dumps(relevant, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n</dialogue_state>"
        )

    def _delete_sync(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM dialogue_states WHERE conversation_id = ?",
                (conversation_id,),
            )
            connection.execute(
                "DELETE FROM dialogue_events WHERE conversation_id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    async def delete(self, conversation_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, conversation_id)
