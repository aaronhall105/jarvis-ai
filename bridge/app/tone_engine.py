from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Sequence


@dataclass(frozen=True)
class ToneProfile:
    """Best-effort conversational tone inferred from the current turn."""

    label: str = "neutral"
    confidence: float = 0.0
    intensity: str = "low"
    cues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def model_guidance(self) -> str:
        guidance = {
            "frustrated": (
                "The user sounds frustrated. Acknowledge the problem briefly, take "
                "responsibility for any Jarvis mistake, avoid cheerfulness, and move "
                "straight to a useful answer or fix."
            ),
            "angry": (
                "The user sounds angry. Stay calm and respectful, do not scold them "
                "for swearing, acknowledge the failure plainly, and focus on fixing it."
            ),
            "happy": (
                "The user sounds pleased or excited. Match that warmth briefly without "
                "becoming overenthusiastic or verbose."
            ),
            "playful": (
                "The user sounds playful or joking. Light humour is welcome when it "
                "does not distract from accuracy or a Home Assistant action."
            ),
            "sad": (
                "The user sounds upset or low. Respond gently and supportively without "
                "claiming emotions or becoming overly sentimental."
            ),
        }
        return guidance.get(
            self.label,
            (
                "Use a warm, familiar and naturally varied British conversational tone. "
                "Be candid and helpful, with occasional understated wit when the subject "
                "is low stakes. Avoid stock assistant phrases."
            ),
        )


