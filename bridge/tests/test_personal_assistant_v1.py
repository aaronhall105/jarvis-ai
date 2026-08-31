import tempfile
import unittest
import sqlite3
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.connectors.audit import ActionReceiptStore
from app.followup_engine import FollowUpEngine
from app.memory_engine import MemoryEngine


class FakeConversations:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.by_key: dict[str, dict[str, str]] = {}
        self.failures = 0

    async def add_assistant_message(
        self,
        conversation_id: str,
        content: str,
        *,
        delivery_key: str | None = None,
    ) -> dict[str, Any]:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("conversation temporarily unavailable")
        assert delivery_key is not None
        existing = self.by_key.get(delivery_key)
        if existing is not None:
            return existing
        message = {
            "conversation_id": conversation_id,
            "content": content,
            "delivery_key": delivery_key,
        }
        self.by_key[delivery_key] = message
        self.messages.append(message)
        return message


class FakeStates:
    def __init__(self) -> None:
        self.entities: list[dict[str, Any]] = []
        self.available = True

    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        if not self.available:
            raise RuntimeError("Home Assistant unavailable")
        return [dict(item) for item in self.entities]


class PersonalAssistantV1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.conversations = FakeConversations()
        self.states = FakeStates()
        self.now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
        self.notifications: list[tuple[str, str]] = []

        async def notify(recipient: str, message: str, title: str = "Jarvis") -> dict[str, Any]:
            self.notifications.append((recipient, message))
            return {"success": True, "command_sent": True, "delivery_confirmed": False}

        self.notify = notify
        self.engine = self._engine()

    async def asyncTearDown(self) -> None:
        await self.engine.stop()
        self.temporary.cleanup()

    def _engine(self) -> FollowUpEngine:
        engine = FollowUpEngine(
            str(self.root / "followups.db"),
            self.conversations,
            self.states,
            notifier=self.notify,
        )
        engine._now = lambda: self.now  # type: ignore[method-assign]
        return engine

    async def _command(
        self,
        text: str,
        *,
        principal: str = "aaron",
        conversation: str = "usr:aaron:conversation-1",
        request_id: str | None = None,
    ):
        return await self.engine.handle_command(
            text,
            principal_id=principal,
            conversation_id=conversation,
            timezone_name="Europe/London",
            request_id=request_id,
            device_id="phone-1",
            originating_endpoint="android_realtime",
        )

    async def test_reminder_persists_restart_and_delivers_once_to_originating_conversation(self):
        created = await self._command(
            "Remind me in 45 minutes to call Mum", request_id="turn-reminder-1"
        )
        self.assertTrue(created.success)
        job = created.details["job"]  # type: ignore[index]
        self.assertEqual(job["principal_id"], "aaron")
        self.assertEqual(job["conversation_id"], "usr:aaron:conversation-1")
        self.assertEqual(job["status"], "pending")

        restarted = self._engine()
        self.engine = restarted
        self.now += timedelta(minutes=46)
        await restarted.run_once()
        await restarted.run_once()

        delivered = await restarted.get(job["job_id"], principal_id="aaron")
        self.assertEqual(delivered["status"], "completed")
        self.assertEqual(delivered["delivery_state"], "delivered")
        self.assertTrue(delivered["verified_at"])
        self.assertEqual(len(self.conversations.messages), 1)
        self.assertEqual(
            self.conversations.messages[0]["conversation_id"],
            "usr:aaron:conversation-1",
        )
        self.assertIn("call mum", self.conversations.messages[0]["content"].casefold())
        self.assertEqual(len(self.notifications), 1)

    async def test_existing_followup_database_migrates_in_place(self):
        path = self.root / "legacy-followups.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE followup_jobs (
                  job_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                  kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                  status TEXT NOT NULL, created_at TEXT NOT NULL,
                  next_run_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  delivered_at TEXT, result_json TEXT,
                  idempotency_key TEXT NOT NULL UNIQUE
                )
                """
            )
        migrated = FollowUpEngine(str(path), self.conversations, self.states)
        with sqlite3.connect(path) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(followup_jobs)")
            }
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        self.assertIn("principal_id", columns)
        self.assertIn("schedule_json", columns)
        self.assertIn("notification_state", columns)
        self.assertEqual(integrity, "ok")
        await migrated.stop()

    async def test_recurring_occurrences_survive_restart_without_duplicates(self):
        created = await self._command(
            "Every 2 hours remind me to stretch", request_id="turn-recurring-1"
        )
        job = created.details["job"]  # type: ignore[index]
        self.assertEqual(job["kind"], "recurring")
        self.assertEqual(job["schedule"]["interval_seconds"], 7200)

        self.now += timedelta(hours=2)
        await self.engine.run_once()
        await self.engine.run_once()
        after_first = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(after_first["status"], "pending")
        self.assertEqual(after_first["occurrence_index"], 1)

        self.engine = self._engine()
        self.now += timedelta(hours=2)
        await self.engine.run_once()
        await self.engine.run_once()
        after_second = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(after_second["occurrence_index"], 2)
        self.assertEqual(len(self.conversations.messages), 2)
        self.assertEqual(len({item["delivery_key"] for item in self.conversations.messages}), 2)

    async def test_condition_unchanged_then_flips_delivers_one_verified_result(self):
        self.states.entities = [
            {
                "entity_id": "sensor.washing_machine",
                "name": "Washing machine",
                "state": "running",
            }
        ]
        created = await self._command(
            "Tell me when the washing machine finishes", request_id="turn-monitor-1"
        )
        job = created.details["job"]  # type: ignore[index]
        await self.engine.run_once()
        self.assertEqual(len(self.conversations.messages), 0)
        waiting = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(waiting["status"], "pending")
        self.assertEqual(waiting["last_observed_state"], "running")

        self.states.entities[0]["state"] = "completed"
        self.now += timedelta(seconds=5)
        await self.engine.run_once()
        self.states.entities[0]["state"] = "running"
        self.now += timedelta(seconds=5)
        await self.engine.run_once()
        self.assertEqual(len(self.conversations.messages), 1)
        completed = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["result"]["verified"])

    async def test_periodic_check_is_a_capability_backed_monitor_not_a_fake_reminder(self):
        self.states.entities = [
            {
                "entity_id": "sensor.outside_temperature",
                "name": "Outside temperature",
                "state": "18",
            }
        ]
        created = await self._command(
            "Every 2 hours check whether outside temperature has changed",
            request_id="periodic-monitor",
        )
        self.assertTrue(created.success)
        job = created.details["job"]  # type: ignore[index]
        self.assertEqual(job["kind"], "periodic")
        self.assertEqual(job["capability_id"], "home_assistant.read_state")
        self.assertEqual(job["payload"]["baseline"], "18")
        self.assertEqual(job["payload"]["interval_seconds"], 7200)

        self.now += timedelta(hours=2)
        await self.engine.run_once()
        self.assertEqual(self.conversations.messages, [])
        self.states.entities[0]["state"] = "19"
        self.now += timedelta(hours=2)
        await self.engine.run_once()
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_non_ha_periodic_monitor_defers_to_existing_external_agent_path(self):
        result = await self._command(
            "Every 2 hours check whether this web page has changed",
            request_id="external-monitor-routing",
        )
        self.assertFalse(result.handled)
        self.assertEqual(await self.engine.list(principal_id="aaron"), [])

    async def test_adversarial_whitespace_commands_use_bounded_parsing(self):
        padding = " " * 4_000
        for command in (
            f"pause {padding}unknown monitor",
            f"move unknown reminder {padding}to Friday",
            f"tell me when unknown device {padding}is online",
            f"every 2 hours check whether unknown page {padding}has changed",
        ):
            await asyncio.wait_for(self._command(command), timeout=0.5)

        memories = MemoryEngine(str(self.root / "bounded-memory.db"))
        for command in (
            f"Do you remember {padding}nothing",
            f"Forget {padding}nothing",
            f"Remember my {padding}preference is tea",
        ):
            await asyncio.wait_for(
                memories.handle_explicit_command(command, owner_key="aaron"),
                timeout=0.5,
            )

    async def test_cancelled_task_never_executes(self):
        created = await self._command(
            "Remind me in 45 minutes to call the dentist", request_id="turn-cancel-create"
        )
        job = created.details["job"]  # type: ignore[index]
        cancelled = await self._command(
            "Cancel my dentist reminder", request_id="turn-cancel-mutate"
        )
        self.assertTrue(cancelled.success)
        self.assertTrue(cancelled.response.startswith("Cancelled task "))
        self.now += timedelta(hours=1)
        await self.engine.run_once()
        current = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(self.conversations.messages, [])

    async def test_monitor_pause_resume_and_ambiguous_reference(self):
        self.states.entities = [
            {"entity_id": "binary_sensor.front_door", "name": "Front door", "state": "off"},
            {"entity_id": "binary_sensor.back_door", "name": "Back door", "state": "off"},
        ]
        first = await self._command("Tell me when front door changes", request_id="front-monitor")
        await self._command("Tell me when back door changes", request_id="back-monitor")
        ambiguous = await self._command("Pause that monitor")
        self.assertFalse(ambiguous.success)
        self.assertIn("more than one", ambiguous.response.casefold())

        job = first.details["job"]  # type: ignore[index]
        paused = await self._command(f"Pause {str(job['job_id'])[:8]} monitor")
        self.assertTrue(paused.success)
        self.states.entities[0]["state"] = "on"
        self.now += timedelta(minutes=5)
        await self.engine.run_once()
        self.assertEqual(self.conversations.messages, [])
        resumed = await self._command(f"Resume {str(job['job_id'])[:8]} monitor")
        self.assertTrue(resumed.success)
        await self.engine.run_once()
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_paused_task_can_be_cancelled_and_conversation_cleanup_includes_paused(self):
        first = await self._command(
            "Every 2 hours remind me to cancel paused work", request_id="paused-cancel-create"
        )
        first_job = first.details["job"]  # type: ignore[index]
        await self.engine.pause(first_job["job_id"], principal_id="aaron")
        cancelled = await self.engine.cancel(first_job["job_id"], principal_id="aaron")
        self.assertEqual(cancelled["status"], "cancelled")

        second = await self._command(
            "Every 2 hours remind me to clean up paused work", request_id="paused-cleanup-create"
        )
        second_job = second.details["job"]  # type: ignore[index]
        await self.engine.pause(second_job["job_id"], principal_id="aaron")
        self.assertEqual(await self.engine.cancel_for_conversation("usr:aaron:conversation-1"), 1)
        cleaned = await self.engine.get(second_job["job_id"], principal_id="aaron")
        self.assertEqual(cleaned["status"], "cancelled")

    async def test_reschedule_fences_old_occurrence(self):
        created = await self._command(
            "Remind me in 45 minutes to post the paperwork", request_id="turn-move-create"
        )
        job = created.details["job"]  # type: ignore[index]
        moved = await self._command(
            "Move that reminder to Friday at 8pm", request_id="turn-move-mutate"
        )
        self.assertTrue(moved.success)
        self.now += timedelta(hours=1)
        await self.engine.run_once()
        self.assertEqual(self.conversations.messages, [])
        current = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertGreater(datetime.fromisoformat(current["next_run_at"]), self.now)
        with self.assertRaisesRegex(ValueError, "timezone"):
            await self.engine.reschedule(
                job["job_id"],
                principal_id="aaron",
                due_at=self.now + timedelta(days=2),
                timezone_name="Invalid/Timezone",
            )

    async def test_delivery_failure_recovers_without_duplicate_message(self):
        created = await self._command(
            "Remind me in 1 minute to test recovery", request_id="turn-recovery"
        )
        job = created.details["job"]  # type: ignore[index]
        self.conversations.failures = 1
        self.now += timedelta(minutes=2)
        await self.engine.run_once()
        pending = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(pending["status"], "delivery_pending")

        self.engine = self._engine()
        self.now += timedelta(seconds=5)
        await self.engine.run_once()
        await self.engine.run_once()
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_restart_during_execution_reclaims_and_executes_once(self):
        created = await self._command(
            "Remind me in 1 minute to recover an executing reminder",
            request_id="executing-recovery",
        )
        job = created.details["job"]  # type: ignore[index]
        self.now += timedelta(minutes=2)
        with self.engine._db() as connection:
            connection.execute(
                "UPDATE followup_jobs SET status='executing' WHERE job_id=?",
                (job["job_id"],),
            )

        self.engine = self._engine()
        await asyncio.gather(self.engine.run_once(), self.engine.run_once())
        await self.engine.run_once()

        completed = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_restart_after_execution_before_delivery_posts_once(self):
        created = await self._command(
            "Remind me in 1 minute to recover pending delivery",
            request_id="pending-delivery-recovery",
        )
        job = created.details["job"]  # type: ignore[index]
        self.now += timedelta(minutes=2)
        with self.engine._db() as connection:
            connection.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',
                delivery_state='pending',delivery_message='Reminder: pending delivery.',
                completion_status='completed',result_json='{"verified":true}',next_run_at=?
                WHERE job_id=?
                """,
                (self.now.isoformat(), job["job_id"]),
            )

        self.engine = self._engine()
        await asyncio.gather(self.engine.run_once(), self.engine.run_once())

        completed = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_cancel_while_claimed_prevents_delivery(self):
        created = await self._command(
            "Remind me in 1 minute to cancel while claimed",
            request_id="cancel-claimed-create",
        )
        job = created.details["job"]  # type: ignore[index]
        with self.engine._db() as connection:
            connection.execute(
                "UPDATE followup_jobs SET status='executing' WHERE job_id=?",
                (job["job_id"],),
            )
        cancelled = await self.engine.cancel(job["job_id"], principal_id="aaron")
        self.assertEqual(cancelled["status"], "cancelled")
        self.now += timedelta(minutes=2)
        await self.engine.run_once()
        self.assertEqual(self.conversations.messages, [])

    async def test_notification_failure_does_not_replace_authoritative_conversation_result(self):
        async def failed_notification(
            recipient: str, message: str, title: str = "Jarvis"
        ) -> dict[str, Any]:
            return {"success": False, "command_sent": False, "delivery_confirmed": False}

        self.engine = FollowUpEngine(
            str(self.root / "notification-failure.db"),
            self.conversations,
            self.states,
            notifier=failed_notification,
        )
        self.engine._now = lambda: self.now  # type: ignore[method-assign]
        created = await self._command(
            "Remind me in 1 minute to verify notification failure",
            request_id="notification-failure",
        )
        job = created.details["job"]  # type: ignore[index]
        self.now += timedelta(minutes=2)
        await self.engine.run_once()
        completed = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["notification_state"], "failed")
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_restart_does_not_repeat_notification_with_unknown_transport_outcome(self):
        created = await self._command(
            "Remind me in 1 minute to verify the notification fence",
            request_id="notification-fence",
        )
        job = created.details["job"]  # type: ignore[index]
        self.now += timedelta(minutes=2)
        await self.engine.run_once()
        self.assertEqual(len(self.notifications), 1)

        # Model the only unknowable transport crash window: Jarvis durably
        # recorded the attempt before calling the notification transport, but
        # restarted before it could persist the transport outcome.
        with self.engine._db() as connection:
            connection.execute(
                """
                UPDATE followup_jobs SET status='delivery_pending',
                delivery_state='pending',notification_state='attempting',delivered_at=NULL
                WHERE job_id=?
                """,
                (job["job_id"],),
            )
        self.notifications.clear()
        self.engine = self._engine()
        await self.engine.run_once()

        completed = await self.engine.get(job["job_id"], principal_id="aaron")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["notification_state"], "outcome_unknown")
        self.assertEqual(self.notifications, [])
        self.assertEqual(len(self.conversations.messages), 1)

    async def test_persistence_failure_never_acknowledges_creation(self):
        async def fail_create(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("database unavailable")

        self.engine.create = fail_create  # type: ignore[method-assign]
        result = await self._command("Remind me in 1 hour to test persistence")
        self.assertTrue(result.handled)
        self.assertFalse(result.success)
        self.assertIn("not created", result.response)

    async def test_principal_task_isolation_and_unavailable_capability_truth(self):
        await self._command("Remind me in 1 hour to call Aaron's garage", request_id="aaron-task")
        await self._command(
            "Remind me in 1 hour to call Amber's garage",
            principal="amber",
            conversation="usr:amber:conversation-1",
            request_id="amber-task",
        )
        aaron = await self.engine.list(principal_id="aaron")
        amber = await self.engine.list(principal_id="amber")
        self.assertEqual(len(aaron), 1)
        self.assertEqual(len(amber), 1)
        self.assertNotEqual(aaron[0]["job_id"], amber[0]["job_id"])

        self.states.available = False
        refused = await self._command("Tell me when the washing machine finishes")
        self.assertTrue(refused.handled)
        self.assertFalse(refused.success)
        self.assertIn("did not create", refused.response)
        self.assertEqual(len(await self.engine.list(principal_id="aaron")), 1)

    async def test_job_creation_and_mutation_use_verified_action_receipts(self):
        receipts = ActionReceiptStore(self.root / "receipts.db")
        self.engine = FollowUpEngine(
            str(self.root / "receipted-followups.db"),
            self.conversations,
            self.states,
            receipts=receipts,
        )
        self.engine._now = lambda: self.now  # type: ignore[method-assign]
        created = await self._command(
            "Remind me in 1 hour to inspect receipts", request_id="receipt-create"
        )
        job = created.details["job"]  # type: ignore[index]
        self.assertEqual(job["action_receipt"]["status"], "verified")
        self.now += timedelta(minutes=1)
        retried_create = await self._command(
            "Remind me in 1 hour to inspect receipts", request_id="receipt-create"
        )
        self.assertEqual(retried_create.details["job"]["job_id"], job["job_id"])
        self.assertEqual(retried_create.details["job"]["action_receipt"]["status"], "verified")
        await self.engine.cancel(job["job_id"], principal_id="aaron", request_id="receipt-cancel")
        retried = await self.engine.cancel(
            job["job_id"], principal_id="aaron", request_id="receipt-cancel"
        )
        self.assertEqual(retried["status"], "cancelled")
        recorded = await receipts.list_recent(conversation_id="usr:aaron:conversation-1")
        self.assertEqual([item.status.value for item in recorded], ["verified", "verified"])
        self.assertEqual({item.requested_operation for item in recorded}, {"create", "cancel"})

    async def test_restart_completes_receipt_after_job_commit(self):
        key = "receipt-after-commit"
        database = self.root / "receipt-recovery-followups.db"
        without_receipts = FollowUpEngine(str(database), self.conversations, self.states)
        without_receipts._now = lambda: self.now  # type: ignore[method-assign]
        due_at = self.now + timedelta(hours=1)
        payload = {"message": "Reminder: receipt recovery.", "notify": False}
        job = await without_receipts.create(
            conversation_id="usr:aaron:conversation-1",
            kind="scheduled",
            payload=payload,
            due_at=due_at,
            idempotency_key=key,
            principal_id="aaron",
            capability_id="personal.reminder",
        )
        await without_receipts.stop()

        receipts = ActionReceiptStore(self.root / "receipt-recovery.db")
        self.engine = FollowUpEngine(
            str(database), self.conversations, self.states, receipts=receipts
        )
        self.engine._now = lambda: self.now  # type: ignore[method-assign]
        target = "new:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        started = await self.engine._begin_mutation_receipt(
            operation="create",
            job_id=target,
            principal_id="aaron",
            conversation_id="usr:aaron:conversation-1",
            request_id=key,
            payload={
                "kind": "scheduled",
                "capability_id": "personal.reminder",
                "request_fingerprint": self.engine._request_fingerprint(
                    "usr:aaron:conversation-1",
                    "scheduled",
                    payload,
                    principal_id="aaron",
                ),
            },
        )
        self.assertEqual(started.status.value, "started")

        recovered = await self.engine.create(
            conversation_id="usr:aaron:conversation-1",
            kind="scheduled",
            payload=payload,
            due_at=due_at,
            idempotency_key=key,
            principal_id="aaron",
            capability_id="personal.reminder",
        )
        self.assertEqual(recovered["job_id"], job["job_id"])
        self.assertEqual(recovered["action_receipt"]["status"], "verified")

    async def test_restart_completes_receipt_after_cancel_commit(self):
        receipts = ActionReceiptStore(self.root / "cancel-recovery-receipts.db")
        self.engine = FollowUpEngine(
            str(self.root / "cancel-recovery-followups.db"),
            self.conversations,
            self.states,
            receipts=receipts,
        )
        self.engine._now = lambda: self.now  # type: ignore[method-assign]
        created = await self._command(
            "Remind me in 1 hour to recover cancellation", request_id="cancel-create"
        )
        job = created.details["job"]  # type: ignore[index]
        started = await self.engine._begin_mutation_receipt(
            operation="cancel",
            job_id=job["job_id"],
            principal_id="aaron",
            conversation_id="usr:aaron:conversation-1",
            request_id="cancel-after-commit",
            payload={"operation": "cancel"},
        )
        self.assertEqual(started.status.value, "started")
        with self.engine._db() as connection:
            connection.execute(
                """
                UPDATE followup_jobs SET status='cancelled',delivery_state='cancelled'
                WHERE job_id=?
                """,
                (job["job_id"],),
            )

        recovered = await self.engine.cancel(
            job["job_id"], principal_id="aaron", request_id="cancel-after-commit"
        )
        self.assertEqual(recovered["status"], "cancelled")
        receipt = await receipts.get(started.action_id)
        self.assertEqual(receipt.status.value, "verified")

    async def test_retry_completes_receipts_after_pause_and_resume_commits(self):
        receipts = ActionReceiptStore(self.root / "state-recovery-receipts.db")
        self.engine = FollowUpEngine(
            str(self.root / "state-recovery-followups.db"),
            self.conversations,
            self.states,
            receipts=receipts,
        )
        self.engine._now = lambda: self.now  # type: ignore[method-assign]
        created = await self._command(
            "Every 2 hours remind me to test state recovery", request_id="state-create"
        )
        job = created.details["job"]  # type: ignore[index]

        pause_receipt = await self.engine._begin_mutation_receipt(
            operation="pause",
            job_id=job["job_id"],
            principal_id="aaron",
            conversation_id="usr:aaron:conversation-1",
            request_id="pause-after-commit",
            payload={"to": "paused"},
        )
        with self.engine._db() as connection:
            connection.execute(
                "UPDATE followup_jobs SET status='paused' WHERE job_id=?", (job["job_id"],)
            )
        paused = await self.engine.pause(
            job["job_id"], principal_id="aaron", request_id="pause-after-commit"
        )
        self.assertEqual(paused["status"], "paused")
        self.assertEqual((await receipts.get(pause_receipt.action_id)).status.value, "verified")

        resume_receipt = await self.engine._begin_mutation_receipt(
            operation="resume",
            job_id=job["job_id"],
            principal_id="aaron",
            conversation_id="usr:aaron:conversation-1",
            request_id="resume-after-commit",
            payload={"to": "pending"},
        )
        with self.engine._db() as connection:
            connection.execute(
                "UPDATE followup_jobs SET status='pending' WHERE job_id=?", (job["job_id"],)
            )
        resumed = await self.engine.resume(
            job["job_id"], principal_id="aaron", request_id="resume-after-commit"
        )
        self.assertEqual(resumed["status"], "pending")
        self.assertEqual((await receipts.get(resume_receipt.action_id)).status.value, "verified")

    async def test_explicit_memory_restart_correction_forget_and_principal_isolation(self):
        path = self.root / "memory.db"
        memories = MemoryEngine(str(path))
        saved = await memories.handle_explicit_command(
            "Remember my favourite takeaway is Chinese", owner_key="aaron"
        )
        chinese = saved["memory"]
        restarted = MemoryEngine(str(path))
        recalled = await restarted.handle_explicit_command(
            "Do you remember my favourite takeaway", owner_key="aaron"
        )
        self.assertIn("Chinese", recalled["response"])
        await restarted.handle_explicit_command(
            "Remember my favourite colour is purple", owner_key="aaron"
        )
        direct_recall = await restarted.handle_explicit_command(
            "What is my favourite takeaway?", owner_key="aaron"
        )
        self.assertIn("Chinese", direct_recall["response"])
        self.assertNotIn("purple", direct_recall["response"].casefold())
        self.assertEqual(direct_recall["intent"], "explicit_memory_recall")
        self.assertIsNone(
            await restarted.handle_explicit_command("What is my phone battery?", owner_key="aaron")
        )

        corrected = await restarted.handle_explicit_command(
            "Actually remember my favourite takeaway is Indian", owner_key="aaron"
        )
        indian = corrected["memory"]
        self.assertEqual(indian["id"], chinese["id"])
        current = [
            item
            for item in await restarted.search("favourite takeaway", owner_key="aaron")
            if item["subject"] == "favourite takeaway"
        ]
        self.assertEqual(
            [item["content"] for item in current], ["My favourite takeaway is Indian."]
        )

        self.assertEqual(await restarted.search("favourite takeaway", owner_key="amber"), [])
        forgotten = await restarted.handle_explicit_command(
            "Forget my favourite takeaway", owner_key="aaron", focused_memory_id=indian["id"]
        )
        self.assertTrue(forgotten["success"])
        remaining_takeaway = [
            item
            for item in await restarted.search("favourite takeaway", owner_key="aaron")
            if item["subject"] == "favourite takeaway"
        ]
        self.assertEqual(remaining_takeaway, [])
        post_forget = await restarted.handle_explicit_command(
            "Do you remember my favourite takeaway", owner_key="aaron"
        )
        self.assertEqual(post_forget["memories"], [])
        self.assertIn("don’t have", post_forget["response"])
        self.assertNotIn("purple", post_forget["response"].casefold())
        self.assertIsNone(
            await restarted.handle_explicit_command("Remember " + "a" * 10_000, owner_key="aaron")
        )


if __name__ == "__main__":
    unittest.main()
