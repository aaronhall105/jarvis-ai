import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from app.proactive_intelligence import Candidate, ProactiveEngine, SettingsModel


class ContextualInitiativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = ProactiveEngine(
            str(Path(self.temp.name) / "proactive.db"),
            enabled=True,
            cooldown=30,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_low_confidence_noncritical_event_is_not_spoken(self) -> None:
        candidate = Candidate(
            "cameras",
            "object",
            "camera.hallway",
            "Object detected",
            "Something may be in the hallway.",
            "single uncertain detection",
            82,
            confidence=0.42,
            evidence=("detector:0.42",),
            room="Hallway",
        )
        event = asyncio.run(self.engine.record(candidate))
        self.assertTrue(event["decision"]["suppress_speech"])
        self.assertEqual(
            "confidence_below_announcement_threshold",
            event["decision"]["suppressed_reason"],
        )
        self.assertEqual(["detector:0.42"], event["evidence"])

    def test_critical_event_bypasses_confidence_suppression(self) -> None:
        event = asyncio.run(
            self.engine.record(
                Candidate(
                    "security",
                    "smoke_detected",
                    "binary_sensor.smoke",
                    "Smoke",
                    "Smoke detected.",
                    "verified safety sensor",
                    100,
                    confidence=0.4,
                )
            )
        )
        self.assertFalse(event["decision"]["suppress_speech"])
        self.assertTrue(event["decision"]["critical"])

    def test_room_routes_to_room_speaker_with_single_device_fallback(self) -> None:
        self.engine.speaker_entity = "assist_satellite.only_preview"
        self.engine.speaker_map = {"living_room": "assist_satellite.living_room"}
        living = asyncio.run(
            self.engine.record(
                Candidate(
                    "presence",
                    "arrival",
                    "person.aaron",
                    "Arrival",
                    "Aaron is home.",
                    "presence changed",
                    82,
                    room="Living Room",
                )
            )
        )
        self.assertEqual("assist_satellite.living_room", living["decision"]["speaker"])

    def test_spoken_initiative_uses_native_start_conversation(self) -> None:
        self.engine.speaker_entity = "assist_satellite.living_room"
        self.engine.quiet = lambda settings, now=None: False
        self.engine.save_settings(SettingsModel(user_id="aaron", speak_enabled=True))
        calls = []

        async def fake_service(service_domain, service, payload):
            calls.append((service_domain, service, payload))
            return {}

        self.engine.ha_service = fake_service
        event = asyncio.run(
            self.engine.record(
                Candidate(
                    "appliances",
                    "cycle_finished",
                    "sensor.washer",
                    "Washer",
                    "The washing machine has finished.",
                    "verified state transition",
                    85,
                )
            )
        )
        conversation_call = next(call for call in calls if call[1] == "start_conversation")
        self.assertEqual("assist_satellite.living_room", conversation_call[2]["entity_id"])
        self.assertEqual(
            "The washing machine has finished.",
            conversation_call[2]["start_message"],
        )
        self.assertGreater(event["reply_until"], int(time.time()))

    def test_reply_window_is_deterministic_and_feedback_suppresses_future_speech(self) -> None:
        candidate = Candidate(
            "cameras",
            "package",
            "camera.front",
            "Package",
            "A package arrived.",
            "camera track",
            85,
            confidence=0.95,
            room="Front Door",
        )
        event = asyncio.run(self.engine.record(candidate))
        self.engine.update(event["id"], reply_until=int(time.time()) + 12)
        reply = asyncio.run(self.engine.handle_reply("don't announce that again", "aaron"))
        self.assertTrue(reply["handled"])
        with self.engine.connection() as connection:
            connection.execute(
                "UPDATE proactive_events SET created_at=? WHERE id=?",
                (int(time.time()) - 31, event["id"]),
            )
        later = asyncio.run(self.engine.record(candidate))
        self.assertEqual("disabled_by_user_feedback", later["decision"]["suppressed_reason"])

    def test_learning_creates_proposal_but_never_auto_approves(self) -> None:
        self.engine.learning_threshold = 3
        candidate = Candidate(
            "appliances",
            "cycle_finished",
            "sensor.washer",
            "Washer finished",
            "The washer finished.",
            "state transition",
            82,
        )
        for _ in range(3):
            event = asyncio.run(self.engine.record(candidate))
            with self.engine.connection() as connection:
                connection.execute(
                    "UPDATE proactive_events SET created_at=? WHERE id=?",
                    (int(time.time()) - 31, event["id"]),
                )
        proposals = self.engine.proposals()
        self.assertEqual(1, len(proposals))
        self.assertEqual("proposed", proposals[0]["status"])
        approved = self.engine.proposal_action(proposals[0]["id"], "approve")
        self.assertEqual("approved", approved["status"])

    def test_existing_database_is_migrated_without_losing_events(self) -> None:
        path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE proactive_events (id TEXT PRIMARY KEY, fingerprint TEXT,"
            " category TEXT, kind TEXT, entity_id TEXT, title TEXT, message TEXT,"
            " reason TEXT, importance INTEGER, target_user TEXT, actions_json TEXT,"
            " status TEXT, created_at INTEGER, updated_at INTEGER, notified_at INTEGER,"
            " spoken_at INTEGER, snoozed_until INTEGER);"
        )
        connection.execute(
            "INSERT INTO proactive_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old",
                "fp",
                "system",
                "old",
                "sensor.old",
                "Old",
                "Old event",
                "legacy",
                80,
                "all",
                json.dumps(["dismiss"]),
                "active",
                1,
                1,
                None,
                None,
                None,
            ),
        )
        connection.commit()
        connection.close()
        migrated = ProactiveEngine(str(path), enabled=False)
        self.assertEqual("Old event", migrated.get_event("old")["message"])
        self.assertEqual(1.0, migrated.get_event("old")["confidence"])

    def test_legacy_orchestrator_suppression_schema_can_coexist(self) -> None:
        path = Path(self.temp.name) / "orchestrator.db"
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE proactive_suppressions (
                 scope_key TEXT PRIMARY KEY, until_at TEXT, reason TEXT,
                 actor TEXT, created_at TEXT NOT NULL)"""
        )
        connection.execute(
            "INSERT INTO proactive_suppressions VALUES(?,?,?,?,?)",
            ("old:scope", None, "legacy", "aaron", "2026-08-21T00:00:00Z"),
        )
        connection.commit()
        connection.close()

        engine = ProactiveEngine(str(path), enabled=False)
        event = asyncio.run(
            engine.record(
                Candidate(
                    "system",
                    "health",
                    "sensor.health",
                    "Health",
                    "System healthy.",
                    "verified",
                    85,
                )
            )
        )
        self.assertIsNotNone(event)
        with engine.connection() as connection:
            self.assertEqual(
                1, connection.execute("SELECT COUNT(*) FROM proactive_suppressions").fetchone()[0]
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(initiative_suppressions)")
            }
        self.assertIn("fingerprint", columns)


if __name__ == "__main__":
    unittest.main()