class ToneEngine:
    """Lightweight tone detection and varied progress acknowledgement selection."""

    _ANGER = re.compile(
        r"\b(?:what\s+the\s+fuck|for\s+fuck(?:'s)?\s+sake|fuck(?:ing)?|"
        r"bullshit|piss(?:ed)?\s+off|stupid|useless|ridiculous)\b|[!?]{3,}",
        re.I,
    )
    _FRUSTRATED = re.compile(
        r"\b(?:not\s+working|doesn['’]?t\s+work|still\s+wrong|again|"
        r"keep(?:s)?\s+(?:asking|failing|doing)|why\s+(?:has|did|does)|"
        r"i\s+already\s+(?:said|told)|fed\s+up|annoy(?:ed|ing)|frustrat(?:ed|ing)|"
        r"come\s+on|seriously)\b",
        re.I,
    )
    _HAPPY = re.compile(
        r"\b(?:brilliant|amazing|excellent|perfect|great|nice|love\s+it|"
        r"that\s+works|it\s+works|working|happy|excited|spot\s+on)\b|[!]{2,}",
        re.I,
    )
    _PLAYFUL = re.compile(
        r"\b(?:lol|lmao|haha+|hehe+|joking|only\s+joking|banter)\b|"
        r"(?:😂|🤣|😄|😆|😉)",
        re.I,
    )
    _SAD = re.compile(
        r"\b(?:sad|upset|down|miserable|heartbroken|worried|anxious|"
        r"not\s+okay|feel(?:ing)?\s+low)\b|(?:😢|😭|☹️|😞)",
        re.I,
    )

    _SHORT_ACK = re.compile(
        r"^\s*(?:yes|yeah|yep|no|nope|okay|ok|thanks|thank\s+you|"
        r"cancel|never\s+mind|nevermind|do\s+it|go\s+ahead)\s*[.!?]*\s*$",
        re.I,
    )
    _TASK_COMMAND = re.compile(
        r"\b(?:scheduled\s+(?:tasks?|actions?)|task\s+history|"
        r"show\s+task|cancel\s+(?:task|my\s+last)|repeat\s+task|"
        r"delete\s+(?:task\s+)?history|clear\s+(?:task\s+)?history|"
        r"what\s+happened\s+to\s+task)\b|"
        r"\b(?:in|after)\s+(?:\d+|one|two|three|four|five|ten|half|a\s+quarter)\s+"
        r"(?:seconds?|minutes?|hours?|days?)\b|"
        r"\b(?:tomorrow\s+at|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
        re.I,
    )
    _SIMPLE_CONTROL = re.compile(
        r"^\s*(?:please\s+)?(?:turn|switch|power|open|close|launch|start|stop|"
        r"pause|resume|play|mute|unmute|send|notify|announce|broadcast|run)\b",
        re.I,
    )
    _STATE_QUERY = re.compile(
        r"\b(?:where|battery|state|status|temperature|humidity|running|finished|"
        r"wash|washing|home|away|on|off|open|closed|playing|paused|sensor|device)\b",
        re.I,
    )
    _WEATHER_ENERGY_QUERY = re.compile(
        r"\b(?:weather|forecast|rain|temperature outside|wind|energy|electricity|"
        r"power usage|consumption|cost|octopus|tariff|rate)\b",
        re.I,
    )
    _MEMORY_QUERY = re.compile(
        r"\b(?:remember|memory|saved|health\s+condition|allerg|intoleran|"
        r"birthday|favourite|favorite|preference|what did i tell you)\b",
        re.I,
    )
    _CONTROL_QUERY = re.compile(
        r"\b(?:turn|switch|open|close|send|notify|run|start|stop|set|launch|"
        r"pause|resume|play|mute|unmute|announce|broadcast)\b",
        re.I,
    )

    _PHRASES: dict[str, tuple[str, ...]] = {
        "frustrated": (
            "You’re right — let me check that properly.",
            "Let me get to the bottom of that.",
            "I’m checking what went wrong.",
            "Let me sort that out.",
            "I’m taking another look.",
            "Let me verify that properly.",
            "I’m checking the details now.",
            "Let me trace that through.",
            "I’m looking into the problem.",
            "Give me a second to check that.",
            "Let me make sure this time.",
            "I’m checking it carefully.",
        ),
        "memory": (
            "Let me check what I remember.",
            "I’m looking through the saved details.",
            "Checking the relevant memory.",
            "Let me look that up.",
            "I’m checking the notes.",
            "Let me search my memory.",
            "I’m finding the relevant detail.",
            "Checking what you told me.",
            "Let me pull up that detail.",
            "I’m looking through the saved context.",
            "Let me check the personal context.",
            "I’m reviewing the saved information.",
            "Checking the memory records.",
            "Let me find that detail.",
            "I’m looking for the right memory.",
        ),
        "state": (
            "Let me check that.",
            "Checking Home Assistant now.",
            "I’m checking the current state.",
            "Let me look at the live status.",
            "Checking the device now.",
            "I’m getting the latest state.",
            "Let me verify that.",
            "I’m checking the house.",
            "Looking up the live reading.",
            "Let me check the sensor.",
            "I’m pulling the current status.",
            "Checking the latest reading.",
            "Let me see what Home Assistant reports.",
            "I’m checking that device.",
            "Getting the live state now.",
            "Let me confirm the current status.",
            "I’m looking at the latest data.",
            "Checking that for you.",
        ),
        "weather_energy": (
            "Let me check the latest figures.",
            "I’m pulling the current data.",
            "Checking the latest reading now.",
            "Let me look at the live figures.",
            "I’m checking the current conditions.",
            "Let me get the latest update.",
            "I’m looking at the newest data.",
            "Checking the current numbers.",
            "Let me pull that information together.",
            "I’m checking the latest rates.",
            "Let me see what the data says.",
            "I’m getting the current figures.",
        ),
        "control": (
            "On it.",
            "I’m handling that now.",
            "Let me check the target first.",
            "Working on that.",
            "I’m setting that up.",
            "Let me verify the device.",
            "I’m sorting that now.",
            "I’m checking before I act.",
            "Let me make sure I’ve got the right device.",
            "I’m taking care of that.",
            "Checking the target now.",
            "Let me handle that.",
        ),
        "playful": (
            "Give me a second.",
            "Let me work my magic.",
            "I’m on the case.",
            "Let me have a look.",
            "Working it out now.",
            "Give me a moment to investigate.",
            "Let me see what I can find.",
            "I’m digging into that.",
        ),
        "happy": (
            "Absolutely — let me check.",
            "Of course — I’m on it.",
            "Certainly — give me a second.",
            "Right away — let me look.",
            "Sure — I’m checking now.",
            "Absolutely — I’ll look into that.",
            "No problem — let me check.",
            "Of course — I’m working that out.",
        ),
        "general": (
            "Give me a second.",
            "Let me think that through.",
            "I’m working that out.",
            "Let me look into that.",
            "I’m checking the details.",
            "Give me a moment to work that out.",
            "Let me put that together.",
            "I’m finding the best answer.",
            "Let me reason that through.",
            "I’m looking into it.",
            "Let me check a few things.",
            "I’m working through that.",
            "Give me a moment.",
            "Let me work that out.",
            "I’m checking what matters here.",
            "Let me make sense of that.",
            "I’m putting the pieces together.",
            "Let me take a closer look.",
            "I’m getting that together.",
            "Let me check properly.",
        ),
    }

    def __init__(self) -> None:
        self._rng = random.SystemRandom()
        self._recent: dict[str, deque[str]] = {}
        self._selection_lock = Lock()

    @staticmethod
    def _recent_assistant_failure(history: Sequence[dict[str, str]]) -> bool:
        for item in reversed(history[-4:]):
            if str(item.get("role") or "").casefold() != "assistant":
                continue
            content = str(item.get("content") or "").casefold()
            return any(
                phrase in content
                for phrase in (
                    "could not determine",
                    "couldn’t",
                    "could not",
                    "failed",
                    "not found",
                    "which device do you mean",
                )
            )
        return False

    def analyse(
        self,
        text: str,
        history: Sequence[dict[str, str]] = (),
    ) -> ToneProfile:
        value = re.sub(r"\s+", " ", text).strip()
        cues: list[str] = []
        scores = {
            "angry": 0.0,
            "frustrated": 0.0,
            "happy": 0.0,
            "playful": 0.0,
            "sad": 0.0,
        }

        for label, pattern, weight in (
            ("angry", self._ANGER, 0.92),
            ("frustrated", self._FRUSTRATED, 0.78),
            ("happy", self._HAPPY, 0.74),
            ("playful", self._PLAYFUL, 0.82),
            ("sad", self._SAD, 0.82),
        ):
            match = pattern.search(value)
            if match:
                scores[label] += weight
                cues.append(match.group(0)[:60])

        if value.isupper() and len(value) >= 8:
            scores["angry"] += 0.25
            cues.append("capital letters")

        if self._recent_assistant_failure(history):
            if scores["angry"] > 0 or scores["frustrated"] > 0:
                scores["frustrated"] += 0.18
                cues.append("follows a failed Jarvis reply")

        if scores["playful"] and scores["angry"]:
            scores["playful"] += 0.10
            scores["angry"] -= 0.12

        label, score = max(scores.items(), key=lambda item: item[1])
        score = max(0.0, min(score, 0.99))
        if score < 0.45:
            return ToneProfile()

        if score >= 0.85:
            intensity = "high"
        elif score >= 0.65:
            intensity = "medium"
        else:
            intensity = "low"

        if label == "angry" and not self._ANGER.search(value):
            label = "frustrated"

        return ToneProfile(
            label=label,
            confidence=round(score, 2),
            intensity=intensity,
            cues=tuple(cues[:4]),
        )

    def should_emit_progress(self, text: str, profile: ToneProfile) -> bool:
        """Return whether a slow voice request merits an interim acknowledgement."""

        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if not value or self._SHORT_ACK.match(value):
            return False
        if self._TASK_COMMAND.search(value):
            return False
        words = value.split()
        if self._SIMPLE_CONTROL.match(value) and len(words) <= 16:
            return False
        if profile.label in {"angry", "frustrated"}:
            return True
        return len(words) >= 4

    def progress_delay_seconds(self, text: str, profile: ToneProfile) -> float:
        """Return how long Jarvis should wait before speaking a filler phrase."""

        value = text.casefold()
        if profile.label in {"angry", "frustrated"}:
            return 0.45
        if self._STATE_QUERY.search(value) or self._MEMORY_QUERY.search(value):
            return 0.65
        if self._WEATHER_ENERGY_QUERY.search(value):
            return 0.70
        if self._CONTROL_QUERY.search(value):
            return 0.75
        return 0.80

    def _choose(self, group: str) -> str:
        phrases = self._PHRASES[group]
        with self._selection_lock:
            recent = self._recent.setdefault(
                group,
                deque(maxlen=max(1, len(phrases) - 1)),
            )
            available = [phrase for phrase in phrases if phrase not in recent]
            phrase = self._rng.choice(available or list(phrases))
            recent.append(phrase)
            return phrase

    def progress_phrase(self, text: str, profile: ToneProfile) -> str:
        """Choose a short request-aware phrase without recent repetition."""

        value = text.casefold()
        if profile.label in {"angry", "frustrated"}:
            return self._choose("frustrated")
        if self._MEMORY_QUERY.search(value):
            return self._choose("memory")
        if self._WEATHER_ENERGY_QUERY.search(value):
            return self._choose("weather_energy")
        if self._STATE_QUERY.search(value):
            return self._choose("state")
        if self._CONTROL_QUERY.search(value):
            return self._choose("control")
        if profile.label == "playful":
            return self._choose("playful")
        if profile.label == "happy":
            return self._choose("happy")
        return self._choose("general")

    @property
    def progress_phrase_count(self) -> int:
        """Return total configured phrase count for diagnostics and tests."""

        return sum(len(phrases) for phrases in self._PHRASES.values())
