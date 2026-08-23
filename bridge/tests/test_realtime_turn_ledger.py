from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.realtime_turn_ledger import (
    RealtimeTurnLedger,
)


class FakeClock:
    def __init__(
        self,
        value: float = 1_000.0,
    ) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RealtimeTurnLedgerTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temp = (
            tempfile.TemporaryDirectory()
        )
        self.path = (
            Path(self.temp.name)
            / "turns.db"
        )
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ledger(self) -> RealtimeTurnLedger:
        return RealtimeTurnLedger(
            self.path,
            clock=self.clock,
        )

    def test_first_claim_is_new(self) -> None:
        ledger = self.ledger()
        try:
            claim = ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=41,
                command="Turn the lights off",
            )

            self.assertTrue(
                claim.is_new
            )
            self.assertEqual(
                "accepted",
                claim.record.status,
            )
        finally:
            ledger.close()

    def test_same_turn_and_command_is_duplicate(
        self,
    ) -> None:
        ledger = self.ledger()

        try:
            first = ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=41,
                command="Turn   the lights off",
            )

            second = ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=41,
                command=" Turn the lights off ",
            )

            self.assertTrue(
                first.is_new
            )
            self.assertTrue(
                second.is_duplicate
            )
            self.assertFalse(
                second.is_new
            )
        finally:
            ledger.close()

    def test_reusing_id_for_different_command_is_conflict(
        self,
    ) -> None:
        ledger = self.ledger()

        try:
            ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=41,
                command="Turn the lights off",
            )

            claim = ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=41,
                command="Turn the lights on",
            )

            self.assertTrue(
                claim.is_conflict
            )
        finally:
            ledger.close()

    def test_claim_survives_database_reopen(
        self,
    ) -> None:
        first = self.ledger()

        first.claim(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=99,
            command="Lock the door",
        )

        first.close()

        second = self.ledger()

        try:
            claim = second.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=99,
                command="Lock the door",
            )

            self.assertTrue(
                claim.is_duplicate
            )
        finally:
            second.close()

    def test_completed_response_is_persistent(
        self,
    ) -> None:
        ledger = self.ledger()

        try:
            ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=52,
                command="What time is it?",
            )

            completed = (
                ledger.mark_completed(
                    client_kind="mobile",
                    device_id="phone-1",
                    conversation_id="chat-1",
                    client_turn_id=52,
                    response={
                        "text": "It is 10:15.",
                        "success": True,
                    },
                )
            )

            self.assertEqual(
                "completed",
                completed.status,
            )
            self.assertEqual(
                {
                    "text": "It is 10:15.",
                    "success": True,
                },
                completed.response,
            )
        finally:
            ledger.close()

    def test_interrupted_turn_is_not_reclassified_as_new(
        self,
    ) -> None:
        ledger = self.ledger()

        try:
            ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=61,
                command="Turn the television off",
            )

            ledger.mark_interrupted(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=61,
            )

            replay = ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=61,
                command="Turn the television off",
            )

            self.assertTrue(
                replay.is_duplicate
            )
            self.assertEqual(
                "interrupted",
                replay.record.status,
            )
        finally:
            ledger.close()

    def test_prune_removes_old_records(
        self,
    ) -> None:
        ledger = self.ledger()

        try:
            ledger.claim(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-old",
                client_turn_id=1,
                command="Old request",
            )

            self.clock.value += 100.0

            removed = ledger.prune(
                max_age_seconds=50.0
            )

            self.assertEqual(
                1,
                removed,
            )

            self.assertIsNone(
                ledger.lookup(
                    client_kind="mobile",
                    device_id="phone-1",
                    conversation_id="chat-old",
                    client_turn_id=1,
                )
            )
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
