from __future__ import annotations

import re


class SpeechRenderPolicy:
    """Keep full text in chat while speaking an efficient complete excerpt."""

    _BOUNDARY = re.compile(r'(?<=[.!?])["”’\')\]]*(?:\s+|$)')

    @classmethod
    def early_segment(
        cls,
        streamed_text: str,
        *,
        minimum_chars: int = 48,
        maximum_chars: int = 360,
    ) -> str:
        text = re.sub(r"\s+", " ", str(streamed_text or "")).strip()
        if len(text) < minimum_chars:
            return ""

        boundaries = [
            match.end()
            for match in cls._BOUNDARY.finditer(text)
            if minimum_chars <= match.end() <= maximum_chars
        ]
        if not boundaries:
            return ""

        end = boundaries[1] if len(boundaries) > 1 else boundaries[0]
        return text[:end].strip()

    @classmethod
    def spoken_text(
        cls,
        response: str,
        *,
        maximum_chars: int = 520,
    ) -> str:
        text = re.sub(r"\s+", " ", str(response or "")).strip()
        if len(text) <= maximum_chars:
            return text

        boundaries = [
            match.end()
            for match in cls._BOUNDARY.finditer(text)
            if match.end() <= maximum_chars
        ]
        if boundaries:
            end = boundaries[min(1, len(boundaries) - 1)]
            excerpt = text[:end].strip()
        else:
            cut = text.rfind(" ", 0, maximum_chars)
            excerpt = text[: cut if cut >= 80 else maximum_chars].rstrip(" ,;:")

        if excerpt and excerpt[-1] not in ".!?":
            excerpt += "."
        return excerpt + " The full reply is in the chat."
