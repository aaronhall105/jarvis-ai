from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


class ActorProtocol(Protocol):
    user_key: str
    display_name: str


class ToolProtocol(Protocol):
    client: Any
    MEDIA_PLAYER_ENTITIES: dict[str, str]
    MEDIA_ACTION_SERVICES: dict[str, tuple[str, dict[str, Any]]]

    async def controllable_devices(self) -> list[dict[str, Any]]: ...

    async def readable_entity_states(
        self,
        *,
        refresh: bool = True,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CapabilityCommandResult:
    handled: bool
    response: str = ""
    success: bool = True
    intent: str = "capability_grounding"
    continue_conversation: bool = False
    details: dict[str, Any] | None = None
    calls: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class EntityCapability:
    entity_id: str
    domain: str
    name: str
    area_name: str | None
    state: str
    attributes: dict[str, Any]
    supported_actions: tuple[str, ...]
    implemented_actions: tuple[str, ...]


_BRIGHTNESS_ACTION = re.compile(
    r"\b(?:dim|dimmed|dimming|brighten|brightened|brightness|"
    r"make\s+(?:it|the|that|this)?\s*(?:light|lamp|floodlight)?\s*"
    r"(?:brighter|dimmer)|set\s+.+?\s+to\s+\d{1,3}\s*(?:%|percent))\b",
    re.I,
)
_BRIGHTNESS_QUESTION = re.compile(
    r"^\s*(?:can|could|would)\s+you\s+(?:dim|brighten)|"
    r"^\s*(?:can|does|is)\s+.+?\s+(?:dim|dimmable|support\s+dimming)|"
    r"^\s*(?:how\s+(?:do|can)\s+i|is\s+it\s+possible\s+to)\s+(?:dim|brighten)",
    re.I,
)
_CAPABILITY_QUESTION = re.compile(
    r"^\s*(?:what|which)\s+(?:can|could)\s+you\s+(?:do|control)\s+(?:with|on)\s+|"
    r"^\s*what\s+(?:can|does)\s+.+?\s+(?:do|support)|"
    r"^\s*what\s+can\s+you\s+do\s+with\s+|"
    r"^\s*can\s+you\s+(?:control|operate|dim|brighten|change|set)\s+",
    re.I,
)
_CORRECTION = re.compile(
    r"^\s*(?:you\s+(?:can(?:not|'t)|cant|could(?:not|n't)|couldnt)\s+do\s+that|"
    r"you\s+(?:did(?:\s+not|n't)|didnt)\s+do\s+that|that\s+(?:did(?:\s+not|n't)|didnt)\s+happen|"
    r"that\s+(?:was(?:\s+not|n't)|wasnt)\s+done|you\s+made\s+that\s+up|"
    r"that(?:'s|s|\s+is)\s+wrong)\s*[.!?]*\s*$",
    re.I,
)
_UNSUPPORTED_DEVICE_ACTION = re.compile(
    r"\b(?:change|set|adjust)\b.{0,80}\b(?:colou?r|temperature|speed|position|"
    r"mode|effect)\b|\b(?:lock|unlock|open|close)\b.{0,80}\b(?:door|lock|"
    r"blind|blinds|curtain|curtains|cover|garage|window)|"
    r"\b(?:start|stop)\b.{0,80}\b(?:washing\s+machine|washer|dryer|dishwasher|oven)\b",
    re.I,
)
_PERCENT = re.compile(r"(?P<value>\d{1,3})\s*(?:%|percent)\b", re.I)
_PRONOUN_TARGET = re.compile(
    r"\b(?:it|that|this|the\s+(?:light|lamp|floodlight|switch|device))\b",
    re.I,
)

_BRIGHTNESS_MODES = {
    "brightness",
    "color_temp",
    "hs",
    "xy",
    "rgb",
    "rgbw",
    "rgbww",
    "white",
}
_COLOUR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}

_ACTION_LABELS = {
    "turn_on": "turn it on",
    "turn_off": "turn it off",
    "set_brightness": "set its brightness",
    "set_colour": "change its colour",
    "set_colour_temperature": "change its colour temperature",
    "play": "play",
    "pause": "pause",
    "stop": "stop playback",
    "mute": "mute",
    "unmute": "unmute",
    "set_volume": "set the volume",
}


