import tempfile
import unittest
from datetime import datetime, timedelta

from app.capability_registry import ActionReceiptStore
from app.conversation_engine import ConversationEngine
from app.followup_engine import FollowUpEngine


class Conversations:
    def __init__(self):
        self.messages = []

    async def add_assistant_message(self, conversation_id, content):
        self.messages.append((conversation_id, content))
        return {}


class IdempotentConversations(Conversations):
    def __init__(self):
        super().__init__()
        self.delivery_keys = set()

    async def add_assistant_message_once(self, conversation_id, content, idempotency_key):
        if idempotency_key not in self.delivery_keys:
            self.delivery_keys.add(idempotency_key)
            self.messages.append((conversation_id, content))
        return {}


class UnavailableConversations:
    def __init__(self):
        self.delivery_attempts = 0

    async def add_assistant_message(self, conversation_id, content):
        self.delivery_attempts += 1
        raise ValueError("Conversation does not exist")

    async def add_assistant_message_once(self, conversation_id, content, idempotency_key):
        self.delivery_attempts += 1
        raise ValueError("Conversation does not exist")


class FailingReceiptFinalizer:
    def __init__(self):
        self.completions = []

    async def begin(self, **kwargs):
        return {"action_id": "receipt-1", **kwargs}

    async def complete(self, action_id, result, **kwargs):
        self.completions.append((action_id, result, kwargs))
        raise RuntimeError("Receipt database unavailable")


class RecordingReceipts:
    def __init__(self):
        self.started = []
        self.completions = []

    async def begin(self, **kwargs):
        action_id = f"receipt-{len(self.started) + 1}"
        self.started.append((action_id, kwargs))
        return {"action_id": action_id, **kwargs}

    async def complete(self, action_id, result, **kwargs):
        self.completions.append((action_id, result, kwargs))
        return {"action_id": action_id, "result": result, **kwargs}


class ReceiptStoreUnavailableAfterCreate:
    def __init__(self):
        self.created = False

    async def begin(self, **kwargs):
        if self.created:
            raise RuntimeError("receipt store offline")
        self.created = True
        return {"action_id": kwargs["action_id"], **kwargs}

    async def get(self, action_id):
        return None


class States:
    def __init__(self, state="off"):
        self.state = state
        self.fail = False

    async def readable_entity_states(self, *, refresh=True):
        if self.fail:
            raise RuntimeError("offline")
        return [{"entity_id": "binary_sensor.test", "name": "Test", "state": self.state}]


class FollowupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conversations = Conversations()
        self.states = States()
        self.engine = FollowUpEngine(
            self.tmp.name + "/jobs.db", self.conversations, self.states, poll_seconds=1
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_time_persists_then_delivers_once(self):
        job = await self.engine.create(
            conversation_id="c",
            kind="time",
            payload={"message": "done"},
            due_at=self.engine._now() - timedelta(seconds=1),
            idempotency_key="one",
        )
        self.assertEqual("pending", job["status"])
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual([("c", "done")], self.conversations.messages)
        self.assertEqual("completed", (await self.engine.get(job["job_id"]))["status"])

    async def test_condition_waits_then_delivers_once(self):
        job = await self.engine.create(
            conversation_id="c",
            kind="condition",
            payload={"entity_id": "binary_sensor.test", "state": "on", "message": "online"},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        await self.engine.run_once()
        self.assertEqual([], self.conversations.messages)
        self.states.state = "on"
        with self.engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                (self.engine._iso(self.engine._now() - timedelta(seconds=1)), job["job_id"]),
            )
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual([("c", "online")], self.conversations.messages)
        self.assertEqual("completed", (await self.engine.get(job["job_id"]))["status"])

    async def test_restart_and_retry_are_durable(self):
        job = await self.engine.create(
            conversation_id="c",
            kind="completion",
            payload={"entity_id": "binary_sensor.test", "state": "on", "message": "finished"},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        self.states.fail = True
        await self.engine.run_once()
        restarted = FollowUpEngine(
            self.tmp.name + "/jobs.db", self.conversations, self.states, poll_seconds=1
        )
        self.states.fail = False
        self.states.state = "on"
        # make the bounded-backoff retry due without waiting in a deterministic test
        with restarted._db() as con:
            con.execute(
                "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                (restarted._iso(restarted._now() - timedelta(seconds=1)), job["job_id"]),
            )
        await restarted.run_once()
        await restarted.run_once()
        self.assertEqual([("c", "finished")], self.conversations.messages)

    async def test_completion_watcher_binds_real_job_and_delivers_once(self):
        source = await self.engine.create(
            conversation_id="c",
            kind="time",
            payload={"message": "source done"},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        watcher = await self.engine.create(
            conversation_id="c",
            kind="completion",
            payload={"source_type": "followup_job", "source_job_id": source["job_id"]},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        await self.engine.run_once()
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual(
            [("c", "source done"), ("c", "The job I was watching has finished.")],
            self.conversations.messages,
        )
        self.assertEqual("completed", (await self.engine.get(watcher["job_id"]))["status"])

    async def test_completion_watcher_reports_real_failure(self):
        source = await self.engine.create(
            conversation_id="c",
            kind="time",
            payload={},
            due_at=self.engine._now() + timedelta(days=1),
        )
        with self.engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET status='failed' WHERE job_id=?", (source["job_id"],)
            )
        await self.engine.create(
            conversation_id="c",
            kind="completion",
            payload={"source_type": "followup_job", "source_job_id": source["job_id"]},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual(
            [("c", "The job I was watching failed, so it did not complete successfully.")],
            self.conversations.messages,
        )

    async def test_periodic_unchanged_then_changed_delivers_once(self):
        job = await self.engine.create(
            conversation_id="c",
            kind="periodic",
            payload={"entity_id": "binary_sensor.test", "baseline": "off", "interval_seconds": 10},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        await self.engine.run_once()
        self.assertEqual([], self.conversations.messages)
        self.states.state = "on"
        with self.engine._db() as con:
            con.execute(
                "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                (self.engine._iso(self.engine._now() - timedelta(seconds=1)), job["job_id"]),
            )
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual([("c", "Test changed to on.")], self.conversations.messages)

    async def test_idempotency_key_returns_same_job(self):
        first = await self.engine.create(
            conversation_id="c",
            kind="time",
            payload={},
            due_at=self.engine._now(),
            idempotency_key="stable",
        )
        second = await self.engine.create(
            conversation_id="c",
            kind="time",
            payload={},
            due_at=self.engine._now(),
            idempotency_key="stable",
        )
        self.assertEqual(first["job_id"], second["job_id"])

    async def test_bounded_failure_posts_truthful_message(self):
        job = await self.engine.create(
            conversation_id="c",
            kind="condition",
            payload={"entity_id": "binary_sensor.test", "state": "on"},
            due_at=self.engine._now() - timedelta(seconds=1),
        )
        self.states.fail = True
        for _ in range(3):
            with self.engine._db() as con:
                con.execute(
                    "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                    (self.engine._iso(self.engine._now() - timedelta(seconds=1)), job["job_id"]),
                )
            await self.engine.run_once()
        self.assertEqual(
            [
                (
                    "c",
                    "I couldn't complete that follow-up because the required service was unavailable.",
                )
            ],
            self.conversations.messages,
        )
        self.assertEqual("failed", (await self.engine.get(job["job_id"]))["status"])

    async def test_receipt_finalization_failure_does_not_reverse_completed_delivery(self):
        conversations = IdempotentConversations()
        receipts = FailingReceiptFinalizer()
        engine = FollowUpEngine(
            self.tmp.name + "/receipt-finalization.db",
            conversations,
            self.states,
            poll_seconds=1,
            receipts=receipts,
        )
        job = await engine.create(
            conversation_id="c",
            kind="time",
            payload={"message": "completed successfully"},
            due_at=engine._now() - timedelta(seconds=1),
        )

        with self.assertLogs("jarvis-core.followups", level="ERROR") as captured:
            for _ in range(3):
                with engine._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                        (engine._iso(engine._now() - timedelta(seconds=1)), job["job_id"]),
                    )
                await engine.run_once()

        completed = await engine.get(job["job_id"])
        self.assertEqual("completed", completed["status"])
        self.assertEqual(0, completed["attempts"])
        self.assertEqual([("c", "completed successfully")], conversations.messages)
        self.assertEqual(1, len(receipts.completions))
        self.assertTrue(
            any("Could not finalize completed follow-up receipt" in line for line in captured.output)
        )

    async def test_terminal_delivery_failure_still_marks_job_failed(self):
        conversations = UnavailableConversations()
        engine = FollowUpEngine(
            self.tmp.name + "/missing-conversation.db",
            conversations,
            self.states,
            poll_seconds=1,
        )
        job = await engine.create(
            conversation_id="deleted",
            kind="time",
            payload={"message": "due"},
            due_at=engine._now() - timedelta(seconds=1),
        )

        with self.assertLogs("jarvis-core.followups", level="ERROR") as captured:
            for _ in range(3):
                with engine._db() as con:
                    con.execute(
                        "UPDATE followup_jobs SET next_run_at=? WHERE job_id=?",
                        (engine._iso(engine._now() - timedelta(seconds=1)), job["job_id"]),
                    )
                await engine.run_once()

        failed = await engine.get(job["job_id"])
        self.assertEqual("failed", failed["status"])
        self.assertEqual(3, failed["attempts"])
        self.assertIsNone(failed["delivered_at"])
        self.assertFalse(failed["result"]["notification_delivered"])
        self.assertIn("Conversation does not exist", failed["result"]["notification_error"])
        self.assertEqual(4, conversations.delivery_attempts)
        await engine.run_once()
        self.assertEqual(4, conversations.delivery_attempts)
        self.assertTrue(
            any(
                "Could not deliver terminal follow-up failure message" in line
                for line in captured.output
            )
        )

    async def test_cancel_for_conversation_cancels_jobs_and_finalizes_receipts(self):
        receipts = RecordingReceipts()
        engine = FollowUpEngine(
            self.tmp.name + "/conversation-cancellation.db",
            self.conversations,
            self.states,
            poll_seconds=1,
            receipts=receipts,
        )
        due = engine._now() - timedelta(seconds=1)
        first = await engine.create(
            conversation_id="usr:aaron:chat-1",
            kind="time",
            payload={"message": "first"},
            due_at=due,
        )
        second = await engine.create(
            conversation_id="usr:aaron:chat-1",
            kind="time",
            payload={"message": "second"},
            due_at=due,
        )
        untouched = await engine.create(
            conversation_id="usr:amber:chat-1",
            kind="time",
            payload={"message": "other user"},
            due_at=engine._now() + timedelta(days=1),
        )

        cancelled = await engine.cancel_for_conversation("usr:aaron:chat-1")
        await engine.run_once()

        self.assertEqual(2, cancelled)
        self.assertEqual("cancelled", (await engine.get(first["job_id"]))["status"])
        self.assertEqual("cancelled", (await engine.get(second["job_id"]))["status"])
        self.assertEqual("pending", (await engine.get(untouched["job_id"]))["status"])
        self.assertEqual([], self.conversations.messages)
        self.assertEqual(2, len(receipts.completions))
        self.assertTrue(
            all(item[2]["status"] == "cancelled" for item in receipts.completions)
        )
        self.assertTrue(
            all(item[1]["error"] == "conversation_deleted" for item in receipts.completions)
        )

    async def test_delivery_is_durable_in_the_same_scoped_conversation(self):
        conversation_path = self.tmp.name + "/conversations.db"
        conversation_id = "usr:aaron:web-chat-1"
        conversation_store = ConversationEngine(conversation_path)
        await conversation_store.ensure_conversation(
            conversation_id=conversation_id,
            source="web:aaron",
        )
        engine = FollowUpEngine(
            self.tmp.name + "/durable-delivery.db",
            conversation_store,
            self.states,
            poll_seconds=1,
        )
        await engine.create(
            conversation_id=conversation_id,
            kind="time",
            payload={"message": "Your durable reminder is ready."},
            due_at=engine._now() - timedelta(seconds=1),
        )

        await engine.run_once()
        reopened_store = ConversationEngine(conversation_path)
        messages = await reopened_store.get_messages(conversation_id, limit=20)

        self.assertEqual(1, len(messages))
        self.assertEqual("assistant", messages[0]["role"])
        self.assertEqual("Your durable reminder is ready.", messages[0]["content"])
        self.assertEqual(conversation_id, messages[0]["conversation_id"])

        restarted_worker = FollowUpEngine(
            self.tmp.name + "/durable-delivery.db",
            reopened_store,
            self.states,
            poll_seconds=1,
        )
        await restarted_worker.run_once()
        self.assertEqual(
            1,
            len(await reopened_store.get_messages(conversation_id, limit=20)),
        )

    async def test_receipt_outage_cannot_reactivate_cancelled_conversation_job(self):
        receipts = FailingReceiptFinalizer()
        engine = FollowUpEngine(
            self.tmp.name + "/cancel-receipt-outage.db",
            self.conversations,
            self.states,
            poll_seconds=1,
            receipts=receipts,
        )
        job = await engine.create(
            conversation_id="usr:aaron:deleted-chat",
            kind="time",
            payload={"message": "must not be delivered"},
            due_at=engine._now() - timedelta(seconds=1),
        )

        with self.assertLogs("jarvis-core.followups", level="ERROR") as captured:
            cancelled = await engine.cancel_for_conversation(
                "usr:aaron:deleted-chat"
            )
        await engine.run_once()

        self.assertEqual(1, cancelled)
        self.assertEqual("cancelled", (await engine.get(job["job_id"]))["status"])
        self.assertEqual([], self.conversations.messages)
        self.assertTrue(
            any(
                "Could not finalize cancelled follow-up receipt" in line
                for line in captured.output
            )
        )

    async def test_real_receipt_is_finalized_only_after_durable_delivery(self):
        receipts = ActionReceiptStore(self.tmp.name + "/actions.db")
        conversations = IdempotentConversations()
        engine = FollowUpEngine(
            self.tmp.name + "/receipted-delivery.db",
            conversations,
            self.states,
            poll_seconds=1,
            receipts=receipts,
        )
        job = await engine.create(
            conversation_id="usr:aaron:chat",
            kind="time",
            payload={"message": "delivered"},
            due_at=engine._now() - timedelta(seconds=1),
            actor_key="aaron",
        )
        started = await receipts.get(job["action_id"])
        self.assertEqual("scheduled", started["status"])
        self.assertIsNone(started["completed_at"])

        await engine.run_once()

        completed_job = await engine.get(job["job_id"])
        completed_receipt = await receipts.get(job["action_id"])
        self.assertEqual("completed", completed_job["status"])
        self.assertTrue(completed_job["receipt_finalized"])
        self.assertEqual("verified", completed_receipt["status"])
        self.assertTrue(completed_receipt["verified"])
        self.assertEqual([("usr:aaron:chat", "delivered")], conversations.messages)

    async def test_receipt_outage_blocks_execution_backs_off_and_degrades_health(self):
        conversations = IdempotentConversations()
        receipts = ReceiptStoreUnavailableAfterCreate()
        engine = FollowUpEngine(
            self.tmp.name + "/receipt-outage-backoff.db",
            conversations,
            self.states,
            poll_seconds=1,
            receipts=receipts,
        )
        job = await engine.create(
            conversation_id="usr:aaron:chat",
            kind="time",
            payload={"message": "must not run"},
            due_at=engine._now() - timedelta(seconds=1),
        )

        with self.assertLogs("jarvis-core.followups", level="ERROR"):
            await engine.run_once()

        blocked = await engine.get(job["job_id"])
        status = await engine.status()
        self.assertEqual("pending", blocked["status"])
        self.assertTrue(blocked["result"]["audit_blocked"])
        self.assertGreater(
            datetime.fromisoformat(blocked["next_run_at"]),
            engine._now(),
        )
        self.assertFalse(status["receipt_healthy"])
        self.assertEqual(1, status["receipt_blocked_jobs"])
        self.assertEqual([], conversations.messages)
