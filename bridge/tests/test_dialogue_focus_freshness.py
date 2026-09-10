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

    async def test_verified_gmail_reply_result_persists_exact_read_focus(self) -> None:
        await self.dialogue.record_result(
            "conversation",
            intent="general",
            success=True,
            response="Yeah, Amber replied.",
            calls=[
                {
                    "tool": "check_recent_gmail_reply",
                    "result": {
                        "success": True,
                        "live_evidence_available": True,
                        "reply_received": True,
                        "recipient": "amber.gill1992@outlook.com",
                        "recipient_name": "Amber Gill",
                        "sent_message_id": "sent-1",
                        "thread_id": "thread-1",
                        "send_receipt_action_id": "action-1",
                        "anchor_source": "principal_durable_receipt",
                    },
                }
            ],
        )

        restarted = DialogueManager(f"{self.temp.name}/dialogue.db")
        state = await restarted.get("conversation")

        self.assertEqual(
            {
                "recipient": "amber.gill1992@outlook.com",
                "recipient_name": "Amber Gill",
                "sent_message_id": "sent-1",
                "thread_id": "thread-1",
                "send_receipt_action_id": "action-1",
                "anchor_source": "principal_durable_receipt",
                "observed_at": "2026-08-26T12:00:00+00:00",
            },
            state.focus["gmail_reply"],
        )

    async def test_verified_named_gmail_action_persists_exact_recipient_focus(self) -> None:
        await self.dialogue.record_result(
            "conversation",
            intent="general",
            success=True,
            response="Done — I sent that email to Amber.",
            calls=[
                {
                    "tool": "prepare_gmail_message",
                    "result": {
                        "success": True,
                        "status": "verified",
                        "operation": "send",
                        "recipient": "amber.gill1992@outlook.com",
                        "recipient_name": "Amber",
                        "recipient_source": "google_contacts",
                    },
                }
            ],
        )

        restarted = DialogueManager(f"{self.temp.name}/dialogue.db")
        state = await restarted.get("conversation")

        self.assertEqual(
            {
                "recipient": "amber.gill1992@outlook.com",
                "recipient_name": "Amber",
                "source": "google_contacts",
                "operation": "send",
                "observed_at": "2026-08-26T12:00:00+00:00",
            },
            state.focus["gmail_recipient"],
        )


if __name__ == "__main__":
    unittest.main()
