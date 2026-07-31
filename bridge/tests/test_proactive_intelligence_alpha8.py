import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.proactive_intelligence import (
    ActionModel,
    Candidate,
    ProactiveEngine,
    Rules,
)


class Alpha8ProactiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = ProactiveEngine(
            str(Path(self.temp.name) / "proactive.db"),
            enabled=True,
            cooldown=300,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_open_door_scores_high(self):
        result = Rules(door_seconds=600).evaluate(
            {"entity_id": "binary_sensor.front_door", "state": "on"},
            {
                "entity_id": "binary_sensor.front_door",
                "state": "on",
                "attributes": {"friendly_name": "Front Door"},
            },
            first_seen=100,
            now=800,
            presence={"aaron": "not_home", "amber": "not_home"},
        )
        self.assertEqual("security", result[0].category)
        self.assertGreaterEqual(result[0].importance, 90)

    def test_washing_machine_finished(self):
        result = Rules().evaluate(
            {"entity_id": "sensor.washing_machine", "state": "running"},
            {
                "entity_id": "sensor.washing_machine",
                "state": "idle",
                "attributes": {"friendly_name": "Washing Machine"},
            },
            first_seen=100,
            now=101,
            presence={"aaron": "home", "amber": "home"},
        )
        self.assertEqual("cycle_finished", result[0].kind)

    def test_duplicate_is_suppressed(self):
        candidate = Candidate(
            "system", "test", "sensor.test", "Test", "Message", "Reason", 85
        )

        async def run():
            first = await self.engine.record(candidate)
            second = await self.engine.record(candidate)
            self.assertIsNotNone(first)
            self.assertIsNone(second)

        asyncio.run(run())

    def test_quiet_hours_cross_midnight(self):
        settings = self.engine.default_settings("aaron")
        london = ZoneInfo("Europe/London")
        self.assertTrue(self.engine.quiet(
            settings,
            datetime(2026, 7, 31, 3, 30, tzinfo=london),
        ))
        self.assertFalse(self.engine.quiet(
            settings,
            datetime(2026, 7, 31, 12, 0, tzinfo=london),
        ))

    def test_lock_control_is_blocked(self):
        candidate = Candidate(
            "security",
            "lock_test",
            "lock.front_door",
            "Lock",
            "Lock event",
            "Test",
            99,
            actions=("turn_off",),
        )

        async def run():
            event = await self.engine.record(candidate)
            with self.assertRaises(ValueError):
                await self.engine.action(
                    event["id"],
                    ActionModel(action="turn_off"),
                )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
