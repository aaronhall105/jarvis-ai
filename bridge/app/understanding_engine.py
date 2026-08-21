from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from app.registry import RegistryEngine
from app.user_context import UserContext

logger = logging.getLogger("jarvis-core.understanding")


_HOME_WORDS = {
    "light", "lights", "lamp", "lamps", "switch", "switches", "plug", "plugs",
    "tv", "television", "speaker", "speakers", "media", "volume", "battery",
    "temperature", "thermostat", "camera", "door", "window", "lock", "alarm",
    "home", "away", "room", "living", "bedroom", "bathroom", "kitchen",
    "hallway", "phone", "watch", "washing", "machine", "oven", "fridge",
    "netflix", "youtube", "iplayer", "prime", "automation", "script", "routine",
    "notify", "notification", "announce", "announcement", "sensor", "occupancy",
}

_ROUTER_WORDS = {
    "where", "what", "which", "who", "when", "why", "how", "is", "are", "was",
    "were", "do", "does", "did", "can", "could", "would", "should", "will",
    "turn", "switch", "power", "open", "close", "start", "stop", "pause", "resume",
    "play", "mute", "unmute", "set", "check", "tell", "show", "send", "run",
    "create", "change", "edit", "update", "remember", "forget", "on", "off",
    "up", "down", "my", "me", "mine", "her", "his", "their", "it", "them",
    "this", "that", "there", "here", "now", "today", "tomorrow",
}

_MANUAL_PHRASES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"\bwhere\s+us\b", re.I), "where is", 0.99),
    (re.compile(r"\bwhat\s+us\b", re.I), "what is", 0.99),
    (re.compile(r"\bwho\s+us\b", re.I), "who is", 0.99),
    (re.compile(r"\bwhy\s+us\b", re.I), "why is", 0.98),
    (re.compile(r"\bwhere\s+are\s+is\b", re.I), "where is", 0.98),
    (re.compile(r"\bwhat\s+are\s+is\b", re.I), "what is", 0.98),
    (re.compile(r"\btun\b", re.I), "turn", 0.97),
    (re.compile(r"\blites?\b", re.I), "lights", 0.97),
    (re.compile(r"\bswich\b", re.I), "switch", 0.96),
    (re.compile(r"\bturn\s+of\b", re.I), "turn off", 0.98),
    (re.compile(r"\bswitch\s+of\b", re.I), "switch off", 0.98),
    (re.compile(r"\bput\s+of\b", re.I), "put off", 0.95),
    (re.compile(r"\blivin(?:g)?\s*room\b", re.I), "living room", 0.98),
    (re.compile(r"\blivingroom\b", re.I), "living room", 0.99),
    (re.compile(r"\bbed\s+room\b", re.I), "bedroom", 0.97),
    (re.compile(r"\bfrontdoor\b", re.I), "front door", 0.98),
    (re.compile(r"\bi\s*player\b", re.I), "iPlayer", 0.98),
    (re.compile(r"\bplay\s+station\b", re.I), "PlayStation", 0.97),
    (re.compile(r"\btele\b", re.I), "TV", 0.91),
)

_PROTECTED_TOKENS = {
    "yes", "no", "ok", "okay", "thanks", "thank", "please", "poo",
    "samba", "jarvis", "alexa", "google", "siri", "homeassistant",
}

