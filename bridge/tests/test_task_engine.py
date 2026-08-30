from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.task_engine import TemporalActionEngine


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 26, 16, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class FakeRegistry:
    async def areas(self):
        return [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "bedroom", "name": "Bedroom"},
        ]


class FakeTools:
    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.calls: list[tuple] = []
        self.devices = [
            {
                "entity_id": "light.living_room_floodlight",
                "name": "Living Room Floodlight",
                "area_name": "Living Room",
            },
            {
                "entity_id": "switch.bedside_fan",
                "name": "Bedside Fan",
                "area_name": "Bedroom",
            },
        ]
        self.verified = True
        self.notifications: list[dict[str, str]] = []

    async def controllable_devices(self):
        return list(self.devices)

    async def control_area_lights(self, area_id: str, turn_on: bool):
        self.calls.append(("area_lights", area_id, turn_on))
        return {
            "success": True,
            "verified": self.verified,
            "response_message": "done",
        }

    async def control_device(self, entity_id: str, turn_on: bool):
        self.calls.append(("device", entity_id, turn_on))
        return {
            "success": True,
            "verified": self.verified,
            "response_message": "done",
        }

    async def run_media_shortcut(self, shortcut: str):
        self.calls.append(("media", shortcut))
        return {"success": True, "command_accepted": True}

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ):
        item = {"recipient": recipient, "message": message, "title": title}
        self.notifications.append(item)
        return {"success": True, **item}


class TemporalActionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.tools = FakeTools()
        self.engine = TemporalActionEngine(
            tools=self.tools,
            database_path=str(Path(self.temp.name) / "tasks.db"),
            now_fn=self.clock.now,
            poll_seconds=1,
        )
        self.actor = Actor()

    async def asyncTearDown(self) -> None:
        await self.engine.stop()
        self.temp.cleanup()

    async def test_relative_area_lights_task_executes_and_verifies(self) -> None:
        result = await self.engine.handle_command(
            "Turn the living room lights off in 10 minutes",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        self.assertIn("Task 1", result.response)
        self.clock.advance(minutes=10)
        processed = await self.engine.process_once()
        self.assertEqual(processed, 1)
        self.assertEqual(self.tools.calls, [("area_lights", "living_room", False)])
        task = await self.engine.get_task(1)
        self.assertEqual(task["status"], "completed")

    async def test_relative_device_task_resolves_exact_device(self) -> None:
        result = await self.engine.handle_command(
            "In 5 minutes turn the living room floodlight on",
            self.actor,
        )
        self.assertTrue(result.success)
        self.clock.advance(minutes=5)
        await self.engine.process_once()
        self.assertEqual(
            self.tools.calls,
            [("device", "light.living_room_floodlight", True)],
        )

    async def test_tv_power_uses_allow_listed_shortcut(self) -> None:
        result = await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        self.assertTrue(result.success)
        self.clock.advance(seconds=30)
        await self.engine.process_once()
        self.assertEqual(self.tools.calls, [("media", "tv_off")])

    async def test_tv_app_can_be_scheduled(self) -> None:
        result = await self.engine.handle_command(
            "Open Netflix in 2 minutes",
            self.actor,
        )
        self.assertTrue(result.success)
        self.clock.advance(minutes=2)
        await self.engine.process_once()
        self.assertEqual(self.tools.calls, [("media", "netflix")])

    async def test_task_survives_engine_restart(self) -> None:
        await self.engine.handle_command(
            "Turn the bedroom lights on in 1 hour",
            self.actor,
        )
        restarted = TemporalActionEngine(
            tools=self.tools,
            database_path=str(Path(self.temp.name) / "tasks.db"),
            now_fn=self.clock.now,
        )
        tasks = await restarted.list_tasks(
            owner_key="aaron",
            statuses={"pending"},
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["action_summary"], "turn the Bedroom lights on")

    async def test_cancel_last_prevents_execution(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 5 minutes",
            self.actor,
        )
        cancelled = await self.engine.handle_command(
            "Cancel my last scheduled action",
            self.actor,
        )
        self.assertTrue(cancelled.success)
        self.clock.advance(minutes=5)
        processed = await self.engine.process_once()
        self.assertEqual(processed, 0)
        self.assertEqual(self.tools.calls, [])

    async def test_owner_cannot_cancel_someone_elses_task(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 5 minutes",
            self.actor,
        )
        amber = Actor(user_key="amber", display_name="Amber")
        result = await self.engine.handle_command("Cancel task 1", amber)
        self.assertFalse(result.success)
        task = await self.engine.get_task(1)
        self.assertEqual(task["status"], "pending")

    async def test_unverified_action_is_failed(self) -> None:
        self.tools.verified = False
        await self.engine.handle_command(
            "Turn the living room lights off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        task = await self.engine.get_task(1)
        self.assertEqual(task["status"], "failed")
        self.assertIn("done", task["error"])

    async def test_show_tasks_is_user_scoped(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 5 minutes",
            self.actor,
        )
        amber = Actor(user_key="amber", display_name="Amber")
        result = await self.engine.handle_command("Show scheduled tasks", amber)
        self.assertEqual(result.response, "You have no pending scheduled actions.")

    async def test_completed_task_history_is_visible(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        self.clock.advance(seconds=30)
        await self.engine.process_once()
        result = await self.engine.handle_command("Show task history", self.actor)
        self.assertTrue(result.handled)
        self.assertIn("Task 1 completed", result.response)

    async def test_task_status_reports_result(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        self.clock.advance(seconds=30)
        await self.engine.process_once()
        result = await self.engine.handle_command("What happened to task 1?", self.actor)
        self.assertIn("Task 1 completed", result.response)

    async def test_task_status_is_owner_scoped(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        amber = Actor(user_key="amber", display_name="Amber")
        result = await self.engine.handle_command("What happened to task 1?", amber)
        self.assertFalse(result.success)
        self.assertIn("couldn’t find task 1", result.response)

    async def test_repeat_task_uses_original_delay(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        self.clock.advance(seconds=30)
        await self.engine.process_once()
        result = await self.engine.handle_command("Repeat task 1", self.actor)
        self.assertTrue(result.success)
        self.assertIn("Task 2", result.response)
        repeated = await self.engine.get_task(2)
        self.assertEqual(
            datetime.fromisoformat(repeated["due_at"]),
            self.clock.now() + timedelta(seconds=30),
        )

    async def test_completed_task_sends_owner_notification(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 30 seconds",
            self.actor,
        )
        self.clock.advance(seconds=30)
        await self.engine.process_once()
        self.assertEqual(len(self.tools.notifications), 1)
        self.assertEqual(self.tools.notifications[0]["recipient"], "aaron")
        self.assertIn("Task 1 completed", self.tools.notifications[0]["message"])

    async def test_failed_task_sends_failure_notification(self) -> None:
        self.tools.verified = False
        await self.engine.handle_command(
            "Turn the living room lights off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        self.assertEqual(len(self.tools.notifications), 1)
        self.assertIn("Task 1 failed", self.tools.notifications[0]["message"])

    async def test_clear_completed_history_keeps_pending(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        await self.engine.handle_command(
            "Turn the bedroom lights on in 1 hour",
            self.actor,
        )
        result = await self.engine.handle_command(
            "Delete completed task history",
            self.actor,
        )
        self.assertIn("Deleted 1 completed task history record", result.response)
        self.assertIsNone(await self.engine.get_task(1))
        self.assertEqual((await self.engine.get_task(2))["status"], "pending")

    async def test_ambiguous_delete_history_requires_confirmation(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        result = await self.engine.handle_command("Delete history", self.actor)
        self.assertTrue(result.handled)
        self.assertEqual(result.response, "Do you mean your completed task history?")
        self.assertIsNotNone(await self.engine.get_task(1))

    async def test_confirmed_ambiguous_delete_removes_completed_history(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        await self.engine.handle_command("Delete history", self.actor)
        result = await self.engine.handle_command("Yes", self.actor)
        self.assertIn("Deleted 1 completed task history record", result.response)
        self.assertIsNone(await self.engine.get_task(1))

    async def test_declined_ambiguous_delete_keeps_history(self) -> None:
        await self.engine.handle_command(
            "Turn the TV off in 10 seconds",
            self.actor,
        )
        self.clock.advance(seconds=10)
        await self.engine.process_once()
        await self.engine.handle_command("Delete history", self.actor)
        result = await self.engine.handle_command("No", self.actor)
        self.assertEqual(result.response, "Okay, I won’t delete your task history.")
        self.assertIsNotNone(await self.engine.get_task(1))

    async def test_tomorrow_absolute_time(self) -> None:
        result = await self.engine.handle_command(
            "Tomorrow at 6:15 am turn the bedroom lights on",
            self.actor,
        )
        self.assertTrue(result.success)
        task = await self.engine.get_task(1)
        due = datetime.fromisoformat(task["due_at"])
        self.assertGreater(due, self.clock.now())
        self.assertIn("tomorrow at 6:15 am", result.response)

    async def test_plain_immediate_command_is_not_intercepted(self) -> None:
        result = await self.engine.handle_command(
            "Turn the living room lights off",
            self.actor,
        )
        self.assertFalse(result.handled)


if __name__ == "__main__":
    unittest.main()
