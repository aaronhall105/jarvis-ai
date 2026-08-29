import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("JARVIS_DATA_DIR", "/tmp/jarvis-test-data")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app import main
from app.conversation_engine import ConversationEngine


class Followups:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_for_conversation(self, conversation_id: str) -> int:
        self.cancelled.append(conversation_id)
        return 2


class Dialogue:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, conversation_id: str) -> bool:
        self.deleted.append(conversation_id)
        return True


class ConversationAPIScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.conversations = ConversationEngine(f"{self.temp.name}/conversations.db")
        self.followups = Followups()
        self.dialogue = Dialogue()
        self.patchers = [
            patch.object(main, "conversations", self.conversations),
            patch.object(main, "followups", self.followups),
            patch.object(main, "dialogue", self.dialogue),
        ]
        for patcher in self.patchers:
            patcher.start()

    async def asyncTearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    async def test_get_returns_public_id_from_identity_scoped_storage(self) -> None:
        await self.conversations.ensure_conversation(
            conversation_id="usr:amber:shared-client-id",
            source="home_assistant:amber",
        )
        await self.conversations.add_assistant_message(
            "usr:amber:shared-client-id",
            "Asynchronous result",
        )

        result = await main.get_conversation(
            "shared-client-id",
            user_id="ha-user-2",
            user_name="Amber",
        )

        self.assertEqual("shared-client-id", result["conversation"]["conversation_id"])
        self.assertEqual("shared-client-id", result["messages"][0]["conversation_id"])

    async def test_delete_cancels_scoped_followups_before_removing_current_chat(self) -> None:
        storage_id = "usr:amber:delete-me"
        await self.conversations.ensure_conversation(
            conversation_id=storage_id,
            source="home_assistant:amber",
        )

        result = await main.delete_conversation(
            "delete-me",
            user_id="ha-user-2",
            user_name="Amber",
        )

        self.assertEqual(2, result["cancelled_followups"])
        self.assertEqual([storage_id], self.followups.cancelled)
        self.assertEqual([storage_id], self.dialogue.deleted)
        self.assertIsNone(await self.conversations.get_conversation(storage_id))

    async def test_list_never_returns_another_principals_conversation(self) -> None:
        await self.conversations.ensure_conversation("usr:aaron:one", source="test")
        await self.conversations.ensure_conversation("usr:amber:two", source="test")

        result = await main.list_conversations(user_name="Amber")

        self.assertEqual(["two"], [item["conversation_id"] for item in result["conversations"]])


if __name__ == "__main__":
    unittest.main()
