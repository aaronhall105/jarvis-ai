import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from app.vision_intelligence import VisionEngine


class VisionIntelligenceAlpha9Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = VisionEngine(
            str(Path(self.directory.name) / "vision.db"),
            enabled=False,
            duplicate_seconds=120,
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_normalises_frigate_mqtt_envelope(self):
        event = self.engine.normalise_event(
            {
                "type": "new",
                "after": {
                    "id": "event-1",
                    "camera": "front_door",
                    "label": "person",
                    "start_time": 1000.0,
                    "current_zones": ["doorstep"],
                    "has_snapshot": True,
                    "data": {"top_score": 0.91},
                },
            }
        )
        self.assertIsNotNone(event)
        self.assertEqual("front_door", event["camera"])
        self.assertEqual("Front Door", event["area"])
        self.assertEqual(
            "camera.front_door_clear",
            event["camera_entity"],
        )
        self.assertEqual(["doorstep"], event["zones"])
        self.assertAlmostEqual(0.91, event["score"])

    def test_person_away_is_critical(self):
        event = {
            "label": "person",
            "camera": "hallway",
            "zones": ["hall"],
            "score": 0.88,
        }
        self.assertEqual(
            100,
            self.engine.importance(event, away=True),
        )

    def test_duplicate_events_are_suppressed(self):
        first = {
            "id": "event-1",
            "camera": "front_door",
            "label": "person",
            "start_time": time.time(),
            "current_zones": ["doorstep"],
        }
        second = {
            "id": "event-2",
            "camera": "front_door",
            "label": "person",
            "start_time": time.time() + 1,
            "current_zones": ["doorstep"],
        }

        first_result = asyncio.run(
            self.engine.ingest(first, publish=False)
        )
        second_result = asyncio.run(
            self.engine.ingest(second, publish=False)
        )

        self.assertFalse(first_result["suppressed"])
        self.assertTrue(second_result["suppressed"])

    def test_camera_query_matching_and_mapping(self):
        self.assertTrue(
            self.engine.matches_query(
                "What happened at the front door?"
            )
        )
        self.assertEqual(
            "front_door",
            self.engine.camera_from_query(
                "Show the front door camera"
            ),
        )
        self.assertFalse(
            self.engine.matches_query(
                "Turn on the bedroom floodlight"
            )
        )

    def test_public_event_has_communication_links(self):
        result = asyncio.run(
            self.engine.ingest(
                {
                    "id": "event-3",
                    "camera": "bedroom",
                    "label": "person",
                    "start_time": time.time(),
                    "has_snapshot": True,
                },
                publish=False,
            )
        )
        public = self.engine.public_event(result)
        self.assertIn("/snapshot", public["snapshot_path"])
        self.assertIn(
            "camera.bedroom_clear",
            public["home_assistant_path"],
        )


if __name__ == "__main__":
    unittest.main()
