from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

VERSION = "17.3.0"
CORE_APPLICATION_VERSION = "2.9.0"
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
INPUT_RATE = 24_000
OUTPUT_RATE = 24_000
VOICE_MODE_REALTIME = "realtime"
VOICE_MODE_HOME_ASSISTANT = "home_assistant"
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

_LOGGER = logging.getLogger("jarvis-realtime-voice")

BrainHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _load_websocket_connect() -> Any:
    """Load the container-only WebSocket client lazily.

    Installer-host validation intentionally runs without third-party site packages.
    The production Jarvis Core container provides ``websockets`` from
    ``bridge/requirements.txt``.
    """

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


@dataclass(frozen=True)
class RealtimeVoiceConfig:
    enabled: bool
    api_key: str
    mobile_token: str
    model: str
    voice: str
    user_id: str
    user_name: str
    user_is_admin: bool
    transcription_prompt: str

    @classmethod
    def from_environment(cls) -> "RealtimeVoiceConfig":
        return cls(
            enabled=_env_bool("JARVIS_REALTIME_ENABLED", True),
            api_key=_env_text("OPENAI_API_KEY"),
            mobile_token=_env_text("JARVIS_MOBILE_VOICE_TOKEN"),
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
        )


def build_session_update(config: RealtimeVoiceConfig, voice: str) -> dict[str, Any]:
    """Configure Realtime as ears and mouth, never as Jarvis's decision maker.

    Semantic VAD still identifies completed turns and interrupts speech, but
    ``create_response`` is disabled. Every accepted transcript is sent through the
    existing Jarvis Core request path before any reply is spoken.
    """

    instructions = (
        "You are the speech renderer for Aaron's private Jarvis Core. "
        "Do not independently answer user requests and do not call tools. "
        "Jarvis Core supplies the final response text. When explicitly asked to speak "
        "text, read it faithfully with natural British conversational prosody, without "
        "adding facts, commentary, headings, or preambles."
    )

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
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
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": "medium",
                        "create_response": False,
                        "interrupt_response": True,
                    },
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
    """Create an out-of-band audio response containing only Core's answer."""

    cleaned = " ".join(str(text or "").split())
    instructions = (
        "Speak the JARVIS RESPONSE below faithfully. Do not answer it, paraphrase it, "
        "summarise it, or add any introduction or closing. Use natural British pacing.\n\n"
        f"JARVIS RESPONSE:\n{cleaned}"
    )
    return {
        "type": "response.create",
        "response": {
            "conversation": "none",
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "output": {
                    "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                    "voice": normalise_voice(voice),
                    "speed": 1.0,
                }
            },
            "metadata": {"source": "jarvis_core", "release": VERSION},
        },
    }