class CapabilityGroundingEngine:
    """Ground device suggestions and write actions in live HA capabilities."""

    VERIFY_DELAYS = (0.25, 0.5, 0.9)

    def __init__(self, tools: ToolProtocol) -> None:
        self.tools = tools

    @staticmethod
    def _normalise(value: str) -> str:
        value = str(value or "").casefold().replace("_", " ")
        value = re.sub(r"[^a-z0-9%\s'-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _raw_modes(attributes: dict[str, Any]) -> set[str]:
        value = attributes.get("supported_color_modes")
        if isinstance(value, (list, tuple, set)):
            return {str(item).casefold() for item in value if str(item).strip()}
        if isinstance(value, str) and value.strip():
            return {value.casefold()}
        return set()

    @classmethod
    def _light_supports_brightness(cls, attributes: dict[str, Any]) -> bool:
        modes = cls._raw_modes(attributes)
        if modes:
            return bool(modes & _BRIGHTNESS_MODES) and modes != {"onoff"}
        return attributes.get("brightness") is not None

    @classmethod
    def _light_supports_colour(cls, attributes: dict[str, Any]) -> bool:
        return bool(cls._raw_modes(attributes) & _COLOUR_MODES)

    @classmethod
    def _light_supports_colour_temperature(cls, attributes: dict[str, Any]) -> bool:
        return "color_temp" in cls._raw_modes(attributes)

    @classmethod
    def _actions_for(
        cls,
        domain: str,
        attributes: dict[str, Any],
        *,
        media_allowlisted: bool,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        supported: list[str] = []
        implemented: list[str] = []

        if domain in {"light", "switch"}:
            supported.extend(["turn_on", "turn_off"])
            implemented.extend(["turn_on", "turn_off"])

        if domain == "light":
            if cls._light_supports_brightness(attributes):
                supported.append("set_brightness")
                implemented.append("set_brightness")
            if cls._light_supports_colour(attributes):
                supported.append("set_colour")
            if cls._light_supports_colour_temperature(attributes):
                supported.append("set_colour_temperature")

        if domain == "media_player" and media_allowlisted:
            media_actions = [
                "play",
                "pause",
                "stop",
                "mute",
                "unmute",
                "set_volume",
            ]
            supported.extend(media_actions)
            implemented.extend(media_actions)

        return tuple(dict.fromkeys(supported)), tuple(dict.fromkeys(implemented))

    async def _inventory(self) -> list[EntityCapability]:
        readable = await self.tools.readable_entity_states(refresh=True)
        raw_states = await self.tools.client.get_states()
        raw_lookup = {
            str(item.get("entity_id") or ""): item for item in raw_states if item.get("entity_id")
        }
        controllable = {
            str(item.get("entity_id")): item
            for item in await self.tools.controllable_devices()
            if item.get("entity_id")
        }
        media_ids = set(self.tools.MEDIA_PLAYER_ENTITIES)

        inventory: list[EntityCapability] = []
        for item in readable:
            entity_id = str(item.get("entity_id") or "")
            if not entity_id or "." not in entity_id:
                continue
            domain = entity_id.split(".", 1)[0]
            raw = raw_lookup.get(entity_id, {})
            attrs = raw.get("attributes")
            attributes = dict(attrs) if isinstance(attrs, dict) else {}
            supported, implemented = self._actions_for(
                domain,
                attributes,
                media_allowlisted=entity_id in media_ids,
            )
            # Limit the inventory to entities Jarvis can control or that are commonly
            # mistaken for controllable entities. This keeps matching deterministic.
            if not supported and domain not in {
                "lock",
                "cover",
                "climate",
                "fan",
                "humidifier",
                "vacuum",
                "valve",
                "water_heater",
            }:
                continue

            source = controllable.get(entity_id, item)
            inventory.append(
                EntityCapability(
                    entity_id=entity_id,
                    domain=domain,
                    name=str(source.get("name") or item.get("name") or entity_id),
                    area_name=(
                        str(source.get("area_name") or item.get("area_name"))
                        if source.get("area_name") or item.get("area_name")
                        else None
                    ),
                    state=str(raw.get("state") or item.get("state") or "unknown"),
                    attributes=attributes,
                    supported_actions=supported,
                    implemented_actions=implemented,
                )
            )
        return inventory

    def _score_candidate(self, text: str, entity: EntityCapability) -> int:
        value = self._normalise(text)
        name = self._normalise(entity.name)
        area = self._normalise(entity.area_name or "")
        entity_tail = self._normalise(entity.entity_id.split(".", 1)[-1])
        domain_terms = {
            "light": {"light", "lamp", "floodlight", "led"},
            "switch": {"switch", "plug"},
            "media_player": {"speaker", "echo", "tv", "television", "player"},
            "lock": {"lock", "door"},
            "cover": {"blind", "blinds", "curtain", "curtains", "cover", "garage"},
            "climate": {"thermostat", "heating", "temperature"},
            "fan": {"fan"},
        }.get(entity.domain, {entity.domain.replace("_", " ")})

        score = 0
        if name and name in value:
            score += 240 + len(name)
        if entity_tail and entity_tail in value:
            score += 160
        if area and area in value:
            score += 100
        if any(re.search(rf"\b{re.escape(term)}s?\b", value) for term in domain_terms):
            score += 35
        name_terms = {term for term in name.split() if len(term) >= 3}
        score += 8 * len(name_terms & set(value.split()))
        return score

    def _history_candidate(
        self,
        history: Sequence[dict[str, Any]],
        inventory: Sequence[EntityCapability],
    ) -> EntityCapability | None:
        for message in reversed(history[-12:]):
            content = self._normalise(str(message.get("content") or ""))
            if not content:
                continue
            ranked = sorted(
                ((self._score_candidate(content, item), item) for item in inventory),
                key=lambda pair: pair[0],
                reverse=True,
            )
            if ranked and ranked[0][0] >= 100:
                if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
                    return ranked[0][1]
        return None

    def _resolve(
        self,
        text: str,
        history: Sequence[dict[str, Any]],
        inventory: Sequence[EntityCapability],
    ) -> tuple[EntityCapability | None, list[EntityCapability]]:
        ranked = sorted(
            ((self._score_candidate(text, item), item) for item in inventory),
            key=lambda pair: (pair[0], pair[1].name),
            reverse=True,
        )
        positive = [item for score, item in ranked if score > 0]
        if ranked and ranked[0][0] >= 100:
            top_score = ranked[0][0]
            tied = [item for score, item in ranked if score == top_score]
            if len(tied) == 1:
                return tied[0], positive

        contextual = self._history_candidate(history, inventory)
        if contextual is not None and (
            _PRONOUN_TARGET.search(text) or not positive or contextual in positive[:4]
        ):
            return contextual, positive

        if len(positive) == 1:
            return positive[0], positive
        return None, positive

    @staticmethod
    def _action_list(entity: EntityCapability) -> str:
        actions = list(entity.implemented_actions)
        if actions == ["turn_on", "turn_off"]:
            return "on and off"
        labels = [_ACTION_LABELS[action] for action in actions if action in _ACTION_LABELS]
        if not labels:
            return "no verified controls"
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]} or {labels[1]}"
        return f"{', '.join(labels[:-1])}, or {labels[-1]}"

    @staticmethod
    def _capability_response(entity: EntityCapability) -> str:
        actions = set(entity.implemented_actions)
        parts: list[str] = []
        if {"turn_on", "turn_off"}.issubset(actions):
            parts.append(f"turn {entity.name} on or off")
        if "set_brightness" in actions:
            parts.append("set its brightness")
        if "play" in actions:
            parts.append("control playback")
        if "set_volume" in actions:
            parts.append("set its volume")
        if not parts:
            return f"I have no verified controls for {entity.name}."
        if len(parts) == 1:
            return f"I can {parts[0]}."
        return f"I can {', '.join(parts[:-1])}, and {parts[-1]}."

    @staticmethod
    def _brightness_pct(attributes: dict[str, Any]) -> int | None:
        raw = attributes.get("brightness")
        if isinstance(raw, (int, float)):
            return max(0, min(100, round(float(raw) * 100 / 255)))
        return None

    def _requested_brightness(self, text: str, entity: EntityCapability) -> int:
        match = _PERCENT.search(text)
        if match:
            return max(1, min(100, int(match.group("value"))))

        current = self._brightness_pct(entity.attributes)
        value = self._normalise(text)
        if "brighten" in value or "brighter" in value:
            return min(100, (current if current is not None else 60) + 20)
        if "dim" in value or "dimmer" in value:
            return max(1, (current if current is not None else 70) - 20)
        return 50

    async def _set_brightness(
        self,
        entity: EntityCapability,
        percentage: int,
    ) -> CapabilityCommandResult:
        if entity.domain != "light" or "set_brightness" not in entity.implemented_actions:
            response = (
                f"The {entity.name} only supports {self._action_list(entity)}, "
                "so it is not dimmable."
            )
            return CapabilityCommandResult(
                handled=True,
                response=response,
                success=True,
                details={
                    "entity_id": entity.entity_id,
                    "requested_action": "set_brightness",
                    "supported": False,
                    "implemented_actions": list(entity.implemented_actions),
                },
            )

        if entity.state.casefold() in {"unavailable", "unknown", ""}:
            return CapabilityCommandResult(
                handled=True,
                response=f"The {entity.name} is currently unavailable.",
                success=True,
                details={
                    "entity_id": entity.entity_id,
                    "requested_action": "set_brightness",
                    "supported": True,
                    "available": False,
                },
            )

        await self.tools.client.call_service(
            domain="light",
            service="turn_on",
            entity_ids=[entity.entity_id],
            service_data={"brightness_pct": percentage},
        )

        final_pct: int | None = None
        for delay in self.VERIFY_DELAYS:
            await asyncio.sleep(delay)
            states = await self.tools.client.get_states()
            state = next(
                (item for item in states if str(item.get("entity_id") or "") == entity.entity_id),
                None,
            )
            if state is None:
                continue
            attrs = state.get("attributes")
            final_pct = self._brightness_pct(attrs if isinstance(attrs, dict) else {})
            if final_pct is not None and abs(final_pct - percentage) <= 3:
                break

        verified = final_pct is not None and abs(final_pct - percentage) <= 3
        if verified:
            response = f"Dimmed {entity.name} to {percentage}%."
        else:
            response = (
                f"I sent the brightness command to {entity.name}, but Home Assistant "
                "did not confirm the requested level."
            )
        result = {
            "success": verified,
            "verified": verified,
            "entity_id": entity.entity_id,
            "name": entity.name,
            "requested_brightness_pct": percentage,
            "current_brightness_pct": final_pct,
            "response_message": response,
        }
        call = {
            "tool": "set_light_brightness",
            "arguments": {
                "entity_id": entity.entity_id,
                "brightness_pct": percentage,
            },
            "result": result,
        }
        return CapabilityCommandResult(
            handled=True,
            response=response,
            success=verified,
            details=result,
            calls=(call,),
        )

    async def handle(
        self,
        *,
        text: str,
        history: Sequence[dict[str, Any]],
        actor: ActorProtocol,
    ) -> CapabilityCommandResult:
        del actor  # Reserved for future per-user capability policies.
        value = self._normalise(text)

        if _CORRECTION.match(value):
            return CapabilityCommandResult(
                handled=True,
                response=(
                    "You’re right. I must not claim a device action unless Home "
                    "Assistant confirms it."
                ),
                details={"correction_acknowledged": True},
            )

        brightness_request = bool(_BRIGHTNESS_ACTION.search(value))
        capability_question = bool(_CAPABILITY_QUESTION.search(value))
        unsupported_request = bool(_UNSUPPORTED_DEVICE_ACTION.search(value))
        if not (brightness_request or capability_question or unsupported_request):
            return CapabilityCommandResult(handled=False)

        inventory = await self._inventory()
        entity, candidates = self._resolve(value, history, inventory)
        if entity is None:
            if candidates:
                names = []
                for item in candidates[:4]:
                    if item.name not in names:
                        names.append(item.name)
                return CapabilityCommandResult(
                    handled=True,
                    response="Which device do you mean — " + ", ".join(names) + "?",
                    continue_conversation=True,
                    details={
                        "clarification_required": True,
                        "candidate_entity_ids": [item.entity_id for item in candidates[:4]],
                    },
                )
            return CapabilityCommandResult(
                handled=True,
                response=(
                    "I couldn’t match that request to a controllable Home Assistant "
                    "device, so I haven’t claimed the action was completed."
                ),
                details={"matched": False},
            )

        if capability_question or _BRIGHTNESS_QUESTION.search(value):
            if brightness_request or "dim" in value or "brightness" in value:
                if "set_brightness" in entity.implemented_actions:
                    response = f"Yes. I can set the brightness of {entity.name}."
                else:
                    response = (
                        f"No. The {entity.name} only supports "
                        f"{self._action_list(entity)}; it is not dimmable."
                    )
            else:
                response = self._capability_response(entity)
                unsupported = [
                    action
                    for action in entity.supported_actions
                    if action not in entity.implemented_actions
                ]
                if unsupported:
                    response += (
                        " The device reports additional features, but Jarvis does not "
                        "yet have verified controls for them."
                    )
            return CapabilityCommandResult(
                handled=True,
                response=response,
                details={
                    "entity_id": entity.entity_id,
                    "supported_actions": list(entity.supported_actions),
                    "implemented_actions": list(entity.implemented_actions),
                },
            )

        if brightness_request:
            return await self._set_brightness(
                entity,
                self._requested_brightness(value, entity),
            )

        # A write request was recognised, but there is no verified Jarvis tool for it.
        return CapabilityCommandResult(
            handled=True,
            response=(
                f"I can’t perform that action on {entity.name}. Its verified Jarvis "
                f"controls are limited to {self._action_list(entity)}."
            ),
            details={
                "entity_id": entity.entity_id,
                "requested_action_supported": False,
                "implemented_actions": list(entity.implemented_actions),
            },
        )
