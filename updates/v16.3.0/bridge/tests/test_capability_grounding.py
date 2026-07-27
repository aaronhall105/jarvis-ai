from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from app.capability_grounding import CapabilityGroundingEngine


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron Hall"


class FakeClient:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = states
        self.calls: list[dict[str, Any]] = []

    async def get_states(self) -> list[dict[str, Any]]:
        return self.states

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_ids: list[str] | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "entity_ids": entity_ids,
                "service_data": service_data,
            }
        )
        if domain == "light" and service == "turn_on":
            requested = int((service_data or {}).get("brightness_pct", 100))
            for state in self.states:
                if state.get("entity_id") in (entity_ids or []):
                    state["state"] = "on"
                    state.setdefault("attributes", {})["brightness"] = round(
                        requested * 255 / 100
                    )


class FakeTools:
    MEDIA_PLAYER_ENTITIES = {
        "media_player.bedroom_echo_pop": "Bedroom Echo Pop",
    }
    MEDIA_ACTION_SERVICES = {
        "play": ("media_play", {}),
        "pause": ("media_pause", {}),
        "stop": ("media_stop", {}),
        "mute": ("volume_mute", {"is_volume_muted": True}),
        "unmute": ("volume_mute", {"is_volume_muted": False}),
    }

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.client = FakeClient(states)

    async def controllable_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": state["entity_id"],
                "domain": state["entity_id"].split(".", 1)[0],
                "name": state["attributes"].get("friendly_name", state["entity_id"]),
                "area_name": state["attributes"].get("area_name"),
                "available": state["state"] not in {"unknown", "unavailable"},
            }
            for state in self.client.states
            if state["entity_id"].split(".", 1)[0] in {"light", "switch"}
        ]

    async def readable_entity_states(
        self,
        *,
        refresh: bool = True,
    ) -> list[dict[str, Any]]:
        del refresh
        return [
            {
                "entity_id": state["entity_id"],
                "domain": state["entity_id"].split(".", 1)[0],
                "name": state["attributes"].get("friendly_name", state["entity_id"]),
                "area_name": state["attributes"].get("area_name"),
                "state": state["state"],
            }
            for state in self.client.states
        ]


def state(
    entity_id: str,
    name: str,
    area: str,
    modes: list[str] | None = None,
    brightness: int | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "friendly_name": name,
        "area_name": area,
    }
    if modes is not None:
        attributes["supported_color_modes"] = modes
    if brightness is not None:
        attributes["brightness"] = brightness
    return {
        "entity_id": entity_id,
        "state": "on",
        "attributes": attributes,
    }


class CapabilityGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_nondimmable_floodlight_is_rejected_without_service_call(self) -> None:
        tools = FakeTools(
            [state("light.bedroom_floodlight", "Bedroom Floodlight", "Bedroom", ["onoff"])]
        )
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="dim the bedroom floodlight",
            history=[],
            actor=Actor(),
        )
        self.assertTrue(result.handled)
        self.assertIn("only supports", result.response)
        self.assertIn("not dimmable", result.response)
        self.assertEqual([], tools.client.calls)
        self.assertFalse(result.details["supported"])

    async def test_dimmable_light_is_changed_and_verified(self) -> None:
        tools = FakeTools(
            [
                state(
                    "light.bedroom_lamp",
                    "Bedroom Lamp",
                    "Bedroom",
                    ["brightness"],
                    brightness=255,
                )
            ]
        )
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="dim the bedroom lamp to 40 percent",
            history=[],
            actor=Actor(),
        )
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        self.assertEqual("Dimmed Bedroom Lamp to 40%.", result.response)
        self.assertEqual(1, len(tools.client.calls))
        self.assertEqual({"brightness_pct": 40}, tools.client.calls[0]["service_data"])
        self.assertEqual("set_light_brightness", result.calls[0]["tool"])
        self.assertTrue(result.calls[0]["result"]["verified"])

    async def test_recent_context_resolves_generic_floodlight_reference(self) -> None:
        tools = FakeTools(
            [
                state("light.bedroom_floodlight", "Bedroom Floodlight", "Bedroom", ["onoff"]),
                state("light.living_room_floodlight", "Living Room Floodlight", "Living Room", ["brightness"]),
            ]
        )
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="dim the floodlight",
            history=[
                {"role": "user", "content": "turn on bedroom floodlight"},
                {"role": "assistant", "content": "Bedroom Floodlight is now on."},
            ],
            actor=Actor(),
        )
        self.assertIn("Bedroom Floodlight", result.response)
        self.assertEqual([], tools.client.calls)

    async def test_capability_question_lists_only_verified_controls(self) -> None:
        tools = FakeTools(
            [state("light.bedroom_floodlight", "Bedroom Floodlight", "Bedroom", ["onoff"])]
        )
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="what can you do with the bedroom floodlight",
            history=[],
            actor=Actor(),
        )
        self.assertTrue(result.handled)
        self.assertIn("turn Bedroom Floodlight on", result.response)
        self.assertIn("or off", result.response)
        self.assertNotIn("brightness", result.response.casefold())

    async def test_plain_on_off_is_left_for_existing_verified_tool_path(self) -> None:
        tools = FakeTools(
            [state("light.bedroom_floodlight", "Bedroom Floodlight", "Bedroom", ["onoff"])]
        )
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="turn off the bedroom floodlight",
            history=[],
            actor=Actor(),
        )
        self.assertFalse(result.handled)

    async def test_correction_does_not_double_down(self) -> None:
        tools = FakeTools([])
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="you cant do that",
            history=[],
            actor=Actor(),
        )
        self.assertTrue(result.handled)
        self.assertIn("must not claim", result.response)

    async def test_unsupported_lock_action_is_rejected(self) -> None:
        lock_state = state("lock.front_door", "Front Door", "Hallway")
        tools = FakeTools([lock_state])
        engine = CapabilityGroundingEngine(tools)
        result = await engine.handle(
            text="unlock the front door",
            history=[],
            actor=Actor(),
        )
        self.assertTrue(result.handled)
        self.assertIn("can’t perform", result.response)
        self.assertEqual([], tools.client.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