class RealtimeVoiceProxy:
    def __init__(self, config: RealtimeVoiceConfig | None = None) -> None:
        self.config = config or RealtimeVoiceConfig.from_environment()
        self.started_at = time.time()
        self.active_sessions = 0
        self.total_sessions = 0
        self.total_audio_input_bytes = 0
        self.total_audio_output_bytes = 0
        self.total_brain_turns = 0
        self.total_discarded_stale_turns = 0
        self.last_error: str | None = None

    @classmethod
    def from_environment(cls) -> "RealtimeVoiceProxy":
        return cls(RealtimeVoiceConfig.from_environment())

    def status(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "core_application_version": CORE_APPLICATION_VERSION,
            "enabled": self.config.enabled,
            "configured": bool(self.config.api_key and self.config.mobile_token),
            "model": self.config.model,
            "default_voice": self.config.voice,
            "supported_voices": list(SUPPORTED_VOICES),
            "voice_modes": [VOICE_MODE_HOME_ASSISTANT, VOICE_MODE_REALTIME],
            "unified_brain": True,
            "automatic_model_answers": False,
            "input_sample_rate": INPUT_RATE,
            "output_sample_rate": OUTPUT_RATE,
            "active_sessions": self.active_sessions,
            "total_sessions": self.total_sessions,
            "total_audio_input_bytes": self.total_audio_input_bytes,
            "total_audio_output_bytes": self.total_audio_output_bytes,
            "total_brain_turns": self.total_brain_turns,
            "total_discarded_stale_turns": self.total_discarded_stale_turns,
            "uptime_seconds": max(0, round(time.time() - self.started_at)),
            "last_error": self.last_error,
        }

    def token_is_valid(self, supplied: str | None) -> bool:
        expected = self.config.mobile_token
        candidate = (supplied or "").strip()
        return bool(expected and candidate and secrets.compare_digest(expected, candidate))

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

        if auth_payload.get("type") != "auth" or not self.token_is_valid(auth_payload.get("token")):
            await self._send_json(client, {"type": "auth.error", "message": "Invalid mobile voice token"})
            await self._close(client, 4403)
            return

        for key in ("device_id", "user_name"):
            value = auth_payload.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()[:200]

        voice_mode = normalise_voice_mode(auth_payload.get("voice_mode"))
        voice = normalise_voice(auth_payload.get("voice"), self.config.voice)
        metadata["voice_mode"] = voice_mode
        metadata["voice"] = voice

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
                "version": VERSION,
                "model": self.config.model,
                "voice": voice,
                "voice_mode": voice_mode,
                "sample_rate": INPUT_RATE,
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
                await upstream.send(json.dumps(build_session_update(self.config, voice)))
                await self._send_json(client, {"type": "status", "message": "Connecting unified Jarvis voice"})

                client_task = asyncio.create_task(
                    self._client_to_openai(
                        client,
                        upstream,
                        brain_handler,
                        metadata,
                        voice_mode,
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
            _LOGGER.exception("Unified mobile voice session failed")
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
                if not pcm:
                    continue
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
                        "message": "Armed — Jarvis Core is the brain",
                        "model": self.config.model,
                        "voice": voice,
                        "voice_mode": voice_mode,
                        "unified_brain": True,
                    },
                )
            elif kind == "input_audio_buffer.speech_started":
                state["generation"] = int(state.get("generation", 0)) + 1
                state["suppress_audio"] = True
                await self._send_json(client, {"type": "speech.started"})
            elif kind == "input_audio_buffer.speech_stopped":
                await self._send_json(client, {"type": "speech.stopped"})
            elif kind == "conversation.item.input_audio_transcription.completed":
                transcript = str(event.get("transcript") or "").strip()
                if transcript:
                    await self._send_json(client, {"type": "user.transcript", "text": transcript})
                    await self._start_brain_turn(
                        transcript,
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
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
                await self._send_json(
                    client,
                    {
                        "type": "turn.done",
                        "status": response.get("status", "completed"),
                        "usage": usage,
                    },
                )
            elif kind == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                message = str(error.get("message") or "OpenAI realtime error")
                # response.cancel can legitimately race with a completed response.
                if "no active response" in message.casefold():
                    continue
                self.last_error = message[:500]
                await self._send_json(client, {"type": "error", "message": self.last_error})

    async def _start_brain_turn(
        self,
        transcript: str,
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
        generation = int(state.get("generation", 0)) + 1
        state["generation"] = generation
        task = asyncio.create_task(
            self._run_brain_turn(
                generation,
                command,
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
        task.add_done_callback(turn_tasks.discard)

    async def _run_brain_turn(
        self,
        generation: int,
        command: str,
        client: Any,
        upstream: Any,
        brain_handler: BrainHandler,
        metadata: dict[str, Any],
        voice_mode: str,
        voice: str,
        state: dict[str, Any],
    ) -> None:
        self.total_brain_turns += 1
        await self._send_json(client, {"type": "brain.started", "command": command})
        try:
            raw_result = await brain_handler(command, dict(metadata))
            if hasattr(raw_result, "model_dump"):
                raw_result = raw_result.model_dump()
            if not isinstance(raw_result, dict):
                raw_result = {"success": True, "response": str(raw_result)}
            response = str(raw_result.get("response") or "").strip()
            if not response:
                response = "I completed that, but Jarvis Core did not return a spoken response."
            conversation_id = str(raw_result.get("conversation_id") or "").strip()
            if conversation_id:
                metadata["conversation_id"] = conversation_id
            result = {
                "success": bool(raw_result.get("success", True)),
                "response": response,
                "intent": raw_result.get("intent"),
                "conversation_id": metadata.get("conversation_id"),
                "model": raw_result.get("model"),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.exception("Unified Jarvis brain turn failed")
            result = {
                "success": False,
                "response": f"Jarvis Core error: {exc}",
                "intent": None,
                "conversation_id": metadata.get("conversation_id"),
                "model": None,
            }

        if generation != int(state.get("generation", 0)):
            self.total_discarded_stale_turns += 1
            await self._send_json(client, {"type": "brain.discarded", "command": command})
            return

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
            },
        )
        state["suppress_audio"] = False
        if voice_mode == VOICE_MODE_HOME_ASSISTANT:
            await self._send_json(client, {"type": "original.tts", "text": result["response"]})
            return
        await upstream.send(json.dumps(speak_response_event(result["response"], voice)))

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
