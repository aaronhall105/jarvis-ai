import asyncio
import tempfile
import unittest
from datetime import timedelta

from app.followup_engine import FollowUpEngine


class Conversations:
    def __init__(self):
        self.messages = []
        self.delivery_keys = set()

    async def add_assistant_message(self, conversation_id, content, *, delivery_key=None):
        if delivery_key is not None and delivery_key in self.delivery_keys:
            return {}
        if delivery_key is not None:
            self.delivery_keys.add(delivery_key)
        self.messages.append((conversation_id, content))
        return {}


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

    async def test_health_snapshot_reports_worker_and_durable_database_state(self):
        stopped = await self.engine.health_snapshot()
        self.assertFalse(stopped["healthy"])
        self.assertEqual("degraded", stopped["status"])
        self.assertEqual("stopped", stopped["worker_state"])
        self.assertFalse(stopped["worker_running"])
        self.assertTrue(stopped["database_healthy"])
        self.assertEqual({"running": False, "state": "stopped"}, stopped["worker"])
        self.assertEqual({"healthy": True}, stopped["database"])

        await self.engine.start()
        try:
            running = await self.engine.health_snapshot()
            self.assertTrue(running["healthy"])
            self.assertEqual("healthy", running["status"])
            self.assertEqual("running", running["worker_state"])
            self.assertTrue(running["worker_running"])
            self.assertTrue(running["database_healthy"])
            self.assertIsNone(running["reason"])
        finally:
            await self.engine.stop()

        stopped_again = await self.engine.health_snapshot()
        self.assertFalse(stopped_again["healthy"])
        self.assertEqual("stopped", stopped_again["worker_state"])
        self.assertTrue(stopped_again["database_healthy"])

    async def test_health_snapshot_redacts_database_failure_details(self):
        self.engine.path = self.engine.path.parent
        snapshot = await self.engine.health_snapshot()
        self.assertFalse(snapshot["healthy"])
        self.assertFalse(snapshot["database_healthy"])
        self.assertEqual({"healthy": False}, snapshot["database"])
        self.assertNotIn(self.tmp.name, repr(snapshot))
        self.assertNotIn("payload", repr(snapshot).lower())

    async def test_start_replaces_finished_or_failed_worker_task(self):
        async def finish():
            return None

        finished = asyncio.create_task(finish())
        await finished
        self.engine._task = finished
        await self.engine.start()
        replacement = self.engine._task
        self.assertIsNot(replacement, finished)
        self.assertIsNotNone(replacement)
        self.assertFalse(replacement.done())
        await self.engine.stop()

        async def fail():
            raise RuntimeError("worker stopped")

        failed = asyncio.create_task(fail())
        await asyncio.gather(failed, return_exceptions=True)
        self.engine._task = failed
        await self.engine.start()
        replacement = self.engine._task
        self.assertIsNot(replacement, failed)
        self.assertIsNotNone(replacement)
        self.assertFalse(replacement.done())
        await self.engine.stop()
