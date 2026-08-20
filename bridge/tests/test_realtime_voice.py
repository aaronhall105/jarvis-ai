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
        self.assertRegex(
            module.VERSION,
            r"^19\.0\.0-alpha\d+(?:\.\d+)?$",
        )
        self.assertRegex(
            module.CORE_APPLICATION_VERSION,
            r"^\d+\.\d+\.\d+$",
        )
        self.assertEqual(module.normalise_conversation_mode("standard"), "standard")
        self.assertEqual(module.normalise_conversation_mode("other"), "live")
        self.assertEqual(module.normalise_eagerness("bad"), "high")

    def test_live_and_standard_session_contracts(self) -> None:
        config = module.RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="mobile",
                        voice_pe_token="voice-pe-test-token",
model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron Amber Jarvis",
        )
        live = module.build_session_update(config, "cedar", "live", "high")
        live_vad = live["session"]["audio"]["input"]["turn_detection"]
        self.assertEqual(live_vad["type"], "server_vad")
        self.assertEqual(live_vad["threshold"], 0.85)
        self.assertEqual(live_vad["silence_duration_ms"], 500)
        self.assertFalse(live_vad["create_response"])
        self.assertTrue(live_vad["interrupt_response"])

        standard = module.build_session_update(config, "cedar", "standard", "low")
        self.assertIsNone(standard["session"]["audio"]["input"]["turn_detection"])

    def test_status_is_product_ready_and_secret_safe(self) -> None:
        config = module.RealtimeVoiceConfig(
            enabled=True,
            api_key="api-secret",
            mobile_token="mobile-secret",
                        voice_pe_token="voice-pe-test-token",
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
                                voice_pe_token="voice-pe-test-token",
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
                                voice_pe_token="voice-pe-test-token",
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


class VoicePEWakeResidueTests(unittest.IsolatedAsyncioTestCase):
    def _proxy(self):
        return module.RealtimeVoiceProxy(
            module.RealtimeVoiceConfig(
                enabled=True,
                api_key="key",
                mobile_token="token",
                voice_pe_token="voice-pe-test-token",
                model="gpt-realtime",
                voice="marin",
                user_id="aaron",
                user_name="Aaron",
                user_is_admin=True,
                transcription_prompt="Aaron Amber Jarvis",
            )
        )

    async def test_first_short_wake_residue_is_dropped_when_next_speech_has_started(
        self,
    ) -> None:
        proxy = self._proxy()
        client = FakeClient()
        commands: list[str] = []

        async def brain(command: str, metadata: dict, on_delta):
            return {"success": True, "response": "unused"}

        async def fake_start_brain_turn(*args):
            commands.append(str(args[0]))

        proxy._start_brain_turn = fake_start_brain_turn

        events = [
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "input_audio_buffer.speech_started"},
            {
                "type": (
                    "conversation.item."
                    "input_audio_transcription.completed"
                ),
                "transcript": "Aaron",
            },
        ]

        async def event_stream():
            for index, event in enumerate(events):
                yield json.dumps(event)

                # Reproduce the real Voice PE failure observed on
                # 2026-08-09: the first bogus VAD segment lasted
                # about 515 ms before the real utterance began.
                if index == 0:
                    state["voice_pe_speech_started_at"] = (
                        module.time.monotonic() - 0.515
                    )

        state = {
            "generation": 0,
            "suppress_audio": False,
            "voice_pe_session_started_at": module.time.monotonic(),
        }

        await proxy._openai_to_client(
            client,
            event_stream(),
            brain,
            {
                "client_kind": "voice_pe",
                "conversation_id": "voice-pe-test",
                "user_name": "Aaron",
            },
            "realtime",
            "live",
            "marin",
            set(),
            state,
        )

        self.assertEqual(commands, [])
        self.assertTrue(state.get("voice_pe_wake_guard_used"))
        self.assertEqual(state.get("voice_pe_transcripts_seen"), 1)
        self.assertEqual(state.get("voice_pe_speech_start_count"), 2)
        self.assertTrue(state.get("voice_pe_speech_active"))

        transcript_messages = [
            message
            for message in client.messages
            if message.get("type") == "user.transcript"
        ]
        self.assertEqual(transcript_messages, [])

    async def test_legitimate_short_first_command_is_not_dropped(
        self,
    ) -> None:
        proxy = self._proxy()
        client = FakeClient()
        commands: list[str] = []

        async def brain(command: str, metadata: dict, on_delta):
            return {"success": True, "response": "unused"}

        async def fake_start_brain_turn(*args):
            commands.append(str(args[0]))

        proxy._start_brain_turn = fake_start_brain_turn

        events = [
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": (
                    "conversation.item."
                    "input_audio_transcription.completed"
                ),
                "transcript": "Lights on",
            },
        ]

        async def event_stream():
            for event in events:
                yield json.dumps(event)

        state = {
            "generation": 0,
            "suppress_audio": False,
            "voice_pe_session_started_at": module.time.monotonic(),
        }

        await proxy._openai_to_client(
            client,
            event_stream(),
            brain,
            {
                "client_kind": "voice_pe",
                "conversation_id": "voice-pe-test",
                "user_name": "Aaron",
            },
            "realtime",
            "live",
            "marin",
            set(),
            state,
        )

        self.assertEqual(commands, ["Lights on"])
        self.assertFalse(bool(state.get("voice_pe_wake_guard_used")))
        self.assertEqual(state.get("voice_pe_transcripts_seen"), 1)
        self.assertEqual(state.get("voice_pe_speech_start_count"), 1)
        self.assertFalse(state.get("voice_pe_speech_active"))

        transcript_messages = [
            message
            for message in client.messages
            if message.get("type") == "user.transcript"
        ]
        self.assertEqual(
            transcript_messages,
            [{"type": "user.transcript", "text": "Lights on"}],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
