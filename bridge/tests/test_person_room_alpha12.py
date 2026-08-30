import unittest

from app.ai_engine import (
    RequestIntent,
    RequestRouter,
)
from app.person_room_context import (
    recent_person_rooms,
    resolve_person_room,
    room_followup_person,
)


HISTORY = [
    {
        "role": "user",
        "content": "Where is Amber",
    },
    {
        "role": "assistant",
        "content": "Amber is at home.",
    },
]


def person_states(
    aaron: str,
    amber: str,
):
    return [
        {
            "domain": "person",
            "entity_id": "person.aaron",
            "name": "Aaron",
            "state": aaron,
        },
        {
            "domain": "person",
            "entity_id": "person.amber",
            "name": "Amber",
            "state": amber,
        },
    ]


class Alpha12PersonRoomTests(unittest.TestCase):
    def test_room_follow_up_resolves_amber(self):
        self.assertEqual(
            "amber",
            room_followup_person(
                "In what room?",
                HISTORY,
            ),
        )

    def test_router_keeps_room_follow_up_read_only(self):
        decision = RequestRouter.classify(
            "In what room?",
            HISTORY,
        )
        self.assertEqual(
            RequestIntent.STATE_QUERY,
            decision.intent,
        )
        self.assertTrue(decision.allow_home_read)
        self.assertFalse(decision.allow_home_control)

    def test_only_amber_home_allows_cautious_inference(self):
        result = resolve_person_room(
            "amber",
            person_states("not_home", "home"),
            {
                "source": "live_snapshots",
                "rooms": ["Living Room"],
                "primary_event": None,
            },
        )
        self.assertTrue(result["handled"])
        self.assertIn(
            "Amber appears to be in the Living Room",
            result["response"],
        )
        self.assertIn(
            "live camera check",
            result["response"],
        )

    def test_two_people_home_preserves_identity_uncertainty(self):
        result = resolve_person_room(
            "amber",
            person_states("home", "home"),
            {
                "source": "live_snapshots",
                "rooms": ["Living Room"],
                "primary_event": None,
            },
        )
        self.assertIn(
            "can't safely tell",
            result["response"],
        )
        self.assertNotIn(
            "Amber appears",
            result["response"],
        )

    def test_no_camera_match_is_not_generic_failure(self):
        result = resolve_person_room(
            "amber",
            person_states("not_home", "home"),
            {
                "source": "live_snapshots",
                "rooms": [],
                "primary_event": None,
            },
        )
        self.assertIn(
            "cameras haven't provided",
            result["response"],
        )

    def test_recent_events_exclude_front_door(self):
        result = recent_person_rooms(
            [
                {
                    "label": "person",
                    "area": "Front Door",
                    "start_time": 995,
                },
                {
                    "label": "person",
                    "area": "Hallway",
                    "start_time": 990,
                },
            ],
            now=1000,
        )
        self.assertEqual(
            ["Hallway"],
            result["rooms"],
        )


if __name__ == "__main__":
    unittest.main()
