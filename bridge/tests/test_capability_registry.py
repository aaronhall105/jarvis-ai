from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from app.ai_engine import AIEngine, RequestIntent, RoutingDecision
from app.capability_registry import (
    ActionReceiptStore,
    CapabilityRegistry,
    register_standard_capabilities,
)
from app.user_context import UserContext


class CapabilityRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.receipts = ActionReceiptStore(f"{self.temp.name}/actions.db")

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_runtime_snapshot_and_admin_apply_registration(self) -> None:
        async def healthy_home_assistant() -> dict[str, object]:
            return {"healthy": True, "message": "connected"}

        registry = CapabilityRegistry(self.receipts)
        register_standard_capabilities(
            registry,
            home_assistant_configured=True,
            model_configured=True,
            code_awareness_configured=False,
            home_assistant_health=healthy_home_assistant,
        )

        available, reason = registry.tool_available("apply_admin_change")
        self.assertTrue(available)
        self.assertIsNone(reason)
        self.assertEqual(
            "homeassistant.admin",
            registry.capability_for_tool("apply_admin_change").capability_id,
        )
        snapshot = await registry.snapshot()
        home = next(
            item for item in snapshot["providers"] if item["provider_id"] == "homeassistant"
        )
        self.assertTrue(home["available"])
        web = next(item for item in snapshot["providers"] if item["provider_id"] == "web")
        self.assertFalse(web["available"])

    async def test_receipts_redact_secrets_and_do_not_equate_acceptance_with_verification(
        self,
    ) -> None:
        started = await self.receipts.begin(
            capability_id="homeassistant.notify",
            provider="homeassistant",
            tool_name="send_mobile_notification",
            requested_action="send",
            target={"recipient": "amber", "access_token": "must-not-be-stored"},
            conversation_id="conversation",
        )
        completed = await self.receipts.complete(
            started["action_id"],
            {"success": True, "command_accepted": True},
        )

        self.assertEqual("[redacted]", completed["target"]["access_token"])
        self.assertEqual("completed", completed["status"])
        self.assertFalse(completed["verified"])

    async def test_terminal_receipt_is_idempotent_and_cannot_be_reversed(self) -> None:
        started = await self.receipts.begin(
            capability_id="homeassistant.control",
            provider="homeassistant",
            tool_name="control_device",
            requested_action="on",
        )
        verified = await self.receipts.complete(
            started["action_id"],
            {"success": True, "verified": True},
        )
        repeated = await self.receipts.complete(
            started["action_id"],
            {"success": False, "error": "late retry"},
            status="failed",
        )

        self.assertEqual("verified", verified["status"])
        self.assertTrue(verified["verified"])
        self.assertEqual(verified, repeated)


