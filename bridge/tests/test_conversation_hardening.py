from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.conversation_engine import ConversationEngine
from app.dialogue_manager import DialogueManager


class ConversationHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.conversations = ConversationEngine(f"{self.temp.name}/conversations.db")
        await self.conversations.create_conversation(conversation_id="conversation")

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_background_delivery_key_is_exactly_once(self) -> None:
        first = await self.conversations.add_assistant_message_once(
            "conversation",
            "The monitored state changed.",
            "followup:job-1",
        )
        second = await self.conversations.add_assistant_message_once(
            "conversation",
            "A retry must not replace the original delivery.",
            "followup:job-1",
        )

        self.assertEqual(first["message_id"], second["message_id"])
        messages = await self.conversations.get_messages("conversation")
        self.assertEqual(1, len(messages))
        self.assertEqual("The monitored state changed.", messages[0]["content"])

    async def test_empty_background_delivery_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.conversations.add_assistant_message_once(
                "conversation",
                "message",
                "   ",
            )


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

        focused = await self.dialogue.focused_person(
            "conversation",
            max_age_seconds=300,
        )
        self.assertEqual("Amber", focused["name"] if focused else None)

        self.now += timedelta(seconds=301)
        self.assertIsNone(
            await self.dialogue.focused_person(
                "conversation",
                max_age_seconds=300,
            )
        )


if __name__ == "__main__":
    unittest.main()
