from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import patch

from app.realtime_voice import (
    DEFAULT_MODEL,
    INPUT_RATE,
    OUTPUT_RATE,
    RealtimeVoiceConfig,
    RealtimeVoiceProxy,
    audio_append_event,
    build_session_update,
    function_output_event,
    openai_websocket_url,
)


class FakeUpstream:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.messages.append(payload)


class RealtimeVoiceTests(unittest.TestCase):
    def config(self) -> RealtimeVoiceConfig:
        return RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="mobile-token",
            model=DEFAULT_MODEL,
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
        )

    def test_environment_configuration(self) -> None:
        values = {
            "JARVIS_REALTIME_ENABLED": "true",
            "OPENAI_API_KEY": "api-key",
            "JARVIS_MOBILE_VOICE_TOKEN": "phone-token",
            "JARVIS_REALTIME_MODEL": "gpt-realtime",
            "JARVIS_REALTIME_VOICE": "marin",
            "JARVIS_REALTIME_USER_ID": "aaron",
            "JARVIS_REALTIME_USER_NAME": "Aaron",
            "JARVIS_REALTIME_USER_IS_ADMIN": "true",
        }
        with patch.dict(os.environ, values, clear=True):
            config = RealtimeVoiceConfig.from_environment()
        self.assertTrue(config.enabled)
        self.assertEqual(config.api_key, "api-key")
        self.assertEqual(config.mobile_token, "phone-token")
        self.assertTrue(config.user_is_admin)


    def test_default_model_is_current_public_realtime_alias(self) -> None:
        self.assertEqual(DEFAULT_MODEL, "gpt-realtime")

    def test_module_can_load_without_host_websockets_package(self) -> None:
        # The installer runs this test with ``python -S``. Importing the module
        # must therefore not require container-only third-party packages.
        proxy = RealtimeVoiceProxy(self.config())
        self.assertEqual(proxy.status()["version"], "17.2.0-r1")

    def test_session_uses_pcm_semantic_vad_and_tool(self) -> None:
        update = build_session_update(self.config())
        session = update["session"]
        self.assertEqual(update["type"], "session.update")
        self.assertEqual(session["type"], "realtime")
        self.assertNotIn("model", session)
        self.assertEqual(session["audio"]["input"]["format"], {"type": "audio/pcm", "rate": INPUT_RATE})
        self.assertEqual(session["audio"]["output"]["format"], {"type": "audio/pcm", "rate": OUTPUT_RATE})
        self.assertEqual(session["audio"]["input"]["transcription"]["model"], "gpt-4o-transcribe")
        vad = session["audio"]["input"]["turn_detection"]
        self.assertEqual(vad["type"], "semantic_vad")
        self.assertTrue(vad["interrupt_response"])
        self.assertTrue(vad["create_response"])
        self.assertEqual(session["tools"][0]["name"], "jarvis_command")

    def test_audio_append_is_exact_base64(self) -> None:
        pcm = b"\x00\x01\xfe\xff"
        event = audio_append_event(pcm)
        self.assertEqual(event["type"], "input_audio_buffer.append")
        self.assertEqual(base64.b64decode(event["audio"]), pcm)

    def test_function_output_is_compact_json(self) -> None:
        event = function_output_event("call-1", {"success": True, "response": "Done"})
        self.assertEqual(event["item"]["type"], "function_call_output")
        self.assertEqual(event["item"]["call_id"], "call-1")
        self.assertEqual(json.loads(event["item"]["output"])["response"], "Done")

    def test_openai_url_escapes_model(self) -> None:
        self.assertEqual(
            openai_websocket_url("gpt-realtime"),
            "wss://api.openai.com/v1/realtime?model=gpt-realtime",
        )

    def test_token_comparison(self) -> None:
        proxy = RealtimeVoiceProxy(self.config())
        self.assertTrue(proxy.token_is_valid("mobile-token"))
        self.assertFalse(proxy.token_is_valid("wrong"))
        self.assertFalse(proxy.token_is_valid(None))

    def test_status_does_not_disclose_secrets(self) -> None:
        status = RealtimeVoiceProxy(self.config()).status()
        self.assertTrue(status["configured"])
        encoded = json.dumps(status)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("mobile-token", encoded)


class RealtimeToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_jarvis_tool_returns_output_and_continues(self) -> None:
        config = RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="token",
            model=DEFAULT_MODEL,
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
        )
        proxy = RealtimeVoiceProxy(config)
        upstream = FakeUpstream()
        client = FakeClient()
        received: list[tuple[str, dict[str, object]]] = []

        async def tool(command: str, metadata: dict[str, object]) -> dict[str, object]:
            received.append((command, metadata))
            return {"success": True, "response": "The living room light is off.", "intent": "lights_off"}

        await proxy._handle_tool_call(
            upstream,
            client,
            tool,
            {"user_id": "aaron", "conversation_id": "voice-1"},
            {
                "call_id": "call-1",
                "name": "jarvis_command",
                "arguments": json.dumps({"command": "Turn the living room light off"}),
            },
        )
        self.assertEqual(received[0][0], "Turn the living room light off")
        self.assertEqual(client.messages[0]["type"], "tool.started")
        self.assertEqual(upstream.messages[-1]["type"], "response.create")
        output = json.loads(upstream.messages[-2]["item"]["output"])
        self.assertTrue(output["success"])
        self.assertIn("living room", output["response"])

    async def test_unsupported_tool_is_rejected_without_handler(self) -> None:
        proxy = RealtimeVoiceProxy(
            RealtimeVoiceConfig(True, "key", "token", DEFAULT_MODEL, "marin", "aaron", "Aaron", True)
        )
        upstream = FakeUpstream()
        client = FakeClient()
        called = False

        async def tool(command: str, metadata: dict[str, object]) -> dict[str, object]:
            nonlocal called
            called = True
            return {}

        await proxy._handle_tool_call(
            upstream,
            client,
            tool,
            {},
            {"call_id": "bad-1", "name": "unknown", "arguments": "{}"},
        )
        self.assertFalse(called)
        output = json.loads(upstream.messages[0]["item"]["output"])
        self.assertFalse(output["success"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
