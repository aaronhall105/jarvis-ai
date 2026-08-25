import unittest
from datetime import datetime, timezone

from app.followup_schedule import resolve_schedule


class FollowupScheduleTests(unittest.TestCase):
    NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)  # Tuesday, 13:00 London

    def due(self, text):
        result = resolve_schedule(text, timezone_name="Europe/London", now_utc=self.NOW)
        self.assertIsNotNone(result)
        return result.due_utc

    def test_tomorrow_dayparts_and_clock(self):
        self.assertEqual(
            datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc), self.due("tell me tomorrow morning")
        )
        self.assertEqual(
            datetime(2026, 8, 26, 19, 0, tzinfo=timezone.utc), self.due("remind me tomorrow at 8pm")
        )

    def test_weekday_and_next_weekday(self):
        self.assertEqual(
            datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc), self.due("tell me Friday morning")
        )
        self.assertEqual(
            datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc),
            self.due("check next Monday afternoon"),
        )

    def test_absolute_date_and_time(self):
        self.assertEqual(
            datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc),
            self.due("remind me on 27 August at 8pm"),
        )

    def test_invalid_time_or_timezone_is_rejected(self):
        self.assertIsNone(
            resolve_schedule("remind me at 28:75", timezone_name="Europe/London", now_utc=self.NOW)
        )
        self.assertIsNone(
            resolve_schedule("remind me tomorrow", timezone_name="Not/AZone", now_utc=self.NOW)
        )

    def test_dst_gap_rejected_and_fall_back_is_deterministic(self):
        spring = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(
            resolve_schedule(
                "remind me on 29 March 2026 at 1:30", timezone_name="Europe/London", now_utc=spring
            )
        )
        autumn = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        result = resolve_schedule(
            "remind me on 25 October 2026 at 1:30", timezone_name="Europe/London", now_utc=autumn
        )
        self.assertIsNotNone(result)
        self.assertEqual(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), result.due_utc)
