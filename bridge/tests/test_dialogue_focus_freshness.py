import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.dialogue_manager import DialogueManager


class DialogueFocusFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dialogue = DialogueManager(f"{self.temp.name}/dialogue.db")
        self.now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        self.dialogue._utc_now = lambda: self.now  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_person_focus_has_a_separate_freshness_boundary(self) -> None:
        await self.dialogue.record_result(
            "conversation",
            intent="state_query",
            success=True,
            response="Amber is home.",
            calls=[
                {
                    "tool": "inspect_presence",
                    "result": {
                        "person": {
                            "entity_id": "person.amber",
                            "name": "Amber",
                            "state": "home",
                        }
                    },
                }
            ],
        )

        self.assertIsNotNone(
            await self.dialogue.focused_person("conversation", max_age_seconds=300)
        )
        self.now += timedelta(seconds=301)
        self.assertIsNone(await self.dialogue.focused_person("conversation", max_age_seconds=300))


if __name__ == "__main__":
    unittest.main()
