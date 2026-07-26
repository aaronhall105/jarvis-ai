"""Pure streaming-state helpers for Jarvis Assist replies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssistantStreamState:
    """Convert Jarvis progress and answer events into HA chat deltas.

    Progress acknowledgements and substantive answers deliberately use separate
    assistant roles. Home Assistant interprets a new role-bearing delta as a new
    assistant message, giving Assist two natural bubbles instead of concatenating
    "One moment" with the final answer.
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
        """Return progress acknowledgements emitted so far."""

        return " ".join(self.progress_parts).strip()

    def progress_events(self, message: str) -> list[dict[str, Any]]:
        """Return HA deltas for one progress acknowledgement."""

        clean = message.strip()
        if not clean:
            return []

        self.progress_parts.append(clean)
        if self.current_message == "progress":
            return [{"content": f" {clean}"}]

        self.current_message = "progress"
        return [{"role": "assistant", "content": clean}]

    def answer_events(self, delta: str) -> list[dict[str, Any]]:
        """Return HA deltas for substantive answer text."""

        if not delta:
            return []

        self.answer_parts.append(delta)
        if self.current_message != "answer":
            self.current_message = "answer"
            return [{"role": "assistant", "content": delta}]

        return [{"content": delta}]

    def final_events(self, speech: str) -> list[dict[str, Any]]:
        """Append any final text not already streamed by Jarvis Core."""

        clean = speech.strip()
        if not clean:
            return []

        current_answer = "".join(self.answer_parts)

        if self.current_message != "answer":
            self.current_message = "answer"
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
            # Preserve the final usable answer without altering the progress bubble.
            missing_text = " " + clean

        if not missing_text:
            return []

        self.answer_parts.append(missing_text)
        return [{"content": missing_text}]
