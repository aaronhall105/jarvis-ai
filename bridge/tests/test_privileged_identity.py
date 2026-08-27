import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ai_engine import AIEngine
from app.user_context import UserContext, scope_conversation_id


class PrivilegedIdentityTests(unittest.TestCase):
    def test_conversation_scope_cannot_target_another_user(self) -> None:
        external, storage = scope_conversation_id("usr:amber:shared", "aaron")

        self.assertEqual(external, "usr:amber:shared")
        self.assertEqual(storage, "usr:aaron:usr:amber:shared")
        self.assertEqual(
            scope_conversation_id("usr:aaron:shared", "aaron"),
            ("usr:aaron:shared", "usr:aaron:shared"),
        )

    def test_claimed_admin_is_not_privileged(self) -> None:
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id="test",
            voice_mode=False,
            privilege_verified=False,
        )

        self.assertTrue(actor.is_admin)
        self.assertFalse(actor.can_admin)


class PrivilegedPlannerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _actor(*, verified: bool) -> UserContext:
        return UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id="test",
            voice_mode=False,
            privilege_verified=verified,
        )

    async def test_unverified_actor_cannot_plan_home_assistant_admin_capability(self) -> None:
        runtime = SimpleNamespace(execute_model_tool=AsyncMock())
        engine = object.__new__(AIEngine)
        engine.external_runtime = runtime
        arguments = {
            "goal": "Read and change Home Assistant configuration",
            "steps": [
                {
                    "step_id": "read",
                    "capability_id": "homeassistant.admin.read",
                },
                {
                    "step_id": "change",
                    "capability_id": "homeassistant.admin.propose",
                },
            ],
        }

        result = await engine._execute_function(
            "create_personal_plan",
            json.dumps(arguments),
            "Read and change the configuration",
            {"create_personal_plan"},
            "usr:aaron:test",
            self._actor(verified=False),
            "request-1",
        )

        self.assertFalse(result["result"]["success"])
        self.assertEqual(
            "admin_permission_required",
            result["result"]["error"]["code"],
        )
        runtime.execute_model_tool.assert_not_awaited()

    def test_verified_aaron_admin_is_privileged(
        self,
    ) -> None:
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            device_id="test",
            voice_mode=False,
            privilege_verified=True,
        )

        self.assertTrue(actor.can_admin)

    def test_verified_non_admin_is_not_privileged(
        self,
    ) -> None:
        actor = UserContext.from_request(
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=False,
            device_id="test",
            voice_mode=False,
            privilege_verified=True,
        )

        self.assertFalse(actor.can_admin)

    def test_verified_other_user_is_not_privileged(
        self,
    ) -> None:
        actor = UserContext.from_request(
            user_id="amber",
            user_name="Amber",
            user_is_admin=True,
            device_id="test",
            voice_mode=False,
            privilege_verified=True,
        )

        self.assertFalse(actor.can_admin)


if __name__ == "__main__":
    unittest.main()
