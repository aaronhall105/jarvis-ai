from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "realtime_voice.py"
spec = importlib.util.spec_from_file_location("realtime_voice_v18", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class FakeUpstream:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class ConfigurationTests(unittest.TestCase):
    def test_release_and_modes(self) -> None:
        self.assertEqual(module.VERSION, "18.3.0")
        self.assertEqual(module.CORE_APPLICATION_VERSION, "3.1.0")
        self.assertEqual(module.normalise_conversation_mode("standard"), "standard")
        self.assertEqual(module.normalise_conversation_mode("other"), "live")
        self.assertEqual(module.normalise_eagerness("bad"), "high")

    def test_live_and_standard_session_contracts(self) -> None:
        config = module.RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="mobile",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron Amber Jarvis",
        )
        live = module.build_session_update(config, "cedar", "live", "high")
        live_vad = live["session"]["audio"]["input"]["turn_detection"]
        self.assertEqual(live_vad["type"], "semantic_vad")
        self.assertEqual(live_vad["eagerness"], "high")
        self.assertFalse(live_vad["create_response"])

        standard = module.build_session_update(config, "cedar", "standard", "low")
        self.assertIsNone(standard["session"]["audio"]["input"]["turn_detection"])

    def test_status_is_product_ready_and_secret_safe(self) -> None:
        config = module.RealtimeVoiceConfig(
            enabled=True,
            api_key="api-secret",
            mobile_token="mobile-secret",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron",
        )
        status = module.RealtimeVoiceProxy(config).status()
        encoded = json.dumps(status)
        self.assertTrue(status["persistent_sessions"])
        self.assertTrue(status["streaming_brain_text"])
        self.assertEqual(status["conversation_modes"], ["live", "standard"])
        self.assertNotIn("api-secret", encoded)
        self.assertNotIn("mobile-secret", encoded)

    def test_conversation_id_is_sanitised(self) -> None:
        value = module.normalise_conversation_id(" mobile-chat-1 / unsafe ", "fallback")
        self.assertEqual(value, "mobile-chat-1unsafe")


    def test_response_create_omits_unsupported_speed_parameter(self) -> None:
        event = module.speak_response_event(
            "Bedroom Floodlight is now on.",
            "marin",
        )
        output = event["response"]["audio"]["output"]

        self.assertEqual(event["type"], "response.create")
        self.assertEqual(output["voice"], "marin")
        self.assertNotIn("speed", output)


class BrainTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_turn_streams_and_does_not_speak(self) -> None:
        proxy = module.RealtimeVoiceProxy(
            module.RealtimeVoiceConfig(
                enabled=True,
                api_key="key",
                mobile_token="token",
                model="gpt-realtime",
                voice="marin",
                user_id="aaron",
                user_name="Aaron",
                user_is_admin=True,
                transcription_prompt="Aaron",
            )
        )
        client = FakeClient()
        upstream = FakeUpstream()

        async def brain(command: str, metadata: dict, on_delta):
            self.assertFalse(metadata["speak"])
            await on_delta("Amber ")
            await on_delta("is home")
            return {"success": True, "response": "Amber is home", "conversation_id": "mobile-chat-1"}

        state = {"generation": 1, "suppress_audio": False}
        await proxy._run_brain_turn(
            1,
            "Where is Amber?",
            False,
            client,
            upstream,
            brain,
            {"conversation_id": "mobile-chat-1"},
            "realtime",
            "marin",
            state,
        )
        types = [message["type"] for message in client.messages]
        self.assertEqual(types.count("brain.delta"), 2)
        self.assertIn("brain.response", types)
        self.assertIn("turn.done", types)
        self.assertEqual(upstream.messages, [])

    async def test_spoken_turn_uses_renderer_after_core(self) -> None:
        proxy = module.RealtimeVoiceProxy(
            module.RealtimeVoiceConfig(
                enabled=True,
                api_key="key",
                mobile_token="token",
                model="gpt-realtime",
                voice="marin",
                user_id="aaron",
                user_name="Aaron",
                user_is_admin=True,
                transcription_prompt="Aaron",
            )
        )
        client = FakeClient()
        upstream = FakeUpstream()

        async def brain(command: str, metadata: dict, on_delta):
            self.assertTrue(metadata["speak"])
            return {"success": True, "response": "Done"}

        await proxy._run_brain_turn(
            1,
            "Turn the light off",
            True,
            client,
            upstream,
            brain,
            {"conversation_id": "mobile-chat-1"},
            "realtime",
            "cedar",
            {"generation": 1, "suppress_audio": False},
        )
        self.assertEqual(upstream.messages[-1]["type"], "response.create")
        self.assertIn("Done", upstream.messages[-1]["response"]["instructions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
