from __future__ import annotations

import unittest

from app.tone_engine import ToneEngine, ToneProfile


class ProgressExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ToneEngine()

    def test_short_control_does_not_show_progress(self) -> None:
        text = "Turn the living room floodlight off"
        self.assertFalse(self.engine.should_emit_progress(text, self.engine.analyse(text)))

    def test_task_commands_do_not_show_progress(self) -> None:
        for text in (
            "Show task history",
            "Delete task history",
            "Turn the TV off in 30 minutes",
        ):
            with self.subTest(text=text):
                self.assertFalse(self.engine.should_emit_progress(text, self.engine.analyse(text)))

    def test_slow_state_question_can_show_progress_quickly(self) -> None:
        text = "What is the current battery state of Amber's phone?"
        profile = self.engine.analyse(text)
        self.assertTrue(self.engine.should_emit_progress(text, profile))
        self.assertLessEqual(self.engine.progress_delay_seconds(text, profile), 0.70)

    def test_phrase_library_has_large_variety(self) -> None:
        self.assertGreaterEqual(self.engine.progress_phrase_count, 100)

    def test_same_request_does_not_repeat_recent_phrase(self) -> None:
        text = "What is the temperature in the living room?"
        phrases = [self.engine.progress_phrase(text, ToneProfile()) for _ in range(10)]
        self.assertEqual(len(phrases), len(set(phrases)))

    def test_phrases_vary_by_request_category(self) -> None:
        phrases = {
            self.engine.progress_phrase(
                "What is the temperature in the living room?",
                ToneProfile(),
            ),
            self.engine.progress_phrase(
                "What do you remember about Amber's health conditions?",
                ToneProfile(),
            ),
            self.engine.progress_phrase(
                "What is today's Octopus electricity rate?",
                ToneProfile(),
            ),
            self.engine.progress_phrase(
                "Explain why the television sometimes fails to wake.",
                ToneProfile(),
            ),
        }
        self.assertGreaterEqual(len(phrases), 4)

    def test_frustrated_request_gets_early_acknowledgement(self) -> None:
        text = "This still doesn't work, check it properly"
        profile = self.engine.analyse(text)
        self.assertEqual(profile.label, "frustrated")
        self.assertTrue(self.engine.should_emit_progress(text, profile))
        self.assertLess(self.engine.progress_delay_seconds(text, profile), 0.5)

    def test_phrases_are_short_enough_for_natural_filler(self) -> None:
        for group in self.engine._PHRASES.values():  # noqa: SLF001 - validation test
            for phrase in group:
                with self.subTest(phrase=phrase):
                    self.assertLessEqual(len(phrase.split()), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
