"""Bounded, deterministic normalization for short spoken commands."""

from __future__ import annotations


MAX_COMMAND_CHARS = 1024


def normalized_command(text: object, *, split_hyphens: bool = False) -> str | None:
    """Return a canonical short command, or ``None`` for oversized input.

    Command recognizers are deliberately bounded before doing any parsing.  This
    keeps attacker-controlled chat input on a linear, predictable path and avoids
    using backtracking regular expressions for small finite command grammars.
    """

    value = str(text or "")
    if len(value) > MAX_COMMAND_CHARS:
        return None
    value = value.casefold().replace("’", "'")
    if split_hyphens:
        value = value.replace("-", " ")
    value = " ".join(value.split()).rstrip(" .!?")
    return value