class ReceiptOrderingTests(unittest.IsolatedAsyncioTestCase):
    def actor(self) -> UserContext:
        return UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id=None,
            voice_mode=False,
        )

    async def test_deterministic_action_receipt_begins_before_side_effect(self) -> None:
        events: list[str] = []

        class Capabilities:
            def capability_for_tool(self, tool_name: str) -> SimpleNamespace:
                return SimpleNamespace(mode="write")

            def tool_available(self, tool_name: str) -> tuple[bool, None]:
                return True, None

            async def begin_tool_action(self, *args, **kwargs):
                events.append("begin")
                return {"action_id": "action-1", "status": "started"}

            async def finish_tool_action(self, receipt, result):
                events.append("finish")
                return {**receipt, "status": "verified", "verified": True}

        engine = AIEngine.__new__(AIEngine)
        engine.capabilities = Capabilities()

        async def operation() -> dict[str, object]:
            events.append("operation")
            return {"success": True, "verified": True}

        call = await engine._run_receipted_action(
            "control_device",
            {"entity_id": "light.test", "action": "on"},
            conversation_id="conversation",
            actor=self.actor(),
            operation=operation,
            failure_message="failed",
        )

        self.assertEqual(["begin", "operation", "finish"], events)
        self.assertEqual("verified", call["action_receipt"]["status"])
        self.assertEqual("action-1", call["result"]["action_id"])

    async def test_receipt_finalization_failure_does_not_raise_after_action(self) -> None:
        class Capabilities:
            def capability_for_tool(self, tool_name: str) -> SimpleNamespace:
                return SimpleNamespace(mode="write")

            def tool_available(self, tool_name: str) -> tuple[bool, None]:
                return True, None

            async def begin_tool_action(self, *args, **kwargs):
                return {"action_id": "action-2", "status": "started"}

            async def finish_tool_action(self, receipt, result):
                raise RuntimeError("receipt store unavailable")

        engine = AIEngine.__new__(AIEngine)
        engine.capabilities = Capabilities()
        with self.assertLogs("jarvis-core.ai", level="ERROR"):
            call = await engine._run_receipted_action(
                "control_device",
                {"entity_id": "light.test", "action": "on"},
                conversation_id="conversation",
                actor=self.actor(),
                operation=lambda: _successful_action(),
                failure_message="failed",
            )

        self.assertTrue(call["result"]["success"])
        self.assertFalse(call["action_receipt"]["completion_recorded"])

    async def test_completed_request_replay_returns_receipt_without_repeating_action(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            receipts = ActionReceiptStore(f"{temporary_directory}/actions.db")
            capabilities = CapabilityRegistry(receipts)
            register_standard_capabilities(
                capabilities,
                home_assistant_configured=True,
                model_configured=True,
                code_awareness_configured=False,
            )
            engine = AIEngine.__new__(AIEngine)
            engine.capabilities = capabilities
            execution_count = 0

            async def operation() -> dict[str, object]:
                nonlocal execution_count
                execution_count += 1
                return {"success": True, "verified": True, "state": "on"}

            arguments = {"entity_id": "light.test", "action": "on"}
            first = await engine._run_receipted_action(
                "control_device",
                arguments,
                conversation_id="conversation",
                actor=self.actor(),
                operation=operation,
                failure_message="failed",
                request_id="request-1",
            )
            replay = await engine._run_receipted_action(
                "control_device",
                arguments,
                conversation_id="conversation",
                actor=self.actor(),
                operation=operation,
                failure_message="failed",
                request_id="request-1",
            )

        self.assertEqual(1, execution_count)
        self.assertEqual(first["result"]["action_id"], replay["result"]["action_id"])
        self.assertTrue(replay["result"]["idempotent_replay"])
        self.assertTrue(replay["result"]["verified"])

    async def test_unfinished_request_replay_fails_closed_without_running_action(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            receipts = ActionReceiptStore(f"{temporary_directory}/actions.db")
            capabilities = CapabilityRegistry(receipts)
            register_standard_capabilities(
                capabilities,
                home_assistant_configured=True,
                model_configured=True,
                code_awareness_configured=False,
            )
            engine = AIEngine.__new__(AIEngine)
            engine.capabilities = capabilities
            arguments = {"entity_id": "light.test", "action": "on"}
            await engine._begin_action_receipt(
                "control_device",
                arguments,
                conversation_id="conversation",
                actor=self.actor(),
                request_id="request-2",
            )

            async def forbidden_operation() -> dict[str, object]:
                self.fail("an unfinished action must never be repeated")

            replay = await engine._run_receipted_action(
                "control_device",
                arguments,
                conversation_id="conversation",
                actor=self.actor(),
                operation=forbidden_operation,
                failure_message="failed",
                request_id="request-2",
            )

        self.assertFalse(replay["result"]["success"])
        self.assertEqual(
            "action_outcome_unknown",
            replay["result"]["error"]["code"],
        )
        self.assertTrue(replay["action_receipt"]["idempotent_replay"])

    async def test_capability_overview_fails_closed_when_provider_is_unhealthy(self) -> None:
        class Capabilities:
            async def snapshot(self):
                return {
                    "capabilities": [
                        {"capability_id": "homeassistant.read", "available": False}
                    ],
                    "providers": [
                        {
                            "provider_id": "homeassistant",
                            "available": False,
                            "reason": "connection failed",
                        }
                    ],
                }

        engine = AIEngine.__new__(AIEngine)
        engine.capabilities = Capabilities()
        reply = await engine._grounded_capability_reply(
            "What can you control at home?",
            RoutingDecision(
                intent=RequestIntent.CAPABILITY_OVERVIEW,
                deterministic_reply="I can control everything.",
            ),
        )

        self.assertIn("don’t currently have a healthy", reply)
        self.assertNotIn("control everything", reply)


async def _successful_action() -> dict[str, object]:
    return {"success": True, "verified": True}


if __name__ == "__main__":
    unittest.main()
