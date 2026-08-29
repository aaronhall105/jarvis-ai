from __future__ import annotations

import unittest
from typing import Any

from app.tool_engine import ToolEngine


def media_state(volume: float | None) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if volume is not None:
        attributes["volume_level"] = volume
    return {
        "success": True,
        "entity": {
            "entity_id": "media_player.bedroom_echo_pop",
            "state": "playing",
            "attributes": attributes,
        },
    }


class ServiceClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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


class VolumeToolEngine(ToolEngine):
    STATE_VERIFY_DELAYS = (0, 0, 0)

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.client = ServiceClient()
        self._states = iter(states)

    async def get_entity_state(self, entity_id: str) -> dict[str, Any]:
        self.asserted_entity_id = entity_id
        return next(self._states)


class MediaVolumeGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_available_mismatched_volume_readback_is_failure(self) -> None:
        engine = VolumeToolEngine(
            [media_state(0.25), media_state(0.25), media_state(0.25), media_state(0.25)]
        )

        result = await engine.set_media_volume(
            "media_player.bedroom_echo_pop",
            70,
        )

        self.assertFalse(result["success"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["verification_available"])
        self.assertTrue(result["command_accepted"])
        self.assertEqual(25, result["current_volume_percent"])
        self.assertIn("still reports 25%", result["response_message"])

    async def test_unavailable_volume_readback_is_accepted_but_unverified(self) -> None:
        engine = VolumeToolEngine(
            [media_state(None), media_state(None), media_state(None), media_state(None)]
        )

        result = await engine.set_media_volume(
            "media_player.bedroom_echo_pop",
            70,
        )

        self.assertTrue(result["success"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["verification_available"])
        self.assertTrue(result["command_accepted"])
        self.assertIsNone(result["current_volume_percent"])
        self.assertIn("requested level is not confirmed", result["response_message"])


if __name__ == "__main__":
    unittest.main()
