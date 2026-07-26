"""Pure streaming-state helpers for Jarvis Assist replies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssistantStreamState:
    """Convert Jarvis progress and answer events into Home Assistant deltas.

    Progress is streamed through Home Assistant's separate ``thinking_content``
    field. It may be displayed while Jarvis is working, but it is not part of the
    final spoken answer and is not committed as a separate assistant bubble.
    """

    current_message: str | None = None
    progress_parts: list[str] = field(default_factory=list)
    answer_parts: list[str] = field(default_factory=list)

    @property
    def answer_text(self) -> str:
        """Return the substantive answer streamed so far."""

        return "".join(self.answer_parts).strip()

    @property
    def progress_text(self) -> str:
        """Return progress text streamed so far."""

        return " ".join(self.progress_parts).strip()

    def progress_events(self, message: str) -> list[dict[str, Any]]:
        """Return a non-spoken thinking delta for one progress phrase."""

        clean = message.strip()
        if not clean:
            return []

        self.progress_parts.append(clean)
        if self.current_message is None:
            self.current_message = "assistant"
            return [{"role": "assistant", "thinking_content": clean}]

        return [{"thinking_content": f" {clean}"}]

    def answer_events(self, delta: str) -> list[dict[str, Any]]:
        """Return deltas for substantive answer text."""

        if not delta:
            return []

        self.answer_parts.append(delta)
        if self.current_message is None:
            self.current_message = "assistant"
            return [{"role": "assistant", "content": delta}]

        # When a thinking message is already streaming, append answer content to
        # the same AssistantContent instead of starting or sending a second bubble.
        return [{"content": delta}]

    def final_events(self, speech: str) -> list[dict[str, Any]]:
        """Append any final text not already streamed by Jarvis Core."""

        clean = speech.strip()
        if not clean:
            return []

        current_answer = "".join(self.answer_parts)

        if self.current_message is None:
            self.current_message = "assistant"
            self.answer_parts.append(clean)
            return [{"role": "assistant", "content": clean}]

        if current_answer and clean.startswith(current_answer):
            missing_text = clean[len(current_answer) :]
        elif current_answer == clean:
            missing_text = ""
        elif not current_answer:
            missing_text = clean
        else:
            # A tool continuation or fallback can replace partial streamed text.
            # Preserve the usable final answer without mixing it into thinking text.
            missing_text = " " + clean

        if not missing_text:
            return []

        self.answer_parts.append(missing_text)
        return [{"content": missing_text}]
