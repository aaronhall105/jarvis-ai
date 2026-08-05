from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
import re
import secrets
import time
import uuid
import httpx
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.speech_render_policy import SpeechRenderPolicy

VERSION = "19.0.0-alpha13"
CORE_APPLICATION_VERSION = "3.6.0"
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
INPUT_RATE = 24_000
OUTPUT_RATE = 24_000
VOICE_MODE_REALTIME = "realtime"
VOICE_MODE_HOME_ASSISTANT = "home_assistant"
CONVERSATION_MODE_LIVE = "live"
CONVERSATION_MODE_STANDARD = "standard"
SUPPORTED_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)
SUPPORTED_EAGERNESS = ("low", "medium", "high")

_LOGGER = logging.getLogger("jarvis-realtime-voice")
DeltaHandler = Callable[[str], Awaitable[None]]
BrainHandler = Callable[[str, dict[str, Any], DeltaHandler], Awaitable[dict[str, Any]]]


def _load_websocket_connect() -> Any:
    try:
        from websockets.asyncio.client import connect as websocket_connect
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Jarvis Core is missing the required 'websockets' package. "
            "Rebuild the container from bridge/requirements.txt."
        ) from exc
    return websocket_connect


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}


def _env_text(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip() or default


def normalise_voice(value: Any, fallback: str = DEFAULT_VOICE) -> str:
    voice = str(value or "").strip().casefold()
    if voice in SUPPORTED_VOICES:
        return voice
    return fallback if fallback in SUPPORTED_VOICES else DEFAULT_VOICE


def normalise_voice_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold().replace("-", "_")
    if mode in {"original", "jarvis", "home_assistant", "homeassistant", "ha"}:
        return VOICE_MODE_HOME_ASSISTANT
    return VOICE_MODE_REALTIME


def normalise_conversation_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    return CONVERSATION_MODE_STANDARD if mode == CONVERSATION_MODE_STANDARD else CONVERSATION_MODE_LIVE


def normalise_eagerness(value: Any) -> str:
    eagerness = str(value or "").strip().casefold()
    return eagerness if eagerness in SUPPORTED_EAGERNESS else "high"


def normalise_conversation_id(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return fallback
    safe = "".join(ch for ch in candidate if ch.isalnum() or ch in "-_:.")[:200]
    return safe or fallback


def normalise_timezone(value: Any, fallback: str = "Europe/London") -> str:
    candidate = str(value or "").strip() or fallback
    try:
        ZoneInfo(candidate)
        return candidate
    except ZoneInfoNotFoundError:
        try:
            ZoneInfo(fallback)
            return fallback
        except ZoneInfoNotFoundError:
            return "UTC"


def trusted_local_context(timezone_name: Any) -> dict[str, Any]:
    timezone_value = normalise_timezone(timezone_name)
    local = datetime.now(ZoneInfo(timezone_value))
    offset = local.utcoffset()
    return {
        "timezone": timezone_value,
        "local_datetime": local.isoformat(timespec="seconds"),
        "local_date": local.date().isoformat(),
        "local_time": local.strftime("%H:%M:%S"),
        "utc_offset_seconds": int(offset.total_seconds()) if offset else 0,
    }


@dataclass(frozen=True)
class RealtimeVoiceConfig:
    enabled: bool
    api_key: str
    mobile_token: str
    voice_pe_token: str
    model: str
    voice: str
    user_id: str
    user_name: str
    user_is_admin: bool
    transcription_prompt: str
    timezone: str = "Europe/London"
    quiet_controls: bool = True
    tts_provider: str = "openai"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    elevenlabs_output_format: str = "pcm_24000"
    @classmethod
    def from_environment(cls) -> "RealtimeVoiceConfig":
        return cls(
            enabled=_env_bool("JARVIS_REALTIME_ENABLED", True),
            api_key=_env_text("OPENAI_API_KEY"),
            mobile_token=_env_text("JARVIS_MOBILE_VOICE_TOKEN"),
            voice_pe_token=_env_text("JARVIS_VOICE_PE_TOKEN"),
            model=_env_text("JARVIS_REALTIME_MODEL", DEFAULT_MODEL),
            voice=normalise_voice(_env_text("JARVIS_REALTIME_VOICE", DEFAULT_VOICE)),
            user_id=_env_text("JARVIS_REALTIME_USER_ID", "aaron"),
            user_name=_env_text("JARVIS_REALTIME_USER_NAME", "Aaron"),
            user_is_admin=_env_bool("JARVIS_REALTIME_USER_IS_ADMIN", True),
            transcription_prompt=_env_text(
                "JARVIS_REALTIME_TRANSCRIPTION_PROMPT",
                (
                    "Private names and smart-home terms may include Aaron, Amber, Jarvis, "
                    "Home Assistant, bedroom floodlight, living room, hallway, front door, "
                    "Reolink, Frigate, Tamworth and Durham. Preserve names exactly."
                ),
            ),
            timezone=normalise_timezone(
                _env_text("JARVIS_TIMEZONE", "Europe/London")
            ),
            quiet_controls=_env_bool(
                "JARVIS_MOBILE_QUIET_CONTROLS",
                True,
            ),
            tts_provider=_env_text(
                "JARVIS_TTS_PROVIDER",
                "openai",
            ).casefold(),
            elevenlabs_api_key=_env_text(
                "ELEVENLABS_API_KEY",
            ),
            elevenlabs_voice_id=_env_text(
                "ELEVENLABS_VOICE_ID",
            ),
            elevenlabs_model_id=_env_text(
                "ELEVENLABS_MODEL_ID",
                "eleven_turbo_v2_5",
            ),
            elevenlabs_output_format=_env_text(
                "ELEVENLABS_OUTPUT_FORMAT",
                "pcm_24000",
            ),
        )


def build_session_update(
    config: RealtimeVoiceConfig,
    voice: str,
    conversation_mode: str,
    eagerness: str,
) -> dict[str, Any]:
    turn_detection: dict[str, Any] | None
    if normalise_conversation_mode(conversation_mode) == CONVERSATION_MODE_LIVE:
        turn_detection = {
            "type": "semantic_vad",
            "eagerness": normalise_eagerness(eagerness),
            "create_response": False,
            "interrupt_response": True,
        }
    else:
        turn_detection = None

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": (
                "You are only the speech renderer for Aaron's private Jarvis Core. "
                "Never independently answer user requests and never call tools. "
                "When Jarvis Core explicitly asks you to speak text, read it faithfully "
                "with natural British conversational prosody and no added commentary."
            ),
            "output_modalities": ["audio"],
            "max_output_tokens": 4096,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": INPUT_RATE},
                    "noise_reduction": {"type": "near_field"},
                    "transcription": {
                        "model": "gpt-4o-transcribe",
                        "language": "en",
                        "prompt": config.transcription_prompt,
                    },
                    "turn_detection": turn_detection,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                    "voice": normalise_voice(voice, config.voice),
                    "speed": 1.0,
                },
            },
            "tools": [],
            "tool_choice": "none",
        },
    }


