from __future__ import annotations

import unittest
from typing import Any

from app.ai_engine import AIEngine
from app.presence import PresenceResolver
from app.user_context import UserContext


class Reader:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self.entities = entities
        self.refreshes = 0

    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        self.refreshes += int(refresh)
        return self.entities


def entity(entity_id: str, name: str, state: str, **attributes: Any) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "name": name,
        "domain": entity_id.split(".", 1)[0],
        "state": state,
        "available": state not in {"unknown", "unavailable"},
        "attributes": attributes,
    }


class PresenceGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_person_state_has_real_source(self) -> None:
        resolver = PresenceResolver(
            Reader(
                [
                    entity("person.aaron", "Aaron", "home", source="device_tracker.aaron_phone"),
                    entity("device_tracker.aaron_phone", "Aaron Phone", "home"),
                ]
            )
        )
        result = await resolver.inspect("Aaron")
        self.assertTrue(result["success"])
        self.assertEqual("device_tracker.aaron_phone", result["source_entity_id"])
        self.assertEqual("Aaron Phone", result["source"]["name"])

    async def test_conflicting_source_is_reported_without_inference(self) -> None:
        resolver = PresenceResolver(
            Reader(
                [
                    entity("person.aaron", "Aaron", "home", source="device_tracker.aaron_phone"),
                    entity("device_tracker.aaron_phone", "Aaron Phone", "not_home"),
                    entity("device_tracker.aaron_watch", "Aaron Watch", "unavailable"),
                ]
            )
        )
        result = await resolver.inspect("Aaron")
        self.assertEqual("home", result["person"]["state"])
        self.assertEqual("not_home", result["source"]["state"])
        self.assertEqual("source_state_differs_from_person", result["conflicts"][0]["reason"])
        self.assertNotIn("generic", str(result).casefold())

    async def test_missing_source_never_creates_a_synthetic_tracker(self) -> None:
        resolver = PresenceResolver(
            Reader(
                [
                    entity("person.aaron", "Aaron", "home"),
                    entity("device_tracker.aaron_phone", "Aaron Phone", "not_home"),
                ]
            )
        )
        result = await resolver.inspect("Aaron")
        self.assertTrue(result["success"])
        self.assertIsNone(result["source"])
        self.assertFalse(result["source_established"])
        self.assertEqual([], result["trackers"])

    async def test_duplicate_people_are_ambiguous(self) -> None:
        resolver = PresenceResolver(
            Reader(
                [
                    entity("person.aaron", "Aaron", "home"),
                    entity("person.aaron_guest", "Aaron", "not_home"),
                ]
            )
        )
        result = await resolver.inspect("Aaron")
        self.assertFalse(result["success"])
        self.assertEqual("ambiguous", result["resolution"])

    async def test_unique_partial_person_name_is_grounded_but_coordinates_are_not_exposed(
        self,
    ) -> None:
        result = await PresenceResolver(
            Reader(
                [
                    entity(
                        "person.aaron_hall",
                        "Aaron Hall",
                        "home",
                        source="device_tracker.aaron_phone",
                        latitude=1.0,
                        longitude=2.0,
                    ),
                    entity("device_tracker.aaron_phone", "Aaron Phone", "home"),
                ]
            )
        ).inspect("Aaron")
        self.assertTrue(result["success"])
        self.assertEqual("person.aaron_hall", result["person"]["entity_id"])
        self.assertNotIn("latitude", result["person"]["attributes"])
        self.assertNotIn("longitude", result["person"]["attributes"])

    async def test_removed_cached_source_is_not_returned(self) -> None:
        # The resolver gets one fresh state graph; absent registry/cache entries
        # cannot be revived as an action or explanatory target.
        reader = Reader(
            [entity("person.aaron", "Aaron", "home", source="device_tracker.old_phone")]
        )
        result = await PresenceResolver(reader).inspect("Aaron")
        self.assertEqual(1, reader.refreshes)
        self.assertIsNone(result["source"])
        self.assertEqual("device_tracker.old_phone", result["source_entity_id"])

    async def test_direct_reply_reports_a_real_conflict_without_inventing_cause(self) -> None:
        class Tools:
            async def inspect_presence(self, reference: str) -> dict[str, Any]:
                return await PresenceResolver(
                    Reader(
                        [
                            entity(
                                "person.aaron", "Aaron", "home", source="device_tracker.aaron_phone"
                            ),
                            entity("device_tracker.aaron_phone", "Aaron Phone", "not_home"),
                            entity("device_tracker.aaron_watch", "Aaron Watch", "unavailable"),
                        ]
                    )
                ).inspect(reference)

        engine = AIEngine.__new__(AIEngine)
        engine.tools = Tools()
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )
        reply = await engine._direct_person_location_reply("Where am I?", actor)
        self.assertIsNotNone(reply)
        text, calls = reply
        self.assertIn("Home Assistant currently reports Aaron is at home", text)
        self.assertIn("Aaron Phone currently reports not_home", text)
        self.assertIn("physical presence", text)
        self.assertNotIn("generic presence", text.casefold())
        self.assertEqual("inspect_presence", calls[0]["tool"])


if __name__ == "__main__":
    unittest.main()
