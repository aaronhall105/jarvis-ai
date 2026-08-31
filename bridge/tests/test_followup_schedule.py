import unittest
from datetime import datetime, timezone

from app.followup_schedule import next_recurrence, resolve_recurrence, resolve_schedule


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
        self.assertEqual(
            datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc),
            self.due("remind me today at 6pm"),
        )
        self.assertIsNone(
            resolve_schedule(
                "remind me today at 10am",
                timezone_name="Europe/London",
                now_utc=self.NOW,
            )
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

    def test_relative_duration_and_reminder_content(self):
        result = resolve_schedule(
            "Remind me in 45 minutes to call Mum",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(self.NOW.replace(minute=45), result.due_utc)
        self.assertEqual("Reminder: call mum.", result.reminder_text)

    def test_tonight_disambiguates_an_unqualified_clock_hour(self):
        result = resolve_schedule(
            "Remind me at 6 tonight to call Mum",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc), result.due_utc)

    def test_supported_recurring_schedules_are_structured(self):
        weekday = resolve_recurrence(
            "Remind me every weekday at 7am to take my lunch",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(weekday)
        self.assertEqual(weekday[0].weekdays, (0, 1, 2, 3, 4))
        self.assertEqual(weekday[0].hour, 7)
        self.assertEqual(weekday[2], "Recurring reminder: take my lunch.")

        sunday = resolve_recurrence(
            "Every Sunday evening remind me to put the bins out",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(sunday)
        self.assertEqual(sunday[0].weekdays, (6,))
        self.assertEqual(sunday[0].hour, 19)

        monthly = resolve_recurrence(
            "Every month on the 1st at 9am remind me to pay the rent",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(monthly)
        self.assertEqual(monthly[0].day_of_month, 1)

        interval = resolve_recurrence(
            "Every 2 hours remind me to stretch",
            timezone_name="Europe/London",
            now_utc=self.NOW,
        )
        self.assertIsNotNone(interval)
        self.assertEqual(interval[0].interval_seconds, 7200)

    def test_recurrence_dst_gap_skips_nonexistent_occurrence(self):
        next_run = next_recurrence(
            {
                "kind": "weekly",
                "timezone": "Europe/London",
                "hour": 1,
                "minute": 30,
                "weekdays": [6],
            },
            after_utc=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(datetime(2026, 4, 5, 0, 30, tzinfo=timezone.utc), next_run)

    def test_invalid_time_or_timezone_is_rejected(self):
        self.assertIsNone(
            resolve_schedule("remind me at 28:75", timezone_name="Europe/London", now_utc=self.NOW)
        )
        self.assertIsNone(
            resolve_schedule("remind me tomorrow", timezone_name="Not/AZone", now_utc=self.NOW)
        )
        self.assertIsNone(resolve_schedule("remind me " + "a" * 10_000, now_utc=self.NOW))

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
