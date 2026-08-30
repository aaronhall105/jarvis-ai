from __future__ import annotations

import asyncio
import unittest

from app.speaker_identity import SpeakerIdentityRuntime


class FakeSpeakerIdentityClient:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start_enrollment(
        self, *, speaker_id: str, display_name: str, is_admin: bool, replace: bool
    ):
        self.calls.append(
            {
                "speaker_id": speaker_id,
                "display_name": display_name,
                "is_admin": is_admin,
                "replace": replace,
            }
        )
        return {
            "session_id": "session-1",
            "phrases": ["The quick brown fox jumps over the lazy dog."],
            "target_samples": 1,
        }


async def _noop_send(payload):
    return None


async def _noop_speak(text: str):
    return None


class SpeakerIdentityAlpha14SecurityTests(unittest.TestCase):
    def _run_name_step(self, metadata: dict[str, object]) -> FakeSpeakerIdentityClient:
        client = FakeSpeakerIdentityClient()
        runtime = SpeakerIdentityRuntime(client, "aaron", True)
        state = {"speaker_enrollment": {"phase": "await_name", "replace": False}}
        handled = asyncio.run(
            runtime.enrollment(
                "Aaron",
                b"",
                metadata,
                state,
                _noop_send,
                _noop_speak,
            )
        )
        self.assertTrue(handled)
        self.assertEqual(len(client.calls), 1)
        return client

    def test_guest_cannot_self_assign_configured_admin_by_name(self) -> None:
        client = self._run_name_step({"user_id": "guest", "user_is_admin": False})
        self.assertFalse(client.calls[0]["is_admin"])

    def test_existing_authenticated_admin_can_reenroll_admin_profile(self) -> None:
        client = self._run_name_step(
            {"user_id": "aaron", "user_is_admin": False, "speaker_household_admin": True}
        )
        self.assertTrue(client.calls[0]["is_admin"])

    def test_recognized_voice_id_admin_does_not_become_core_admin(self) -> None:
        client = FakeSpeakerIdentityClient()
        runtime = SpeakerIdentityRuntime(client, "aaron", True)
        metadata: dict[str, object] = {}
        result = {
            "recognized": True,
            "reason": "recognized",
            "score": 0.9,
            "margin": 0.2,
            "speaker": {
                "speaker_id": "aaron",
                "display_name": "Aaron",
                "is_admin": True,
            },
        }
        self.assertTrue(runtime.apply(metadata, result))
        self.assertEqual(metadata["user_id"], "aaron")
        self.assertFalse(metadata["user_is_admin"])
        self.assertTrue(metadata["speaker_household_admin"])

    def test_unknown_identity_clears_household_admin_state(self) -> None:
        client = FakeSpeakerIdentityClient()
        runtime = SpeakerIdentityRuntime(client, "aaron", True)
        metadata: dict[str, object] = {
            "user_id": "aaron",
            "user_is_admin": False,
            "speaker_household_admin": True,
            "speaker_id": "aaron",
        }
        runtime.set_unknown(metadata, "below_threshold")
        self.assertEqual(metadata["user_id"], "guest")
        self.assertFalse(metadata["user_is_admin"])
        self.assertFalse(metadata["speaker_household_admin"])
        self.assertEqual(metadata["speaker_id"], "unknown")


if __name__ == "__main__":
    unittest.main()