def openai_websocket_url(model: str) -> str:
    return f"wss://api.openai.com/v1/realtime?model={quote(model, safe='-.')}"


def audio_append_event(pcm: bytes) -> dict[str, str]:
    return {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(pcm).decode("ascii"),
    }


def speak_response_event(text: str, voice: str) -> dict[str, Any]:
    cleaned = " ".join(str(text or "").split())
    return {
        "type": "response.create",
        "response": {
            "conversation": "none",
            "output_modalities": ["audio"],
            "instructions": (
                "Speak the JARVIS RESPONSE below faithfully. Do not answer it, paraphrase it, "
                "summarise it, or add any introduction or closing. Use natural British pacing.\n\n"
                f"JARVIS RESPONSE:\n{cleaned}"
            ),
            "audio": {
                "output": {
                    "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                    "voice": normalise_voice(voice),
                }
            },
            "metadata": {"source": "jarvis_core", "release": VERSION},
        },
    }



def _normalise_voice_closure(value: Any) -> str:
    text = str(value or "").casefold()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("'", "")

    prefixes = re.compile(
        r"^(?:(?:okay|ok|alright|all right|right|well)\s+)+"
    )
    jarvis_prefix = re.compile(r"^(?:hey\s+)?jarvis\s+")
    jarvis_suffix = re.compile(r"\s+jarvis$")
    polite_edge = re.compile(
        r"^(?:please\s+)|(?:\s+please)$"
    )

    previous = None
    while previous != text:
        previous = text
        text = prefixes.sub("", text).strip()
        text = jarvis_prefix.sub("", text).strip()
        text = jarvis_suffix.sub("", text).strip()
        text = polite_edge.sub("", text).strip()
        text = re.sub(r"\s+", " ", text).strip()

    return text


def _match_voice_closure(
    value: Any,
    user_name: str = "",
) -> tuple[str, str] | None:
    text = _normalise_voice_closure(value)

    if not text or len(text.split()) > 7:
        return None

    silent = {
        "be quiet",
        "be quiet now",
        "stay quiet",
        "stay quiet now",
        "keep quiet",
        "keep quiet now",
        "quiet",
        "quiet now",
        "hush",
        "hush now",
        "silence",
        "silence now",
        "stop listening",
        "stop listening now",
        "quit listening",
        "quit listening now",
        "stop talking",
        "stop talking now",
        "quit talking",
        "quit talking now",
        "do not listen",
        "dont listen",
        "do not listen anymore",
        "dont listen anymore",
        "leave me alone",
        "never mind",
        "nevermind",
        "cancel",
        "stop",
    }

    done = {
        "thats all",
        "that is all",
        "thatll be all",
        "that will be all",
        "thats everything",
        "that is everything",
        "thatll do",
        "that will do",
        "all done",
        "were done",
        "we are done",
        "im done",
        "i am done",
        "done for now",
        "were finished",
        "we are finished",
        "im finished",
        "i am finished",
        "finished",
        "end conversation",
        "end the conversation",
        "finish conversation",
        "finish the conversation",
        "close conversation",
        "close the conversation",
        "end chat",
        "end the chat",
        "close chat",
        "close the chat",
        "no more",
    }

    thanks = {
        "thanks",
        "thanks a lot",
        "many thanks",
        "thank you",
        "thank you very much",
        "cheers",
    }

    goodbye = {
        "bye",
        "bye bye",
        "goodbye",
        "good bye",
        "goodnight",
        "good night",
        "see you",
        "see you later",
        "speak later",
        "talk later",
        "catch you later",
    }

    if text in silent:
        return ("silent", "")

    if text in goodbye:
        first_name = str(user_name or "").strip().split(" ", 1)[0]
        response = (
            f"Goodbye, {first_name}."
            if first_name
            else "Goodbye."
        )
        return ("goodbye", response)

    if text in thanks:
        return ("thanks", "You're welcome.")

    if text in done:
        return ("done", "Okay.")

    return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _clean_event_message(value: Any, limit: int = 240) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


