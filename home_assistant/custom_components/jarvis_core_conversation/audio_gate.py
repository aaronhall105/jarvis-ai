from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import time
from typing import Literal

GateAction = Literal["accept", "reject"]
ExpectationKind = Literal["yes_no", "choice", "slot", "dictation", "open"]


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Decision made before a follow-up transcript reaches Jarvis Core."""

    action: GateAction
    reason: str
    confidence: float
    expectation_kind: ExpectationKind | None = None

    @property
    def accepted(self) -> bool:
        return self.action == "accept"


@dataclass(frozen=True, slots=True)
class FollowUpExpectation:
    """One short-lived follow-up turn expected from a voice satellite."""

    key: str
    conversation_id: str
    satellite_id: str
    device_id: str
    assistant_speech: str
    intent_name: str
    kind: ExpectationKind
    armed_at: float
    expires_at: float


_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9'\s%-]")

_YES_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|correct|right|okay|ok|sure|please do|do it|"
    r"go ahead|confirm|confirmed|yes please|that is right|that's right|i do|i would)$",
    re.I,
)
_NO_RE = re.compile(
    r"^(?:no|nope|nah|negative|cancel|do not|don't|dont|not now|"
    r"leave it|never mind|nevermind|stop)$",
    re.I,
)
_DIRECT_RE = re.compile(
    r"^(?:(?:hey\s+)?jarvis\s+)?(?:"
    r"turn|switch|power|set|dim|brighten|open|close|lock|unlock|"
    r"play|pause|resume|stop|mute|unmute|start|cancel|remind|remember|"
    r"notify|send|announce|broadcast|check|show|tell|find|list|"
    r"what|where|who|when|why|how|is|are|was|were|can|could|would|"
    r"should|do|does|did|has|have"
    r")\b",
    re.I,
)
_ACTION_RE = re.compile(
    r"^(?:(?:hey\s+)?jarvis\s+)?(?:"
    r"turn|switch|power|set|dim|brighten|open|close|lock|unlock|"
    r"play|pause|resume|stop|mute|unmute|start|cancel|remind|remember|"
    r"notify|send|announce|broadcast|check|show|tell|find|list"
    r")\b",
    re.I,
)
_QUESTION_START_RE = re.compile(
    r"^(?:what|where|who|when|why|how|is|are|was|were|can|could|would|"
    r"should|do|does|did|has|have)\b",
    re.I,
)
_WAKE_ONLY_RE = re.compile(
    r"^(?:hey\s+jarvis|jarvis|okay\s+nabu|ok\s+nabu)$",
    re.I,
)
_BACKGROUND_RE = re.compile(
    r"\b(?:and then|i was like|he was like|she was like|they were like|"
    r"you know what i mean|anyway so|on the television|on the tv|"
    r"he said that|she said that|they said that|we were talking about)\b",
    re.I,
)
_FILLER_RE = re.compile(
    r"^(?:uh+|um+|erm+|hmm+|mm+|ah+|oh+|right right|yeah yeah|"
    r"okay okay|ok ok|hello hello)$",
    re.I,
)


def normalise_transcript(value: str) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("‘", "'")
    text = _NON_WORD_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _token_overlap(first: str, second: str) -> float:
    a = set(first.split())
    b = set(second.split())
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _looks_like_self_echo(transcript: str, assistant_speech: str) -> bool:
    heard = normalise_transcript(transcript)
    spoken = normalise_transcript(assistant_speech)
    if len(heard.split()) < 3 or len(spoken.split()) < 3:
        return False
    if heard in spoken or spoken in heard:
        return True
    ratio = SequenceMatcher(None, heard, spoken).ratio()
    overlap = _token_overlap(heard, spoken)
    return ratio >= 0.74 or (overlap >= 0.84 and ratio >= 0.55)


def _expectation_kind(speech: str, intent_name: str) -> ExpectationKind:
    text = normalise_transcript(speech)
    intent_value = normalise_transcript(intent_name)
    if (
        re.search(
            r"\b(?:yes or no|would you like|do you want|shall i|can i|may i|"
            r"confirm|say confirm|is that correct|are you sure)\b",
            text,
        )
        or re.match(r"^(?:should i)\b", text)
        or intent_value in {"confirmation", "admin change"}
    ):
        return "yes_no"
    if re.search(
        r"\b(?:which|choose|select|first or second|which one|which room|"
        r"which device|which light|which person)\b",
        text,
    ):
        return "choice"
    if re.search(
        r"\b(?:what should (?:it|the message|the notification|i) say|"
        r"what (?:message|announcement) should|what would you like (?:it|me) to say|"
        r"tell me (?:the|what) message|what should i announce)\b",
        text,
    ):
        return "dictation"
    if "?" in str(speech) or re.search(
        r"^(?:what|where|who|when|why|how)\b|"
        r"\b(?:please tell me|please specify|tell me the|give me the)\b",
        text,
    ):
        return "slot"
    return "open"


class SmartAudioGate:
    """Reject likely ambient follow-up speech before it reaches the AI."""

    def __init__(self) -> None:
        self._expectations: dict[str, FollowUpExpectation] = {}

    @staticmethod
    def _key(
        conversation_id: str | None,
        satellite_id: str | None,
        device_id: str | None,
    ) -> str:
        endpoint = str(satellite_id or device_id or "unknown").strip()
        conversation = str(conversation_id or "").strip()
        return f"{endpoint}::{conversation}"

    def _purge_expired(self, now: float, *, keep_key: str | None = None) -> None:
        stale = [
            key
            for key, expectation in self._expectations.items()
            if key != keep_key and now > expectation.expires_at
        ]
        for key in stale:
            self._expectations.pop(key, None)

    def arm(
        self,
        *,
        conversation_id: str | None,
        satellite_id: str | None,
        device_id: str | None,
        assistant_speech: str,
        intent_name: str | None,
        timeout_seconds: int,
        now: float | None = None,
    ) -> FollowUpExpectation:
        timestamp = time.monotonic() if now is None else float(now)
        self._purge_expired(timestamp)
        key = self._key(conversation_id, satellite_id, device_id)
        expectation = FollowUpExpectation(
            key=key,
            conversation_id=str(conversation_id or ""),
            satellite_id=str(satellite_id or ""),
            device_id=str(device_id or ""),
            assistant_speech=str(assistant_speech or ""),
            intent_name=str(intent_name or ""),
            kind=_expectation_kind(assistant_speech, str(intent_name or "")),
            armed_at=timestamp,
            expires_at=timestamp + max(3, min(int(timeout_seconds), 20)),
        )
        self._expectations[key] = expectation
        return expectation

    def clear(
        self,
        *,
        conversation_id: str | None,
        satellite_id: str | None,
        device_id: str | None,
    ) -> None:
        self._expectations.pop(
            self._key(conversation_id, satellite_id, device_id),
            None,
        )

    def evaluate(
        self,
        *,
        transcript: str,
        conversation_id: str | None,
        satellite_id: str | None,
        device_id: str | None,
        now: float | None = None,
    ) -> GateDecision:
        timestamp = time.monotonic() if now is None else float(now)
        key = self._key(conversation_id, satellite_id, device_id)
        self._purge_expired(timestamp, keep_key=key)
        expectation = self._expectations.pop(key, None)
        if expectation is None:
            return GateDecision("accept", "not_a_gated_follow_up", 1.0, None)

        if timestamp > expectation.expires_at:
            return GateDecision("reject", "follow_up_window_expired", 0.99, expectation.kind)

        text = normalise_transcript(transcript)
        words = text.split()
        word_count = len(words)
        if not text:
            return GateDecision("reject", "empty_transcript", 1.0, expectation.kind)
        if _WAKE_ONLY_RE.fullmatch(text):
            return GateDecision("reject", "wake_word_only", 0.98, expectation.kind)
        if _FILLER_RE.fullmatch(text):
            return GateDecision("reject", "speech_filler_only", 0.96, expectation.kind)
        if _looks_like_self_echo(text, expectation.assistant_speech):
            return GateDecision("reject", "assistant_self_echo", 0.97, expectation.kind)

        direct = _DIRECT_RE.search(text) is not None
        action_request = _ACTION_RE.search(text) is not None
        question_start = _QUESTION_START_RE.search(text) is not None
        addressed = bool(re.match(r"^(?:hey\s+)?jarvis\b", text))
        background = _BACKGROUND_RE.search(text) is not None

        if expectation.kind == "yes_no":
            if _YES_RE.fullmatch(text) or _NO_RE.fullmatch(text):
                return GateDecision("accept", "expected_confirmation", 0.99, expectation.kind)
            if action_request or addressed:
                return GateDecision("accept", "explicit_new_request", 0.90, expectation.kind)
            return GateDecision("reject", "unrelated_to_confirmation", 0.90, expectation.kind)

        if expectation.kind == "choice":
            if action_request or addressed:
                return GateDecision("accept", "explicit_new_request", 0.90, expectation.kind)
            if question_start:
                return GateDecision("reject", "unrelated_question", 0.86, expectation.kind)
            if 1 <= word_count <= 8 and not background:
                return GateDecision("accept", "short_choice_answer", 0.88, expectation.kind)
            return GateDecision("reject", "unlikely_choice_answer", 0.84, expectation.kind)

        if expectation.kind == "slot":
            if action_request or addressed:
                return GateDecision("accept", "explicit_answer_or_request", 0.90, expectation.kind)
            if question_start:
                return GateDecision("reject", "unrelated_question", 0.86, expectation.kind)
            if 1 <= word_count <= 12 and not background:
                return GateDecision("accept", "short_slot_answer", 0.82, expectation.kind)
            return GateDecision("reject", "unlikely_slot_answer", 0.82, expectation.kind)

        if expectation.kind == "dictation":
            if addressed or action_request:
                return GateDecision("accept", "explicit_dictation_request", 0.88, expectation.kind)
            if 1 <= word_count <= 40 and not background and not question_start:
                return GateDecision("accept", "expected_dictation", 0.80, expectation.kind)
            return GateDecision("reject", "unlikely_dictation", 0.80, expectation.kind)

        # Open follow-ups are deliberately conservative. A direct command/question
        # is accepted, as is one concise natural reply. Long conversational speech is
        # much more likely to be television or another person in the room.
        if direct or addressed:
            return GateDecision("accept", "explicit_open_follow_up", 0.88, expectation.kind)
        if 1 <= word_count <= 10 and not background:
            return GateDecision("accept", "concise_open_follow_up", 0.72, expectation.kind)
        return GateDecision("reject", "probable_ambient_chatter", 0.78, expectation.kind)
