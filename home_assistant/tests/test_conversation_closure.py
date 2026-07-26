from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "jarvis_core_conversation"
    / "closure.py"
)
spec = importlib.util.spec_from_file_location("jarvis_closure", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

match_conversation_closure = module.match_conversation_closure
closure_response = module.closure_response
normalise_closure_phrase = module.normalise_closure_phrase


class ConversationClosureTests(unittest.TestCase):
    def test_requested_phrases_end_the_session(self) -> None:
        phrases = (
            "That is all.",
            "That's all",
            "Goodbye",
            "Good bye, Jarvis.",
            "Be quiet",
            "Stop listening now",
            "End the conversation",
            "Thanks Jarvis",
            "Okay Jarvis, that'll do.",
            "Good night",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(match_conversation_closure(phrase))

    def test_silence_commands_return_no_speech(self) -> None:
        for phrase in ("be quiet", "stop listening", "hush", "cancel", "never mind"):
            closure = match_conversation_closure(phrase)
            self.assertIsNotNone(closure)
            assert closure is not None
            self.assertEqual(closure.kind, "silent")
            self.assertEqual(closure_response(closure, "Aaron Hall"), "")

    def test_polite_and_goodbye_responses_are_brief(self) -> None:
        thanks = match_conversation_closure("thank you, Jarvis")
        goodbye = match_conversation_closure("goodbye")
        done = match_conversation_closure("that is all")
        assert thanks is not None and goodbye is not None and done is not None
        self.assertEqual(closure_response(thanks, "Aaron Hall"), "You're welcome.")
        self.assertEqual(closure_response(goodbye, "Aaron Hall"), "Goodbye, Aaron.")
        self.assertEqual(closure_response(done, "Aaron Hall"), "Okay.")

    def test_normalisation_accepts_stt_variants(self) -> None:
        self.assertEqual(normalise_closure_phrase("Okay, Jarvis — good bye please"), "good bye")
        self.assertEqual(normalise_closure_phrase("That’s all!"), "thats all")

    def test_normal_commands_do_not_accidentally_close(self) -> None:
        phrases = (
            "stop the television",
            "turn the bedroom light off",
            "thank you for turning the light off",
            "be quiet in the bedroom",
            "cancel task 12",
            "goodbye lights turn off",
            "tell me when the conversation ended",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNone(match_conversation_closure(phrase))

    def test_local_interception_precedes_http_request(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "jarvis_core_conversation"
            / "conversation.py"
        ).read_text()
        self.assertLess(
            source.index("closure = match_conversation_closure"),
            source.index("session.post("),
        )
        self.assertIn("continue_conversation=False", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