def sanitise_tool_events(value: Any) -> list[dict[str, Any]]:
    # Return privacy-safe tool results for the trusted mobile client.
    if not isinstance(value, list):
        return []

    events: list[dict[str, Any]] = []
    for raw_call in value[:20]:
        if not isinstance(raw_call, dict):
            continue

        name = re.sub(
            r"[^a-zA-Z0-9_.-]+",
            "_",
            str(raw_call.get("tool") or "").strip(),
        )[:100].strip("_")
        if not name:
            continue

        raw_result = raw_call.get("result")
        result = raw_result if isinstance(raw_result, dict) else {}

        if "verified" in result:
            success = bool(result.get("verified"))
        elif "success" in result:
            success = bool(result.get("success"))
        elif "error" in result:
            success = False
        else:
            success = True

        message = _clean_event_message(
            result.get("response_message")
            or result.get("message")
            or result.get("error")
            or (
                f"{name.replace('_', ' ')} completed"
                if success
                else f"{name.replace('_', ' ')} failed"
            )
        )

        events.append({
            "tool": name,
            "success": success,
            "message": message,
        })

    return events


QUIET_CONTROL_TOOLS = {
    "control_device",
    "control_area_lights",
    "control_area_switches",
    "run_media_shortcut",
    "control_media_player",
    "set_media_volume",
}


def control_voice_policy(command: str, tool_events: list[dict[str, Any]], *, enabled: bool) -> tuple[bool, str]:
    if not enabled or len(tool_events) != 1:
        return False, ""
    event = tool_events[0]
    if str(event.get("tool") or "") not in QUIET_CONTROL_TOOLS:
        return False, ""
    if not bool(event.get("success")):
        return False, ""
    message = _clean_event_message(event.get("message"), 240)
    lowered = message.casefold()
    if any(term in lowered for term in ("failed", "could not", "unavailable", "not responding", "not confirming", "still reports", "has not reported")):
        return False, ""
    if "already" in lowered:
        return True, "Already done."
    normalised = " ".join(str(command or "").casefold().split())
    if re.search(r"\b(?:turn|switch|power)\b.*\boff\b", normalised):
        choices = ("Done.", "Done.", "That's off.", "Done, sir.", "Consider it handled.", "Done.", "Certainly.", "Done.")
    elif "light" in normalised and re.search(r"\b(?:turn|switch|power)\b.*\bon\b", normalised):
        choices = ("Done.", "Done.", "It's on.", "Done, sir.", "Let there be light.", "Done.", "Certainly.", "Done.")
    else:
        choices = ("Done.", "Done.", "Certainly.", "Done, sir.", "Consider it handled.", "Done.", "All sorted.", "Done.")
    return True, choices[sum(normalised.encode("utf-8")) % len(choices)]


