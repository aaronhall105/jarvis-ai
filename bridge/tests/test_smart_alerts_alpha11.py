import unittest

from app.proactive_intelligence import Rules
from app.proactive_policy import (
    proactive_notification_tag,
    proactive_speech_allowed,
)


PRESENCE = {
    "aaron": "home",
    "amber": "home",
}


class Alpha11SmartAlertTests(unittest.TestCase):
    def evaluate(self, previous, current, age=4_000):
        return Rules(
            oven_seconds=1_800,
        ).evaluate(
            previous,
            current,
            first_seen=100,
            now=100 + age,
            presence=PRESENCE,
        )

    def test_battery_cycle_count_is_ignored(self):
        events = self.evaluate(
            {
                "entity_id": "sensor.phone_battery_cycle_count",
                "state": "1",
                "attributes": {
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            },
            {
                "entity_id": "sensor.phone_battery_cycle_count",
                "state": "0",
                "attributes": {
                    "friendly_name": ("Aaron's Phone Battery cycle count"),
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            },
        )
        self.assertFalse(any(event.category == "batteries" for event in events))

    def test_battery_power_is_ignored(self):
        events = self.evaluate(
            {
                "entity_id": "sensor.phone_battery_power",
                "state": "2",
                "attributes": {
                    "unit_of_measurement": "W",
                },
            },
            {
                "entity_id": "sensor.phone_battery_power",
                "state": "0",
                "attributes": {
                    "friendly_name": ("Aaron's Phone Battery power"),
                    "unit_of_measurement": "W",
                },
            },
        )
        self.assertFalse(any(event.category == "batteries" for event in events))

    def test_real_battery_alerts_only_on_threshold_crossing(self):
        previous = {
            "entity_id": "sensor.aaron_phone_battery_level",
            "state": "19",
            "attributes": {
                "friendly_name": "Aaron's Phone Battery level",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        }
        current = {
            **previous,
            "state": "14",
        }

        events = self.evaluate(previous, current)
        batteries = [event for event in events if event.category == "batteries"]
        self.assertEqual(1, len(batteries))
        self.assertEqual("battery_low", batteries[0].kind)

        repeated = self.evaluate(current, current)
        self.assertFalse(any(event.category == "batteries" for event in repeated))

    def test_critical_battery_can_follow_low_battery(self):
        previous = {
            "entity_id": "sensor.aaron_phone_battery_level",
            "state": "9",
            "attributes": {
                "friendly_name": "Aaron's Phone Battery level",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        }
        current = {
            **previous,
            "state": "4",
        }

        events = self.evaluate(previous, current)
        self.assertEqual("battery_critical", events[0].kind)
        self.assertGreaterEqual(events[0].importance, 95)

    def test_oven_helpers_are_ignored(self):
        names = (
            "Oven Alert Reset",
            "Oven Preheat Alert",
        )
        for index, name in enumerate(names):
            with self.subTest(name=name):
                events = self.evaluate(
                    {
                        "entity_id": f"input_boolean.oven_{index}",
                        "state": "off",
                    },
                    {
                        "entity_id": f"input_boolean.oven_{index}",
                        "state": "on",
                        "attributes": {
                            "friendly_name": name,
                        },
                    },
                )
                self.assertFalse(any(event.kind == "oven_left_on" for event in events))

    def test_physical_oven_still_alerts(self):
        events = self.evaluate(
            {
                "entity_id": "switch.kitchen_oven",
                "state": "on",
            },
            {
                "entity_id": "switch.kitchen_oven",
                "state": "on",
                "attributes": {
                    "friendly_name": "Kitchen Oven",
                },
            },
        )
        self.assertTrue(any(event.kind == "oven_left_on" for event in events))

    def test_battery_never_speaks(self):
        event = {
            "kind": "battery_critical",
            "category": "batteries",
            "importance": 96,
        }
        self.assertFalse(
            proactive_speech_allowed(
                event,
                quiet=False,
            )
        )

    def test_safety_speaks_even_in_quiet_hours(self):
        event = {
            "kind": "smoke_detected",
            "category": "security",
            "importance": 100,
        }
        self.assertTrue(
            proactive_speech_allowed(
                event,
                quiet=True,
            )
        )

    def test_battery_notifications_replace_each_other(self):
        first = proactive_notification_tag(
            {
                "category": "batteries",
                "kind": "battery_low",
                "target_user": "aaron",
                "entity_id": "sensor.phone_battery",
            }
        )
        second = proactive_notification_tag(
            {
                "category": "batteries",
                "kind": "battery_critical",
                "target_user": "aaron",
                "entity_id": "sensor.watch_battery",
            }
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
