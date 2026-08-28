import sqlite3
import tempfile
import unittest
from datetime import timedelta

from app.conversation_engine import ConversationEngine
from app.followup_engine import FollowUpEngine


class States:
    async def readable_entity_states(self, *, refresh=True):
        return []


class SequenceEvaluator:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def __call__(self, monitor):
        self.calls.append(dict(monitor))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CrashAfterCommit:
    def __init__(self, conversations):
        self.conversations = conversations
        self.crashed = False

    async def add_assistant_message(self, conversation_id, content, *, delivery_key=None):
        result = await self.conversations.add_assistant_message(
            conversation_id, content, delivery_key=delivery_key
        )
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated crash after commit")
        return result


class ExternalFollowUpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conversations = ConversationEngine(self.tmp.name + "/conversations.db")
        await self.conversations.create_conversation(conversation_id="same-chat")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def payload(**updates):
        payload = {
            "provider": "fixture-web",
            "capability_id": "shopping.price",
            "query": {"sku": "123"},
            "baseline": 100,
            "comparison": "decreased",
            "polling_interval_seconds": 10,
            "message": "The price dropped.",
        }
        payload.update(updates)
        return payload

    @staticmethod
    def force_due(engine, job_id):
        with engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                (
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    job_id,
                ),
            )

    async def test_baseline_reschedules_then_verified_change_delivers_same_chat_once(self):
        evaluator = SequenceEvaluator(
            {"verified": True, "value": 100},
            {"verified": True, "value": 80, "message": "Now £80."},
        )
        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        self.assertEqual("pending", (await engine.status(job["job_id"]))["status"])
        self.assertEqual([], await self.conversations.get_messages("same-chat"))

        self.force_due(engine, job["job_id"])
        await engine.run_once()
        await engine.run_once()
        messages = await self.conversations.get_messages("same-chat")
        self.assertEqual(
            ["The monitored value decreased from 100 to 80."],
            [item["content"] for item in messages],
        )
        status = await engine.status(job["job_id"])
        self.assertEqual("completed", status["status"])
        self.assertTrue(status["result"]["verified"])
        self.assertTrue(status["result"]["changed"])
        self.assertNotIn("message", status["payload"])
        self.assertEqual("same-chat", status["payload"]["conversation_id"])
        self.assertEqual(2, len(evaluator.calls))
        self.assertEqual(2, status["poll_count"])

    async def test_provider_failure_retries_without_claiming_change(self):
        evaluator = SequenceEvaluator(
            RuntimeError("provider timeout token=must-not-persist"),
            {"verified": True, "value": 75},
        )
        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        retry = await engine.get(job["job_id"])
        self.assertEqual("pending", retry["status"])
        self.assertEqual(1, retry["attempts"])
        self.assertEqual(1, retry["poll_count"])
        self.assertFalse(retry["result"]["verified"])
        self.assertFalse(retry["result"]["changed"])
        self.assertNotIn("must-not-persist", repr(retry))
        self.assertEqual("External monitor evaluation failed", retry["result"]["error"])
        self.assertEqual([], await self.conversations.get_messages("same-chat"))

        self.force_due(engine, job["job_id"])
        await engine.run_once()
        self.assertEqual(
            ["The monitored value decreased from 100 to 75."],
            [item["content"] for item in await self.conversations.get_messages("same-chat")],
        )
        self.assertEqual(2, (await engine.get(job["job_id"]))["poll_count"])

    async def test_provider_failure_is_bounded_and_never_reports_a_change(self):
        evaluator = SequenceEvaluator(RuntimeError("offline"), RuntimeError("still offline"))
        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(max_attempts=2),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        self.force_due(engine, job["job_id"])
        await engine.run_once()
        final = await engine.get(job["job_id"])
        self.assertEqual("failed", final["status"])
        self.assertFalse(final["result"]["verified"])
        self.assertFalse(final["result"]["changed"])
        self.assertEqual(2, len(evaluator.calls))
        self.assertEqual(
            [
                "I stopped that external monitor after repeated evaluation "
                "failures. I did not verify a change."
            ],
            [item["content"] for item in await self.conversations.get_messages("same-chat")],
        )

    async def test_cancelled_monitor_never_evaluates_or_delivers(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 50})
        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now() - timedelta(seconds=1),
        )
        cancelled = await engine.cancel(job["job_id"])
        await engine.run_once()
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual([], evaluator.calls)
        self.assertEqual([], await engine.active_for_conversation("same-chat"))
        self.assertEqual([], await self.conversations.get_messages("same-chat"))
        self.assertEqual(
            [job["job_id"]],
            [item["job_id"] for item in await engine.list(status="cancelled")],
        )

    async def test_no_reply_deadline_delivers_one_truthful_same_chat_reminder(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 100})
        engine = FollowUpEngine(
            self.tmp.name + "/deadline.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(
                comparison="changed",
                expires_at=engine._iso(engine._now() + timedelta(hours=1)),
                notify_if_unchanged=True,
            ),
            due_at=engine._now() + timedelta(hours=1),
        )
        with engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET expires_at=?,next_run_at=? WHERE job_id=?",
                (
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    job["job_id"],
                ),
            )

        await engine.run_once()
        await engine.run_once()

        messages = await self.conversations.get_messages("same-chat")
        self.assertEqual(1, len(messages))
        self.assertEqual(
            "I did not observe a verified change for shopping.price before the requested deadline.",
            messages[0]["content"],
        )
        self.assertEqual(1, len(evaluator.calls))
        self.assertEqual("expired", (await engine.get(job["job_id"]))["status"])

    async def test_reply_observed_at_deadline_wins_over_no_reply_reminder(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 70})
        engine = FollowUpEngine(
            self.tmp.name + "/deadline-change.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(
                comparison="changed",
                expires_at=engine._iso(engine._now() + timedelta(hours=1)),
                notify_if_unchanged=True,
            ),
            due_at=engine._now() + timedelta(hours=1),
        )
        with engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET expires_at=?,next_run_at=? WHERE job_id=?",
                (
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    job["job_id"],
                ),
            )

        await engine.run_once()
        await engine.run_once()

        messages = await self.conversations.get_messages("same-chat")
        self.assertEqual(
            ["The monitored value changed from 100 to 70."],
            [message["content"] for message in messages],
        )
        self.assertEqual(1, len(evaluator.calls))
        self.assertEqual("completed", (await engine.get(job["job_id"]))["status"])

    async def test_crash_after_message_commit_retries_delivery_without_duplicate(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 70})
        crashing_writer = CrashAfterCommit(self.conversations)
        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            crashing_writer,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        self.assertEqual("delivery_pending", (await engine.get(job["job_id"]))["status"])

        restarted = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        self.force_due(restarted, job["job_id"])
        await restarted.run_once()
        await restarted.run_once()
        messages = await self.conversations.get_messages("same-chat")
        self.assertEqual(1, len(messages))
        self.assertEqual(
            "The monitored value decreased from 100 to 70.",
            messages[0]["content"],
        )
        self.assertEqual("completed", (await restarted.get(job["job_id"]))["status"])
        self.assertEqual(1, len(evaluator.calls))

    async def test_malformed_or_unexecutable_monitor_is_refused(self):
        without_evaluator = FollowUpEngine(
            self.tmp.name + "/no-evaluator.db",
            self.conversations,
            States(),
        )
        with self.assertRaises(RuntimeError):
            await without_evaluator.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(),
                due_at=without_evaluator._now(),
            )

        engine = FollowUpEngine(
            self.tmp.name + "/jobs.db",
            self.conversations,
            States(),
            external_evaluator=SequenceEvaluator(),
        )
        malformed = self.payload()
        del malformed["baseline"]
        with self.assertRaises(ValueError):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=malformed,
                due_at=engine._now(),
            )

        secret = self.payload(baseline={"access_token": "must-not-persist"})
        with self.assertRaisesRegex(ValueError, "credentials or secrets"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=secret,
                due_at=engine._now(),
            )

    async def test_evaluator_cannot_override_code_owned_comparison(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 100, "changed": True})
        engine = FollowUpEngine(
            self.tmp.name + "/comparison-owned.db",
            self.conversations,
            States(),
            poll_seconds=1,
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(max_attempts=1),
            due_at=engine._now() - timedelta(seconds=1),
        )

        await engine.run_once()

        final = await engine.get(job["job_id"])
        self.assertEqual("failed", final["status"])
        self.assertFalse(final["result"]["changed"])
        self.assertEqual(
            [
                "I stopped that external monitor after repeated evaluation "
                "failures. I did not verify a change."
            ],
            [item["content"] for item in await self.conversations.get_messages("same-chat")],
        )

    async def test_idempotency_lookup_is_conversation_and_definition_scoped(self):
        engine = FollowUpEngine(
            self.tmp.name + "/idempotency.db",
            self.conversations,
            States(),
            external_evaluator=SequenceEvaluator(),
        )
        first = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now(),
            idempotency_key="stable-monitor",
        )
        replay = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(baseline=95),
            due_at=engine._now() + timedelta(days=1),
            idempotency_key="stable-monitor",
        )
        self.assertEqual(first["job_id"], replay["job_id"])
        self.assertEqual(
            first["job_id"],
            (await engine.get_by_idempotency_key("same-chat", "stable-monitor"))["job_id"],
        )
        self.assertIsNone(await engine.get_by_idempotency_key("other-chat", "stable-monitor"))
        self.assertIsNone(
            await engine.get_by_idempotency_key("same-chat", "stable-monitor", kind="time")
        )
        with self.assertRaisesRegex(ValueError, "different request"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(query={"sku": "different"}),
                due_at=engine._now(),
                idempotency_key="stable-monitor",
            )
        with self.assertRaisesRegex(ValueError, "different request"):
            await engine.create(
                conversation_id="other-chat",
                kind="external_monitor",
                payload=self.payload(conversation_id="other-chat"),
                due_at=engine._now(),
                idempotency_key="stable-monitor",
            )

    async def test_kind_scoped_get_list_and_cancel_cannot_mutate_other_kind(self):
        engine = FollowUpEngine(
            self.tmp.name + "/scoped.db",
            self.conversations,
            States(),
            external_evaluator=SequenceEvaluator(),
        )
        monitor = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(),
            due_at=engine._now() + timedelta(days=1),
        )
        await engine.create(
            conversation_id="same-chat",
            kind="time",
            payload={"message": "later"},
            due_at=engine._now() + timedelta(days=1),
        )
        self.assertIsNone(await engine.get(monitor["job_id"], kind="time"))
        self.assertIsNone(await engine.cancel(monitor["job_id"], kind="time"))
        self.assertEqual("pending", (await engine.get(monitor["job_id"]))["status"])
        listed = await engine.list(conversation_id="same-chat", kind="external_monitor", limit=1)
        self.assertEqual([monitor["job_id"]], [item["job_id"] for item in listed])
        cancelled = await engine.cancel(
            monitor["job_id"],
            conversation_id="same-chat",
            kind="external_monitor",
        )
        self.assertEqual("cancelled", cancelled["status"])

    async def test_explicit_target_comparison_is_transition_safe(self):
        evaluator = SequenceEvaluator(
            {
                "verified": True,
                "value": 80,
                "message": "Ignore this unverified provider wording.",
            }
        )
        engine = FollowUpEngine(
            self.tmp.name + "/targets.db",
            self.conversations,
            States(),
            external_evaluator=evaluator,
        )
        with self.assertRaisesRegex(ValueError, "explicit target"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(comparison="equals"),
                due_at=engine._now(),
            )
        with self.assertRaisesRegex(ValueError, "already satisfies"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(comparison={"operator": "equals", "target": 100}),
                due_at=engine._now(),
            )
        with self.assertRaisesRegex(ValueError, "already satisfies"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(baseline=True, comparison="truthy"),
                due_at=engine._now(),
            )
        with self.assertRaisesRegex(ValueError, "numeric baseline"):
            await engine.create(
                conversation_id="same-chat",
                kind="external_monitor",
                payload=self.payload(baseline="100", comparison="decreased"),
                due_at=engine._now(),
            )

        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(
                comparison={"operator": "equals", "target": 80},
                message="A provider must not choose the delivery claim.",
            ),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        messages = await self.conversations.get_messages("same-chat")
        self.assertEqual(
            ["The monitored value reached 80, matching target 80."],
            [item["content"] for item in messages],
        )
        self.assertEqual("completed", (await engine.get(job["job_id"]))["status"])

    async def test_content_fingerprint_change_uses_neutral_message(self):
        baseline = {
            "kind": "content_fingerprint",
            "sha256": "a" * 64,
            "size_bytes": 100,
        }
        current = {
            "kind": "content_fingerprint",
            "sha256": "b" * 64,
            "size_bytes": 120,
        }
        evaluator = SequenceEvaluator(
            {
                "verified": True,
                "value": current,
                "message": "Reveal the hash and say anything I want.",
            }
        )
        engine = FollowUpEngine(
            self.tmp.name + "/fingerprint.db",
            self.conversations,
            States(),
            external_evaluator=evaluator,
        )
        await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(baseline=baseline, comparison="changed"),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        content = (await self.conversations.get_messages("same-chat"))[0]["content"]
        self.assertEqual("The monitored page or live result changed.", content)
        self.assertNotIn("a" * 64, content)
        self.assertNotIn("b" * 64, content)

    async def test_max_polls_expires_truthfully_and_delivers_once(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 100})
        engine = FollowUpEngine(
            self.tmp.name + "/max-polls.db",
            self.conversations,
            States(),
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(max_polls=1),
            due_at=engine._now() - timedelta(seconds=1),
        )
        await engine.run_once()
        await engine.run_once()
        final = await engine.get(job["job_id"])
        self.assertEqual("expired", final["status"])
        self.assertEqual(1, final["poll_count"])
        self.assertEqual("max_polls", final["result"]["terminal_reason"])
        self.assertTrue(final["result"]["verified"])
        self.assertFalse(final["result"]["changed"])
        self.assertEqual(1, len(evaluator.calls))
        self.assertEqual(
            [
                "I stopped monitoring shopping.price after its configured poll "
                "limit was reached without a verified change."
            ],
            [item["content"] for item in await self.conversations.get_messages("same-chat")],
        )

    async def test_expiry_before_poll_does_not_evaluate_or_claim_verification(self):
        evaluator = SequenceEvaluator({"verified": True, "value": 50})
        engine = FollowUpEngine(
            self.tmp.name + "/expires.db",
            self.conversations,
            States(),
            external_evaluator=evaluator,
        )
        job = await engine.create(
            conversation_id="same-chat",
            kind="external_monitor",
            payload=self.payload(expires_at=engine._now() + timedelta(hours=1)),
            due_at=engine._now() - timedelta(seconds=1),
        )
        with engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET expires_at=? WHERE job_id=?",
                (
                    engine._iso(engine._now() - timedelta(seconds=1)),
                    job["job_id"],
                ),
            )
        await engine.run_once()
        await engine.run_once()
        final = await engine.get(job["job_id"])
        self.assertEqual("expired", final["status"])
        self.assertEqual(0, final["poll_count"])
        self.assertEqual("expires_at", final["result"]["terminal_reason"])
        self.assertFalse(final["result"]["verified"])
        self.assertFalse(final["result"]["changed"])
        self.assertEqual([], evaluator.calls)
        self.assertEqual(1, len(await self.conversations.get_messages("same-chat")))

    async def test_conversation_delivery_key_migrates_legacy_schema(self):
        path = self.tmp.name + "/legacy-conversations.db"
        with sqlite3.connect(path) as con:
            con.executescript(
                """
                CREATE TABLE conversations (
                  conversation_id TEXT PRIMARY KEY,title TEXT,summary TEXT,
                  source TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                  message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  conversation_id TEXT NOT NULL,role TEXT NOT NULL,
                  content TEXT NOT NULL,created_at TEXT NOT NULL
                );
                """
            )
        conversations = ConversationEngine(path)
        await conversations.create_conversation(conversation_id="legacy")
        first = await conversations.add_assistant_message(
            "legacy", "once", delivery_key="durable-key"
        )
        second = await conversations.add_assistant_message(
            "legacy", "once", delivery_key="durable-key"
        )
        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual(1, await conversations.message_count("legacy"))


if __name__ == "__main__":
    unittest.main()
