from __future__ import annotations

import re
from typing import Any, Sequence


class ReplyBudgetPolicy:
    """Select a response ceiling from the request instead of one fixed limit."""

    _CONTROL = re.compile(
        r"\b(?:turn|switch|power|open|close|lock|unlock|pause|resume|mute|"
        r"unmute|volume|battery|temperature|where is|what is on|is .* on)\b",
        re.I,
    )
    _CREATIVE = re.compile(
        r"\b(?:tell|write|make|create|continue)\b.{0,50}"
        r"\b(?:story|poem|letter|scene|script|article|essay|bedtime tale)\b|"
        r"\b(?:story|poem|essay|article)\b",
        re.I,
    )
    _DETAILED = re.compile(
        r"\b(?:in detail|detailed|thorough|step by step|full explanation|"
        r"explain fully|complete guide|deep dive|analyse|review)\b",
        re.I,
    )

    @classmethod
    def latest_user_text(cls, input_items: Sequence[Any]) -> str:
        for item in reversed(list(input_items or [])):
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").casefold() != "user":
                continue
            content = item.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        value = part.get("text") or part.get("content")
                        if value:
                            parts.append(str(value))
                return " ".join(parts).strip()
        return ""

    @classmethod
    def output_tokens(
        cls,
        request_text: str,
        *,
        voice_mode: bool,
        text_cap: int,
        voice_cap: int,
    ) -> int:
        text = " ".join(str(request_text or "").split())
        cap = max(100, voice_cap if voice_mode else text_cap)

        if cls._CREATIVE.search(text):
            target = 1600 if voice_mode else 2400
        elif cls._DETAILED.search(text):
            target = 1100 if voice_mode else 1800
        elif cls._CONTROL.search(text) and len(text) <= 180:
            target = 240
        else:
            target = 700 if voice_mode else 1000

        return max(100, min(cap, target))
