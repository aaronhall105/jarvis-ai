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


class QueueClient(FakeClient):
    def __init__(self, incoming: list[dict]) -> None:
        super().__init__()
        self.incoming = list(incoming)

    async def receive(self) -> dict:
        return self.incoming.pop(0)


class AuthClient(FakeClient):
    def __init__(self, auth: dict) -> None:
        super().__init__()
        self.auth = auth
        self.accepted = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        return json.dumps(self.auth)

    async def close(self, code: int) -> None:
        self.close_code = code


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
        self.assertEqual(module.normalise_voice_endpoint("watch"), "WATCH")
        self.assertEqual(module.normalise_voice_endpoint("anything"), "PHONE")
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

    def test_session_contract_accepts_registry_transcription_prompt(self) -> None:
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
            transcription_prompt="static fallback",
        )

        update = module.build_session_update(
            config,
            "cedar",
            "live",
            "high",
            transcription_prompt="Jarvis, Aaron, Living Room, TV television",
        )

        self.assertEqual(
            update["session"]["audio"]["input"]["transcription"]["prompt"],
            "Jarvis, Aaron, Living Room, TV television",
        )

    def test_session_contract_never_exceeds_provider_prompt_limit(self) -> None:
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
            transcription_prompt="fallback",
        )
        update = module.build_session_update(
            config, "cedar", "live", "high", transcription_prompt="x" * 1400
        )
        assert len(update["session"]["audio"]["input"]["transcription"]["prompt"]) == 1024

    def test_session_requests_input_transcription_logprobs(self) -> None:
        config = module.RealtimeVoiceConfig(
            enabled=True, api_key="secret", mobile_token="mobile",
            voice_pe_token="voice-pe", model="gpt-realtime", voice="marin",
            user_id="aaron", user_name="Aaron", user_is_admin=True,
            transcription_prompt="Jarvis",
        )
        update = module.build_session_update(config, "marin", "live", "medium")
        self.assertEqual(
            update["session"]["include"],
            ["item.input_audio_transcription.logprobs"],
        )

    def test_transcription_confidence_uses_geometric_mean(self) -> None:
        confidence = module.input_transcription_confidence({
            "logprobs": [{"token": "turn", "logprob": -0.1}, {"token": "tv", "logprob": -0.3}]
        })
        self.assertAlmostEqual(confidence, 0.8187, places=4)
        self.assertIsNone(module.input_transcription_confidence({"logprobs": []}))

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

    def test_original_mobile_voice_uses_configured_elevenlabs(self) -> None:
        proxy = module.RealtimeVoiceProxy(module.RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="mobile",
            voice_pe_token="voice-pe",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron",
            tts_provider="elevenlabs",
            elevenlabs_api_key="configured",
            elevenlabs_voice_id="configured",
        ))
        self.assertTrue(proxy._use_direct_elevenlabs({
            "client_kind": "mobile",
            "requested_voice": "original",
        }))
        self.assertFalse(proxy._use_direct_elevenlabs({
            "client_kind": "mobile",
            "requested_voice": "cedar",
        }))


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


