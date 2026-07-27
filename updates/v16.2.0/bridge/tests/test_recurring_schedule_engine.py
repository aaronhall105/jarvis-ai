from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.recurring_schedule_engine import RecurringScheduleEngine
from app.task_engine import ActionPlan


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron"


class Clock:
    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class FakeRegistry:
    def __init__(self) -> None:
        self.items = [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "bedroom", "name": "Bedroom"},
        ]

    async def areas(self):
        return list(self.items)


class FakeTools:
    MEDIA_SHORTCUTS = {
        "tv_on": {"name": "TV"},
        "tv_off": {"name": "TV"},
        "netflix": {"name": "Netflix"},
    }

    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.devices = [
            {
                "entity_id": "light.living_room_floodlight",
                "name": "Living Room Floodlight",
                "area_name": "Living Room",
                "available": True,
            },
            {
                "entity_id": "switch.bedside_fan",
                "name": "Bedside Fan",
                "area_name": "Bedroom",
                "available": True,
            },
        ]
        self.notifications: list[dict[str, str]] = []

    async def controllable_devices(self):
        return list(self.devices)

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ):
        item = {"recipient": recipient, "message": message, "title": title}
        self.notifications.append(item)
        return {"success": True, **item}


class FakeActionEngine:
    def __init__(self, tools: FakeTools) -> None:
        self.tools = tools
        self.calls: list[tuple] = []
        self.verified = True

    async def _resolve_action(self, text: str):
        value = text.casefold().strip()
        turn_on = " on" in f" {value}" or value.endswith("on")
        if "living room lights" in value:
            return ActionPlan(
                action_type="area_lights",
                payload={"area_id": "living_room", "turn_on": turn_on},
                summary=f"turn the Living Room lights {'on' if turn_on else 'off'}",
            )
        if "bedroom lights" in value:
            return ActionPlan(
                action_type="area_lights",
                payload={"area_id": "bedroom", "turn_on": turn_on},
                summary=f"turn the Bedroom lights {'on' if turn_on else 'off'}",
            )
        if "bedside fan" in value:
            return ActionPlan(
                action_type="device_control",
                payload={"entity_id": "switch.bedside_fan", "turn_on": turn_on},
                summary=f"turn Bedside Fan {'on' if turn_on else 'off'}",
            )
        if "tv" in value:
            return ActionPlan(
                action_type="media_shortcut",
                payload={"shortcut": "tv_on" if turn_on else "tv_off"},
                summary=f"turn the TV {'on' if turn_on else 'off'}",
            )
        if "netflix" in value:
            return ActionPlan(
                action_type="media_shortcut",
                payload={"shortcut": "netflix"},
                summary="open Netflix",
            )
        return "I can currently schedule lights, switches, TV power and configured TV apps."

    async def _execute_action(self, action_type: str, payload: dict):
        self.calls.append((action_type, dict(payload)))
        return {
            "success": True,
            "verified": self.verified,
            "response_message": "done",
        }


class RecurringScheduleEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.tools = FakeTools()
        self.actions = FakeActionEngine(self.tools)
        self.engine = RecurringScheduleEngine(
            tools=self.tools,
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "schedules.db"),
            now_fn=self.clock.now,
            poll_seconds=1,
            misfire_grace_seconds=300,
        )
        self.actor = Actor()

    async def asyncTearDown(self) -> None:
        await self.engine.stop()
        self.temp.cleanup()

    async def test_weekday_schedule_creates_exact_next_run(self) -> None:
        result = await self.engine.handle_command(
            "Every weekday at 6:30 am turn the bedroom lights on",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        item = (result.details or {})["schedule"]
        self.assertEqual(item["recurrence_type"], "weekly")
        self.assertEqual(item["weekdays"], [0, 1, 2, 3, 4])
        self.assertEqual(item["local_time"], "06:30")
        self.assertEqual(
            datetime.fromisoformat(item["next_run_at"]),
            datetime(2026, 7, 27, 5, 30, tzinfo=timezone.utc),
        )

    async def test_suffix_daily_schedule_is_parsed(self) -> None:
        result = await self.engine.handle_command(
            "Turn the TV off every night at 10 pm",
            self.actor,
        )
        self.assertTrue(result.success)
        item = (result.details or {})["schedule"]
        self.assertEqual(item["action_payload"], {"shortcut": "tv_off"})
        self.assertEqual(item["local_time"], "22:00")

    async def test_named_day_schedule_runs_and_reschedules(self) -> None:
        result = await self.engine.handle_command(
            "Every Monday at 6:05 am turn the living room lights off",
            self.actor,
        )
        item = (result.details or {})["schedule"]
        self.clock.advance(minutes=5)
        processed = await self.engine.process_once()
        self.assertEqual(processed, 1)
        self.assertEqual(len(self.actions.calls), 1)
        refreshed = await self.engine.get_schedule(item["schedule_id"])
        self.assertEqual(refreshed["run_count"], 1)
        self.assertEqual(refreshed["status"], "active")
        self.assertGreater(
            datetime.fromisoformat(refreshed["next_run_at"]),
            self.clock.now(),
        )
        runs = await self.engine.list_runs(item["schedule_id"], owner_key="aaron")
        self.assertEqual(runs[0]["status"], "completed")

    async def test_interval_schedule_uses_anchor_without_drift(self) -> None:
        result = await self.engine.handle_command(
            "Every 2 hours turn the bedside fan on",
            self.actor,
        )
        item = (result.details or {})["schedule"]
        self.assertEqual(item["interval_seconds"], 7200)
        self.clock.advance(hours=2)
        await self.engine.process_once()
        refreshed = await self.engine.get_schedule(item["schedule_id"])
        self.assertEqual(
            datetime.fromisoformat(refreshed["next_run_at"]),
            datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
        )

    async def test_duplicate_schedule_is_not_created(self) -> None:
        first = await self.engine.handle_command(
            "Every day at 7 am turn the bedroom lights on",
            self.actor,
        )
        second = await self.engine.handle_command(
            "Daily at 7 am turn the bedroom lights on",
            self.actor,
        )
        self.assertTrue((second.details or {})["duplicate"])
        self.assertEqual(
            (first.details or {})["schedule"]["schedule_id"],
            (second.details or {})["schedule"]["schedule_id"],
        )
        items = await self.engine.list_schedules(owner_key="aaron")
        self.assertEqual(len(items), 1)

    async def test_pause_resume_and_cancel_are_owner_scoped(self) -> None:
        created = await self.engine.handle_command(
            "Every day at 7 am turn the bedroom lights on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        amber = Actor(user_key="amber", display_name="Amber")
        denied = await self.engine.handle_command(f"Pause schedule {schedule_id}", amber)
        self.assertFalse(denied.success)
        paused = await self.engine.handle_command(
            f"Pause schedule {schedule_id}",
            self.actor,
        )
        self.assertTrue(paused.success)
        self.assertEqual((await self.engine.get_schedule(schedule_id))["status"], "paused")
        resumed = await self.engine.handle_command(
            f"Resume schedule {schedule_id}",
            self.actor,
        )
        self.assertTrue(resumed.success)
        cancelled = await self.engine.handle_command(
            f"Cancel schedule {schedule_id}",
            self.actor,
        )
        self.assertTrue(cancelled.success)
        self.assertEqual((await self.engine.get_schedule(schedule_id))["status"], "cancelled")

    async def test_change_time_recalculates_next_run(self) -> None:
        created = await self.engine.handle_command(
            "Every day at 7 am turn the bedroom lights on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        changed = await self.engine.handle_command(
            f"Change schedule {schedule_id} to 11 pm",
            self.actor,
        )
        self.assertTrue(changed.success)
        item = await self.engine.get_schedule(schedule_id)
        self.assertEqual(item["local_time"], "23:00")
        self.assertEqual(item["recurrence_description"], "every day at 11 pm")

    async def test_missed_run_beyond_grace_is_skipped(self) -> None:
        created = await self.engine.handle_command(
            "Every 1 hour turn the bedside fan on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        self.clock.advance(hours=2)
        await self.engine.process_once()
        self.assertEqual(self.actions.calls, [])
        runs = await self.engine.list_runs(schedule_id, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "skipped")
        item = await self.engine.get_schedule(schedule_id)
        self.assertGreater(datetime.fromisoformat(item["next_run_at"]), self.clock.now())

    async def test_missed_run_within_grace_executes_once(self) -> None:
        created = await self.engine.handle_command(
            "Every 1 hour turn the bedside fan on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        self.clock.advance(hours=1, minutes=2)
        await self.engine.process_once()
        self.assertEqual(len(self.actions.calls), 1)
        runs = await self.engine.list_runs(schedule_id, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "completed")

    async def test_device_capability_is_checked_again_before_execution(self) -> None:
        created = await self.engine.handle_command(
            "Every 1 hour turn the bedside fan on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        self.tools.devices[1]["available"] = False
        self.clock.advance(hours=1)
        await self.engine.process_once()
        self.assertEqual(self.actions.calls, [])
        runs = await self.engine.list_runs(schedule_id, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("unavailable", runs[0]["error"])

    async def test_schedule_survives_restart(self) -> None:
        await self.engine.handle_command(
            "Every Saturday at 9 am turn the living room lights on",
            self.actor,
        )
        restarted = RecurringScheduleEngine(
            tools=self.tools,
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "schedules.db"),
            now_fn=self.clock.now,
        )
        items = await restarted.list_schedules(owner_key="aaron", statuses={"active"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["weekdays"], [5])

    async def test_list_next_status_and_history_are_user_scoped(self) -> None:
        created = await self.engine.handle_command(
            "Every 1 hour turn the bedside fan on",
            self.actor,
        )
        schedule_id = (created.details or {})["schedule"]["schedule_id"]
        listed = await self.engine.handle_command("What schedules do I have?", self.actor)
        self.assertIn(f"Schedule {schedule_id}", listed.response)
        next_item = await self.engine.handle_command("When will my next schedule run?", self.actor)
        self.assertIn(f"schedule {schedule_id}", next_item.response)
        status = await self.engine.handle_command(
            f"When will schedule {schedule_id} run?",
            self.actor,
        )
        self.assertIn(f"Schedule {schedule_id}", status.response)
        amber = Actor(user_key="amber", display_name="Amber")
        denied = await self.engine.handle_command(
            f"Show schedule {schedule_id} history",
            amber,
        )
        self.assertFalse(denied.success)

    async def test_unclear_recurrence_asks_for_exact_time(self) -> None:
        result = await self.engine.handle_command(
            "Every weekday turn the bedroom lights on",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertFalse(result.success)
        self.assertIn("exact time", result.response)

    async def test_spring_forward_nonexistent_time_moves_to_valid_local_time(self) -> None:
        self.clock.value = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
        result = await self.engine.handle_command(
            "Every Sunday at 1:30 am turn the living room lights on",
            self.actor,
        )
        item = (result.details or {})["schedule"]
        next_run = datetime.fromisoformat(item["next_run_at"])
        # 01:30 does not exist in Europe/London on 29 March 2026. The engine
        # preserves the intended elapsed wall time and schedules 02:30 BST.
        self.assertEqual(next_run, datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc))

    async def test_fall_back_ambiguous_time_runs_once(self) -> None:
        self.clock.value = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        result = await self.engine.handle_command(
            "Every Sunday at 1:30 am turn the living room lights on",
            self.actor,
        )
        item = (result.details or {})["schedule"]
        first_run = datetime.fromisoformat(item["next_run_at"])
        self.assertEqual(first_run, datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc))
        self.clock.value = first_run
        await self.engine.process_once()
        refreshed = await self.engine.get_schedule(item["schedule_id"])
        next_run = datetime.fromisoformat(refreshed["next_run_at"])
        self.assertGreater(next_run, datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc))
        runs = await self.engine.list_runs(item["schedule_id"], owner_key="aaron")
        self.assertEqual(len(runs), 1)

    async def test_plain_one_off_command_is_not_intercepted(self) -> None:
        result = await self.engine.handle_command(
            "Turn the bedroom lights on in 10 minutes",
            self.actor,
        )
        self.assertFalse(result.handled)

    async def test_completed_and_failed_runs_notify_owner(self) -> None:
        await self.engine.handle_command(
            "Every 1 hour turn the bedside fan on",
            self.actor,
        )
        self.clock.advance(hours=1)
        await self.engine.process_once()
        self.assertEqual(len(self.tools.notifications), 1)
        self.assertIn("Schedule 1 ran", self.tools.notifications[0]["message"])

        await self.engine.handle_command(
            "Every 2 hours turn the living room lights off",
            self.actor,
        )
        self.actions.verified = False
        self.clock.advance(hours=1)
        await self.engine.process_once()
        self.assertGreaterEqual(len(self.tools.notifications), 2)
        self.assertTrue(
            any("failed" in item["message"].casefold() for item in self.tools.notifications)
        )


if __name__ == "__main__":
    unittest.main()
