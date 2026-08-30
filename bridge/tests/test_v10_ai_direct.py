import unittest

from app.ai_engine import AIEngine
from app.user_context import UserContext


class FakeTools:
    async def search_entity_states(self, **kwargs):
        return {
            "success": True,
            "entities": [
                {
                    "entity_id": "person.amber",
                    "friendly_name": "Amber",
                    "state": "home",
                }
            ],
        }


class DirectStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_person_location_fast_path(self):
        engine = AIEngine.__new__(AIEngine)
        engine.tools = FakeTools()
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )
        reply = await engine._direct_person_location_reply("where is Amber", actor)
        self.assertIsNotNone(reply)
        text, calls = reply
        self.assertEqual(text, "Amber is at home.")
        self.assertEqual(calls[0]["tool"], "search_entity_states")


if __name__ == "__main__":
    unittest.main()
