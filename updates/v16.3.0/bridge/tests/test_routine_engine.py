from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from app.routine_engine import RoutineEngine
from app.task_engine import TemporalActionEngine


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron"


class FakeRegistry:
    async def areas(self):
        return [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "hallway", "name": "Hallway"},
            {"area_id": "bedroom", "name": "Bedroom"},
        ]


class FakeTools:
    MEDIA_SHORTCUTS = {
        "tv_on": {},
        "tv_off": {},
        "netflix": {},
        "youtube": {},
        "bbc_iplayer": {},
        "prime_video": {},
    }

    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.calls: list[tuple] = []
        self.fail_entity: str | None = None
        self.devices = [
            {
                "entity_id": "light.living_room_floodlight",
                "name": "Living Room Floodlight",
                "area_name": "Living Room",
                "available": True,
            },
            {
                "entity_id": "light.hallway_light",
                "name": "Hallway Light",
                "area_name": "Hallway",
                "available": True,
            },
            {
                "entity_id": "switch.bedside_fan",
                "name": "Bedside Fan",
                "area_name": "Bedroom",
                "available": True,
            },
        ]
        self.home_routines = [
            {
                "domain": "script",
                "entity_id": "script.bedtime",
                "name": "Bedtime",
                "state": "off",
            },
            {
                "domain": "automation",
                "entity_id": "automation.good_morning",
                "name": "Good Morning",
                "state": "on",
            },
        ]

    async def controllable_devices(self):
        return list(self.devices)

    async def control_area_lights(self, area_id: str, turn_on: bool):
        self.calls.append(("area_lights", area_id, turn_on))
        return {
            "success": True,
            "verified": True,
            "response_message": "done",
        }

    async def control_device(self, entity_id: str, turn_on: bool):
        self.calls.append(("device", entity_id, turn_on))
        if entity_id == self.fail_entity:
            return {
                "success": False,
                "verified": False,
                "response_message": "device failed",
            }
        return {
            "success": True,
            "verified": True,
            "response_message": "done",
        }

    async def run_media_shortcut(self, shortcut: str):
        self.calls.append(("media", shortcut))
        return {
            "success": True,
            "verified": shortcut in {"tv_on", "tv_off"},
            "command_accepted": True,
            "response_message": "accepted",
        }

    async def runnable_routines(self, limit: int = 100):
        return list(self.home_routines)[:limit]

    async def run_home_routine(self, entity_id: str, *, name: str | None = None):
        self.calls.append(("home_routine", entity_id, name))
        return {
            "success": True,
            "verified": False,
            "command_accepted": True,
            "response_message": "started",
        }

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ):
        self.calls.append(("notify", recipient, message, title))
        return {
            "success": True,
            "verified": False,
            "command_accepted": True,
            "response_message": "sent",
        }

    async def announce_message(self, target: str, message: str):
        self.calls.append(("announce", target, message))
        return {
            "success": True,
            "verified": False,
            "command_accepted": True,
            "response_message": "announced",
        }


class RoutineEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.tools = FakeTools()
        self.actions = TemporalActionEngine(
            tools=self.tools,
            database_path=str(Path(self.temp.name) / "tasks.db"),
        )
        self.routines = RoutineEngine(
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "routines.db"),
        )
        self.actor = Actor()

    async def asyncTearDown(self) -> None:
        await self.actions.stop()
        self.temp.cleanup()

    async def test_create_saved_routine_resolves_exact_steps(self) -> None:
        result = await self.routines.handle_command(
            "Create a routine called Movie Night: turn the living room lights off, "
            "turn the TV on and open Netflix",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        routine = result.details["routine"]
        self.assertEqual(routine["name"], "Movie Night")
        self.assertEqual(routine["step_count"], 3)
        self.assertEqual(routine["action_type"], "sequence")

    async def test_run_saved_routine_executes_in_order(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the living room lights off, "
            "turn the TV on and open Netflix",
            self.actor,
        )
        result = await self.routines.handle_command("Start Movie Night", self.actor)
        self.assertTrue(result.success)
        self.assertEqual(
            self.tools.calls,
            [
                ("area_lights", "living_room", False),
                ("media", "tv_on"),
                ("media", "netflix"),
            ],
        )
        self.assertIn("completed all 3 steps", result.response)

    async def test_ad_hoc_scene_executes_without_saving(self) -> None:
        result = await self.routines.handle_command(
            "Turn the hallway lights on and turn the TV off",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.intent, "scene_run")
        self.assertTrue(result.success)
        self.assertEqual(len(await self.routines.list_routines(owner_key="aaron")), 0)

    async def test_sequence_stops_after_failed_step(self) -> None:
        self.tools.fail_entity = "switch.bedside_fan"
        await self.routines.handle_command(
            "Create routine Broken Night: turn the hallway lights on, "
            "turn the bedside fan on and open Netflix",
            self.actor,
        )
        result = await self.routines.handle_command("Run Broken Night", self.actor)
        self.assertFalse(result.success)
        self.assertEqual(
            self.tools.calls,
            [
                ("area_lights", "hallway", True),
                ("device", "switch.bedside_fan", True),
            ],
        )
        self.assertIn("Step 2 failed", result.response)

    async def test_duplicate_name_is_not_created(self) -> None:
        first = await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        second = await self.routines.handle_command(
            "Create routine Movie Night: turn the TV off and open YouTube",
            self.actor,
        )
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(second.details["duplicate"])
        self.assertEqual(len(await self.routines.list_routines(owner_key="aaron")), 1)

    async def test_owner_cannot_run_someone_elses_routine(self) -> None:
        await self.routines.handle_command(
            "Create routine Private: turn the TV on and open Netflix",
            self.actor,
        )
        amber = Actor(user_key="amber", display_name="Amber")
        result = await self.routines.handle_command("Run routine 1", amber)
        self.assertFalse(result.success)
        self.assertIn("couldn’t find routine 1", result.response)
        self.assertEqual(self.tools.calls, [])

    async def test_disable_enable_and_delete(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        disabled = await self.routines.handle_command("Disable routine 1", self.actor)
        self.assertTrue(disabled.success)
        run_disabled = await self.routines.handle_command("Run routine 1", self.actor)
        self.assertFalse(run_disabled.success)
        self.assertIn("disabled", run_disabled.response)
        enabled = await self.routines.handle_command("Enable routine 1", self.actor)
        self.assertTrue(enabled.success)
        deleted = await self.routines.handle_command("Delete routine 1", self.actor)
        self.assertTrue(deleted.success)
        self.assertIsNone(await self.routines.get_routine(1))

    async def test_rename_routine(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        result = await self.routines.handle_command(
            "Rename routine 1 to Cinema Mode",
            self.actor,
        )
        self.assertTrue(result.success)
        renamed = await self.routines.get_routine(1)
        self.assertEqual(renamed["name"], "Cinema Mode")

    async def test_restart_persists_routine(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        restarted = RoutineEngine(
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "routines.db"),
        )
        item = await restarted.get_routine(1)
        self.assertEqual(item["name"], "Movie Night")
        self.assertEqual(item["step_count"], 2)

    async def test_run_history_is_recorded(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        await self.routines.handle_command("Run routine 1", self.actor)
        result = await self.routines.handle_command(
            "Show history for routine 1",
            self.actor,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.details["runs"][0]["status"], "completed")

    async def test_show_and_list_are_owner_scoped(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        amber = Actor(user_key="amber", display_name="Amber")
        listed = await self.routines.handle_command("Show my routines", amber)
        self.assertEqual(listed.response, "You have no saved routines.")
        shown = await self.routines.handle_command("Show routine 1", amber)
        self.assertFalse(shown.success)

    async def test_notify_me_uses_owner(self) -> None:
        result = await self.routines.handle_command(
            "Create routine Phone Test: notify me that the test is complete and "
            "turn the hallway lights on",
            self.actor,
        )
        self.assertTrue(result.success)
        await self.routines.handle_command("Run Phone Test", self.actor)
        self.assertEqual(self.tools.calls[0], ("notify", "aaron", "the test is complete", "Jarvis"))

    async def test_announcement_step_is_supported(self) -> None:
        await self.routines.handle_command(
            "Create routine Welcome Home: turn the hallway lights on and "
            "announce that Amber is home in the living room",
            self.actor,
        )
        result = await self.routines.handle_command("Run Welcome Home", self.actor)
        self.assertTrue(result.success)
        self.assertEqual(
            self.tools.calls[-1],
            ("announce", "living_room", "Amber is home"),
        )

    async def test_existing_home_assistant_routine_can_run(self) -> None:
        result = await self.routines.handle_command("Run bedtime routine", self.actor)
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        self.assertEqual(
            self.tools.calls,
            [("home_routine", "script.bedtime", "Bedtime")],
        )

    async def test_unknown_run_name_falls_through(self) -> None:
        result = await self.routines.handle_command("Start something unknown", self.actor)
        self.assertFalse(result.handled)

    async def test_conditional_command_is_not_intercepted_as_scene(self) -> None:
        result = await self.routines.handle_command(
            "When Amber gets home, turn the hallway lights on and turn the TV on",
            self.actor,
        )
        self.assertFalse(result.handled)

    async def test_recurring_command_is_not_intercepted_as_scene(self) -> None:
        result = await self.routines.handle_command(
            "Every night at 10 pm turn the TV off and turn the hallway lights off",
            self.actor,
        )
        self.assertFalse(result.handled)

    async def test_too_many_steps_is_rejected(self) -> None:
        action = " and ".join(["turn the TV on"] * 9)
        result = await self.routines.handle_command(
            f"Create routine Too Much: {action}",
            self.actor,
        )
        self.assertFalse(result.success)
        self.assertIn("no more than eight steps", result.response)

    async def test_sequence_can_include_app_command_accepted_steps(self) -> None:
        result = await self.routines.handle_command(
            "Turn the TV on and open Netflix",
            self.actor,
        )
        self.assertTrue(result.success)
        self.assertIn("completed all 2 steps", result.response)

    async def test_status_reports_version_and_counts(self) -> None:
        await self.routines.handle_command(
            "Create routine Movie Night: turn the TV on and open Netflix",
            self.actor,
        )
        status = await self.routines.status()
        self.assertEqual(status["version"], "16.3.0")
        self.assertEqual(status["routine_counts"]["active"], 1)


if __name__ == "__main__":
    unittest.main()
