import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import main
from app.capability_registry import (
    ActionReceiptStore,
    CapabilityRegistry,
    register_standard_capabilities,
)
from app.conversation_engine import ConversationEngine


class Followups:
    def __init__(self):
        self.cancelled = []
        self.conversations = None
        self.conversation_existed_when_cancelled = None

    async def cancel_for_conversation(self, conversation_id):
        self.cancelled.append(conversation_id)
        if self.conversations is not None:
            self.conversation_existed_when_cancelled = bool(
                await self.conversations.get_conversation(conversation_id)
            )
        return 2


class Dialogue:
    def __init__(self):
        self.deleted = []

    async def delete(self, conversation_id):
        self.deleted.append(conversation_id)
        return True


class ConversationDeliveryAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conversations = ConversationEngine(self.tmp.name + "/conversations.db")
        self.followups = Followups()
        self.followups.conversations = self.conversations
        self.dialogue = Dialogue()
        self.patchers = [
            patch.object(main, "conversations", self.conversations),
            patch.object(main, "followups", self.followups),
            patch.object(main, "dialogue", self.dialogue),
        ]
        for patcher in self.patchers:
            patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    async def test_external_id_gets_identity_scoped_and_returned_as_external(self):
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
            limit=100,
            user_id="ha-user-2",
            user_name="Amber",
        )

        self.assertEqual("shared-client-id", result["conversation"]["conversation_id"])
        self.assertEqual("shared-client-id", result["messages"][0]["conversation_id"])
        self.assertEqual("Asynchronous result", result["messages"][0]["content"])

    async def test_delete_scopes_id_and_cancels_followups_before_removal(self):
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

        self.assertEqual(
            {
                "success": True,
                "conversation_id": "delete-me",
                "cancelled_followups": 2,
            },
            result,
        )
        self.assertEqual([storage_id], self.followups.cancelled)
        self.assertTrue(self.followups.conversation_existed_when_cancelled)
        self.assertEqual([storage_id], self.dialogue.deleted)
        self.assertIsNone(await self.conversations.get_conversation(storage_id))

    def test_already_scoped_ids_remain_compatible(self):
        self.assertEqual(
            ("web-chat", "usr:aaron:web-chat"),
            main._conversation_scope_for_identity(
                "web-chat",
                user_id=None,
                user_name=None,
            ),
        )

    def test_explicit_and_voice_turn_request_ids_are_stable(self):
        explicit = main.TextCommandRequest(
            text="Turn on the light",
            request_id="client-request-1",
        )
        voice = main.TextCommandRequest(
            text="Turn on the light",
            voice_session_id="mobile-session",
            voice_session_turn=17,
        )

        self.assertEqual("client-request-1", main._action_request_id(explicit))
        self.assertEqual("voice:mobile-session:17", main._action_request_id(voice))

    async def test_realtime_turn_id_reaches_the_request_id_boundary(self):
        execute = AsyncMock(return_value={"success": True, "response": "Done."})

        async def on_delta(_text):
            return None

        with patch.object(main, "_execute_ai_request", execute):
            await main._realtime_brain_handler(
                "Turn on the light",
                {
                    "session_id": "mobile-session",
                    "client_turn_id": 23,
                    "conversation_id": "conversation",
                },
                on_delta,
            )

        request = execute.await_args.args[0]
        self.assertEqual(23, request.voice_session_turn)
        self.assertEqual("voice:mobile-session:23", main._action_request_id(request))

    def test_followup_retry_key_uses_stable_request_id(self):
        first = main._followup_idempotency_key(
            "conversation",
            "Remind me in 5 minutes",
            "time",
            "request-1",
        )
        repeated = main._followup_idempotency_key(
            "conversation",
            "  remind me   in 5 MINUTES ",
            "time",
            "request-1",
        )
        different_request = main._followup_idempotency_key(
            "conversation",
            "Remind me in 5 minutes",
            "time",
            "request-2",
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, different_request)

    async def test_legacy_http_action_boundary_is_receipted_and_idempotent(self):
        receipts = ActionReceiptStore(self.tmp.name + "/legacy-actions.db")
        capabilities = CapabilityRegistry(receipts)
        register_standard_capabilities(
            capabilities,
            home_assistant_configured=True,
            model_configured=True,
            code_awareness_configured=False,
        )
        actor = main._api_action_actor(None, None)
        execution_count = 0

        async def operation():
            nonlocal execution_count
            execution_count += 1
            return {"success": True, "verified": True, "state": "on"}

        with patch.object(main.ai, "capabilities", capabilities):
            first = await main._run_api_tool_action(
                "control_device",
                {"entity_id": "light.test", "action": "on"},
                actor=actor,
                conversation_id=None,
                request_id="legacy-request-1",
                operation=operation,
            )
            replay = await main._run_api_tool_action(
                "control_device",
                {"entity_id": "light.test", "action": "on"},
                actor=actor,
                conversation_id=None,
                request_id="legacy-request-1",
                operation=operation,
            )

        self.assertEqual(1, execution_count)
        self.assertEqual(first["action_id"], replay["action_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual("verified", replay["action_receipt"]["status"])
        self.assertEqual(
            ("usr:amber:legacy", "usr:amber:legacy"),
            main._conversation_scope_for_identity(
                "usr:amber:legacy",
                user_id="ha-user-2",
                user_name="Amber",
            ),
        )

    def test_foreign_scoped_id_is_rejected(self):
        with self.assertRaises(main.HTTPException) as captured:
            main._conversation_scope_for_identity(
                "usr:aaron:private-chat",
                user_id="ha-user-2",
                user_name="Amber",
            )

        self.assertEqual(404, captured.exception.status_code)

    async def test_conversation_list_is_owner_filtered_and_externalized(self):
        await self.conversations.ensure_conversation(
            conversation_id="usr:aaron:aaron-chat",
            source="home_assistant:aaron",
        )
        await self.conversations.ensure_conversation(
            conversation_id="usr:amber:amber-chat",
            source="home_assistant:amber",
        )

        aaron = await main.list_conversations()
        amber = await main.list_conversations(
            user_id="ha-user-2",
            user_name="Amber",
        )

        self.assertEqual(
            ["aaron-chat"],
            [item["conversation_id"] for item in aaron["conversations"]],
        )
        self.assertEqual(
            ["amber-chat"],
            [item["conversation_id"] for item in amber["conversations"]],
        )


if __name__ == "__main__":
    unittest.main()
