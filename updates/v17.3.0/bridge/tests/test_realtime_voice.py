from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.realtime_voice import (  # noqa: E402
    CORE_APPLICATION_VERSION,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    SUPPORTED_VOICES,
    VERSION,
    VOICE_MODE_HOME_ASSISTANT,
    VOICE_MODE_REALTIME,
    RealtimeVoiceConfig,
    RealtimeVoiceProxy,
    audio_append_event,
    build_session_update,
    normalise_voice,
    normalise_voice_mode,
    openai_websocket_url,
    speak_response_event,
)


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.binary: list[bytes] = []

    async def send_json(self, value: dict[str, object]) -> None:
        self.messages.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


class FakeUpstream:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


def config(**overrides: object) -> RealtimeVoiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "api_key": "sk-test-secret",
        "mobile_token": "mobile-secret",
        "model": DEFAULT_MODEL,
        "voice": DEFAULT_VOICE,
        "user_id": "aaron",
        "user_name": "Aaron",
        "user_is_admin": True,
        "transcription_prompt": "Names include Aaron and Amber. Preserve Amber exactly.",
    }
    values.update(overrides)
    return RealtimeVoiceConfig(**values)  # type: ignore[arg-type]


class UnifiedSessionTests(unittest.TestCase):
    def test_release_versions(self) -> None:
        self.assertEqual("17.3.0", VERSION)
        self.assertEqual("2.9.0", CORE_APPLICATION_VERSION)
        self.assertEqual("gpt-realtime", DEFAULT_MODEL)

    def test_session_makes_core_authoritative(self) -> None:
        event = build_session_update(config(), "cedar")
        session = event["session"]
        self.assertEqual([], session["tools"])
        self.assertEqual("none", session["tool_choice"])
        turn = session["audio"]["input"]["turn_detection"]
        self.assertFalse(turn["create_response"])
        self.assertTrue(turn["interrupt_response"])
        self.assertIn("Amber", session["audio"]["input"]["transcription"]["prompt"])
        self.assertEqual("cedar", session["audio"]["output"]["voice"])

    def test_voice_modes_and_voice_allow_list(self) -> None:
        self.assertEqual(VOICE_MODE_HOME_ASSISTANT, normalise_voice_mode("original"))
        self.assertEqual(VOICE_MODE_HOME_ASSISTANT, normalise_voice_mode("HA"))
        self.assertEqual(VOICE_MODE_REALTIME, normalise_voice_mode("realtime"))
        self.assertEqual("cedar", normalise_voice("CEDAR"))
        self.assertEqual(DEFAULT_VOICE, normalise_voice("not-a-real-voice"))
        self.assertIn("marin", SUPPORTED_VOICES)
        self.assertIn("cedar", SUPPORTED_VOICES)

    def test_speak_event_is_out_of_band_and_faithful(self) -> None:
        event = speak_response_event("Amber is at home.", "marin")
        response = event["response"]
        self.assertEqual("none", response["conversation"])
        self.assertEqual(["audio"], response["output_modalities"])
        self.assertIn("Amber is at home.", response["instructions"])
        self.assertIn("Do not answer it", response["instructions"])

    def test_audio_append_is_exact_base64(self) -> None:
        pcm = b"\x00\x01\xfe\xff"
        event = audio_append_event(pcm)
        self.assertEqual(base64.b64encode(pcm).decode("ascii"), event["audio"])

    def test_openai_url_escapes_model(self) -> None:
        self.assertEqual(
            "wss://api.openai.com/v1/realtime?model=gpt-realtime",
            openai_websocket_url("gpt-realtime"),
        )


class UnifiedBrainTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_turn_runs_through_brain_and_updates_conversation(self) -> None:
        proxy = RealtimeVoiceProxy(config())
        client = FakeClient()
        upstream = FakeUpstream()
        metadata: dict[str, object] = {"conversation_id": "mobile-1", "user_id": "aaron"}
        state: dict[str, object] = {"generation": 1, "suppress_audio": True}
        calls: list[tuple[str, dict[str, object]]] = []

        async def brain(command: str, supplied: dict[str, object]) -> dict[str, object]:
            calls.append((command, supplied))
            return {
                "success": True,
                "response": "Amber is at home.",
                "conversation_id": "conversation-amber",
                "intent": "where_is_person",
                "model": "jarvis-core",
            }

        await proxy._run_brain_turn(
            1,
            "Where is Amber?",
            client,
            upstream,
            brain,
            metadata,
            VOICE_MODE_REALTIME,
            "marin",
            state,
        )

        self.assertEqual("Where is Amber?", calls[0][0])
        self.assertEqual("conversation-amber", metadata["conversation_id"])
        types = [str(message.get("type")) for message in client.messages]
        self.assertEqual(["brain.started", "brain.response"], types)
        self.assertEqual("Amber is at home.", client.messages[-1]["text"])
        self.assertEqual("response.create", upstream.messages[-1]["type"])
        self.assertIn("Amber is at home.", upstream.messages[-1]["response"]["instructions"])
        self.assertEqual(1, proxy.total_brain_turns)

    async def test_original_voice_returns_core_text_to_home_assistant_tts(self) -> None:
        proxy = RealtimeVoiceProxy(config())
        client = FakeClient()
        upstream = FakeUpstream()
        metadata: dict[str, object] = {"conversation_id": "mobile-2"}
        state: dict[str, object] = {"generation": 7, "suppress_audio": True}

        async def brain(command: str, supplied: dict[str, object]) -> dict[str, object]:
            return {"success": True, "response": "The bedroom floodlight is off."}

        await proxy._run_brain_turn(
            7,
            "Turn off the bedroom floodlight",
            client,
            upstream,
            brain,
            metadata,
            VOICE_MODE_HOME_ASSISTANT,
            "marin",
            state,
        )

        self.assertEqual([], upstream.messages)
        self.assertEqual("original.tts", client.messages[-1]["type"])
        self.assertEqual("The bedroom floodlight is off.", client.messages[-1]["text"])

    async def test_interrupted_stale_brain_result_is_not_spoken(self) -> None:
        proxy = RealtimeVoiceProxy(config())
        client = FakeClient()
        upstream = FakeUpstream()
        metadata: dict[str, object] = {"conversation_id": "mobile-3"}
        state: dict[str, object] = {"generation": 3, "suppress_audio": True}

        async def brain(command: str, supplied: dict[str, object]) -> dict[str, object]:
            return {"success": True, "response": "Old answer"}

        await proxy._run_brain_turn(
            2,
            "old request",
            client,
            upstream,
            brain,
            metadata,
            VOICE_MODE_REALTIME,
            "marin",
            state,
        )

        self.assertEqual("brain.discarded", client.messages[-1]["type"])
        self.assertEqual([], upstream.messages)
        self.assertEqual(1, proxy.total_discarded_stale_turns)


class ConfigurationTests(unittest.TestCase):
    def test_environment_configuration(self) -> None:
        old = dict(os.environ)
        try:
            os.environ.update(
                {
                    "OPENAI_API_KEY": "key",
                    "JARVIS_MOBILE_VOICE_TOKEN": "token",
                    "JARVIS_REALTIME_VOICE": "cedar",
                    "JARVIS_REALTIME_TRANSCRIPTION_PROMPT": "Amber and Aaron",
                }
            )
            loaded = RealtimeVoiceConfig.from_environment()
            self.assertEqual("cedar", loaded.voice)
            self.assertIn("Amber", loaded.transcription_prompt)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_status_does_not_disclose_secrets(self) -> None:
        proxy = RealtimeVoiceProxy(config())
        raw = json.dumps(proxy.status())
        self.assertNotIn("sk-test-secret", raw)
        self.assertNotIn("mobile-secret", raw)
        self.assertTrue(proxy.status()["unified_brain"])
        self.assertFalse(proxy.status()["automatic_model_answers"])

    def test_token_comparison(self) -> None:
        proxy = RealtimeVoiceProxy(config())
        self.assertTrue(proxy.token_is_valid("mobile-secret"))
        self.assertFalse(proxy.token_is_valid("wrong"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
