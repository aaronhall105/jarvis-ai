import json
import unittest

from app.ai_engine import AIEngine
from app.user_context import UserContext


class FakeRegistry:
    async def areas(self):
        return [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "bedroom", "name": "Bedroom"},
        ]


class FakeTools:
    READABLE_DOMAINS = {"binary_sensor", "sensor", "light"}
    ANNOUNCEMENT_TARGETS = ()
    MEDIA_SHORTCUTS = {}
    MEDIA_PLAYER_ENTITIES = {}
    MEDIA_ACTION_SERVICES = {}
    NOTIFICATION_SERVICES = {"aaron": "notify.aaron"}

    def __init__(self):
        self.calls = []

    async def controllable_devices(self):
        return []

    async def control_area_lights(self, area_id, turn_on):
        self.calls.append(("lights", area_id, turn_on))
        return {"success": True, "area_id": area_id}

    async def search_entity_states(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"success": True, "entities": []}


def actor(*, voice=True, area="living_room"):
    return UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        device_id="jarvis_voice_living_room",
        voice_mode=voice,
        area_id=area,
    )


class RoomAwareVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = AIEngine.__new__(AIEngine)
        self.engine.registry = FakeRegistry()
        self.engine.tools = FakeTools()
        self.engine.code_awareness = None

    async def execute(self, name, arguments, text, current_actor=None):
        return await self.engine._execute_function(
            name=name,
            arguments_json=json.dumps(arguments),
            user_text=text,
            authorised_tools={name},
            conversation_id="room-test",
            actor=current_actor or actor(),
        )

    async def test_unqualified_lights_are_for_satellite_room(self):
        result = await self.execute(
            "control_area_lights",
            {"area_id": "bedroom", "action": "turn_off"},
            "Turn the lights off",
        )
        self.assertEqual(result["arguments"]["area_id"], "living_room")
        self.assertEqual(self.engine.tools.calls, [("lights", "living_room", False)])

    async def test_explicit_room_always_overrides_satellite_room(self):
        result = await self.execute(
            "control_area_lights",
            {"area_id": "bedroom", "action": "turn_on"},
            "Turn the bedroom lights on",
        )
        self.assertEqual(result["arguments"]["area_id"], "bedroom")
        self.assertEqual(self.engine.tools.calls, [("lights", "bedroom", True)])

    async def test_local_window_query_is_scoped_to_satellite_room(self):
        result = await self.execute(
            "search_entity_states",
            {
                "query": "window",
                "domain": "binary_sensor",
                "area_id": None,
                "state_filter": None,
                "limit": 12,
            },
            "Is the window open?",
        )
        self.assertEqual(result["arguments"]["area_id"], "living_room")
        self.assertEqual(self.engine.tools.calls[0][1]["area_id"], "living_room")

    async def test_named_room_window_query_is_not_rewritten(self):
        result = await self.execute(
            "search_entity_states",
            {
                "query": "bedroom window",
                "domain": "binary_sensor",
                "area_id": "bedroom",
                "state_filter": None,
                "limit": 12,
            },
            "Is the bedroom window open?",
        )
        self.assertEqual(result["arguments"]["area_id"], "bedroom")

    async def test_mobile_request_has_no_implicit_room(self):
        result = await self.execute(
            "control_area_lights",
            {"area_id": "bedroom", "action": "turn_off"},
            "Turn the lights off",
            actor(voice=False),
        )
        self.assertEqual(result["arguments"]["area_id"], "bedroom")

    async def test_tool_contract_tells_model_the_local_area(self):
        definitions = await self.engine._home_control_tools(actor=actor())
        light_tool = next(item for item in definitions if item["name"] == "control_area_lights")
        self.assertIn("area_id living_room", light_tool["description"])


if __name__ == "__main__":
    unittest.main()