class RealtimeVoiceProxy:
    def __init__(self, config: RealtimeVoiceConfig | None = None) -> None:
        self.config = config or RealtimeVoiceConfig.from_environment()
        self.started_at = time.time()
        self.active_sessions = 0
        self.total_sessions = 0
        self.total_audio_input_bytes = 0
        self.total_audio_output_bytes = 0
        self.total_brain_turns = 0
        self.total_streamed_text_chunks = 0
        self.total_discarded_stale_turns = 0
        self.total_tool_calls = 0
        self.total_memory_turns = 0
        self.total_context_syncs = 0
        self.last_error: str | None = None

    @classmethod
    def from_environment(cls) -> "RealtimeVoiceProxy":
        return cls(RealtimeVoiceConfig.from_environment())

    def status(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "core_application_version": CORE_APPLICATION_VERSION,
            "enabled": self.config.enabled,
            "configured": bool(
                self.config.api_key
                and (
                    self.config.mobile_token
                    or self.config.voice_pe_token
                )
            ),
            "mobile_configured": bool(
                self.config.api_key
                and self.config.mobile_token
            ),
            "voice_pe_configured": bool(
                self.config.api_key
                and self.config.voice_pe_token
            ),
            "model": self.config.model,
            "default_voice": self.config.voice,
            "supported_voices": list(SUPPORTED_VOICES),
            "voice_modes": [VOICE_MODE_HOME_ASSISTANT, VOICE_MODE_REALTIME],
            "conversation_modes": [CONVERSATION_MODE_LIVE, CONVERSATION_MODE_STANDARD],
            "supported_vad_eagerness": list(SUPPORTED_EAGERNESS),
            "unified_brain": True,
            "automatic_model_answers": False,
            "persistent_sessions": True,
            "streaming_brain_text": True,
            "android_default_assistant": True,
            "assistant_overlay": True,
            "always_on_wake_host": "voice_interaction_service",
            "input_sample_rate": INPUT_RATE,
            "output_sample_rate": OUTPUT_RATE,
            "active_sessions": self.active_sessions,
            "total_sessions": self.total_sessions,
            "total_audio_input_bytes": self.total_audio_input_bytes,
            "total_audio_output_bytes": self.total_audio_output_bytes,
            "total_brain_turns": self.total_brain_turns,
            "total_streamed_text_chunks": self.total_streamed_text_chunks,
            "total_discarded_stale_turns": self.total_discarded_stale_turns,
            "total_tool_calls": self.total_tool_calls,
            "total_memory_turns": self.total_memory_turns,
            "total_context_syncs": self.total_context_syncs,
            "mobile_context_protocol": "alpha5.1",
            "timezone": self.config.timezone,
            "uptime_seconds": max(0, round(time.time() - self.started_at)),
            "last_error": self.last_error,
        }

    def token_is_valid(
        self,
        supplied: str | None,
        client_kind: str = "mobile",
    ) -> bool:
        expected = (
            self.config.voice_pe_token
            if client_kind == "voice_pe"
            else self.config.mobile_token
        )
        candidate = (supplied or "").strip()
        return bool(
            expected
            and candidate
            and secrets.compare_digest(expected, candidate)
        )

    async def handle(self, client: Any, brain_handler: BrainHandler) -> None:
        await client.accept()
        session_id = f"mobile-{uuid.uuid4()}"
        metadata: dict[str, Any] = {
            "session_id": session_id,
            "conversation_id": session_id,
            "user_id": self.config.user_id,
            "user_name": self.config.user_name,
            "user_is_admin": self.config.user_is_admin,
            "device_id": "jarvis_android",
        }

        try:
            auth = await asyncio.wait_for(client.receive_text(), timeout=12)
            auth_payload = json.loads(auth)
        except Exception:
            await self._send_json(client, {"type": "auth.error", "message": "Authentication required"})
            await self._close(client, 4401)
            return

        client_kind = (
            str(auth_payload.get("client_kind") or "mobile")
            .strip()
            .casefold()
            .replace("-", "_")
        )

        if client_kind not in {"mobile", "voice_pe"}:
            await self._send_json(
                client,
                {
                    "type": "auth.error",
                    "message": "Unsupported realtime voice client",
                },
            )
            await self._close(client, 4403)
            return

        if (
            auth_payload.get("type") != "auth"
            or not self.token_is_valid(
                auth_payload.get("token"),
                client_kind,
            )
        ):
            await self._send_json(
                client,
                {
                    "type": "auth.error",
                    "message": "Invalid realtime voice token",
                },
            )
            await self._close(client, 4403)
            return

        metadata["client_kind"] = client_kind
        metadata["voice_endpoint_kind"] = (
            "voice_pe"
            if client_kind == "voice_pe"
            else "android"
        )

        for key in ("device_id", "user_name"):
            value = auth_payload.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()[:200]

        supplied_user_id = auth_payload.get("user_id")
        if isinstance(supplied_user_id, str):
            candidate = re.sub(
                r"[^a-z0-9_-]+",
                "_",
                supplied_user_id.strip().lower(),
            ).strip("_")[:80]
            if candidate:
                metadata["user_id"] = candidate
                metadata["user_is_admin"] = bool(
                    self.config.user_is_admin
                    and candidate == self.config.user_id
                )

        metadata["response_style"] = "natural"
        metadata["reasoning_effort"] = "medium"
        metadata["mobile_fast_response"] = True
        metadata["client_timezone"] = normalise_timezone(
            auth_payload.get("timezone"),
            self.config.timezone,
        )
        metadata.update(trusted_local_context(self.config.timezone))

        metadata["conversation_id"] = normalise_conversation_id(
            auth_payload.get("conversation_id"),
            session_id,
        )
        voice_mode = normalise_voice_mode(auth_payload.get("voice_mode"))
        conversation_mode = normalise_conversation_mode(auth_payload.get("conversation_mode"))
        voice = normalise_voice(auth_payload.get("voice"), self.config.voice)
        eagerness = normalise_eagerness(auth_payload.get("vad_eagerness"))
        metadata.update(
            voice_mode=voice_mode,
            voice=voice,
            conversation_mode=conversation_mode,
            vad_eagerness=eagerness,
        )

        if not self.config.enabled:
            await self._send_json(client, {"type": "error", "message": "Realtime voice is disabled"})
            await self._close(client, 4410)
            return
        if not self.config.api_key:
            await self._send_json(client, {"type": "error", "message": "OPENAI_API_KEY is not configured"})
            await self._close(client, 4411)
            return

        await self._send_json(
            client,
            {
                "type": "auth.ok",
                "client_kind": client_kind,
                "version": VERSION,
                "model": self.config.model,
                "voice": voice,
                "voice_mode": voice_mode,
                "conversation_mode": conversation_mode,
                "conversation_id": metadata["conversation_id"],
                "sample_rate": INPUT_RATE,
                "transport": "websocket_pcm",
                "unified_brain": True,
            },
        )

        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        turn_tasks: set[asyncio.Task[Any]] = set()
        state: dict[str, Any] = {"generation": 0, "suppress_audio": False}
        self.active_sessions += 1
        self.total_sessions += 1
        try:
            websocket_connect = _load_websocket_connect()
            async with websocket_connect(
                openai_websocket_url(self.config.model),
                additional_headers=headers,
                max_size=None,
                max_queue=64,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15,
                close_timeout=5,
            ) as upstream:
                await upstream.send(
                    json.dumps(build_session_update(self.config, voice, conversation_mode, eagerness))
                )
                await self._send_json(client, {"type": "status", "message": "Connecting Jarvis voice"})

                client_task = asyncio.create_task(
                    self._client_to_openai(
                        client,
                        upstream,
                        brain_handler,
                        metadata,
                        voice_mode,
                        conversation_mode,
                        voice,
                        turn_tasks,
                        state,
                    )
                )
                upstream_task = asyncio.create_task(
                    self._openai_to_client(
                        client,
                        upstream,
                        brain_handler,
                        metadata,
                        voice_mode,
                        conversation_mode,
                        voice,
                        turn_tasks,
                        state,
                    )
                )
                done, pending = await asyncio.wait(
                    {client_task, upstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    error = task.exception()
                    if error is not None:
                        raise error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)[:500]
            _LOGGER.exception("Jarvis mobile voice session failed")
            await self._send_json(client, {"type": "error", "message": self.last_error})
        finally:
            for task in tuple(turn_tasks):
                task.cancel()
            if turn_tasks:
                await asyncio.gather(*turn_tasks, return_exceptions=True)
            self.active_sessions = max(0, self.active_sessions - 1)
            await self._close(client, 1000)

    async def _client_to_openai(
        self,
        client: Any,
        upstream: Any,
        brain_handler: BrainHandler,
        metadata: dict[str, Any],
        voice_mode: str,
        conversation_mode: str,
        voice: str,
        turn_tasks: set[asyncio.Task[Any]],
        state: dict[str, Any],
    ) -> None:
        while True:
            message = await client.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                return

            pcm = message.get("bytes")
            if isinstance(pcm, bytes):
                if not pcm or conversation_mode != CONVERSATION_MODE_LIVE:
                    continue

                if metadata.get("client_kind") == "voice_pe":
                    pcm, resample_state = audioop.ratecv(
                        pcm,
                        2,
                        1,
                        16_000,
                        INPUT_RATE,
                        state.get("voice_pe_resample_state"),
                    )
                    state["voice_pe_resample_state"] = resample_state

                    if not pcm:
                        continue

                state["pcm_diagnostic_chunks"] = (
                    int(state.get("pcm_diagnostic_chunks", 0)) + 1
                )
                diagnostic_chunks = int(state["pcm_diagnostic_chunks"])

                if diagnostic_chunks == 1 or diagnostic_chunks % 100 == 0:
                    _LOGGER.info(
                        "Voice PE PCM diagnostic: chunks=%d bytes=%d rms=%d peak=%d",
                        diagnostic_chunks,
                        len(pcm),
                        audioop.rms(pcm, 2),
                        audioop.max(pcm, 2),
                    )

                self.total_audio_input_bytes += len(pcm)
                await upstream.send(json.dumps(audio_append_event(pcm)))
                continue

            raw = message.get("text")
            if not isinstance(raw, str) or not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = payload.get("type")
            if kind == "ping":
                await self._send_json(client, {"type": "pong", "time": time.time()})
            elif kind == "cancel":
                state["generation"] = int(state.get("generation", 0)) + 1
                state["suppress_audio"] = True
                await upstream.send(json.dumps({"type": "response.cancel"}))
            elif kind == "text":
                text = str(payload.get("text") or "").strip()
                if text:
                    await self._start_brain_turn(
                        text,
                        bool(payload.get("speak", True)),
                        client,
                        upstream,
                        brain_handler,
                        metadata,
                        voice_mode,
                        voice,
                        turn_tasks,
                        state,
                    )
            elif kind == "stop":
                return

    async def _openai_to_client(
        self,
        client: Any,
        upstream: Any,
        brain_handler: BrainHandler,
        metadata: dict[str, Any],
        voice_mode: str,
        conversation_mode: str,
        voice: str,
        turn_tasks: set[asyncio.Task[Any]],
        state: dict[str, Any],
    ) -> None:
        async for raw in upstream:
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = str(event.get("type") or "")
            if kind == "session.updated":
                await self._send_json(
                    client,
                    {
                        "type": "ready",
                        "message": "Jarvis is ready",
                        "model": self.config.model,
                        "voice": voice,
                        "voice_mode": voice_mode,
                        "conversation_mode": conversation_mode,
                        "conversation_id": metadata.get("conversation_id"),
                        "transport": "websocket_pcm",
                        "unified_brain": True,
                    },
                )
                await self._send_json(
                    client,
                    {
                        "type": "session.context",
                        "conversation_id": metadata.get("conversation_id"),
                        "user_name": metadata.get("user_name"),
                        "message_count": 0,
                    },
                )
                self.total_context_syncs += 1
            elif kind == "input_audio_buffer.speech_started":
                if (
                    metadata.get("client_kind") == "voice_pe"
                    and bool(state.get("turn_in_progress"))
                ):
                    continue
                state["generation"] = int(state.get("generation", 0)) + 1
                state["suppress_audio"] = True
                await self._send_json(client, {"type": "speech.started"})
            elif kind == "input_audio_buffer.speech_stopped":
                if (
                    metadata.get("client_kind") == "voice_pe"
                    and bool(state.get("turn_in_progress"))
                ):
                    continue
                await self._send_json(client, {"type": "speech.stopped"})
            elif kind == "conversation.item.input_audio_transcription.completed":
                transcript = str(
                    event.get("transcript") or ""
                ).strip()

                if (
                    transcript
                    and conversation_mode
                    == CONVERSATION_MODE_LIVE
                ):
                    await self._send_json(
                        client,
                        {
                            "type": "user.transcript",
                            "text": transcript,
                        },
                    )

                    closure = _match_voice_closure(
                        transcript,
                        str(
                            metadata.get("user_name")
                            or ""
                        ),
                    )

                    if closure is not None:
                        closure_kind, closure_response = closure

                        state["generation"] = (
                            int(state.get("generation", 0))
                            + 1
                        )
                        state["suppress_audio"] = True

                        await upstream.send(
                            json.dumps(
                                {
                                    "type": "response.cancel"
                                }
                            )
                        )

                        await self._send_json(
                            client,
                            {
                                "type": "closure.detected",
                                "kind": closure_kind,
                                "text": transcript,
                            },
                        )

                        if not closure_response:
                            await self._send_json(
                                client,
                                {
                                    "type": "session.close",
                                    "reason": "voice_closure",
                                    "kind": closure_kind,
                                },
                            )
                        else:
                            state[
                                "close_after_response"
                            ] = closure_kind
                            state["suppress_audio"] = False

                            if self._use_direct_elevenlabs(metadata):
                                handled = await self._stream_elevenlabs_response(
                                    client,
                                    closure_response,
                                    state,
                                )
                                if not handled:
                                    await upstream.send(
                                        json.dumps(
                                            speak_response_event(
                                                closure_response,
                                                voice,
                                            )
                                        )
                                    )
                            else:
                                await upstream.send(
                                    json.dumps(
                                        speak_response_event(
                                            closure_response,
                                            voice,
                                        )
                                    )
                                )
                    else:
                        await self._start_brain_turn(
                            transcript,
                            True,
                            client,
                            upstream,
                            brain_handler,
                            metadata,
                            voice_mode,
                            voice,
                            turn_tasks,
                            state,
                        )
            elif kind == "response.created":
                state["suppress_audio"] = False
            elif kind == "response.output_audio.delta":
                if bool(state.get("suppress_audio")) or voice_mode == VOICE_MODE_HOME_ASSISTANT:
                    continue
                encoded = event.get("delta")
                if isinstance(encoded, str) and encoded:
                    try:
                        audio = base64.b64decode(encoded, validate=True)
                    except Exception:
                        continue
                    self.total_audio_output_bytes += len(audio)
                    await client.send_bytes(audio)
                    await asyncio.sleep(len(audio) / 48000.0)
            elif kind == "response.output_audio_transcript.delta":
                delta = str(event.get("delta") or "")
                if delta and voice_mode == VOICE_MODE_REALTIME:
                    await self._send_json(client, {"type": "assistant.transcript.delta", "text": delta})
            elif kind == "response.output_audio_transcript.done":
                transcript = str(event.get("transcript") or "").strip()
                if transcript and voice_mode == VOICE_MODE_REALTIME:
                    await self._send_json(client, {"type": "assistant.transcript.done", "text": transcript})
            elif kind == "response.output_audio.done":
                if voice_mode == VOICE_MODE_REALTIME:
                    await self._send_json(client, {"type": "audio.done"})
            elif kind == "response.done":
                response = (
                    event.get("response")
                    if isinstance(
                        event.get("response"),
                        dict,
                    )
                    else {}
                )

                usage = (
                    response.get("usage")
                    if isinstance(
                        response.get("usage"),
                        dict,
                    )
                    else None
                )

                await self._complete_audio_response(
                    client,
                    upstream,
                    state,
                    status=str(
                        response.get(
                            "status",
                            "completed",
                        )
                    ),
                    usage=usage,
                )


            elif kind == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                message = str(error.get("message") or "OpenAI realtime error")
                if "no active response" in message.casefold():
                    continue
                self.last_error = message[:500]
                await self._send_json(client, {"type": "error", "message": self.last_error})

    async def _complete_audio_response(
        self,
        client: Any,
        upstream: Any,
        state: dict[str, Any],
        *,
        status: str = "completed",
        usage: Any = None,
    ) -> None:
        """
        Finish the current audio response or start its queued
        continuation.

        The first streamed speech response may finish before the
        Jarvis brain has produced the complete final text. In that
        case completion is deferred until the remainder is known.
        """

        if bool(
            state.get("early_speech_active")
        ):
            if not bool(
                state.get("brain_turn_complete")
            ):
                state["early_audio_done"] = True
                return

            state["early_speech_active"] = False

            remainder = str(
                state.pop(
                    "queued_speech_remainder",
                    "",
                )
                or ""
            ).strip()

            if remainder:
                state[
                    "continuation_speech_active"
                ] = True

                await upstream.send(
                    json.dumps(
                        speak_response_event(
                            remainder,
                            str(
                                state.get(
                                    "active_voice",
                                    self.config.voice,
                                )
                            ),
                        )
                    )
                )

                await self._send_json(
                    client,
                    {
                        "type": "speech.continuation",
                        "characters": len(remainder),
                    },
                )

                return

        state[
            "continuation_speech_active"
        ] = False
        state["turn_in_progress"] = False

        await self._send_json(
            client,
            {
                "type": "turn.done",
                "status": status,
                "usage": usage,
            },
        )

        closure_kind = state.pop(
            "close_after_response",
            None,
        )

        if closure_kind:
            await self._send_json(
                client,
                {
                    "type": "session.close",
                    "reason": "voice_closure",
                    "kind": closure_kind,
                },
            )

    def _use_direct_elevenlabs(
        self,
        metadata: dict[str, Any],
    ) -> bool:
        return (
            self.config.tts_provider == "elevenlabs"
            and metadata.get("client_kind") == "voice_pe"
        )

    async def _stream_elevenlabs_response(
        self,
        client: Any,
        text: str,
        state: dict[str, Any],
    ) -> bool:
        spoken_text = " ".join(str(text or "").split()).strip()
        if not spoken_text:
            return False

        api_key = self.config.elevenlabs_api_key
        voice_id = self.config.elevenlabs_voice_id

        if not api_key or not voice_id:
            self.last_error = (
                "ElevenLabs API key or voice ID is not configured"
            )
            await self._send_json(
                client,
                {
                    "type": "status",
                    "message": (
                        "Original Jarvis voice is not configured; "
                        "using realtime fallback"
                    ),
                },
            )
            return False

        url = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{quote(voice_id, safe='')}/stream"
        )

        headers = {
            "xi-api-key": api_key,
            "accept": "audio/pcm",
            "content-type": "application/json",
        }

        payload = {
            "text": spoken_text,
            "model_id": self.config.elevenlabs_model_id,
        }

        await self._send_json(
            client,
            {
                "type": "status",
                "message": "Rendering original Jarvis voice",
            },
        )

        try:
            timeout = httpx.Timeout(
                60.0,
                connect=10.0,
            )

            async with httpx.AsyncClient(timeout=timeout) as session:
                async with session.stream(
                    "POST",
                    url,
                    params={
                        "output_format":
                            self.config.elevenlabs_output_format,
                    },
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()

                    pending = bytearray()
                    frame_size = 4096

                    async for data in response.aiter_bytes():
                        if not data:
                            continue

                        pending.extend(data)

                        while len(pending) >= frame_size:
                            chunk = bytes(pending[:frame_size])
                            del pending[:frame_size]

                            self.total_audio_output_bytes += len(chunk)
                            await client.send_bytes(chunk)

                            # 24 kHz, mono, signed 16-bit PCM.
                            await asyncio.sleep(len(chunk) / 48_000.0)

                    if len(pending) % 2:
                        pending = pending[:-1]

                    if pending:
                        chunk = bytes(pending)
                        self.total_audio_output_bytes += len(chunk)
                        await client.send_bytes(chunk)
                        await asyncio.sleep(len(chunk) / 48_000.0)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"ElevenLabs TTS failed: {exc}"[:500]
            _LOGGER.exception("Direct ElevenLabs speech failed")

            await self._send_json(
                client,
                {
                    "type": "status",
                    "message": (
                        "Original Jarvis voice failed; "
                        "using realtime fallback"
                    ),
                },
            )
            return False

        await self._send_json(
            client,
            {
                "type": "assistant.transcript.done",
                "text": spoken_text,
            },
        )
        await self._send_json(
            client,
            {"type": "audio.done"},
        )

        state["turn_in_progress"] = False

        await self._send_json(
            client,
            {
                "type": "turn.done",
                "status": "completed",
                "usage": None,
            },
        )

        closure_kind = state.pop(
            "close_after_response",
            None,
        )

        if closure_kind:
            await self._send_json(
                client,
                {
                    "type": "session.close",
                    "reason": "voice_closure",
                    "kind": closure_kind,
                },
            )

        return True

    async def _start_brain_turn(
        self,
        transcript: str,
        speak: bool,
        client: Any,
        upstream: Any,
        brain_handler: BrainHandler,
        metadata: dict[str, Any],
        voice_mode: str,
        voice: str,
        turn_tasks: set[asyncio.Task[Any]],
        state: dict[str, Any],
    ) -> None:
        command = " ".join(str(transcript or "").split()).strip()
        if not command:
            return
        if (
            metadata.get("client_kind") == "voice_pe"
            and bool(state.get("turn_in_progress"))
        ):
            await self._send_json(
                client,
                {
                    "type": "turn.ignored",
                    "reason": "voice_pe_turn_in_progress",
                },
            )
            return
        state["turn_in_progress"] = True
        generation = int(state.get("generation", 0)) + 1
        state["generation"] = generation
        state.pop("queued_speech_remainder", None)
        state["brain_turn_complete"] = False
        state["early_audio_done"] = False
        state["early_speech_active"] = False
        state["continuation_speech_active"] = False
        task = asyncio.create_task(
            self._run_brain_turn(
                generation,
                command,
                speak,
                client,
                upstream,
                brain_handler,
                metadata,
                voice_mode,
                voice,
                state,
            )
        )
        turn_tasks.add(task)

        def finish_turn_task(completed: asyncio.Task[Any]) -> None:
            turn_tasks.discard(completed)
            if completed.cancelled():
                state["turn_in_progress"] = False
            elif completed.exception() is not None:
                state["turn_in_progress"] = False

        task.add_done_callback(finish_turn_task)

    async def _run_brain_turn(
        self,
        generation: int,
        command: str,
        speak: bool,
        client: Any,
        upstream: Any,
        brain_handler: BrainHandler,
        metadata: dict[str, Any],
        voice_mode: str,
        voice: str,
        state: dict[str, Any],
    ) -> None:
        self.total_brain_turns += 1
        state["active_voice"] = voice
        await self._send_json(client, {"type": "brain.started", "command": command})

        speech_buffer = ""
        early_speech_sent = False
        early_speech_text = ""

        async def on_delta(delta: str) -> None:
            nonlocal speech_buffer, early_speech_sent, early_speech_text
            if generation != int(state.get("generation", 0)):
                return
            text = str(delta or "")
            if not text:
                return
            self.total_streamed_text_chunks += 1
            await self._send_json(client, {"type": "brain.delta", "text": text})

            if not speak or early_speech_sent or metadata.get("client_kind") == "voice_pe":
                return

            speech_buffer += text
            segment = SpeechRenderPolicy.early_segment(speech_buffer)
            if not segment:
                return

            early_speech_sent = True
            early_speech_text = segment
            state["early_speech_active"] = True
            state["brain_turn_complete"] = False
            state["early_audio_done"] = False
            if voice_mode == VOICE_MODE_HOME_ASSISTANT:
                await self._send_json(
                    client,
                    {
                        "type": "original.tts",
                        "text": segment,
                        "streaming_preview": True,
                    },
                )
            else:
                state["suppress_audio"] = False
                await upstream.send(json.dumps(speak_response_event(segment, voice)))

        try:
            turn_metadata = dict(metadata)
            turn_metadata.update(
                trusted_local_context(self.config.timezone)
            )
            turn_metadata["speak"] = bool(speak)
            raw_result = await brain_handler(command, turn_metadata, on_delta)
            if hasattr(raw_result, "model_dump"):
                raw_result = raw_result.model_dump()
            if not isinstance(raw_result, dict):
                raw_result = {"success": True, "response": str(raw_result)}
            response = str(raw_result.get("response") or "").strip()
            if not response:
                response = "I completed that, but Jarvis Core did not return a response."
            conversation_id = str(raw_result.get("conversation_id") or "").strip()
            if conversation_id:
                metadata["conversation_id"] = conversation_id
            tool_events = sanitise_tool_events(
                raw_result.get("calls")
            )
            quiet_control, compact_response = control_voice_policy(
                command,
                tool_events,
                enabled=self.config.quiet_controls and metadata.get("client_kind") != "voice_pe",
            )
            if quiet_control:
                response = compact_response
            memory_used = bool(raw_result.get("memory_used", False))
            message_count = _safe_int(
                raw_result.get("message_count"),
                0,
            )
            user_payload = raw_result.get("user")
            user_name = (
                str(user_payload.get("name") or "").strip()
                if isinstance(user_payload, dict)
                else str(metadata.get("user_name") or "").strip()
            )
            result = {
                "success": bool(raw_result.get("success", True)),
                "response": response,
                "intent": raw_result.get("intent"),
                "conversation_id": metadata.get("conversation_id"),
                "model": raw_result.get("model"),
                "tool_events": tool_events,
                "tool_called": bool(
                    raw_result.get("tool_called", bool(tool_events))
                ),
                "quiet_control": quiet_control,
                "memory_used": memory_used,
                "message_count": message_count,
                "user_name": user_name,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.exception("Jarvis brain turn failed")
            result = {
                "success": False,
                "response": f"Jarvis Core error: {exc}",
                "intent": None,
                "conversation_id": metadata.get("conversation_id"),
                "model": None,
                "tool_events": [],
                "tool_called": False,
                "quiet_control": False,
                "memory_used": False,
                "message_count": 0,
                "user_name": str(
                    metadata.get("user_name") or ""
                ).strip(),
            }

        if generation != int(state.get("generation", 0)):
            self.total_discarded_stale_turns += 1
            await self._send_json(client, {"type": "brain.discarded", "command": command})
            return

        for tool_event in result.get("tool_events", []):
            await self._send_json(
                client,
                {
                    "type": "tool.completed",
                    "tool": tool_event["tool"],
                    "success": tool_event["success"],
                    "message": tool_event["message"],
                    "conversation_id": result["conversation_id"],
                },
            )
            self.total_tool_calls += 1

        await self._send_json(
            client,
            {
                "type": "memory.context",
                "memory_used": bool(result.get("memory_used")),
                "message_count": _safe_int(
                    result.get("message_count"),
                    0,
                ),
                "conversation_id": result["conversation_id"],
            },
        )
        if bool(result.get("memory_used")):
            self.total_memory_turns += 1

        await self._send_json(
            client,
            {
                "type": "session.context",
                "conversation_id": result["conversation_id"],
                "user_name": result.get("user_name"),
                "message_count": _safe_int(
                    result.get("message_count"),
                    0,
                ),
            },
        )
        self.total_context_syncs += 1

        await self._send_json(
            client,
            {
                "type": "turn.summary",
                "success": result["success"],
                "tool_called": bool(result.get("tool_called")),
                "memory_used": bool(result.get("memory_used")),
                "message_count": _safe_int(
                    result.get("message_count"),
                    0,
                ),
                "conversation_id": result["conversation_id"],
                "user_name": result.get("user_name"),
            },
        )

        await self._send_json(
            client,
            {
                "type": "brain.response",
                "text": result["response"],
                "success": result["success"],
                "intent": result["intent"],
                "conversation_id": result["conversation_id"],
                "model": result["model"],
                "voice_mode": voice_mode,
                "quiet_control": bool(result.get("quiet_control")),
            },
        )

        if not speak or bool(result.get("quiet_control")):
            state["turn_in_progress"] = False
            await self._send_json(
                client,
                {"type": "turn.done", "status": "completed", "usage": None},
            )
            return
        if early_speech_sent:
            complete_spoken_response = (
                SpeechRenderPolicy.spoken_text(
                    result["response"]
                )
            )

            remainder = (
                SpeechRenderPolicy.remaining_text(
                    complete_spoken_response,
                    early_speech_text,
                )
            )

            state[
                "queued_speech_remainder"
            ] = remainder

            state[
                "brain_turn_complete"
            ] = True

            await self._send_json(
                client,
                {
                    "type": "speech.remainder.ready",
                    "characters": len(remainder),
                },
            )

            if bool(
                state.pop(
                    "early_audio_done",
                    False,
                )
            ):
                await self._complete_audio_response(
                    client,
                    upstream,
                    state,
                    status="completed",
                    usage=None,
                )

            return

        spoken_response = SpeechRenderPolicy.spoken_text(result["response"])
        state["suppress_audio"] = False
        if voice_mode == VOICE_MODE_HOME_ASSISTANT:
            await self._send_json(
                client,
                {"type": "original.tts", "text": spoken_response},
            )
            state["turn_in_progress"] = False
            return
        if self._use_direct_elevenlabs(metadata):
            handled = await self._stream_elevenlabs_response(
                client,
                spoken_response,
                state,
            )
            if handled:
                return

        await upstream.send(
            json.dumps(
                speak_response_event(
                    spoken_response,
                    voice,
                )
            )
        )

    @staticmethod
    async def _send_json(client: Any, payload: dict[str, Any]) -> None:
        try:
            await client.send_json(payload)
        except Exception:
            return

    @staticmethod
    async def _close(client: Any, code: int) -> None:
        try:
            await client.close(code=code)
        except Exception:
            return
