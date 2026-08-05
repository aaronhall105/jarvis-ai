from __future__ import annotations

import re


class SpeechRenderPolicy:
    """Prepare fast, natural and complete spoken responses."""

    _BOUNDARY = re.compile(
        r'(?<=[.!?])["”’\')\]]*(?:\s+|$)'
    )

    _SOFT_BOUNDARY = re.compile(
        r"[,;:]\s+"
    )

    @classmethod
    def normalise(
        cls,
        value: str,
    ) -> str:
        return re.sub(
            r"\s+",
            " ",
            str(value or ""),
        ).strip()

    @classmethod
    def early_segment(
        cls,
        streamed_text: str,
        *,
        minimum_chars: int = 32,
        preferred_chars: int = 96,
        maximum_chars: int = 180,
    ) -> str:
        """
        Return an early natural speech segment.

        Prefer the first completed sentence. If the model streams a
        long sentence without punctuation, permit a bounded phrase at
        a comma, semicolon, colon or safe word boundary.
        """

        text = cls.normalise(streamed_text)

        if len(text) < minimum_chars:
            return ""

        sentence_boundaries = [
            match.end()
            for match in cls._BOUNDARY.finditer(text)
            if minimum_chars <= match.end() <= maximum_chars
        ]

        if sentence_boundaries:
            return text[
                : sentence_boundaries[0]
            ].strip()

        if len(text) < preferred_chars:
            return ""

        soft_boundaries = [
            match.end()
            for match in cls._SOFT_BOUNDARY.finditer(text)
            if minimum_chars <= match.end() <= maximum_chars
        ]

        if soft_boundaries:
            suitable = [
                boundary
                for boundary in soft_boundaries
                if boundary <= preferred_chars
            ]

            end = (
                suitable[-1]
                if suitable
                else soft_boundaries[0]
            )

            return text[:end].strip()

        if len(text) < maximum_chars:
            return ""

        cut = text.rfind(
            " ",
            minimum_chars,
            maximum_chars,
        )

        if cut < minimum_chars:
            cut = maximum_chars

        segment = text[:cut].rstrip(" ,;:")

        if segment and segment[-1] not in ".!?":
            segment += "…"

        return segment

    @classmethod
    def remaining_text(
        cls,
        complete_response: str,
        spoken_prefix: str,
    ) -> str:
        """
        Return the part of a complete response not already spoken.

        Both strings are normalised first so streamed whitespace
        differences do not cause the first section to be repeated.
        """

        complete = cls.normalise(
            complete_response
        )

        prefix = cls.normalise(
            spoken_prefix
        ).rstrip("…")

        if not complete:
            return ""

        if not prefix:
            return complete

        if complete.casefold().startswith(
            prefix.casefold()
        ):
            remainder = complete[
                len(prefix):
            ].lstrip(" ,;:-")

            return remainder.strip()

        # Conservative fallback: avoid repeating the entire reply if
        # the streaming prefix differs only slightly from final text.
        probe = prefix[:80].casefold()

        if probe:
            position = complete.casefold().find(
                probe
            )

            if position == 0:
                remainder = complete[
                    min(len(prefix), len(complete)):
                ].lstrip(" ,;:-")

                return remainder.strip()

        return complete

    @classmethod
    def spoken_text(
        cls,
        response: str,
        *,
        maximum_chars: int = 520,
    ) -> str:
        text = cls.normalise(response)

        if len(text) <= maximum_chars:
            return text

        boundaries = [
            match.end()
            for match in cls._BOUNDARY.finditer(text)
            if match.end() <= maximum_chars
        ]

        if boundaries:
            end = boundaries[
                min(1, len(boundaries) - 1)
            ]

            excerpt = text[:end].strip()
        else:
            cut = text.rfind(
                " ",
                0,
                maximum_chars,
            )

            excerpt = text[
                : cut if cut >= 80 else maximum_chars
            ].rstrip(" ,;:")

        if excerpt and excerpt[-1] not in ".!?":
            excerpt += "."

        return (
            excerpt
            + " The full reply is in the chat."
        )
