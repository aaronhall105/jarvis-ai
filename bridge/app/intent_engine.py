import re
from dataclasses import dataclass
from typing import Any

from app.registry import RegistryEngine
from app.tool_engine import ToolEngine


class IntentError(ValueError):
    pass


@dataclass
class ParsedIntent:
    intent: str
    area_id: str
    area_name: str
    confidence: float
    original_text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "confidence": self.confidence,
            "original_text": self.original_text,
        }


class IntentEngine:
    LIGHT_TERMS = {
        "light",
        "lights",
        "lamp",
        "lamps",
        "lighting",
    }

    TURN_ON_TERMS = {
        "turn on",
        "switch on",
        "put on",
        "lights on",
        "light on",
    }

    TURN_OFF_TERMS = {
        "turn off",
        "switch off",
        "shut off",
        "lights off",
        "light off",
    }

    def __init__(
        self,
        registry: RegistryEngine,
        tools: ToolEngine,
    ) -> None:
        self.registry = registry
        self.tools = tools

    @staticmethod
    def _normalise(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9\s'-]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    async def _area_aliases(self) -> dict[str, dict[str, str]]:
        snapshot = await self.registry.ensure_loaded()
        aliases: dict[str, dict[str, str]] = {}

        for area in snapshot.areas:
            area_id = area.get("area_id") or area.get("id")
            area_name = area.get("name")

            if not area_id or not area_name:
                continue

            possible_names = {
                self._normalise(area_id.replace("_", " ")),
                self._normalise(area_name),
            }

            for alias in area.get("aliases", []):
                if alias:
                    possible_names.add(self._normalise(alias))

            for possible_name in possible_names:
                aliases[possible_name] = {
                    "area_id": area_id,
                    "area_name": area_name,
                }

        return aliases

    async def resolve_area(
        self,
        text: str,
    ) -> dict[str, str]:
        normalised = self._normalise(text)
        aliases = await self._area_aliases()

        matches = [
            (alias, area)
            for alias, area in aliases.items()
            if re.search(
                rf"\b{re.escape(alias)}\b",
                normalised,
            )
        ]

        if not matches:
            raise IntentError("I could not determine which Home Assistant area you meant.")

        matches.sort(
            key=lambda item: len(item[0]),
            reverse=True,
        )

        return matches[0][1]

    def resolve_action(self, text: str) -> str:
        normalised = self._normalise(text)

        if any(phrase in normalised for phrase in self.TURN_OFF_TERMS):
            return "turn_off"

        if any(phrase in normalised for phrase in self.TURN_ON_TERMS):
            return "turn_on"

        raise IntentError("I could not determine whether you wanted the device on or off.")

    def resolve_domain(self, text: str) -> str:
        normalised = self._normalise(text)
        words = set(normalised.split())

        if words.intersection(self.LIGHT_TERMS):
            return "light"

        raise IntentError("I currently understand light-control requests only.")

    async def parse(self, text: str) -> ParsedIntent:
        if not text or not text.strip():
            raise IntentError("The request cannot be empty.")

        area = await self.resolve_area(text)
        action = self.resolve_action(text)
        domain = self.resolve_domain(text)

        return ParsedIntent(
            intent=f"{domain}.{action}",
            area_id=area["area_id"],
            area_name=area["area_name"],
            confidence=1.0,
            original_text=text,
        )

    async def execute(
        self,
        text: str,
    ) -> dict[str, Any]:
        parsed = await self.parse(text)

        if parsed.intent == "light.turn_on":
            result = await self.tools.control_area_lights(
                area_id=parsed.area_id,
                turn_on=True,
            )
        elif parsed.intent == "light.turn_off":
            result = await self.tools.control_area_lights(
                area_id=parsed.area_id,
                turn_on=False,
            )
        else:
            raise IntentError(f"Unsupported intent: {parsed.intent}")

        action_text = "turned on" if parsed.intent.endswith("turn_on") else "turned off"

        entity_count = len(result.get("entities", []))

        if result.get("success"):
            response_text = (
                f"I have {action_text} "
                f"{entity_count} light"
                f"{'' if entity_count == 1 else 's'} "
                f"in the {parsed.area_name}."
            )
        else:
            response_text = result.get(
                "message",
                "The request could not be completed.",
            )

        return {
            "success": bool(result.get("success")),
            "response": response_text,
            "parsed": parsed.as_dict(),
            "result": result,
        }
