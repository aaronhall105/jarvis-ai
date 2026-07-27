from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.conditional_action_engine import ConditionalActionEngine
from app.task_engine import ActionPlan


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)

    def set(self, value: datetime) -> None:
        self.value = value


class FakeTools:
    MEDIA_SHORTCUTS = {
        "tv_off": {"state_entity_id": "media_player.living_room_tv"},
        "tv_on": {"state_entity_id": "media_player.living_room_tv"},
    }

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {
            "binary_sensor.front_door": self._entity(
                "binary_sensor.front_door", "Front Door", "off", "binary_sensor"
            ),
            "sensor.aaron_phone_battery": self._entity(
                "sensor.aaron_phone_battery", "Aaron Phone Battery", "55", "sensor", unit="%"
            ),
            "sensor.living_room_temperature": self._entity(
                "sensor.living_room_temperature", "Living Room Temperature", "20", "sensor", unit="°C"
            ),
            "person.amber": self._entity("person.amber", "Amber", "home", "person"),
            "person.aaron": self._entity("person.aaron", "Aaron", "home", "person"),
            "sensor.washing_machine_status": self._entity(
                "sensor.washing_machine_status", "Washing Machine Status", "running", "sensor"
            ),
            "switch.bedside_fan": self._entity(
                "switch.bedside_fan", "Bedside Fan", "off", "switch"
            ),
            "light.hallway": self._entity("light.hallway", "Hallway Light", "on", "light"),
            "media_player.living_room_tv": self._entity(
                "media_player.living_room_tv", "Living Room TV", "on", "media_player"
            ),
            "sensor.front_door_battery": self._entity(
                "sensor.front_door_battery", "Front Door Battery", "90", "sensor", unit="%"
            ),
            "sensor.front_door_signal": self._entity(
                "sensor.front_door_signal", "Front Door Signal", "80", "sensor", unit="%"
            ),
        }
        self.notifications: list[dict[str, str]] = []

    @staticmethod
    def _entity(
        entity_id: str,
        name: str,
        state: str,
        domain: str,
        *,
        unit: str | None = None,
        available: bool = True,
    ) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "name": name,
            "state": state,
            "domain": domain,
            "unit": unit,
            "available": available,
            "area_name": "Living Room" if "living_room" in entity_id else None,
        }

    def set_state(self, entity_id: str, state: str, *, available: bool = True) -> None:
        self.states[entity_id]["state"] = state
        self.states[entity_id]["available"] = available

    async def readable_entity_states(self, *, refresh: bool = True):
        return [dict(item) for item in self.states.values()]

    async def search_entity_states(
        self,
        query: str,
        *,
        domain: str | None = None,
        area_id: str | None = None,
        state_filter: str | None = None,
        limit: int = 12,
    ):
        key = self._normalise(query)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in self.states.values():
            if domain and item["domain"] != domain:
                continue
            name_key = self._normalise(item["name"])
            id_key = self._normalise(item["entity_id"])
            combined = f"{name_key} {id_key}"
            score = 0
            if key == name_key:
                score = 100
            elif key == id_key:
                score = 95
            elif key and key in combined:
                score = 50
            elif key and all(part in combined for part in key.split()):
                score = 25
            if score:
                ranked.append((score, dict(item)))
        ranked.sort(key=lambda value: value[0], reverse=True)
        return {"success": True, "count": len(ranked[:limit]), "entities": [x[1] for x in ranked[:limit]]}

    @staticmethod
    def _normalise(value: str) -> str:
        return value.casefold().replace("_", " ").replace(".", " ").strip()

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
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.success = True

    async def _resolve_action(self, text: str, actor_key: str | None = None):
        value = text.casefold()
        if " and " in value:
            parts = [part.strip() for part in value.split(" and ") if part.strip()]
            plans = [await self._resolve_action(part, actor_key=actor_key) for part in parts]
            if any(isinstance(plan, str) for plan in plans):
                return "Unsupported compound action."
            return ActionPlan(
                action_type="sequence",
                payload={"steps": [asdict(plan) for plan in plans], "stop_on_error": True},
                summary=", then ".join(plan.summary for plan in plans),
            )
        if "hallway" in value and "light" in value:
            turn_on = " on" in value and "off" not in value
            return ActionPlan(
                action_type="device_control",
                payload={"entity_id": "light.hallway", "turn_on": turn_on},
                summary=f"turn Hallway Light {'on' if turn_on else 'off'}",
            )
        if "bedside fan" in value:
            turn_on = " on" in value and "off" not in value
            return ActionPlan(
                action_type="device_control",
                payload={"entity_id": "switch.bedside_fan", "turn_on": turn_on},
                summary=f"turn Bedside Fan {'on' if turn_on else 'off'}",
            )
        if "tv" in value:
            return ActionPlan(
                action_type="media_shortcut",
                payload={"shortcut": "tv_off"},
                summary="turn the TV off",
            )
        return "Unsupported action."

    async def _execute_action(self, action_type: str, payload: dict[str, Any]):
        if action_type == "sequence":
            results = []
            for step in payload["steps"]:
                results.append(await self._execute_action(step["action_type"], step["payload"]))
            return {
                "success": all(result.get("success") for result in results),
                "verified": all(result.get("verified") for result in results),
                "steps": results,
                "response_message": "done",
            }
        self.calls.append((action_type, dict(payload)))
        if self.success:
            return {"success": True, "verified": True, "response_message": "done"}
        return {"success": False, "verified": False, "response_message": "failed"}


class ConditionalActionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.tools = FakeTools()
        self.actions = FakeActionEngine()
        self.engine = ConditionalActionEngine(
            tools=self.tools,
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "conditions.db"),
            poll_seconds=1,
            default_cooldown_seconds=0,
            default_debounce_seconds=0,
            now_fn=self.clock.now,
        )
        self.actor = Actor()

    async def asyncTearDown(self) -> None:
        await self.engine.stop()
        self.temp.cleanup()

    async def create_door_rule(self, text: str | None = None):
        result = await self.engine.handle_command(
            text or "When the front door opens, then notify me",
            self.actor,
        )
        self.assertTrue(result.handled)
        self.assertTrue(result.success)
        return result

    async def test_state_rule_uses_current_state_as_baseline(self) -> None:
        await self.create_door_rule()
        processed = await self.engine.process_once()
        self.assertEqual(processed, 0)
        self.assertEqual(self.tools.notifications, [])

    async def test_state_edge_triggers_once(self) -> None:
        await self.create_door_rule()
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 1)
        self.assertEqual(len(self.tools.notifications), 1)
        self.assertEqual(await self.engine.process_once(), 0)
        self.assertEqual(len(self.tools.notifications), 1)

    async def test_duplicate_rule_is_not_created(self) -> None:
        await self.create_door_rule()
        result = await self.create_door_rule()
        self.assertIn("already exists", result.response)
        rules = await self.engine.list_rules(owner_key="aaron")
        self.assertEqual(len(rules), 1)

    async def test_next_time_creates_one_shot_rule(self) -> None:
        result = await self.engine.handle_command(
            "Next time the front door opens, then notify me",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("binary_sensor.front_door", "on")
        await self.engine.process_once()
        rule = await self.engine.get_rule(1)
        self.assertEqual(rule["status"], "completed")

    async def test_cooldown_suppresses_repeated_edge(self) -> None:
        await self.create_door_rule(
            "When the front door opens, then notify me with a 10 minute cooldown"
        )
        self.tools.set_state("binary_sensor.front_door", "on")
        await self.engine.process_once()
        self.tools.set_state("binary_sensor.front_door", "off")
        await self.engine.process_once()
        self.clock.advance(minutes=1)
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 0)
        runs = await self.engine.list_runs(1, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "skipped")

    async def test_debounce_requires_condition_to_remain_true(self) -> None:
        await self.create_door_rule(
            "When the front door opens for 5 seconds, then notify me"
        )
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 0)
        self.clock.advance(seconds=4)
        self.assertEqual(await self.engine.process_once(), 0)
        self.clock.advance(seconds=1)
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_debounce_candidate_is_cancelled_if_state_reverts(self) -> None:
        await self.create_door_rule(
            "When the front door opens for 5 seconds, then notify me"
        )
        self.tools.set_state("binary_sensor.front_door", "on")
        await self.engine.process_once()
        self.tools.set_state("binary_sensor.front_door", "off")
        self.clock.advance(seconds=5)
        self.assertEqual(await self.engine.process_once(), 0)
        self.assertEqual(self.tools.notifications, [])

    async def test_numeric_below_triggers_on_crossing(self) -> None:
        result = await self.engine.handle_command(
            "When Aaron Phone Battery drops below 20%, then notify me",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("sensor.aaron_phone_battery", "19")
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_numeric_above_triggers_on_crossing(self) -> None:
        result = await self.engine.handle_command(
            "When Living Room Temperature goes above 25 degrees, then turn the hallway light off",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("sensor.living_room_temperature", "26")
        self.assertEqual(await self.engine.process_once(), 1)
        self.assertEqual(self.actions.calls[0][0], "device_control")

    async def test_presence_leave_trigger(self) -> None:
        result = await self.engine.handle_command(
            "When Amber leaves home, then turn the hallway light off",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("person.amber", "not_home")
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_presence_arrive_trigger(self) -> None:
        self.tools.set_state("person.amber", "not_home")
        result = await self.engine.handle_command(
            "When Amber arrives home, then turn the hallway light on",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("person.amber", "home")
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_finishes_accepts_idle_state(self) -> None:
        result = await self.engine.handle_command(
            "When Washing Machine Status finishes, then notify me",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("sensor.washing_machine_status", "idle")
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_cross_midnight_window_allows_late_trigger(self) -> None:
        self.clock.set(datetime(2026, 7, 27, 22, 30, tzinfo=timezone.utc))
        result = await self.engine.handle_command(
            "When the front door opens between 10 pm and 7 am, then notify me",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 1)

    async def test_outside_window_updates_baseline_without_triggering(self) -> None:
        self.clock.set(datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc))
        result = await self.engine.handle_command(
            "When the front door opens after 11 pm, then notify me",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 0)
        self.clock.set(datetime(2026, 7, 27, 23, 30, tzinfo=timezone.utc))
        self.assertEqual(await self.engine.process_once(), 0)

    async def test_rule_survives_restart(self) -> None:
        await self.create_door_rule()
        restarted = ConditionalActionEngine(
            tools=self.tools,
            action_engine=self.actions,
            database_path=str(Path(self.temp.name) / "conditions.db"),
            now_fn=self.clock.now,
            default_cooldown_seconds=0,
            default_debounce_seconds=0,
        )
        rules = await restarted.list_rules(owner_key="aaron", statuses={"active"})
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["trigger_entity_id"], "binary_sensor.front_door")

    async def test_pause_resume_and_cancel_are_owner_scoped(self) -> None:
        await self.create_door_rule()
        amber = Actor(user_key="amber", display_name="Amber")
        denied = await self.engine.handle_command("Pause rule 1", amber)
        self.assertFalse(denied.success)
        paused = await self.engine.handle_command("Pause rule 1", self.actor)
        self.assertTrue(paused.success)
        resumed = await self.engine.handle_command("Resume rule 1", self.actor)
        self.assertTrue(resumed.success)
        cancelled = await self.engine.handle_command("Cancel rule 1", self.actor)
        self.assertTrue(cancelled.success)
        self.assertEqual((await self.engine.get_rule(1))["status"], "cancelled")

    async def test_failed_action_is_recorded_and_notified(self) -> None:
        result = await self.engine.handle_command(
            "When the front door opens, then turn the hallway light off",
            self.actor,
        )
        self.assertTrue(result.success)
        self.actions.success = False
        self.tools.set_state("binary_sensor.front_door", "on")
        await self.engine.process_once()
        runs = await self.engine.list_runs(1, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("failed", self.tools.notifications[-1]["title"].casefold())

    async def test_unavailable_trigger_does_not_execute(self) -> None:
        await self.create_door_rule()
        self.tools.set_state("binary_sensor.front_door", "on", available=False)
        self.assertEqual(await self.engine.process_once(), 0)
        self.assertEqual(self.tools.notifications, [])

    async def test_list_status_and_history_are_owner_scoped(self) -> None:
        await self.create_door_rule()
        amber = Actor(user_key="amber", display_name="Amber")
        listed = await self.engine.handle_command("Show my rules", amber)
        self.assertEqual(listed.response, "You have no conditional rules.")
        status = await self.engine.handle_command("Show rule 1", self.actor)
        self.assertIn("Rule 1 is active", status.response)
        history = await self.engine.handle_command("Show history for rule 1", self.actor)
        self.assertIn("has not triggered", history.response)

    async def test_change_cooldown(self) -> None:
        await self.create_door_rule()
        result = await self.engine.handle_command(
            "Change rule 1 cooldown to 15 minutes",
            self.actor,
        )
        self.assertTrue(result.success)
        self.assertEqual((await self.engine.get_rule(1))["cooldown_seconds"], 900)

    async def test_change_time_window(self) -> None:
        await self.create_door_rule()
        result = await self.engine.handle_command(
            "Change rule 1 time window to between 10 pm and 7 am",
            self.actor,
        )
        self.assertTrue(result.success)
        rule = await self.engine.get_rule(1)
        self.assertEqual(rule["window_start_minute"], 1320)
        self.assertEqual(rule["window_end_minute"], 420)

    async def test_ambiguous_entity_is_rejected(self) -> None:
        result = await self.engine.handle_command(
            "When front door drops below 20%, then notify me",
            self.actor,
        )
        self.assertFalse(result.success)
        self.assertIn("more than one", result.response)

    async def test_plain_command_is_not_intercepted(self) -> None:
        result = await self.engine.handle_command(
            "Turn the hallway light off",
            self.actor,
        )
        self.assertFalse(result.handled)


    async def test_timed_condition_executes_when_state_is_true(self) -> None:
        result = await self.engine.handle_command(
            "At 7 pm, turn the TV off only if it is still on",
            self.actor,
        )
        self.assertTrue(result.success)
        self.clock.set(datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(await self.engine.process_once(), 1)
        self.assertEqual(self.actions.calls[-1][1]["shortcut"], "tv_off")
        self.assertEqual((await self.engine.get_rule(1))["status"], "completed")

    async def test_timed_condition_skips_when_state_is_false(self) -> None:
        result = await self.engine.handle_command(
            "At 7 pm, turn the TV off only if it is still on",
            self.actor,
        )
        self.assertTrue(result.success)
        self.tools.set_state("media_player.living_room_tv", "off")
        self.clock.set(datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(await self.engine.process_once(), 0)
        self.assertEqual(self.actions.calls, [])
        self.assertEqual((await self.engine.get_rule(1))["status"], "completed")
        runs = await self.engine.list_runs(1, owner_key="aaron")
        self.assertEqual(runs[0]["status"], "skipped")

    async def test_timed_condition_rolls_past_time_to_tomorrow(self) -> None:
        self.clock.set(datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc))
        result = await self.engine.handle_command(
            "At 6 pm, turn the TV off only if it is still on",
            self.actor,
        )
        self.assertTrue(result.success)
        rule = await self.engine.get_rule(1)
        due = datetime.fromisoformat(rule["trigger_payload"]["due_at"])
        self.assertGreater(due, self.clock.now())
        self.assertEqual(due.date().isoformat(), "2026-07-28")

    async def test_notification_rule_rejects_other_recipient(self) -> None:
        result = await self.engine.handle_command(
            "When the front door opens, then notify Amber",
            self.actor,
        )
        self.assertFalse(result.success)
        self.assertIn("only notify its owner", result.response)

    async def test_multi_step_condition_executes_all_actions(self) -> None:
        result = await self.engine.handle_command(
            "When the front door opens, then turn the hallway light off and turn the bedside fan on",
            self.actor,
        )
        self.assertTrue(result.success)
        rule = result.details["rule"]
        self.assertEqual(rule["action_type"], "sequence")
        self.tools.set_state("binary_sensor.front_door", "on")
        self.assertEqual(await self.engine.process_once(), 1)
        self.assertEqual(
            self.actions.calls,
            [
                ("device_control", {"entity_id": "light.hallway", "turn_on": False}),
                ("device_control", {"entity_id": "switch.bedside_fan", "turn_on": True}),
            ],
        )



if __name__ == "__main__":
    unittest.main()
