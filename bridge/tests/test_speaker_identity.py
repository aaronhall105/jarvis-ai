from __future__ import annotations
import unittest
from app.speaker_identity import (
    extract_display_name,
    phrase_match_score,
    parse_speaker_management_command,
    speaker_id_from_name,
)


class SpeakerIdentityParsingTests(unittest.TestCase):
    def test_fast_enrollment_phrases(self):
        self.assertEqual(
            parse_speaker_management_command("Jarvis, learn a new voice"), ("enroll", "")
        )
        self.assertEqual(parse_speaker_management_command("add new user"), ("enroll", ""))

    def test_list_profiles_command(self):
        self.assertEqual(parse_speaker_management_command("Who do you recognise?"), ("list", ""))

    def test_relearn_and_forget(self):
        self.assertEqual(
            parse_speaker_management_command("relearn Amber's voice"), ("relearn", "Amber")
        )
        self.assertEqual(
            parse_speaker_management_command("forget Daniel's voice"), ("forget", "Daniel")
        )

    def test_name_cleanup_and_id(self):
        self.assertEqual(extract_display_name("My name is Amber."), "Amber")
        self.assertEqual(speaker_id_from_name("Mary-Jane"), "mary_jane")

    def test_phrase_matching_tolerates_small_stt_changes(self):
        expected = "Tomorrow morning remind me to check the weather before I leave."
        actual = "tomorrow morning remind me to check weather before i leave"
        self.assertGreater(phrase_match_score(actual, expected), 0.70)

    def test_semantic_enrollment_intent_and_stt_corruption(self):
        examples = (
            "Love a new voice",
            "I want you to learn my voice",
            "Can you remember my voice?",
            "Set up voice recognition for me",
            "Could you add someone so you know who is speaking?",
            "Create a new speaker profile",
            "Register somebody new",
        )
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(
                    parse_speaker_management_command(example),
                    ("enroll", ""),
                )

    def test_action_intent_beats_question_words(self):
        self.assertEqual(
            parse_speaker_management_command("Add someone so you know who is speaking"),
            ("enroll", ""),
        )
        self.assertEqual(
            parse_speaker_management_command(
                "Can you register my partner so you recognise who is talking?"
            ),
            ("enroll", ""),
        )

    def test_semantic_management_intents(self):
        self.assertEqual(
            parse_speaker_management_command("Which voice profiles have you saved?"),
            ("list", ""),
        )
        self.assertEqual(
            parse_speaker_management_command("Please update Amber's voice profile"),
            ("relearn", "Amber"),
        )
        self.assertEqual(
            parse_speaker_management_command("Remove Daniel's voice profile"),
            ("forget", "Daniel"),
        )
        self.assertEqual(
            parse_speaker_management_command("Stop this voice enrollment"),
            ("cancel", ""),
        )

    def test_unrelated_voice_sentences_do_not_start_enrollment(self):
        examples = (
            "Turn the volume down",
            "Use a new speaking voice",
            "Switch to a different voice",
            "Why does your voice sound different?",
            "Tell Amber I am on my way home",
        )
        for example in examples:
            with self.subTest(example=example):
                self.assertIsNone(parse_speaker_management_command(example))


if __name__ == "__main__":
    unittest.main()
