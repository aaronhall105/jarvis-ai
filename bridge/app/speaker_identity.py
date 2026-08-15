from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://jarvis-speaker-verifier:8091"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on", "enabled"}


def normalise_identity_text(value: Any) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9'\s-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def speaker_id_from_name(value: Any) -> str:
    text = normalise_identity_text(value)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:64]


def extract_display_name(value: Any) -> str:
    raw = " ".join(str(value or "").strip().split())
    lowered = raw.casefold()
    for prefix in (
        "my name is ", "i am ", "i'm ", "im ", "this is ",
        "it's ", "its ", "call me ",
    ):
        if lowered.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    raw = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]+", "", raw).strip(" -'")
    words = [word for word in raw.split() if word]
    if not words or len(words) > 4:
        return ""
    name = " ".join(words)
    name = " ".join(part[:1].upper() + part[1:] for part in name.split())
    return name if 2 <= len(name) <= 80 else ""


def phrase_match_score(actual: Any, expected: Any) -> float:
    left = normalise_identity_text(actual)
    right = normalise_identity_text(expected)
    if not left or not right:
        return 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_words = set(left.split())
    right_words = set(right.split())
    overlap = len(left_words & right_words) / max(1, len(right_words))
    return max(sequence, overlap)


def parse_speaker_management_command(value: Any) -> tuple[str, str] | None:
    text = normalise_identity_text(value)
    if not text:
        return None
    if text in {
        "cancel voice enrollment", "cancel voice enrolment", "cancel enrollment",
        "cancel enrolment", "stop voice enrollment", "stop voice enrolment",
    }:
        return ("cancel", "")
    if (
        "who do you recognise" in text or "who do you recognize" in text
        or "whose voices do you know" in text or "list voice profiles" in text
        or "list speaker profiles" in text
    ):
        return ("list", "")
    stripped = text
    for prefix in ("hey jarvis ", "jarvis "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break
    if stripped in {
        "learn a new voice", "learn new voice", "add a new voice", "add new voice",
        "add a new user", "add new user", "register a new voice", "register new voice",
        "learn my voice", "register my voice",
    }:
        return ("enroll", "")
    if text in {"relearn my voice", "re learn my voice", "update my voice profile"}:
        return ("relearn", "")
    for pattern in (
        r"^(?:jarvis )?relearn (.+?)(?:'s)? voice$",
        r"^(?:jarvis )?re learn (.+?)(?:'s)? voice$",
        r"^(?:jarvis )?update (.+?)(?:'s)? voice profile$",
    ):
        match = re.match(pattern, text)
        if match:
            return ("relearn", extract_display_name(match.group(1)))
    for pattern in (
        r"^(?:jarvis )?forget (.+?)(?:'s)? voice$",
        r"^(?:jarvis )?delete (.+?)(?:'s)? voice profile$",
        r"^(?:jarvis )?remove (.+?)(?:'s)? voice profile$",
    ):
        match = re.match(pattern, text)
        if match:
            return ("forget", extract_display_name(match.group(1)))
    return None


QUALITY_MESSAGES = {
    "empty_audio": "I didn't get enough audio. Please say the sentence again.",
    "too_short": "That was a little too short. Please say the whole sentence again.",
    "too_long": "That sample was too long. Please just say the sentence I gave you.",
    "too_quiet": "That was too quiet. Please move a little closer and say it again.",
    "clipping": "That was too loud for a clean sample. Please move slightly back and try again.",
    "voice_inconsistent": "That sample sounded different from the others. Please make sure only you are speaking and try again.",
    "enrollment_inconsistent": "Those samples were not consistent enough to build a reliable voice profile. We'll need to start the voice enrollment again.",
}


def friendly_quality_message(reason: Any) -> str:
    return QUALITY_MESSAGES.get(
        str(reason or "").strip(),
        "I couldn't use that voice sample reliably. Please say the sentence again.",
    )


class SpeakerIdentityError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


@dataclass(frozen=True)
class SpeakerIdentityClient:
    enabled: bool
    base_url: str
    timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "SpeakerIdentityClient":
        return cls(
            enabled=env_bool("JARVIS_SPEAKER_IDENTITY_ENABLED", False),
            base_url=(os.getenv("JARVIS_SPEAKER_IDENTITY_URL", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL),
            timeout_seconds=float(os.getenv("JARVIS_SPEAKER_IDENTITY_TIMEOUT_SECONDS", "3.0")),
        )

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
                 pcm: bytes | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise SpeakerIdentityError("speaker_identity_disabled")
        url = self.base_url + path
        if query:
            url += "?" + urlencode({key: str(value) for key, value in query.items()})
        body: bytes | None = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif pcm is not None:
            body = pcm
            headers["Content-Type"] = "application/octet-stream"
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.load(response)
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                error_payload = {}
            message = str(error_payload.get("message") or error_payload.get("error") or f"speaker_identity_http_{exc.code}")
            raise SpeakerIdentityError(message, status=exc.code, payload=error_payload) from exc
        except Exception as exc:
            raise SpeakerIdentityError(f"speaker_identity_unavailable: {exc}") from exc
        if not isinstance(result, dict):
            raise SpeakerIdentityError("speaker_identity_invalid_response")
        return result

    def health(self) -> dict[str, Any]:
        return self._request("/health")

    def list_speakers(self) -> dict[str, Any]:
        return self._request("/speakers")

    def identify(self, pcm: bytes) -> dict[str, Any]:
        return self._request("/identify", method="POST", pcm=pcm)

    def start_enrollment(self, *, speaker_id: str, display_name: str, is_admin: bool, replace: bool) -> dict[str, Any]:
        return self._request(
            "/enroll/start", method="POST",
            payload={"speaker_id": speaker_id, "display_name": display_name, "is_admin": bool(is_admin), "replace": bool(replace)},
        )

    def add_sample(self, *, session_id: str, phrase_index: int, pcm: bytes) -> dict[str, Any]:
        try:
            return self._request(
                "/enroll/sample", method="POST", pcm=pcm,
                query={"session_id": session_id, "phrase_index": phrase_index},
            )
        except SpeakerIdentityError as exc:
            if exc.status == 422 and exc.payload:
                return exc.payload
            raise

    def finish_enrollment(self, session_id: str) -> dict[str, Any]:
        try:
            return self._request("/enroll/finish", method="POST", payload={"session_id": session_id})
        except SpeakerIdentityError as exc:
            if exc.status == 422 and exc.payload:
                return exc.payload
            raise

    def cancel_enrollment(self, session_id: str) -> dict[str, Any]:
        return self._request("/enroll/cancel", method="POST", payload={"session_id": session_id})

    def delete_speaker(self, speaker_id: str) -> dict[str, Any]:
        return self._request("/speakers/delete", method="POST", payload={"speaker_id": speaker_id})

# Realtime orchestration lives here so realtime_voice.py only needs a small,
# auditable integration hook.
import asyncio
import time
from collections.abc import Awaitable, Callable

EventSender = Callable[[dict[str, Any]], Awaitable[None]]
Speaker = Callable[[str], Awaitable[None]]


@dataclass
class SpeakerIdentityRuntime:
    client: SpeakerIdentityClient
    configured_user_id: str
    configured_user_is_admin: bool

    def set_unknown(self, metadata: dict[str, Any], reason: str) -> None:
        metadata.update(
            user_id="guest",
            user_name="Guest",
            user_is_admin=False,
            speaker_id="unknown",
            speaker_name="Unknown",
            speaker_recognized=False,
            speaker_confidence=None,
            speaker_identity_reason=reason,
        )

    def apply(self, metadata: dict[str, Any], result: dict[str, Any], source: str = "voice_id") -> bool:
        if not bool(result.get("recognized")):
            self.set_unknown(metadata, str(result.get("reason") or "unknown"))
            return False
        raw = result.get("speaker")
        profile = raw if isinstance(raw, dict) else {}
        speaker_id = speaker_id_from_name(profile.get("speaker_id") or profile.get("display_name"))
        name = extract_display_name(profile.get("display_name") or speaker_id)
        if not speaker_id or not name:
            self.set_unknown(metadata, "invalid_profile")
            return False
        admin = bool(
            profile.get("is_admin")
            and self.configured_user_is_admin
            and speaker_id == self.configured_user_id
        )
        metadata.update(
            user_id=speaker_id,
            user_name=name,
            user_is_admin=admin,
            speaker_id=speaker_id,
            speaker_name=name,
            speaker_recognized=True,
            speaker_confidence=result.get("score"),
            speaker_match_margin=result.get("margin"),
            speaker_identity_source=source,
            speaker_identity_reason=str(result.get("reason") or "recognized"),
        )
        return True

    async def identify(self, pcm: bytes, metadata: dict[str, Any], state: dict[str, Any], send: EventSender) -> dict[str, Any] | None:
        if not pcm:
            recent = state.get("speaker_last_identity")
            if isinstance(recent, dict) and time.monotonic() - float(recent.get("at", 0)) <= 45:
                cached = recent.get("result")
                if isinstance(cached, dict):
                    self.apply(metadata, cached, "recent_short_followup")
                    return cached
            self.set_unknown(metadata, "missing_audio")
            return None
        try:
            result = await asyncio.to_thread(self.client.identify, pcm)
        except SpeakerIdentityError:
            self.set_unknown(metadata, "service_unavailable")
            await send({"type": "speaker.unknown", "reason": "service_unavailable"})
            return None
        if self.apply(metadata, result):
            state["speaker_last_identity"] = {"at": time.monotonic(), "result": result}
            profile = result.get("speaker") if isinstance(result.get("speaker"), dict) else {}
            await send({
                "type": "speaker.identified",
                "speaker_id": profile.get("speaker_id"),
                "name": profile.get("display_name"),
                "score": result.get("score"),
                "margin": result.get("margin"),
            })
            return result
        reason = str(result.get("reason") or "unknown")
        recent = state.get("speaker_last_identity")
        if reason in {"too_short", "empty_audio"} and isinstance(recent, dict) and time.monotonic() - float(recent.get("at", 0)) <= 45:
            cached = recent.get("result")
            if isinstance(cached, dict):
                self.apply(metadata, cached, "recent_short_followup")
                return cached
        await send({"type": "speaker.unknown", "reason": reason, "score": result.get("score"), "margin": result.get("margin")})
        return result

    async def enrollment(self, transcript: str, pcm: bytes, metadata: dict[str, Any], state: dict[str, Any], send: EventSender, speak: Speaker) -> bool:
        flow = state.get("speaker_enrollment")
        if not isinstance(flow, dict):
            return False
        command = parse_speaker_management_command(transcript)
        if command and command[0] == "cancel":
            session_id = str(flow.get("session_id") or "")
            if session_id:
                try: await asyncio.to_thread(self.client.cancel_enrollment, session_id)
                except SpeakerIdentityError: pass
            state.pop("speaker_enrollment", None)
            await speak("Voice enrollment cancelled.")
            return True
        if flow.get("phase") == "await_name":
            name = extract_display_name(transcript)
            if not name:
                await speak("I didn't catch the name. Please just tell me the person's first name.")
                return True
            speaker_id = speaker_id_from_name(name)
            replace = bool(flow.get("replace"))
            admin = bool(speaker_id == self.configured_user_id and self.configured_user_is_admin)
            try:
                started = await asyncio.to_thread(
                    self.client.start_enrollment,
                    speaker_id=speaker_id, display_name=name, is_admin=admin, replace=replace,
                )
            except SpeakerIdentityError as exc:
                state.pop("speaker_enrollment", None)
                if exc.status == 409:
                    await speak(f"I already know {name}'s voice. Say, Jarvis, relearn {name}'s voice, if you want to replace it.")
                else:
                    await speak("I couldn't start voice enrollment. The Voice ID service is unavailable.")
                return True
            phrases = started.get("phrases")
            if not isinstance(phrases, list) or not phrases:
                state.pop("speaker_enrollment", None); await speak("I couldn't start a valid voice enrollment session."); return True
            flow.update(phase="samples", session_id=str(started.get("session_id") or ""), speaker_id=speaker_id,
                        display_name=name, phrases=[str(x) for x in phrases], phrase_index=0)
            await send({"type":"speaker.enrollment.started","speaker_id":speaker_id,"name":name,"target_samples":started.get("target_samples")})
            await speak(f"Great, {name}. I'll learn your voice now. Say this naturally: {phrases[0]}")
            return True
        if flow.get("phase") != "samples":
            state.pop("speaker_enrollment", None); return True
        phrases = flow.get("phrases"); index = int(flow.get("phrase_index", 0))
        if not isinstance(phrases, list) or index >= len(phrases):
            state.pop("speaker_enrollment", None); return True
        expected = str(phrases[index])
        if len(normalise_identity_text(transcript).split()) < 4 or phrase_match_score(transcript, expected) < .28:
            await speak(f"Please say the full sentence: {expected}"); return True
        if not pcm:
            await speak(f"I didn't get a clean voice sample. Please say it again: {expected}"); return True
        try:
            sample = await asyncio.to_thread(self.client.add_sample, session_id=str(flow.get("session_id") or ""), phrase_index=index, pcm=pcm)
        except SpeakerIdentityError:
            await speak("The Voice ID service had a problem with that sample. Please say the sentence again."); return True
        if not bool(sample.get("accepted")):
            await speak(f"{friendly_quality_message(sample.get('reason'))} {expected}"); return True
        index += 1; flow["phrase_index"] = index
        await send({"type":"speaker.enrollment.progress","accepted_samples":sample.get("accepted_samples"),"target_samples":sample.get("target_samples")})
        if index < len(phrases):
            await speak(f"Good. Next sentence: {phrases[index]}"); return True
        try:
            finished = await asyncio.to_thread(self.client.finish_enrollment, str(flow.get("session_id") or ""))
        except SpeakerIdentityError:
            state.pop("speaker_enrollment", None); await speak("I couldn't finish the voice profile. Please start voice enrollment again."); return True
        if not bool(finished.get("enrolled")):
            state.pop("speaker_enrollment", None); await speak(friendly_quality_message(finished.get("reason"))); return True
        profile = finished.get("speaker") if isinstance(finished.get("speaker"), dict) else {}
        synthetic={"recognized":True,"reason":"guided_enrollment","speaker":profile,"score":1.0,"margin":1.0}
        self.apply(metadata, synthetic, "guided_enrollment")
        state["speaker_last_identity"]={"at":time.monotonic(),"result":synthetic}; state.pop("speaker_enrollment", None)
        name=str(profile.get("display_name") or "you")
        await send({"type":"speaker.enrollment.done","speaker_id":profile.get("speaker_id"),"name":name,"sample_count":profile.get("sample_count")})
        await speak(f"Done, {name}. Your voice profile is saved. I'll recognise you from now on.")
        return True

    async def process(self, transcript: str, pcm: bytes, metadata: dict[str, Any], state: dict[str, Any], send: EventSender, speak: Speaker) -> bool:
        if not self.client.enabled:
            return False
        if isinstance(state.get("speaker_enrollment"), dict):
            return await self.enrollment(transcript, pcm, metadata, state, send, speak)
        await self.identify(pcm, metadata, state, send)
        command = parse_speaker_management_command(transcript)
        if command is None: return False
        action, name = command
        await send({"type":"user.transcript","text":transcript})
        if action == "list":
            try: data=await asyncio.to_thread(self.client.list_speakers)
            except SpeakerIdentityError: await speak("I can't reach the Voice ID service right now."); return True
            names=[str(x.get("display_name")) for x in data.get("speakers",[]) if isinstance(x,dict) and x.get("display_name")]
            answer="I don't have any saved voice profiles yet." if not names else (f"I recognise {names[0]}." if len(names)==1 else "I recognise "+", ".join(names[:-1])+f" and {names[-1]}.")
            await speak(answer); return True
        if action == "enroll":
            state["speaker_enrollment"]={"phase":"await_name","replace":False}; await speak("Of course. What is the person's name?"); return True
        current_id=str(metadata.get("speaker_id") or ""); current_name=str(metadata.get("speaker_name") or ""); admin=bool(metadata.get("user_is_admin"))
        if action == "relearn":
            requested=name or (current_name if current_id not in {"","unknown"} else ""); requested_id=speaker_id_from_name(requested)
            if not requested_id: await speak("Tell me whose voice you want me to relearn."); return True
            if not (admin or current_id==requested_id): await speak("I can only replace your own voice profile, unless Aaron is speaking."); return True
            state["speaker_enrollment"]={"phase":"await_name","replace":True}
            return await self.enrollment(requested,b"",metadata,state,send,speak)
        if action == "forget":
            requested_id=speaker_id_from_name(name)
            if not admin: await speak("Only Aaron can delete household voice profiles."); return True
            if not requested_id: await speak("Tell me whose voice profile to forget."); return True
            try: deleted=await asyncio.to_thread(self.client.delete_speaker,requested_id)
            except SpeakerIdentityError: await speak("I couldn't find that saved voice profile."); return True
            await speak(f"I've forgotten {deleted.get('display_name') or name}'s voice profile."); return True
        return False
