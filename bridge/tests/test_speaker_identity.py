from __future__ import annotations
import unittest
from app.speaker_identity import extract_display_name, phrase_match_score, parse_speaker_management_command, speaker_id_from_name

class SpeakerIdentityParsingTests(unittest.TestCase):
    def test_fast_enrollment_phrases(self):
        self.assertEqual(parse_speaker_management_command("Jarvis, learn a new voice"), ("enroll", ""))
        self.assertEqual(parse_speaker_management_command("add new user"), ("enroll", ""))
    def test_list_profiles_command(self):
        self.assertEqual(parse_speaker_management_command("Who do you recognise?"), ("list", ""))
    def test_relearn_and_forget(self):
        self.assertEqual(parse_speaker_management_command("relearn Amber's voice"), ("relearn", "Amber"))
        self.assertEqual(parse_speaker_management_command("forget Daniel's voice"), ("forget", "Daniel"))
    def test_name_cleanup_and_id(self):
        self.assertEqual(extract_display_name("My name is Amber."), "Amber")
        self.assertEqual(speaker_id_from_name("Mary-Jane"), "mary_jane")
    def test_phrase_matching_tolerates_small_stt_changes(self):
        expected="Tomorrow morning remind me to check the weather before I leave."
        actual="tomorrow morning remind me to check weather before i leave"
        self.assertGreater(phrase_match_score(actual, expected), 0.70)

if __name__ == "__main__":
    unittest.main()