_WRITE_ACTION_RE = re.compile(
    r"\b(?:turn|switch|power|open|close|lock|unlock|start|stop|pause|resume|play|"
    r"mute|unmute|set|send|notify|announce|run|create|change|edit|update|delete)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class UnderstandingResult:
    original_text: str
    interpreted_text: str
    confidence: float
    corrections: tuple[str, ...]
    house_relevant: bool
    needs_clarification: bool = False
    clarification: str | None = None

    @property
    def changed(self) -> bool:
        return self.original_text != self.interpreted_text

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UnderstandingEngine:
    """Conservative typo/STT repair grounded in the actual Home Assistant registry."""

    def __init__(self, registry: RegistryEngine) -> None:
        self.registry = registry
        self._lexicon_stamp: str | None = None
        self._lexicon_cache: tuple[set[str], set[str], set[str]] | None = None

    @staticmethod
    def is_write_action(text: str) -> bool:
        return bool(_WRITE_ACTION_RE.search(text or ""))

    @staticmethod
    def _clean(value: str) -> str:
        value = unicodedata.normalize("NFKC", value)
        value = value.replace("’", "'").replace("`", "'")
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @staticmethod
    def _display_candidate(candidate: str) -> str:
        lowered = candidate.casefold()
        if lowered == "tv":
            return "TV"
        if lowered == "iplayer":
            return "iPlayer"
        if lowered == "playstation":
            return "PlayStation"
        if lowered in {"aaron", "amber", "jarvis"}:
            return lowered.title()
        return candidate

    async def _registry_lexicon(self) -> tuple[set[str], set[str], set[str]]:
        snapshot = await self.registry.ensure_loaded()
        stamp = str(snapshot.refreshed_at or "")
        if self._lexicon_cache is not None and stamp == self._lexicon_stamp:
            return self._lexicon_cache

        phrases: set[str] = {
            "living room", "front door", "home assistant", "prime video",
            "bbc iplayer", "android tv", "smart watch", "washing machine",
            "Aaron's phone", "Amber's phone", "Aaron", "Amber", "Jarvis",
        }
        anchor_tokens: set[str] = {"aaron", "amber", "jarvis"}
        device_tokens: set[str] = set()

        for area in snapshot.areas:
            values = [str(area.get("name") or "").strip()]
            values.extend(str(alias).strip() for alias in (area.get("aliases") or []))
            for value in values:
                if not value:
                    continue
                phrases.add(value)
                for token in re.findall(r"[A-Za-z][A-Za-z0-9']+", value):
                    normalised = token.casefold().strip("'")
                    if len(normalised) >= 4:
                        anchor_tokens.add(normalised)

        for device in snapshot.devices:
            for key in ("name_by_user", "name", "model"):
                value = str(device.get(key) or "").strip()
                if not value:
                    continue
                phrases.add(value)
                for token in re.findall(r"[A-Za-z][A-Za-z0-9']+", value):
                    normalised = token.casefold().strip("'")
                    if len(normalised) >= 4:
                        device_tokens.add(normalised)

        for entity in snapshot.entities:
            domain = str(entity.get("entity_id") or "").split(".", 1)[0]
            for key in ("name", "original_name", "entity_id"):
                value = str(entity.get(key) or "").strip()
                if not value:
                    continue
                if key == "entity_id" and "." in value:
                    value = value.split(".", 1)[1].replace("_", " ")
                phrases.add(value)
                for token in re.findall(r"[A-Za-z][A-Za-z0-9']+", value):
                    normalised = token.casefold().strip("'")
                    if len(normalised) < 4:
                        continue
                    if domain == "person":
                        anchor_tokens.add(normalised)
                    else:
                        device_tokens.add(normalised)

        for state in snapshot.states:
            entity_id = str(state.get("entity_id") or "")
            if not entity_id.startswith("person."):
                continue
            friendly = str((state.get("attributes") or {}).get("friendly_name") or "").strip()
            if friendly:
                phrases.add(friendly)
                for token in re.findall(r"[A-Za-z][A-Za-z0-9']+", friendly):
                    if len(token) >= 4:
                        anchor_tokens.add(token.casefold())

        self._lexicon_stamp = stamp
        self._lexicon_cache = (phrases, anchor_tokens, device_tokens)
        return self._lexicon_cache

    @staticmethod
    def _adjacent_transposition(left: str, right: str) -> bool:
        if len(left) != len(right):
            return False
        differences = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        if len(differences) != 2 or differences[1] != differences[0] + 1:
            return False
        first, second = differences
        return left[first] == right[second] and left[second] == right[first]

    @classmethod
    def _closest(cls, token: str, candidates: set[str]) -> tuple[str | None, float, bool]:
        best: str | None = None
        best_score = 0.0
        second_score = 0.0
        for candidate in candidates:
            if abs(len(candidate) - len(token)) > max(3, len(candidate) // 2):
                continue
            score = difflib.SequenceMatcher(None, token, candidate).ratio()
            if cls._adjacent_transposition(token, candidate):
                score = max(score, 0.98)
            if score > best_score:
                second_score = best_score
                best_score = score
                best = candidate
            elif score > second_score:
                second_score = score
        ambiguous = best_score - second_score < 0.025 and second_score >= 0.82
        return best, best_score, ambiguous

    @staticmethod
    def _restore_spacing(text: str) -> str:
        text = re.sub(r"\s+([,.;!?])", r"\1", text)
        text = re.sub(r"([,.;!?])(\S)", r"\1 \2", text)
        return re.sub(r"\s+", " ", text).strip()

    async def interpret(
        self,
        text: str,
        history: Sequence[dict[str, str]],
        actor: UserContext,
    ) -> UnderstandingResult:
        original = self._clean(text)
        interpreted = original
        corrections: list[str] = []
        confidence_scores: list[float] = []

        for pattern, replacement, score in _MANUAL_PHRASES:
            updated, count = pattern.subn(replacement, interpreted)
            if count:
                corrections.append(f"{interpreted!r} → {updated!r}")
                confidence_scores.append(score)
                interpreted = updated

        recent_text = " ".join(str(item.get("content", "")) for item in history[-8:])
        if re.search(r"\b(?:she|her|hers)\b", interpreted, re.I) and re.search(
            r"\bAmber\b", recent_text, re.I
        ):
            updated = re.sub(r"\bshe\b", "Amber", interpreted, flags=re.I)
            if updated != interpreted:
                corrections.append(f"{interpreted!r} → {updated!r}")
                confidence_scores.append(0.96)
                interpreted = updated
        if re.search(r"\b(?:he|him|his)\b", interpreted, re.I) and re.search(
            r"\bAaron\b", recent_text, re.I
        ):
            updated = re.sub(r"\bhe\b", "Aaron", interpreted, flags=re.I)
            if updated != interpreted:
                corrections.append(f"{interpreted!r} → {updated!r}")
                confidence_scores.append(0.96)
                interpreted = updated

        phrases, anchor_tokens, device_tokens = await self._registry_lexicon()
        phrase_keys = {self._clean(value).casefold() for value in phrases if value.strip()}

        parts = re.findall(r"[A-Za-z0-9']+|[^A-Za-z0-9']+", interpreted)
        write_action = bool(_WRITE_ACTION_RE.search(interpreted))
        original_words = set(re.findall(r"[a-z]+", interpreted.casefold()))
        preliminary_home = bool(original_words & _HOME_WORDS or write_action)
        candidates = set(_HOME_WORDS) | set(_ROUTER_WORDS) | set(anchor_tokens)
        if preliminary_home:
            candidates.update(device_tokens)
        ambiguous_corrections: list[tuple[str, str, float]] = []

        for index, part in enumerate(parts):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9']*", part):
                continue
            token = part.casefold().strip("'")
            if token in candidates or token in _PROTECTED_TOKENS or len(token) < 4:
                continue

            best, score, ambiguous = self._closest(token, candidates)
            threshold = 0.89 if write_action else 0.85
            if best is None or score < threshold:
                continue
            if ambiguous:
                ambiguous_corrections.append((part, best, score))
                continue

            replacement = self._display_candidate(best)
            if part[:1].isupper() and replacement not in {"TV", "iPlayer", "PlayStation"}:
                replacement = replacement[:1].upper() + replacement[1:]
            parts[index] = replacement
            corrections.append(f"{part!r} → {replacement!r}")
            confidence_scores.append(score)

        interpreted = self._restore_spacing("".join(parts))

        lowered = interpreted.casefold()
        recent = " ".join(str(item.get("content", "")) for item in history[-6:]).casefold()
        house_relevant = bool(
            set(re.findall(r"[a-z]+", lowered)) & _HOME_WORDS
            or any(phrase in lowered for phrase in phrase_keys if len(phrase) >= 4)
            or (re.search(r"\b(?:it|them|that|those|there|she|he)\b", lowered) and
                bool(set(re.findall(r"[a-z]+", recent)) & _HOME_WORDS))
        )

        confidence = min(confidence_scores) if confidence_scores else 1.0
        needs_clarification = False
        clarification: str | None = None

        if ambiguous_corrections and write_action:
            original_token, best, score = ambiguous_corrections[0]
            needs_clarification = True
            clarification = (
                f"Did you mean {self._display_candidate(best)} when you said "
                f"{original_token!r}?"
            )
            confidence = min(confidence, score)

        # Never silently reinterpret a low-confidence write action. Safe questions can
        # still flow to the model, which may ask one natural clarification if needed.
        if write_action and corrections and confidence < 0.86:
            needs_clarification = True
            clarification = clarification or (
                f"Did you mean: “{interpreted.rstrip('.?!')}”?"
            )

        logger.info(
            "Understanding original=%r interpreted=%r confidence=%.3f corrections=%s "
            "house_relevant=%s user=%s",
            original,
            interpreted,
            confidence,
            len(corrections),
            house_relevant,
            actor.user_key,
        )

        return UnderstandingResult(
            original_text=original,
            interpreted_text=interpreted,
            confidence=round(confidence, 3),
            corrections=tuple(corrections),
            house_relevant=house_relevant,
            needs_clarification=needs_clarification,
            clarification=clarification,
        )
