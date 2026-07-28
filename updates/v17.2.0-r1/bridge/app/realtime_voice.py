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

VERSION = "17.2.0-r1"
CORE_APPLICATION_VERSION = "2.8.0"
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
INPUT_RATE = 24_000
OUTPUT_RATE = 24_000

_LOGGER = logging.getLogger("jarvis-realtime-voice")

ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


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

    @classmethod
    def from_environment(cls) -> "RealtimeVoiceConfig":
        return cls(
            enabled=_env_bool("JARVIS_REALTIME_ENABLED", True),
            api_key=_env_text("OPENAI_API_KEY"),
            mobile_token=_env_text("JARVIS_MOBILE_VOICE_TOKEN"),
            model=_env_text("JARVIS_REALTIME_MODEL", DEFAULT_MODEL),
            voice=_env_text("JARVIS_REALTIME_VOICE", DEFAULT_VOICE),
            user_id=_env_text("JARVIS_REALTIME_USER_ID", "aaron"),
            user_name=_env_text("JARVIS_REALTIME_USER_NAME", "Aaron"),
            user_is_admin=_env_bool("JARVIS_REALTIME_USER_IS_ADMIN", True),
        )


def build_session_update(config: RealtimeVoiceConfig) -> dict[str, Any]:
    """Build a conservative Realtime session configuration.

    The audio format and turn-detection fields match the current Realtime API.
    Tool calls are intentionally restricted to Jarvis-specific actions so ordinary
    conversation stays on the low-latency realtime model.
    """

    instructions = (
        "You are Jarvis, Aaron's private home and personal voice assistant. "
        "Speak naturally, warmly and briefly unless Aaron asks for detail. "
        "This is a live spoken conversation, so do not use markdown, headings, "
        "tables or long lists. Never say that you are waiting for a wake word while "
        "the live session is active. Allow Aaron to interrupt and immediately follow "
        "his newest request. Use the jarvis_command tool only for Home Assistant "
        "device control, house status, routines, schedules, reminders, persistent "
        "Jarvis memory, or other actions that require Aaron's private Jarvis Core. "
        "Do not call the tool for ordinary conversation or general knowledge. When "
        "a tool returns a response, speak that result naturally and do not read JSON."
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
                    },
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": "medium",
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                    "voice": config.voice,
                    "speed": 1.0,
                },
            },
            "tools": [
                {
                    "type": "function",
                    "name": "jarvis_command",
                    "description": (
                        "Run one private Jarvis Core command. Use only for smart-home "
                        "control or status, routines, schedules, reminders, memory, "
                        "and other actions that require Aaron's private systems."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": (
                                    "The complete natural-language command to send "
                                    "to Jarvis Core."
                                ),
                            }
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                }
            ],
            "tool_choice": "auto",
        },
    }


def openai_websocket_url(model: str) -> str:
    return f"wss://api.openai.com/v1/realtime?model={quote(model, safe='-.')}"


def audio_append_event(pcm: bytes) -> dict[str, str]:
    return {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(pcm).decode("ascii"),
    }


