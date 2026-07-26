import unittest

from app.house_context import HouseContextEngine
from app.registry import RegistrySnapshot
from app.user_context import UserContext


class FakeClient:
    async def get_states(self):
        return [
            {"entity_id": "person.aaron", "state": "home", "attributes": {"friendly_name": "Aaron"}},
            {"entity_id": "person.amber", "state": "home", "attributes": {"friendly_name": "Amber"}},
            {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room Light"}},
            {"entity_id": "media_player.tv", "state": "playing", "attributes": {"friendly_name": "Living Room TV"}},
            {"entity_id": "sensor.phone_battery", "state": "42", "attributes": {"friendly_name": "Aaron Phone Battery", "unit_of_measurement": "%"}},
        ]


class FakeRegistry:
    def __init__(self):
        self.client = FakeClient()
        self.snapshot = RegistrySnapshot(states=[], refreshed_at="now")

    async def ensure_loaded(self):
        return self.snapshot


class HouseContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = HouseContextEngine(FakeRegistry())
        self.actor = UserContext.from_request(
            user_id="aaron", user_name="Aaron", user_is_admin=True,
            device_id=None, voice_mode=True,
        )

    async def test_presence_is_coarse(self):
        result = await self.engine.context_for("Where is Amber?", [], self.actor)
        self.assertIn("Amber presence: home", result.text)
        self.assertIn("does not reveal room or activity", result.text)

    async def test_broad_house_request_includes_active_devices(self):
        result = await self.engine.context_for("What is happening in the flat?", [], self.actor)
        self.assertIn("Living Room Light: on", result.text)
        self.assertIn("Living Room TV: playing", result.text)
        self.assertNotIn("Aaron Phone Battery", result.text)


if __name__ == "__main__":
    unittest.main()
