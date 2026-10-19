"""Bounded, content-free ownership of provider input audio items.

Input ownership is independent of assistant response generations. Tombstones live
for the provider connection; exhausting the bound closes that connection rather
than forgetting a delivered item and accepting its duplicate later.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


TRANSCRIPTION_TIMEOUT_SECONDS = 30.0
MAX_SPEECH_SECONDS = 300.0
MAX_INPUT_ITEMS = 2048


@dataclass
class InputTurn:
    item_id: str
    generation: int
    deadline: float
    times: dict[str, float] = field(default_factory=dict)
    outcome: str | None = None
    final_claimed: bool = False


class InputLifecycle:
    def __init__(
        self,
        observe: Callable[[InputTurn, str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.items: dict[str, InputTurn] = {}
        self.observe = observe
        self.clock = clock
        self.closed = False

    def ensure(self, item_id: str, generation: int) -> InputTurn | None:
        if not item_id or self.closed:
            return None
        if item_id not in self.items:
            if len(self.items) >= MAX_INPUT_ITEMS:
                raise RuntimeError("Realtime input item capacity reached; renew provider")
            turn = InputTurn(item_id, generation, self.clock() + TRANSCRIPTION_TIMEOUT_SECONDS)
            self.items[item_id] = turn
            self.note(turn, "observed")
        return self.items[item_id]

    def note(self, turn: InputTurn, stage: str) -> None:
        if stage in turn.times:
            return
        turn.times[stage] = self.clock()
        self.observe(turn, stage)

    def finish(self, turn: InputTurn, outcome: str) -> None:
        if turn.outcome is None:
            turn.outcome = outcome
            self.note(turn, outcome)

    def started(self, item_id: str, generation: int) -> InputTurn | None:
        existing = self.items.get(item_id)
        if existing is not None:
            return existing
        # Latest speech owns the conversational input. Pending older items are
        # explicitly superseded, never rebound to the new response generation.
        self.cancel("superseded")
        turn = self.ensure(item_id, generation)
        if turn is not None:
            turn.deadline = self.clock() + MAX_SPEECH_SECONDS
            self.note(turn, "speech_started")
        return turn

    def progress(self, item_id: str, generation: int, stage: str) -> InputTurn | None:
        turn = self.ensure(item_id, generation)
        if turn is None or turn.outcome is not None:
            return turn
        if stage in {"speech_stopped", "committed", "item_created", "first_delta"}:
            turn.deadline = min(turn.deadline, self.clock() + TRANSCRIPTION_TIMEOUT_SECONDS)
        self.note(turn, stage)
        return turn

    def claim_final(self, item_id: str, generation: int, *, empty: bool) -> InputTurn | None:
        self.expire()
        turn = self.items.get(item_id)
        # Require ownership from VAD/commit/item/delta, not text alone. A final
        # from an unknown old item must not acquire the current generation.
        if turn is None:
            return None
        if turn.outcome is not None or turn.final_claimed:
            self.observe(
                turn,
                "duplicate_final_suppressed" if turn.final_claimed else "late_final_discarded",
            )
            return None
        turn.final_claimed = True
        self.note(turn, "transcript_completed")
        if empty:
            self.finish(
                turn,
                "empty_transcript_after_speech" if "speech_started" in turn.times else "no_speech",
            )
            return None
        return turn

    def expire(self) -> list[InputTurn]:
        expired = []
        for turn in self.items.values():
            if turn.outcome is None and self.clock() >= turn.deadline:
                self.finish(turn, "transcription_timeout")
                expired.append(turn)
        return expired

    def cancel(self, outcome: str, through_generation: int | None = None) -> None:
        for turn in self.items.values():
            if through_generation is None or turn.generation <= through_generation:
                self.finish(turn, outcome)

    def close(self) -> None:
        self.cancel("disconnected")
        self.closed = True
