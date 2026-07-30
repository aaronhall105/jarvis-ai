import unittest
from datetime import datetime

from app.realtime_voice import (
    RealtimeVoiceConfig,
    normalise_timezone,
    trusted_local_context,
)


class RealtimeVoiceAlpha5_1Tests(unittest.TestCase):
    def test_london_timezone_is_valid(self) -> None:
        self.assertEqual(
            "Europe/London",
            normalise_timezone("Europe/London"),
        )

    def test_invalid_timezone_falls_back(self) -> None:
        self.assertEqual(
            "Europe/London",
            normalise_timezone("Not/AZone", "Europe/London"),
        )

    def test_trusted_context_is_offset_aware(self) -> None:
        context = trusted_local_context("Europe/London")
        parsed = datetime.fromisoformat(context["local_datetime"])
        self.assertIsNotNone(parsed.utcoffset())
        self.assertEqual("Europe/London", context["timezone"])
        self.assertIn("local_date", context)
        self.assertIn("local_time", context)

    def test_environment_defaults_to_london(self) -> None:
        config = RealtimeVoiceConfig.from_environment()
        self.assertEqual("Europe/London", config.timezone)


if __name__ == "__main__":
    unittest.main()