def function_output_event(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
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
        self.total_tool_calls = 0
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
            "voice": self.config.voice,
            "input_sample_rate": INPUT_RATE,
            "output_sample_rate": OUTPUT_RATE,
            "active_sessions": self.active_sessions,
            "total_sessions": self.total_sessions,
            "total_audio_input_bytes": self.total_audio_input_bytes,
            "total_audio_output_bytes": self.total_audio_output_bytes,
            "total_tool_calls": self.total_tool_calls,
            "uptime_seconds": max(0, round(time.time() - self.started_at)),
            "last_error": self.last_error,
        }

    def token_is_valid(self, supplied: str | None) -> bool:
        expected = self.config.mobile_token
        candidate = (supplied or "").strip()
        return bool(expected and candidate and secrets.compare_digest(expected, candidate))

    async def handle(self, client: Any, tool_handler: ToolHandler) -> None:
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

        # The authenticated token selects the fixed Core user. The phone may label
        # its device and display name, but cannot promote itself to another user.
        for key in ("device_id", "user_name"):
            value = auth_payload.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()[:200]

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
                "voice": self.config.voice,
                "sample_rate": INPUT_RATE,
            },
        )

        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        tool_tasks: set[asyncio.Task[Any]] = set()
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
                await upstream.send(json.dumps(build_session_update(self.config)))
                await self._send_json(client, {"type": "status", "message": "Connecting to realtime voice"})

                client_task = asyncio.create_task(self._client_to_openai(client, upstream))
                upstream_task = asyncio.create_task(
                    self._openai_to_client(client, upstream, tool_handler, metadata, tool_tasks)
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
            _LOGGER.exception("Realtime mobile voice session failed")
            await self._send_json(client, {"type": "error", "message": self.last_error})
        finally:
            for task in tuple(tool_tasks):
                task.cancel()
            if tool_tasks:
                await asyncio.gather(*tool_tasks, return_exceptions=True)
            self.active_sessions = max(0, self.active_sessions - 1)
            await self._close(client, 1000)

    async def _client_to_openai(self, client: Any, upstream: Any) -> None:
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
                await upstream.send(json.dumps({"type": "response.cancel"}))
            elif kind == "text":
                text = str(payload.get("text") or "").strip()
                if text:
                    await upstream.send(
                        json.dumps(
                            {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": text}],
                                },
                            }
                        )
                    )
                    await upstream.send(json.dumps({"type": "response.create"}))
            elif kind == "stop":
                return

    async def _openai_to_client(
        self,
        client: Any,
        upstream: Any,
        tool_handler: ToolHandler,
        metadata: dict[str, Any],
        tool_tasks: set[asyncio.Task[Any]],
    ) -> None:
        suppress_audio = False
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
                        "message": "Live — talk naturally",
                        "model": self.config.model,
                        "voice": self.config.voice,
                    },
                )
            elif kind == "input_audio_buffer.speech_started":
                # Stop forwarding any late packets from the interrupted response.
                suppress_audio = True
                await self._send_json(client, {"type": "speech.started"})
            elif kind == "input_audio_buffer.speech_stopped":
                await self._send_json(client, {"type": "speech.stopped"})
            elif kind == "conversation.item.input_audio_transcription.completed":
                transcript = str(event.get("transcript") or "").strip()
                if transcript:
                    await self._send_json(client, {"type": "user.transcript", "text": transcript})
            elif kind == "response.created":
                suppress_audio = False
            elif kind == "response.output_audio.delta":
                if suppress_audio:
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
                if delta:
                    await self._send_json(client, {"type": "assistant.transcript.delta", "text": delta})
            elif kind == "response.output_audio_transcript.done":
                transcript = str(event.get("transcript") or "").strip()
                if transcript:
                    await self._send_json(client, {"type": "assistant.transcript.done", "text": transcript})
            elif kind == "response.output_audio.done":
                await self._send_json(client, {"type": "audio.done"})
            elif kind == "response.function_call_arguments.done":
                task = asyncio.create_task(
                    self._handle_tool_call(upstream, client, tool_handler, metadata, event)
                )
                tool_tasks.add(task)
                task.add_done_callback(tool_tasks.discard)
            elif kind == "response.done":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
                await self._send_json(
                    client,
                    {
                        "type": "response.done",
                        "status": response.get("status", "completed"),
                        "usage": usage,
                    },
                )
            elif kind == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                message = str(error.get("message") or "OpenAI realtime error")
                self.last_error = message[:500]
                await self._send_json(client, {"type": "error", "message": self.last_error})

    async def _handle_tool_call(
        self,
        upstream: Any,
        client: Any,
        tool_handler: ToolHandler,
        metadata: dict[str, Any],
        event: dict[str, Any],
    ) -> None:
        call_id = str(event.get("call_id") or "").strip()
        name = str(event.get("name") or "").strip()
        if not call_id:
            return

        result: dict[str, Any]
        if name != "jarvis_command":
            result = {"success": False, "response": f"Unsupported tool: {name or 'unknown'}"}
        else:
            try:
                arguments = json.loads(str(event.get("arguments") or "{}"))
                command = str(arguments.get("command") or "").strip()
            except Exception:
                command = ""
            if not command:
                result = {"success": False, "response": "Jarvis command was empty."}
            else:
                self.total_tool_calls += 1
                await self._send_json(client, {"type": "tool.started", "command": command})
                try:
                    raw_result = await tool_handler(command, dict(metadata))
                    if hasattr(raw_result, "model_dump"):
                        raw_result = raw_result.model_dump()
                    if not isinstance(raw_result, dict):
                        raw_result = {"success": True, "response": str(raw_result)}
                    response = str(raw_result.get("response") or "").strip()
                    result = {
                        "success": bool(raw_result.get("success", True)),
                        "response": response or "The Jarvis command completed.",
                        "intent": raw_result.get("intent"),
                        "conversation_id": raw_result.get("conversation_id"),
                    }
                except Exception as exc:
                    _LOGGER.exception("Realtime Jarvis tool call failed")
                    result = {"success": False, "response": f"Jarvis Core error: {exc}"}
                await self._send_json(client, {"type": "tool.done", "result": result})

        await upstream.send(json.dumps(function_output_event(call_id, result)))
        await upstream.send(json.dumps({"type": "response.create"}))

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
