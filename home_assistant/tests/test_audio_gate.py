from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "jarvis_core_conversation"
    / "audio_gate.py"
)
spec = importlib.util.spec_from_file_location("jarvis_audio_gate", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
SmartAudioGate = module.SmartAudioGate


class SmartAudioGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SmartAudioGate()
        self.key = {
            "conversation_id": "conv-1",
            "satellite_id": "assist_satellite.living_room",
            "device_id": "device-1",
        }

    def arm(self, speech: str, intent: str = "clarification", now: float = 10.0):
        return self.gate.arm(
            **self.key,
            assistant_speech=speech,
            intent_name=intent,
            timeout_seconds=8,
            now=now,
        )

    def decide(self, text: str, now: float = 12.0):
        return self.gate.evaluate(transcript=text, **self.key, now=now)

    def test_first_turn_without_expectation_is_accepted(self):
        decision = self.decide("turn the bedroom light on")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "not_a_gated_follow_up")

    def test_assistant_self_echo_is_rejected(self):
        self.arm("Which room do you mean?")
        decision = self.decide("which room do you mean")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "assistant_self_echo")

    def test_expected_yes_is_accepted(self):
        self.arm("Would you like me to turn it off?")
        decision = self.decide("yes please")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "expected_confirmation")

    def test_unrelated_background_is_rejected_during_confirmation(self):
        self.arm("Would you like me to turn it off?")
        decision = self.decide("and then she said that we should go to the shops")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "unrelated_to_confirmation")

    def test_short_choice_is_accepted(self):
        self.arm("Which room do you mean?")
        decision = self.decide("the bedroom")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "short_choice_answer")

    def test_long_unrelated_slot_answer_is_rejected(self):
        self.arm("What time should I set it for?")
        decision = self.decide("and then we were talking about the football and what happened yesterday")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "unlikely_slot_answer")

    def test_explicit_new_command_is_accepted(self):
        self.arm("Which room do you mean?")
        decision = self.decide("turn the kitchen light off instead")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "explicit_new_request")

    def test_expired_follow_up_is_rejected(self):
        self.arm("Which room do you mean?", now=10.0)
        decision = self.decide("bedroom", now=19.0)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "follow_up_window_expired")

    def test_gate_is_one_shot(self):
        self.arm("Which room do you mean?")
        first = self.decide("bedroom")
        second = self.decide("ordinary new request")
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.reason, "not_a_gated_follow_up")

    def test_wake_word_only_is_rejected(self):
        self.arm("Which room do you mean?")
        decision = self.decide("hey jarvis")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "wake_word_only")

    def test_unrelated_question_is_rejected_as_choice_answer(self):
        self.arm("Which room do you mean?")
        decision = self.decide("what are you doing here")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "unrelated_question")

    def test_expected_notification_dictation_can_be_longer(self):
        self.arm("What should the notification say?")
        decision = self.decide("Please remember to bring the parcel home after work and leave it by the front door")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "expected_dictation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
