import unittest

from app.reply_policy import ReplyBudgetPolicy
from app.speech_render_policy import SpeechRenderPolicy
from app.realtime_voice import VERSION


class FinalPolishAlpha13Test(unittest.TestCase):
    def test_release_identity(self):
        self.assertEqual(VERSION, "19.0.0-alpha13")

    def test_story_gets_long_voice_budget(self):
        budget = ReplyBudgetPolicy.output_tokens(
            "Tell me a long story about a hidden library",
            voice_mode=True,
            text_cap=2600,
            voice_cap=1800,
        )
        self.assertEqual(budget, 1600)

    def test_control_stays_compact(self):
        budget = ReplyBudgetPolicy.output_tokens(
            "Turn off the bedroom floodlight",
            voice_mode=True,
            text_cap=2600,
            voice_cap=1800,
        )
        self.assertEqual(budget, 240)

    def test_latest_user_text_handles_responses_shape(self):
        value = ReplyBudgetPolicy.latest_user_text(
            [
                {"role": "assistant", "content": "Hello"},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Explain it in detail"}
                    ],
                },
            ]
        )
        self.assertEqual(value, "Explain it in detail")

    def test_early_speech_waits_for_complete_sentence(self):
        self.assertEqual(
            SpeechRenderPolicy.early_segment(
                "This reply has not finished yet"
            ),
            "",
        )
        self.assertEqual(
            SpeechRenderPolicy.early_segment(
                "Here is the useful opening sentence. "
                "The full explanation continues in the chat."
            ),
            "Here is the useful opening sentence. "
            "The full explanation continues in the chat.",
        )

    def test_long_speech_is_complete_and_bounded(self):
        response = (
            "This is the first complete sentence. "
            "This is the second complete sentence. "
            + ("More written detail follows. " * 60)
        )
        spoken = SpeechRenderPolicy.spoken_text(
            response,
            maximum_chars=120,
        )
        self.assertLess(len(spoken), 190)
        self.assertTrue(
            spoken.endswith("The full reply is in the chat.")
        )


if __name__ == "__main__":
    unittest.main()
