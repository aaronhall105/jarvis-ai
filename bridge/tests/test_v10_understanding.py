import unittest

from app.registry import RegistrySnapshot
from app.understanding_engine import UnderstandingEngine
from app.user_context import UserContext


class FakeRegistry:
    def __init__(self):
        self.snapshot = RegistrySnapshot(
            areas=[
                {"area_id": "living_room", "name": "Living Room", "aliases": ["Lounge"]},
                {"area_id": "bedroom", "name": "Bedroom", "aliases": []},
            ],
            devices=[
                {
                    "id": "phone_a",
                    "name_by_user": "Aaron's Phone",
                    "name": "SM-G996B",
                    "model": "SM-G996B",
                },
                {
                    "id": "phone_b",
                    "name_by_user": "Amber Phone",
                    "name": "Amber Phone",
                    "model": "SM-S911U1",
                },
            ],
            entities=[
                {"entity_id": "person.amber", "name": "Amber", "original_name": "Amber"},
                {
                    "entity_id": "light.living_room_floodlight",
                    "name": "Living Room Floodlight",
                    "original_name": "Floodlight",
                },
                {
                    "entity_id": "media_player.samsung_tv",
                    "name": "Living Room TV",
                    "original_name": "TV",
                },
            ],
            states=[
                {
                    "entity_id": "person.amber",
                    "state": "home",
                    "attributes": {"friendly_name": "Amber"},
                },
            ],
            refreshed_at="now",
        )

    async def ensure_loaded(self):
        return self.snapshot


class UnderstandingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = UnderstandingEngine(FakeRegistry())
        self.actor = UserContext.from_request(
            user_id="aaron-id",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )

    async def test_where_us_amber(self):
        result = await self.engine.interpret("where us amber", [], self.actor)
        self.assertEqual(result.interpreted_text.casefold(), "where is amber")
        self.assertTrue(result.house_relevant)
        self.assertGreaterEqual(result.confidence, 0.98)

    async def test_living_room_light_typo(self):
        result = await self.engine.interpret("tun off the livin room lites", [], self.actor)
        self.assertEqual(result.interpreted_text.casefold(), "turn off the living room lights")
        self.assertFalse(result.needs_clarification)

    async def test_general_topic_not_forced_into_home(self):
        result = await self.engine.interpret("I want to be Samba", [], self.actor)
        self.assertEqual(result.interpreted_text, "I want to be Samba")
        self.assertFalse(result.house_relevant)

    async def test_private_activity_not_changed(self):
        result = await self.engine.interpret("is she having a poo?", [], self.actor)
        self.assertEqual(result.interpreted_text.casefold(), "is she having a poo?")

    async def test_known_person_typo(self):
        result = await self.engine.interpret("where is amebr", [], self.actor)
        self.assertEqual(result.interpreted_text.casefold(), "where is amber")

    async def test_email_literal_is_not_split_by_understanding(self):
        original = "Send an email to amber.gill1992@outlook.com asking if she's free for dinner."

        result = await self.engine.interpret(original, [], self.actor)

        self.assertIn(
            "amber.gill1992@outlook.com",
            result.interpreted_text,
        )
        self.assertNotIn(
            "amber. gill1992@outlook. com",
            result.interpreted_text,
        )

    def test_write_action_detection_is_public_and_conservative(self):
        self.assertTrue(self.engine.is_write_action("turn off the television"))
        self.assertTrue(self.engine.is_write_action("send Amber a notification"))
        self.assertFalse(self.engine.is_write_action("what time is it"))


if __name__ == "__main__":
    unittest.main()
