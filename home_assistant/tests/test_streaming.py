from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "jarvis_core_conversation"
    / "streaming.py"
)
spec = importlib.util.spec_from_file_location("jarvis_streaming", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
AssistantStreamState = module.AssistantStreamState


class AssistantStreamStateTests(unittest.TestCase):
    def test_progress_uses_thinking_content(self) -> None:
        state = AssistantStreamState()
        self.assertEqual(
            state.progress_events("Checking Home Assistant now."),
            [
                {
                    "role": "assistant",
                    "thinking_content": "Checking Home Assistant now.",
                }
            ],
        )

    def test_answer_after_progress_stays_in_same_message(self) -> None:
        state = AssistantStreamState()
        state.progress_events("Let me check the current state.")
        self.assertEqual(state.answer_events("The light"), [{"content": "The light"}])
        self.assertEqual(state.answer_events(" is off."), [{"content": " is off."}])

    def test_answer_without_progress_starts_assistant_message(self) -> None:
        state = AssistantStreamState()
        self.assertEqual(
            state.answer_events("Done."),
            [{"role": "assistant", "content": "Done."}],
        )

    def test_final_does_not_repeat_streamed_answer(self) -> None:
        state = AssistantStreamState()
        state.progress_events("Working that out now.")
        state.answer_events("The TV is off.")
        self.assertEqual(state.final_events("The TV is off."), [])

    def test_final_after_thinking_adds_only_answer_content(self) -> None:
        state = AssistantStreamState()
        state.progress_events("I’m checking the saved details.")
        self.assertEqual(
            state.final_events("Amber is lactose intolerant."),
            [{"content": "Amber is lactose intolerant."}],
        )

    def test_blank_progress_is_ignored(self) -> None:
        state = AssistantStreamState()
        self.assertEqual(state.progress_events("   "), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
