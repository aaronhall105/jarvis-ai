import unittest

from app.user_context import UserContext


class PrivilegedIdentityTests(unittest.TestCase):
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
