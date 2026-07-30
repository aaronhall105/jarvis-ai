import unittest

from app.realtime_voice import sanitise_tool_events


class RealtimeVoiceAlpha5Tests(unittest.TestCase):
    def test_sanitises_tool_result_without_arguments(self) -> None:
        events = sanitise_tool_events([
            {
                "tool": "control_device",
                "arguments": {
                    "entity_id": "light.private_bedroom",
                    "token": "must-not-leak",
                },
                "result": {
                    "success": True,
                    "response_message": "Bedroom light is now off.",
                },
            }
        ])

        self.assertEqual(1, len(events))
        self.assertEqual("control_device", events[0]["tool"])
        self.assertTrue(events[0]["success"])
        self.assertEqual(
            "Bedroom light is now off.",
            events[0]["message"],
        )
        self.assertNotIn("arguments", events[0])
        self.assertNotIn("token", str(events[0]))

    def test_handles_verified_and_failed_results(self) -> None:
        events = sanitise_tool_events([
            {
                "tool": "control_area_lights",
                "result": {
                    "verified": True,
                    "response_message": "Living room lights are off.",
                },
            },
            {
                "tool": "run_home_routine",
                "result": {
                    "success": False,
                    "error": "Routine was unavailable.",
                },
            },
        ])

        self.assertTrue(events[0]["success"])
        self.assertFalse(events[1]["success"])
        self.assertEqual(
            "Routine was unavailable.",
            events[1]["message"],
        )


if __name__ == "__main__":
    unittest.main()
