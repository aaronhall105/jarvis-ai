from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from app.realtime_input_lifecycle import InputLifecycle
from app.realtime_voice import RealtimeVoiceConfig, RealtimeVoiceProxy


def event(kind: str, item: str = "a", **fields):
    return {"type": kind, "item_id": item, **fields}


def prefix(item="a"):
    return [
        event("input_audio_buffer.speech_started", item),
        event("input_audio_buffer.speech_stopped", item),
        event("input_audio_buffer.committed", item),
        {
            "type": "conversation.item.created",
            "item": {"id": item, "role": "user", "content": [{"type": "input_audio"}]},
        },
    ]


def final(item="a", text="What is the current state?"):
    return event("conversation.item.input_audio_transcription.completed", item, transcript=text)


class Client:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class Stream:
    def __init__(self, events):
        self.events = events
        self.sent = []

    async def __aiter__(self):
        for item in self.events:
            if callable(item):
                item()
            else:
                yield json.dumps(item)
            await asyncio.sleep(0)

    async def send(self, value):
        self.sent.append(json.loads(value))


class InputLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.observed = []
        self.tracker = InputLifecycle(
            lambda turn, stage: self.observed.append(stage), clock=lambda: self.now
        )

    def test_timeout_does_not_extend_on_deltas_and_rejects_late_final(self):
        self.tracker.started("a", 1)
        self.tracker.progress("a", 1, "committed")
        self.now += 29
        self.tracker.progress("a", 1, "first_delta")
        self.assertEqual(self.tracker.expire(), [])
        self.now += 1
        self.assertEqual(len(self.tracker.expire()), 1)
        self.assertEqual(self.tracker.items["a"].outcome, "transcription_timeout")
        self.assertIsNone(self.tracker.claim_final("a", 99, empty=False))
        self.assertIn("late_final_discarded", self.observed)

    def test_speech_stop_without_commit_is_also_bounded(self):
        self.tracker.started("a", 1)
        self.tracker.progress("a", 1, "speech_stopped")
        self.now += 30
        self.assertEqual(len(self.tracker.expire()), 1)

    def test_active_speech_has_a_generous_absolute_bound(self):
        self.tracker.started("a", 1)
        self.now += 299
        self.assertEqual(self.tracker.expire(), [])
        self.now += 1
        self.assertEqual(len(self.tracker.expire()), 1)

    def test_cancel_of_old_response_does_not_cancel_new_speech(self):
        self.tracker.started("a", 5)
        self.tracker.cancel("cancelled", through_generation=4)
        self.assertIsNotNone(self.tracker.claim_final("a", 6, empty=False))

    def test_cancel_and_supersession_are_terminal(self):
        self.tracker.started("a", 1)
        self.tracker.started("b", 2)
        self.assertEqual(self.tracker.items["a"].outcome, "superseded")
        self.tracker.cancel("cancelled", 2)
        self.assertEqual(self.tracker.items["b"].outcome, "cancelled")
        self.assertIsNone(self.tracker.claim_final("a", 2, empty=False))
        self.assertIsNone(self.tracker.claim_final("b", 3, empty=False))

    def test_no_speech_and_empty_after_speech_differ(self):
        self.tracker.progress("a", 1, "committed")
        self.tracker.claim_final("a", 1, empty=True)
        self.assertEqual(self.tracker.items["a"].outcome, "no_speech")
        self.tracker.started("b", 2)
        self.tracker.claim_final("b", 2, empty=True)
        self.assertEqual(self.tracker.items["b"].outcome, "empty_transcript_after_speech")

    def test_capacity_never_evicts_dedup_tombstones(self):
        with patch("app.realtime_input_lifecycle.MAX_INPUT_ITEMS", 1):
            self.tracker.started("a", 1)
            self.tracker.finish(self.tracker.items["a"], "delivered_to_core")
            with self.assertRaises(RuntimeError):
                self.tracker.ensure("b", 2)
            self.assertIn("a", self.tracker.items)

    def test_disconnect_fences_unknown_and_known_late_items(self):
        self.tracker.started("a", 1)
        self.tracker.close()
        self.assertEqual(self.tracker.items["a"].outcome, "disconnected")
        self.assertIsNone(self.tracker.claim_final("a", 3, empty=False))
        self.assertIsNone(self.tracker.claim_final("unknown", 3, empty=False))


class RealtimeInputIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.proxy = RealtimeVoiceProxy(
            RealtimeVoiceConfig(
                enabled=True,
                api_key="test",
                mobile_token="test",
                voice_pe_token="test",
                model="gpt-realtime",
                voice="marin",
                user_id="test",
                user_name="Test",
                user_is_admin=False,
                transcription_prompt="",
            )
        )
        self.client = Client()
        self.state = {"generation": 0, "suppress_audio": False}
        self.metadata = {
            "session_id": "session",
            "client_kind": "mobile",
            "voice_endpoint": "PHONE",
        }
        self.tasks = set()
        self.commands = []

        async def run_brain(
            generation, command, speak, client, upstream, handler, metadata, *args, **kwargs
        ):
            self.commands.append((command, metadata["voice_endpoint"]))

        self.proxy._run_brain_turn = run_brain

    async def consume(self, events, *, disconnect=False):
        method = self.proxy._openai_to_client if disconnect else self.proxy._consume_openai_events
        await method(
            self.client,
            Stream(events),
            None,
            self.metadata,
            "realtime",
            "live",
            "marin",
            self.tasks,
            self.state,
        )
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks))

    def turn(self, item="a"):
        return self.state["input_lifecycle"].items[item]

    async def test_success_without_any_delta_exactly_one_core_turn(self):
        await self.consume(prefix() + [final()])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.turn().outcome, "delivered_to_core")
        self.assertEqual(
            set(self.turn().times),
            {
                "observed",
                "speech_started",
                "speech_stopped",
                "committed",
                "item_created",
                "transcript_completed",
                "delivered_to_core",
            },
        )
        self.assertFalse(self.proxy.config.speculative_reads_enabled)

    async def test_success_with_deltas_and_no_early_text(self):
        def assert_private():
            self.assertFalse(any(m["type"] == "user.transcript" for m in self.client.messages))
            self.assertEqual(self.commands, [])

        await self.consume(
            prefix()
            + [
                event("conversation.item.input_audio_transcription.delta", delta="private text"),
                assert_private,
                final(),
            ]
        )
        self.assertEqual(len(self.commands), 1)
        self.assertIn("first_delta", self.turn().times)

    async def test_transcription_failed_is_terminal_without_core_or_private_error(self):
        await self.consume(
            prefix()
            + [
                event(
                    "conversation.item.input_audio_transcription.failed",
                    error={"code": "test", "message": "SECRET PROVIDER BODY"},
                ),
                final(),
            ]
        )
        self.assertEqual(self.commands, [])
        self.assertEqual(self.turn().outcome, "transcription_failed")
        self.assertNotIn("SECRET", json.dumps(self.client.messages))
        self.assertEqual(sum(m["type"] == "error" for m in self.client.messages), 1)

    async def test_duplicate_completion_only_delivers_once(self):
        await self.consume(prefix() + [final(), final()])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.proxy.input_lifecycle_counts["duplicate_final_suppressed"], 1)

    async def test_empty_transcript_never_enters_planner(self):
        await self.consume(prefix() + [final(text="  ")])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.turn().outcome, "empty_transcript_after_speech")

    async def test_unknown_final_cannot_acquire_new_generation(self):
        await self.consume([final("unowned")])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.proxy.input_lifecycle_counts["unowned_final_discarded"], 1)

    async def test_rapid_pending_turn_is_explicitly_superseded(self):
        await self.consume(prefix("a") + prefix("b") + [final("a"), final("b")])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.turn("a").outcome, "superseded")
        self.assertEqual(self.turn("b").outcome, "delivered_to_core")

    async def test_sequential_genuine_turns_both_deliver(self):
        await self.consume(prefix("a") + [final("a")] + prefix("b") + [final("b")])
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.turn("a").generation, 1)
        self.assertEqual(self.turn("b").generation, 3)

    async def test_noncritical_delta_exception_does_not_kill_final(self):
        original = self.proxy._dispatch_openai_events

        async def faulty(*args, **kwargs):
            if "transcription.delta" in args[1].raw:
                raise ValueError("diagnostic failed")
            await original(*args, **kwargs)

        self.proxy._dispatch_openai_events = faulty
        await self.consume(
            prefix()
            + [
                event("conversation.item.input_audio_transcription.delta", delta="x"),
                final(),
            ]
        )
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.proxy.input_lifecycle_counts["receive_loop_errors"], 1)

    async def test_lifecycle_telemetry_exception_is_nonfatal(self):
        with patch("app.realtime_voice._LOGGER.info", side_effect=ValueError("telemetry")):
            tracker = self.proxy._input_lifecycle(self.state, self.metadata)
            tracker.started("a", 1)
            tracker.finish(tracker.items["a"], "cancelled")
        self.assertEqual(self.proxy.active_input_turns, 0)
        self.assertGreater(self.proxy.input_lifecycle_counts["diagnostic_errors"], 0)

    async def test_metrics_and_speech_diagnostic_failure_cannot_lose_completion(self):
        with (
            patch(
                "app.realtime_voice.runtime_metrics.increment", side_effect=ValueError("metrics")
            ),
            patch("app.realtime_voice._LOGGER.info", side_effect=ValueError("logging")),
        ):
            await self.consume(prefix() + [final()])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.turn().outcome, "delivered_to_core")

    async def test_delta_pressure_preserves_final_and_bounds_partial_memory(self):
        await self.consume(
            prefix()
            + [
                event("conversation.item.input_audio_transcription.delta", delta="x" * 128)
                for _ in range(1000)
            ]
            + [final()]
        )
        self.assertEqual(len(self.commands), 1)
        self.assertLessEqual(len(self.state["speculative_partials"]["a"]), 4096)
        self.assertEqual(len(self.state["input_lifecycle"].items), 1)

    async def test_disconnect_during_speech_or_transcription(self):
        for events in [prefix()[:1], prefix()]:
            with self.subTest(events=len(events)):
                self.state = {"generation": 0}
                await self.consume(events, disconnect=True)
                self.assertEqual(self.turn().outcome, "disconnected")
                self.assertEqual(self.proxy.active_input_turns, 0)

    async def test_timeout_final_is_stale_even_without_watchdog_tick(self):
        def advance():
            self.turn().deadline = 0

        await self.consume(prefix() + [advance, final()])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.turn().outcome, "transcription_timeout")

    async def test_phone_watch_same_provider_item_ids_are_isolated(self):
        await self.consume(prefix())
        phone_state = self.state
        self.state = {"generation": 0}
        self.metadata = {**self.metadata, "session_id": "watch-session", "voice_endpoint": "WATCH"}
        await self.consume(prefix() + [final()])
        self.assertEqual(self.commands[0][1], "WATCH")
        self.assertIsNone(phone_state["input_lifecycle"].items["a"].outcome)
        phone_state["input_lifecycle"].close()
        self.assertEqual(phone_state["input_lifecycle"].items["a"].outcome, "disconnected")
        self.assertEqual(self.turn().outcome, "delivered_to_core")

    async def test_cancel_existing_input_rejects_late_completion(self):
        def cancel():
            self.state["input_lifecycle"].cancel("cancelled", 1)
            self.state["generation"] = 2

        await self.consume(prefix() + [cancel, final()])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.turn().outcome, "cancelled")

    async def test_disconnect_after_final_claim_before_core_delivery(self):
        async def disconnected(*args, **kwargs):
            self.state["input_lifecycle"].close()
            raise asyncio.CancelledError

        original = self.proxy._dispatch_openai_events
        await self.consume(prefix())
        self.proxy._dispatch_openai_events = disconnected
        with self.assertRaises(asyncio.CancelledError):
            await self.consume([final()])
        self.proxy._dispatch_openai_events = original
        await self.consume([final()])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.turn().outcome, "disconnected")

    async def test_watchdog_terminates_idle_committed_item_without_more_events(self):
        await self.consume(prefix())
        tracker = self.state["input_lifecycle"]
        self.turn().deadline = 0
        watchdog = asyncio.create_task(self.proxy._input_watchdog(self.client, tracker, self.state))
        try:
            async with asyncio.timeout(2):
                while self.turn().outcome is None:
                    await asyncio.sleep(0.01)
            self.assertEqual(self.turn().outcome, "transcription_timeout")
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        await self.consume([final()])
        self.assertEqual(self.commands, [])

    async def test_actual_playback_cancel_does_not_cancel_new_barge_in_input(self):
        self.state = {"generation": 4, "active_generation": 4, "active_client_turn_id": 17}
        await self.consume(prefix())

        class CancelClient(Client):
            def __init__(self):
                super().__init__()
                self.incoming = [
                    {
                        "type": "websocket.receive",
                        "text": json.dumps({"type": "cancel", "client_turn_id": 17}),
                    },
                    {"type": "websocket.disconnect"},
                ]

            async def receive(self):
                return self.incoming.pop(0)

        upstream = Stream([])
        await self.proxy._client_to_openai(
            CancelClient(),
            upstream,
            None,
            self.metadata,
            "realtime",
            "live",
            "marin",
            self.tasks,
            self.state,
        )
        self.assertIsNone(self.turn().outcome)
        self.assertIn({"type": "response.cancel"}, upstream.sent)
        await self.consume([final()])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.turn().outcome, "delivered_to_core")

    async def test_pressure_preserves_failed_event_and_status_is_content_free(self):
        await self.consume(
            prefix()
            + [
                event("conversation.item.input_audio_transcription.delta", delta="PRIVATE")
                for _ in range(100)
            ]
            + [event("conversation.item.input_audio_transcription.failed")]
        )
        status = self.proxy.status()["input_lifecycle"]
        self.assertEqual(status["active_input_turns"], 0)
        self.assertEqual(status["last_terminal_outcome"], "transcription_failed")
        self.assertNotIn("PRIVATE", json.dumps(status))
        self.assertNotIn("session", json.dumps(status))

    async def test_current_failure_notice_survives_old_playback_generation_change(self):
        await self.consume(prefix())
        # Cancelling older output can advance response generation while this
        # input remains current. Recovery must use input ownership, too.
        self.state["generation"] += 1
        await self.consume([event("conversation.item.input_audio_transcription.failed")])
        self.assertEqual(sum(m["type"] == "error" for m in self.client.messages), 1)