class CancellationStateMachineTests(unittest.IsolatedAsyncioTestCase):
    def _proxy(self):
        return module.RealtimeVoiceProxy(module.RealtimeVoiceConfig(
            enabled=True,
            api_key="key",
            mobile_token="token",
            voice_pe_token="voice-pe",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Jarvis",
        ))

    async def test_cancel_stops_local_turn_and_upstream_response(self) -> None:
        proxy = self._proxy()
        client = QueueClient([
            {"type": "websocket.receive", "text": json.dumps({
                "type": "cancel", "client_turn_id": 41,
            })},
            {"type": "websocket.disconnect"},
        ])
        upstream = FakeUpstream()
        cancelled = asyncio.Event()

        async def old_turn():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(old_turn())
        await asyncio.sleep(0)
        tasks = {task}
        state = {
            "generation": 7,
            "active_generation": 7,
            "active_client_turn_id": 41,
            "turn_in_progress": True,
            "turn_in_progress_generation": 7,
            "suppress_audio": False,
        }
        await proxy._client_to_openai(
            client, upstream, lambda *_: None, {}, "realtime", "standard",
            "marin", tasks, state,
        )
        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.cancelled())
        self.assertEqual(upstream.messages, [{"type": "response.cancel"}])
        self.assertEqual(state["generation"], 8)
        self.assertFalse(state["turn_in_progress"])
        self.assertTrue(state["suppress_audio"])
        self.assertEqual(client.messages[-1]["type"], "turn.cancelled")

    async def test_repeated_cancel_is_idempotent(self) -> None:
        proxy = self._proxy()
        client = QueueClient([
            {"type": "websocket.receive", "text": '{"type":"cancel"}'},
            {"type": "websocket.receive", "text": '{"type":"cancel"}'},
            {"type": "websocket.disconnect"},
        ])
        upstream = FakeUpstream()
        state = {"generation": 2, "suppress_audio": False}
        await proxy._client_to_openai(
            client, upstream, lambda *_: None, {}, "realtime", "standard",
            "marin", set(), state,
        )
        self.assertEqual(state["generation"], 4)
        self.assertEqual(
            upstream.messages,
            [{"type": "response.cancel"}, {"type": "response.cancel"}],
        )

    async def test_old_openai_audio_and_done_cannot_finish_new_generation(self) -> None:
        proxy = self._proxy()
        client = FakeClient()
        upstream = FakeUpstream()
        old_audio = module.base64.b64encode(b"old audio").decode()
        events = [
            {"type": "response.output_audio.delta", "response_id": "old", "delta": old_audio},
            {"type": "response.output_audio.done", "response_id": "old"},
            {"type": "response.done", "response": {"id": "old", "status": "cancelled"}},
        ]

        async def stream():
            for event in events:
                yield json.dumps(event)

        state = {
            "generation": 9,
            "active_generation": 9,
            "active_client_turn_id": 52,
            "suppress_audio": False,
            "openai_response_turns": {
                "old": {"generation": 8, "client_turn_id": 51, "provider_epoch": 0},
            },
        }
        await proxy._consume_openai_events(
            client, stream(), lambda *_: None, {}, "realtime", "standard",
            "marin", set(), state,
        )
        self.assertEqual(client.messages, [])

    async def test_cancelled_generation_cannot_clear_new_turn_terminal_state(self) -> None:
        terminal = asyncio.Event()
        state = {
            "turn_in_progress": True,
            "turn_in_progress_generation": 12,
            "turn_terminal_event": terminal,
        }
        module._mark_turn_terminal(state, 11)
        self.assertTrue(state["turn_in_progress"])
        self.assertFalse(terminal.is_set())
        module._mark_turn_terminal(state, 12)
        self.assertFalse(state["turn_in_progress"])
        self.assertTrue(terminal.is_set())


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


class VoicePeWakeArbiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.arbiter = module.VoicePeWakeArbiter(
            contention_seconds=1.25,
            clock=lambda: self.now,
        )

    def test_first_satellite_owns_wake(self) -> None:
        claim = self.arbiter.claim("living-room", "session-1")
        self.assertTrue(claim.granted)
        self.assertEqual(claim.owner_device_id, "living-room")

    def test_nearby_satellite_is_rejected_during_contention(self) -> None:
        self.arbiter.claim("living-room", "session-1")
        self.now += 0.2
        claim = self.arbiter.claim("kitchen", "session-2")
        self.assertFalse(claim.granted)
        self.assertEqual(claim.owner_device_id, "living-room")
        self.assertEqual(claim.retry_after_ms, 1050)

    def test_duplicate_connection_from_owner_is_also_rejected(self) -> None:
        self.arbiter.claim("living-room", "session-1")
        self.now += 0.1
        claim = self.arbiter.claim("living-room", "session-2")
        self.assertFalse(claim.granted)
        self.assertEqual(claim.owner_session_id, "session-1")

    def test_new_wake_after_window_gets_new_owner(self) -> None:
        self.arbiter.claim("living-room", "session-1")
        self.now += 1.251
        claim = self.arbiter.claim("kitchen", "session-2")
        self.assertTrue(claim.granted)
        self.assertEqual(claim.owner_device_id, "kitchen")


class VoicePeWakeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_losing_satellite_closes_before_provider_connection(self) -> None:
        proxy = module.RealtimeVoiceProxy(module.RealtimeVoiceConfig(
            enabled=True,
            api_key="secret",
            mobile_token="mobile",
            voice_pe_token="voice-pe",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron",
        ))
        proxy.voice_pe_wake_arbiter.claim("living-room", "owner-session")
        client = AuthClient({
            "type": "auth",
            "token": "voice-pe",
            "client_kind": "voice_pe",
            "device_id": "kitchen",
            "area_id": "kitchen",
            "conversation_id": "voice-pe-kitchen",
        })

        async def brain(command: str, metadata: dict, on_delta):
            raise AssertionError("losing satellite must not reach the brain")

        await proxy.handle(client, brain)

        self.assertTrue(client.accepted)
        self.assertEqual(client.close_code, 4429)
        self.assertEqual(proxy.active_sessions, 0)
        self.assertEqual(proxy.total_sessions, 0)
        self.assertEqual(proxy.total_voice_pe_wake_rejections, 1)
        self.assertEqual(client.messages[0]["type"], "session.close")
        self.assertEqual(client.messages[0]["reason"], "wake_contention")
        self.assertEqual(client.messages[0]["owner_device_id"], "living-room")


if __name__ == "__main__":
    unittest.main(verbosity=2)
