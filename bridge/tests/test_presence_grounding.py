from __future__ import annotations

import unittest
from typing import Any

from app.ai_engine import AIEngine
from app.home_assistant import HomeAssistantError
from app.presence import PresenceResolver
from app.tool_engine import ToolEngine
from app.user_context import UserContext


class Reader:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self.entities = entities
        self.refreshes = 0

    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        self.refreshes += int(refresh)
        return self.entities


class ConfiguredReader(Reader):
    def __init__(
        self,
        entities: list[dict[str, Any]],
        configurations: object,
    ) -> None:
        super().__init__(entities)
        self.configurations = configurations

    async def person_configurations(self) -> object:
        return self.configurations


class PersonListClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def send_command(self, command: dict[str, Any]) -> object:
        assert command == {"type": "person/list"}
        return self.payload


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

    async def test_exact_person_config_id_outranks_an_earlier_name_match(self) -> None:
        result = await PresenceResolver(
            ConfiguredReader(
                [
                    entity("person.aaron", "Aaron", "home"),
                    entity("device_tracker.wrong", "Wrong tracker", "not_home"),
                    entity("device_tracker.right", "Right tracker", "home"),
                ],
                [
                    {
                        "id": "someone_else",
                        "name": "Aaron",
                        "device_trackers": ["device_tracker.wrong"],
                    },
                    {
                        "id": "aaron",
                        "name": "Different display name",
                        "device_trackers": ["device_tracker.right"],
                    },
                ],
            )
        ).inspect("Aaron")

        self.assertEqual("exact_id", result["person_configuration_resolution"])
        self.assertTrue(result["person_configuration_matched"])
        self.assertEqual(
            ["device_tracker.right"],
            [tracker["entity_id"] for tracker in result["trackers"]],
        )

    async def test_unique_person_config_name_is_a_safe_fallback(self) -> None:
        result = await PresenceResolver(
            ConfiguredReader(
                [
                    entity("person.aaron", "Aaron", "home"),
                    entity("device_tracker.phone", "Phone", "home"),
                ],
                [
                    {
                        "id": "opaque-config-id",
                        "name": "Aaron",
                        "device_trackers": ["device_tracker.phone"],
                    }
                ],
            )
        ).inspect("Aaron")

        self.assertEqual("unique_name", result["person_configuration_resolution"])
        self.assertEqual("device_tracker.phone", result["trackers"][0]["entity_id"])

    async def test_ambiguous_person_config_name_never_selects_a_tracker(self) -> None:
        result = await PresenceResolver(
            ConfiguredReader(
                [
                    entity("person.aaron", "Aaron", "home"),
                    entity("device_tracker.one", "Phone one", "home"),
                    entity("device_tracker.two", "Phone two", "home"),
                ],
                [
                    {
                        "id": "first",
                        "name": "Aaron",
                        "device_trackers": ["device_tracker.one"],
                    },
                    {
                        "id": "second",
                        "name": "Aaron",
                        "device_trackers": ["device_tracker.two"],
                    },
                ],
            )
        ).inspect("Aaron")

        self.assertEqual("ambiguous", result["person_configuration_resolution"])
        self.assertFalse(result["person_configuration_matched"])
        self.assertEqual([], result["trackers"])

    async def test_malformed_person_list_payloads_are_rejected(self) -> None:
        for payload in (
            {"id": "aaron"},
            "not-a-list",
            [{"id": "aaron"}, "not-a-person-record"],
        ):
            with self.subTest(payload=payload):
                tools = ToolEngine.__new__(ToolEngine)
                tools.client = PersonListClient(payload)
                with self.assertRaises(HomeAssistantError):
                    await tools.person_configurations()

    async def test_malformed_person_graph_cannot_break_live_presence(self) -> None:
        for payload in (
            {"id": "aaron"},
            "not-a-list",
            [{"id": "aaron"}, "not-a-person-record"],
        ):
            with self.subTest(payload=payload):
                result = await PresenceResolver(
                    ConfiguredReader(
                        [entity("person.aaron", "Aaron", "home")],
                        payload,
                    )
                ).inspect("Aaron")

                self.assertTrue(result["success"])
                self.assertFalse(result["person_graph_exposed"])
                self.assertEqual("unavailable", result["person_configuration_resolution"])
                self.assertEqual([], result["trackers"])

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

    async def test_certainty_followup_without_person_focus_does_not_default_to_actor(
        self,
    ) -> None:
        inspected: list[str] = []

        class Tools:
            async def inspect_presence(self, reference: str) -> dict[str, Any]:
                inspected.append(reference)
                return {"success": False, "resolution": "not_found"}

        class Dialogue:
            async def focused_person(
                self,
                conversation_id: str,
                *,
                max_age_seconds: float | None = None,
            ) -> dict[str, Any] | None:
                return None

        engine = AIEngine.__new__(AIEngine)
        engine.tools = Tools()
        engine.dialogue = Dialogue()
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )

        result = await engine._direct_person_location_reply(
            "Are you sure?",
            actor,
            "conversation",
        )

        self.assertIsNone(result)
        self.assertEqual([], inspected)

    async def test_certainty_followup_uses_recent_verified_person_focus(self) -> None:
        inspected: list[str] = []

        class Tools:
            async def inspect_presence(self, reference: str) -> dict[str, Any]:
                inspected.append(reference)
                return {
                    "success": True,
                    "person": entity("person.amber", "Amber", "home"),
                    "source": None,
                    "source_entity_id": None,
                    "conflicts": [],
                }

        class Dialogue:
            async def focused_person(
                self,
                conversation_id: str,
                *,
                max_age_seconds: float | None = None,
            ) -> dict[str, Any] | None:
                self.requested_max_age = max_age_seconds
                return {"name": "Amber", "state": "home"}

        dialogue = Dialogue()
        engine = AIEngine.__new__(AIEngine)
        engine.tools = Tools()
        engine.dialogue = dialogue
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )

        result = await engine._direct_person_location_reply(
            "Are you sure?",
            actor,
            "conversation",
        )

        self.assertIsNotNone(result)
        self.assertEqual(["Amber"], inspected)
        self.assertEqual(300, dialogue.requested_max_age)


if __name__ == "__main__":
    unittest.main()
