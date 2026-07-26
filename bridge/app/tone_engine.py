from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
            "Use a calm, natural and direct conversational tone.",
        )


class ToneEngine:
    """
    Lightweight tone detection for response-style adaptation.

    This is deliberately not a diagnosis or a claim about the user's true emotion.
    It detects surface cues in the current message and uses recent turns only as a
    weak tie-breaker.
    """

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

        # Laughter can soften profanity into playful frustration, but explicit
        # anger still wins when it is substantially stronger.
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

        # Prefer frustration over anger for ordinary product complaints unless
        # explicit strong profanity/hostility is present.
        if label == "angry" and not self._ANGER.search(value):
            label = "frustrated"

        return ToneProfile(
            label=label,
            confidence=round(score, 2),
            intensity=intensity,
            cues=tuple(cues[:4]),
        )

    def progress_phrase(self, text: str, profile: ToneProfile) -> str:
        value = text.casefold()
        if profile.label in {"angry", "frustrated"}:
            return "You're right — let me check that properly."
        if re.search(r"\b(?:where|battery|state|status|running|finished|wash|washing|on|off)\b", value):
            return "Let me check that."
        if re.search(r"\b(?:turn|switch|open|close|send|notify|run|start|stop|set)\b", value):
            return "On it."
        if profile.label == "playful":
            return "Haha, one moment."
        if profile.label == "happy":
            return "Absolutely — one moment."
        return "One moment."
