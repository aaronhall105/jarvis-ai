from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

ClosureKind = Literal["silent", "done", "thanks", "goodbye"]


@dataclass(frozen=True, slots=True)
class ConversationClosure:
    """A locally recognised instruction to end follow-up listening."""

    kind: ClosureKind
    normalised_text: str


_PREFIXES = re.compile(
    r"^(?:(?:okay|ok|alright|all right|right|well)\s+)+",
    re.IGNORECASE,
)
_JARVIS_PREFIX = re.compile(r"^(?:hey\s+)?jarvis\s+", re.IGNORECASE)
_JARVIS_SUFFIX = re.compile(r"\s+jarvis$", re.IGNORECASE)
_POLITE_EDGE = re.compile(r"^(?:please\s+)|(?:\s+please)$", re.IGNORECASE)

_SILENT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"be quiet(?: now)?",
        r"(?:stay|keep) quiet(?: now)?",
        r"quiet(?: now)?",
        r"hush(?: now)?",
        r"silence(?: now)?",
        r"(?:stop|quit) listening(?: now)?",
        r"(?:stop|quit) talking(?: now)?",
        r"(?:do not|dont) listen(?: anymore| any more)?",
        r"leave me alone",
        r"never mind",
        r"nevermind",
        r"cancel",
        r"stop",
    )
)

_DONE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"thats all",
        r"that is all",
        r"thatll be all",
        r"that will be all",
        r"thats everything",
        r"that is everything",
        r"thatll do",
        r"that will do",
        r"all done",
        r"were done",
        r"we are done",
        r"im done",
        r"i am done",
        r"done for now",
        r"were finished",
        r"we are finished",
        r"im finished",
        r"i am finished",
        r"finished",
        r"end(?: the)? conversation",
        r"finish(?: the)? conversation",
        r"close(?: the)? conversation",
        r"end(?: the)? chat",
        r"close(?: the)? chat",
        r"no more",
    )
)

_THANKS_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"thanks",
        r"thanks a lot",
        r"many thanks",
        r"thank you",
        r"thank you very much",
        r"cheers",
    )
)

_GOODBYE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"bye",
        r"bye bye",
        r"goodbye",
        r"good bye",
        r"goodnight",
        r"good night",
        r"see you",
        r"see you later",
        r"speak later",
        r"talk later",
        r"catch you later",
    )
)


def normalise_closure_phrase(value: str) -> str:
    """Normalise a short closing phrase while retaining exact-command safety."""

    text = str(value or "").casefold()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("'", "")

    # Remove harmless conversational wrappers, then the assistant's name.
    previous = None
    while previous != text:
        previous = text
        text = _PREFIXES.sub("", text).strip()
        text = _JARVIS_PREFIX.sub("", text).strip()
        text = _JARVIS_SUFFIX.sub("", text).strip()
        text = _POLITE_EDGE.sub("", text).strip()
        text = re.sub(r"\s+", " ", text)

    return text


def _matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.fullmatch(text) is not None for pattern in patterns)


def match_conversation_closure(value: str) -> ConversationClosure | None:
    """Recognise an explicit short instruction to end this conversation session."""

    text = normalise_closure_phrase(value)
    if not text or len(text.split()) > 7:
        return None
    if _matches(text, _SILENT_PATTERNS):
        return ConversationClosure("silent", text)
    if _matches(text, _GOODBYE_PATTERNS):
        return ConversationClosure("goodbye", text)
    if _matches(text, _THANKS_PATTERNS):
        return ConversationClosure("thanks", text)
    if _matches(text, _DONE_PATTERNS):
        return ConversationClosure("done", text)
    return None


def closure_response(closure: ConversationClosure, user_name: str = "") -> str:
    """Return the brief final speech, or blank text for silence commands."""

    if closure.kind == "silent":
        return ""
    if closure.kind == "thanks":
        return "You're welcome."
    if closure.kind == "goodbye":
        first_name = str(user_name or "").strip().split(" ", 1)[0]
        return f"Goodbye, {first_name}." if first_name else "Goodbye."
    return "Okay."
