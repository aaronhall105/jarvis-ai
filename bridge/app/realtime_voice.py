from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
import wave
from pathlib import Path

import httpx
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.speech_render_policy import SpeechRenderPolicy
from app.speaker_identity import SpeakerIdentityClient, SpeakerIdentityRuntime
from app.runtime_observability import runtime_metrics
from app.version import CORE_APPLICATION_VERSION, JARVIS_RELEASE, REALTIME_PROTOCOL_VERSION

VERSION = JARVIS_RELEASE
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
INPUT_RATE = 24_000
OUTPUT_RATE = 24_000
PROVIDER_SESSION_LIFETIME_SECONDS = 60 * 60
PROVIDER_SESSION_RENEWAL_LEAD_SECONDS = 2 * 60
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
VOICE_PE_WAKE_CONTENTION_SECONDS = 1.25

_LOGGER = logging.getLogger("jarvis-realtime-voice")
DeltaHandler = Callable[[str], Awaitable[None]]
BrainHandler = Callable[[str, dict[str, Any], DeltaHandler], Awaitable[dict[str, Any]]]


@dataclass
class ProviderSessionLease:
    """Track the usable lifetime of one authoritative provider connection."""

    epoch: int
    established_at: float
    lifetime_seconds: float = PROVIDER_SESSION_LIFETIME_SECONDS
    renewal_lead_seconds: float = PROVIDER_SESSION_RENEWAL_LEAD_SECONDS
    renewal_pending: bool = False

    @property
    def renewal_at(self) -> float:
        return self.established_at + max(
            0.0,
            self.lifetime_seconds - self.renewal_lead_seconds,
        )

    def renewal_required(self, now: float) -> bool:
        required = now >= self.renewal_at
        if required:
            self.renewal_pending = True
        return required


@dataclass(frozen=True)
class VoicePeWakeClaim:
    granted: bool
    owner_device_id: str
    owner_session_id: str
    retry_after_ms: int = 0


class VoicePeWakeArbiter:
    """Select one satellite when several hear the same nearby wake word."""

    def __init__(
        self,
        contention_seconds: float = VOICE_PE_WAKE_CONTENTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.contention_seconds = max(0.1, float(contention_seconds))
        self._clock = clock
        self._owner_device_id = ""
        self._owner_session_id = ""
        self._claimed_at = float("-inf")

    def claim(self, device_id: str, session_id: str) -> VoicePeWakeClaim:
        now = self._clock()
        elapsed = now - self._claimed_at
        if elapsed < self.contention_seconds:
            return VoicePeWakeClaim(
                granted=False,
                owner_device_id=self._owner_device_id,
                owner_session_id=self._owner_session_id,
                retry_after_ms=max(
                    1,
                    round((self.contention_seconds - elapsed) * 1000),
                ),
            )

        self._owner_device_id = device_id
        self._owner_session_id = session_id
        self._claimed_at = now
        return VoicePeWakeClaim(
            granted=True,
            owner_device_id=device_id,
            owner_session_id=session_id,
        )


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


# ---------------------------------------------------------------------------
# Voice PE speaker-profile capture
#
# Capture-only diagnostic phase. This DOES NOT decide whether a speaker is
# Aaron and DOES NOT block transcripts. It stores the exact 24 kHz PCM span
# associated with each OpenAI server-VAD item_id so that speaker embeddings
# can be calibrated later.
# ---------------------------------------------------------------------------

SPEAKER_CAPTURE_RATE = INPUT_RATE
SPEAKER_CAPTURE_SAMPLE_WIDTH = 2
SPEAKER_CAPTURE_DIR = Path("/tmp/jarvis-speaker-captures")
SPEAKER_CAPTURE_DIAGNOSTICS = (
    os.getenv("JARVIS_SPEAKER_CAPTURE_DIAGNOSTICS", "false")
    .strip().casefold() in {"1", "true", "yes", "on"}
)

SPEAKER_VERIFY_URL = os.getenv(
    "JARVIS_SPEAKER_VERIFY_URL",
    "http://jarvis-speaker-verifier:8091/score",
).strip()
SPEAKER_VERIFY_TIMEOUT_SECONDS = 3.0

# Conservative Core-side speaker suppression.
#
# OFF by default. The sidecar remains observe-only.
# Core fails open for short audio, setup/wake-like residue,
# missing audio, verifier errors, policy mismatches, uncertain
# results and Aaron-like results.
SPEAKER_SUPPRESSION_ENABLED = (
    os.getenv(
        "JARVIS_SPEAKER_SUPPRESSION_ENABLED",
        "false",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)

SPEAKER_SUPPRESSION_MIN_SECONDS = 1.0
SPEAKER_SUPPRESSION_POLICY_VERSION = "V1.3"



def _speaker_capture_append_pcm(
    state: dict[str, Any],
    pcm: bytes,
) -> None:
    archive = state.get("speaker_capture_pcm_24k")

    if not isinstance(archive, bytearray):
        archive = bytearray()
        state["speaker_capture_pcm_24k"] = archive

    archive.extend(pcm)


def _speaker_capture_mark_start(
    state: dict[str, Any],
    event: dict[str, Any],
) -> None:
    item_id = str(event.get("item_id") or "").strip()
    audio_start_ms = event.get("audio_start_ms")

    if (
        not item_id
        or not isinstance(audio_start_ms, (int, float))
    ):
        return

    segments = state.get("speaker_capture_segments")

    if not isinstance(segments, dict):
        segments = {}
        state["speaker_capture_segments"] = segments

    segments[item_id] = {
        "start_ms": int(audio_start_ms),
    }


def _speaker_capture_mark_stop(
    state: dict[str, Any],
    event: dict[str, Any],
) -> None:
    item_id = str(event.get("item_id") or "").strip()
    audio_end_ms = event.get("audio_end_ms")

    if (
        not item_id
        or not isinstance(audio_end_ms, (int, float))
    ):
        return

    segments = state.get("speaker_capture_segments")

    if not isinstance(segments, dict):
        segments = {}
        state["speaker_capture_segments"] = segments

    segment = segments.get(item_id)

    if not isinstance(segment, dict):
        segment = {}
        segments[item_id] = segment

    segment["end_ms"] = int(audio_end_ms)


def _speaker_capture_write_segment(
    state: dict[str, Any],
    event: dict[str, Any],
    transcript: str,
) -> None:
    item_id = str(event.get("item_id") or "").strip()

    if not item_id:
        return

    segments = state.get("speaker_capture_segments")

    if not isinstance(segments, dict):
        return

    segment = segments.get(item_id)

    if not isinstance(segment, dict):
        _LOGGER.info(
            "JARVIS SPEAKER CAPTURE SKIP: "
            "missing VAD segment item=%s transcript=%r",
            item_id,
            transcript,
        )
        return

    start_ms = segment.get("start_ms")
    end_ms = segment.get("end_ms")

    if (
        not isinstance(start_ms, int)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        _LOGGER.info(
            "JARVIS SPEAKER CAPTURE SKIP: "
            "invalid timing item=%s start_ms=%r end_ms=%r "
            "transcript=%r",
            item_id,
            start_ms,
            end_ms,
            transcript,
        )
        return

    archive = state.get("speaker_capture_pcm_24k")

    if not isinstance(archive, bytearray):
        return

    start_frame = round(
        (start_ms / 1000.0) * SPEAKER_CAPTURE_RATE
    )
    end_frame = round(
        (end_ms / 1000.0) * SPEAKER_CAPTURE_RATE
    )

    start_byte = max(
        0,
        start_frame * SPEAKER_CAPTURE_SAMPLE_WIDTH,
    )
    end_byte = min(
        len(archive),
        end_frame * SPEAKER_CAPTURE_SAMPLE_WIDTH,
    )

    if end_byte <= start_byte:
        _LOGGER.info(
            "JARVIS SPEAKER CAPTURE SKIP: "
            "audio not yet available item=%s "
            "start_byte=%s end_byte=%s archive=%s",
            item_id,
            start_byte,
            end_byte,
            len(archive),
        )
        return

    pcm = bytes(archive[start_byte:end_byte])

    SPEAKER_CAPTURE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_item = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        item_id,
    )[:80]

    stamp = time.time_ns()

    wav_path = SPEAKER_CAPTURE_DIR / (
        f"{stamp}-{safe_item}.wav"
    )

    json_path = wav_path.with_suffix(".json")

    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SPEAKER_CAPTURE_SAMPLE_WIDTH)
        wav_file.setframerate(SPEAKER_CAPTURE_RATE)
        wav_file.writeframes(pcm)

    metadata = {
        "item_id": item_id,
        "transcript": transcript,
        "audio_start_ms": start_ms,
        "audio_end_ms": end_ms,
        "duration_ms": end_ms - start_ms,
        "sample_rate": SPEAKER_CAPTURE_RATE,
        "sample_width_bytes": SPEAKER_CAPTURE_SAMPLE_WIDTH,
        "channels": 1,
        "pcm_bytes": len(pcm),
        "wav": str(wav_path),
    }

    json_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    _LOGGER.info(
        "JARVIS SPEAKER CAPTURE SAVED | "
        "item=%s duration_ms=%s bytes=%s "
        "transcript=%r wav=%s",
        item_id,
        end_ms - start_ms,
        len(pcm),
        transcript,
        wav_path,
    )





def _speaker_capture_segment_pcm(
    state: dict[str, Any],
    event: dict[str, Any],
) -> bytes | None:
    """Return the exact 24 kHz PCM span for one VAD item."""

    item_id = str(
        event.get("item_id")
        or ""
    ).strip()

    if not item_id:
        return None

    segments = state.get(
        "speaker_capture_segments"
    )

    if not isinstance(
        segments,
        dict,
    ):
        return None

    segment = segments.get(
        item_id
    )

    if not isinstance(
        segment,
        dict,
    ):
        return None

    start_ms = segment.get(
        "start_ms"
    )

    end_ms = segment.get(
        "end_ms"
    )

    if (
        not isinstance(
            start_ms,
            int,
        )
        or not isinstance(
            end_ms,
            int,
        )
        or end_ms <= start_ms
    ):
        return None

    archive = state.get(
        "speaker_capture_pcm_24k"
    )

    if not isinstance(
        archive,
        bytearray,
    ):
        return None

    start_frame = round(
        (
            start_ms
            / 1000.0
        )
        * SPEAKER_CAPTURE_RATE
    )

    end_frame = round(
        (
            end_ms
            / 1000.0
        )
        * SPEAKER_CAPTURE_RATE
    )

    start_byte = max(
        0,
        start_frame
        * SPEAKER_CAPTURE_SAMPLE_WIDTH,
    )

    end_byte = min(
        len(archive),
        end_frame
        * SPEAKER_CAPTURE_SAMPLE_WIDTH,
    )

    if end_byte <= start_byte:
        return None

    return bytes(
        archive[
            start_byte:end_byte
        ]
    )


def _speaker_verify_request(
    pcm: bytes,
) -> dict[str, Any]:
    """Blocking HTTP request; called only through asyncio.to_thread()."""

    import urllib.request

    request = urllib.request.Request(
        SPEAKER_VERIFY_URL,
        data=pcm,
        method="POST",
        headers={
            "Content-Type":
                "application/octet-stream",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=SPEAKER_VERIFY_TIMEOUT_SECONDS,
    ) as response:
        payload = json.load(
            response
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "speaker verifier returned "
            "non-object response"
        )

    return payload



async def _speaker_verify_gate_decision(
    event: dict[str, Any],
    transcript: str,
    pcm: bytes,
) -> tuple[bool, str]:
    """Return True only for confirmed V1.3 strong background.

    This function is deliberately fail-open.
    """

    item_id = str(
        event.get("item_id")
        or ""
    ).strip()

    if (
        not item_id
        or not pcm
    ):
        return (
            False,
            "MISSING_AUDIO_FAIL_OPEN",
        )

    duration_seconds = (
        len(pcm)
        / (
            SPEAKER_CAPTURE_RATE
            * SPEAKER_CAPTURE_SAMPLE_WIDTH
        )
    )

    if (
        duration_seconds
        < SPEAKER_SUPPRESSION_MIN_SECONDS
    ):
        _LOGGER.info(
            "JARVIS SPEAKER GATE BYPASS | "
            "item=%s reason=SHORT_ALLOW "
            "duration=%.3f transcript=%r",
            item_id,
            duration_seconds,
            transcript,
        )

        return (
            False,
            "SHORT_ALLOW",
        )

    started = time.monotonic()

    try:
        result = await asyncio.to_thread(
            _speaker_verify_request,
            pcm,
        )

    except Exception as exc:
        _LOGGER.warning(
            "JARVIS SPEAKER GATE FAIL OPEN | "
            "item=%s reason=VERIFY_ERROR "
            "error=%s transcript=%r",
            item_id,
            str(exc)[:300],
            transcript,
        )

        return (
            False,
            "VERIFY_ERROR_FAIL_OPEN",
        )

    elapsed_ms = round(
        (
            time.monotonic()
            - started
        )
        * 1000
    )

    policy_version = str(
        result.get(
            "policy_version",
            "",
        )
    )

    policy_decision = str(
        result.get(
            "policy_decision",
            "UNKNOWN",
        )
    )

    suppress_candidate = (
        result.get(
            "policy_suppress_candidate"
        )
        is True
    )

    window_short_score = result.get(
        "window_short_score"
    )

    if (
        policy_version
        != SPEAKER_SUPPRESSION_POLICY_VERSION
    ):
        _LOGGER.warning(
            "JARVIS SPEAKER GATE FAIL OPEN | "
            "item=%s reason=POLICY_VERSION "
            "expected=%s actual=%s "
            "decision=%s request_ms=%s "
            "transcript=%r",
            item_id,
            SPEAKER_SUPPRESSION_POLICY_VERSION,
            policy_version,
            policy_decision,
            elapsed_ms,
            transcript,
        )

        return (
            False,
            "POLICY_VERSION_FAIL_OPEN",
        )

    suppress = (
        policy_decision
        == "STRONG_BACKGROUND"
        and suppress_candidate
    )

    reason = (
        "STRONG_BACKGROUND_DROP"
        if suppress
        else "ALLOW"
    )

    _LOGGER.info(
        "JARVIS SPEAKER GATE | "
        "item=%s duration=%.3f "
        "window_short=%s "
        "decision=%s "
        "candidate=%s "
        "suppress=%s "
        "reason=%s "
        "request_ms=%s "
        "transcript=%r",
        item_id,
        duration_seconds,
        window_short_score,
        policy_decision,
        suppress_candidate,
        suppress,
        reason,
        elapsed_ms,
        transcript,
    )

    return (
        suppress,
        reason,
    )

def _speaker_verify_schedule(
    state: dict[str, Any],
    event: dict[str, Any],
    transcript: str,
    pcm: bytes,
) -> None:
    """Schedule observe-only verification without delaying the brain."""

    item_id = str(
        event.get("item_id")
        or ""
    ).strip()

    if (
        not item_id
        or not pcm
    ):
        return

    tasks = state.get(
        "speaker_verify_tasks"
    )

    if not isinstance(
        tasks,
        set,
    ):
        tasks = set()
        state[
            "speaker_verify_tasks"
        ] = tasks

    async def observe() -> None:
        started = time.monotonic()

        try:
            result = await asyncio.to_thread(
                _speaker_verify_request,
                pcm,
            )

        except Exception as exc:
            _LOGGER.warning(
                "JARVIS SPEAKER VERIFY FAILED | "
                "item=%s error=%s transcript=%r",
                item_id,
                str(exc)[:300],
                transcript,
            )
            return

        elapsed_ms = round(
            (
                time.monotonic()
                - started
            )
            * 1000
        )

        score = result.get(
            "score"
        )

        classification = str(
            result.get(
                "classification",
                "UNKNOWN",
            )
        )

        action = str(
            result.get(
                "action",
                "OBSERVE_ONLY",
            )
        )

        inference_ms = result.get(
            "inference_ms"
        )

        gate_enabled = bool(
            result.get(
                "gate_enabled",
                False,
            )
        )

        _LOGGER.info(
            "JARVIS SPEAKER VERIFY | "
            "item=%s score=%s "
            "classification=%s "
            "action=%s "
            "gate_enabled=%s "
            "request_ms=%s "
            "inference_ms=%s "
            "bytes=%s "
            "transcript=%r",
            item_id,
            score,
            classification,
            action,
            gate_enabled,
            elapsed_ms,
            inference_ms,
            len(pcm),
            transcript,
        )

    task = asyncio.create_task(
        observe()
    )

    tasks.add(
        task
    )

    task.add_done_callback(
        tasks.discard
    )


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
                "",
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
    transcription_prompt: str | None = None,
) -> dict[str, Any]:
    turn_detection: dict[str, Any] | None
    if normalise_conversation_mode(conversation_mode) == CONVERSATION_MODE_LIVE:
        turn_detection = {
            "type": "server_vad",
            "threshold": 0.85,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
            "create_response": False,
            "interrupt_response": True,
        }
    else:
        turn_detection = None

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "include": ["item.input_audio_transcription.logprobs"],
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
                    "noise_reduction": {"type": "far_field"},
                    "transcription": {
                        "model": "gpt-4o-transcribe",
                        "language": "en",
                        "prompt": (
                            transcription_prompt[:1024]
                            if transcription_prompt is not None
                            else config.transcription_prompt[:1024]
                        ),
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


def input_transcription_confidence(event: dict[str, Any]) -> float | None:
    """Return geometric mean token probability from optional Realtime logprobs."""
    raw = event.get("logprobs")
    if not isinstance(raw, list):
        return None
    values: list[float] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = item.get("logprob")
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(max(-20.0, min(0.0, float(value))))
    if not values:
        return None
    return round(math.exp(sum(values) / len(values)), 4)


def openai_websocket_url(model: str) -> str:
    return f"wss://api.openai.com/v1/realtime?model={quote(model, safe='-.')}"


def audio_append_event(pcm: bytes) -> dict[str, str]:
    return {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(pcm).decode("ascii"),
    }


def speak_response_event(
    text: str,
    voice: str,
    *,
    generation: int = 0,
    client_turn_id: int = 0,
) -> dict[str, Any]:
    cleaned = " ".join(str(text or "").split())
    response_metadata = {"source": "jarvis_core", "release": VERSION}
    if generation > 0:
        response_metadata["jarvis_generation"] = str(int(generation))
    if client_turn_id > 0:
        response_metadata["jarvis_client_turn_id"] = str(int(client_turn_id))
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
            "metadata": response_metadata,
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


def _provider_epoch_is_current(
    state: dict[str, Any],
    provider_epoch: int,
) -> bool:
    """Allow legacy/unit-test calls with epoch zero, fence live provider tasks."""

    return provider_epoch <= 0 or provider_epoch == _safe_int(
        state.get("provider_epoch")
    )


def _turn_is_active(state: dict[str, Any]) -> bool:
    return bool(state.get("turn_in_progress"))


def _mark_turn_started(state: dict[str, Any]) -> None:
    state["turn_in_progress"] = True
    terminal = state.get("turn_terminal_event")
    if isinstance(terminal, asyncio.Event):
        terminal.clear()


def _mark_turn_terminal(
    state: dict[str, Any],
    generation: int | None = None,
) -> None:
    """Mark the safe renewal boundary after all legitimate turn audio ends."""

    if (
        generation is not None
        and _safe_int(state.get("turn_in_progress_generation")) != generation
    ):
        return
    state["turn_in_progress"] = False
    state.pop("turn_in_progress_generation", None)
    terminal = state.get("turn_terminal_event")
    if isinstance(terminal, asyncio.Event):
        terminal.set()


def _turn_payload(
    generation: int,
    client_turn_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event = dict(payload)
    event["generation"] = max(0, int(generation))
    if client_turn_id > 0:
        event["client_turn_id"] = int(client_turn_id)
    return event


def _active_turn_payload(
    state: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _turn_payload(
        _safe_int(state.get("active_generation"), _safe_int(state.get("generation"))),
        _safe_int(state.get("active_client_turn_id")),
        payload,
    )


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
        self.speaker_identity = SpeakerIdentityClient.from_environment()
        self.speaker_identity_runtime = SpeakerIdentityRuntime(
            self.speaker_identity,
            self.config.user_id,
            self.config.user_is_admin,
        )
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
        self.total_provider_renewals = 0
        self.total_provider_recoveries = 0
        self.total_voice_pe_wake_claims = 0
        self.total_voice_pe_wake_rejections = 0
        self.last_voice_pe_wake_owner: str | None = None
        self.last_error: str | None = None
        self._provider_epoch_counter = 0
        self.voice_pe_wake_arbiter = VoicePeWakeArbiter()
        self.voice_pe_session_started: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.voice_pe_session_ended: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.transcription_prompt_provider: Callable[[dict[str, Any]], Awaitable[str]] | None = None

    def _next_provider_epoch(self) -> int:
        self._provider_epoch_counter += 1
        return self._provider_epoch_counter

    async def _wait_for_safe_provider_renewal(
        self,
        state: dict[str, Any],
        lease: ProviderSessionLease,
    ) -> bool:
        """Wait until renewal is due and the current turn/audio is terminal."""

        remaining = lease.renewal_at - time.monotonic()
        if remaining > 0:
            try:
                await asyncio.wait_for(
                    asyncio.Event().wait(),
                    timeout=remaining,
                )
            except TimeoutError:
                pass

        if not _provider_epoch_is_current(state, lease.epoch):
            return False

        lease.renewal_required(time.monotonic())
        state["provider_renewal_pending"] = True

        if _turn_is_active(state):
            terminal = state.get("turn_terminal_event")
            if not isinstance(terminal, asyncio.Event):
                return False
            while _turn_is_active(state):
                await terminal.wait()
                if not _provider_epoch_is_current(state, lease.epoch):
                    return False

        if not _provider_epoch_is_current(state, lease.epoch):
            return False

        # No await is allowed between this final idle check and transition
        # ownership. A client command arriving afterwards is queued for the new
        # provider instead of starting on the connection being retired.
        state["provider_transitioning"] = True
        return True

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
            "total_provider_renewals": self.total_provider_renewals,
            "total_provider_recoveries": self.total_provider_recoveries,
            "total_voice_pe_wake_claims": self.total_voice_pe_wake_claims,
            "total_voice_pe_wake_rejections": self.total_voice_pe_wake_rejections,
            "last_voice_pe_wake_owner": self.last_voice_pe_wake_owner,
            "voice_pe_wake_contention_ms": round(
                self.voice_pe_wake_arbiter.contention_seconds * 1000
            ),
            "mobile_context_protocol": "alpha5.1",
            "realtime_protocol_version": REALTIME_PROTOCOL_VERSION,
            "timezone": self.config.timezone,
            "uptime_seconds": max(0, round(time.time() - self.started_at)),
            "last_error": self.last_error,
            "provider_session_lifetime_seconds": PROVIDER_SESSION_LIFETIME_SECONDS,
            "provider_session_renewal_lead_seconds": PROVIDER_SESSION_RENEWAL_LEAD_SECONDS,
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
        session_id = f"{client_kind}-{uuid.uuid4()}"
        metadata["session_id"] = session_id
        metadata["voice_endpoint_kind"] = (
            "voice_pe"
            if client_kind == "voice_pe"
            else "android"
        )

        for key in ("device_id", "area_id", "user_name"):
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

        if client_kind == "voice_pe" and self.speaker_identity.enabled:
            # The Voice PE token authenticates the device, not the human.
            # Personal identity starts as Guest and is resolved per utterance.
            metadata["user_id"] = "guest"
            metadata["user_name"] = "Guest"
            metadata["user_is_admin"] = False
            metadata["speaker_household_admin"] = False
            metadata["speaker_id"] = "unknown"
            metadata["speaker_name"] = "Unknown"
            metadata["speaker_recognized"] = False

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
        requested_voice = str(auth_payload.get("voice") or "").strip().casefold()
        voice = normalise_voice(requested_voice, self.config.voice)
        eagerness = normalise_eagerness(auth_payload.get("vad_eagerness"))
        metadata.update(
            voice_mode=voice_mode,
            voice=voice,
            requested_voice=requested_voice,
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

        if client_kind == "voice_pe":
            device_id = str(metadata.get("device_id") or "").strip()
            if not device_id or device_id == "jarvis_android":
                await self._send_json(
                    client,
                    {
                        "type": "auth.error",
                        "message": "Voice satellite requires a stable device_id",
                    },
                )
                await self._close(client, 4400)
                return

            claim = self.voice_pe_wake_arbiter.claim(device_id, session_id)
            if not claim.granted:
                self.total_voice_pe_wake_rejections += 1
                runtime_metrics.increment("voice_wake_contention_rejected")
                _LOGGER.info(
                    "VOICE_PE_WAKE_REJECTED device=%s session=%s owner_device=%s owner_session=%s retry_after_ms=%s",
                    device_id,
                    session_id,
                    claim.owner_device_id,
                    claim.owner_session_id,
                    claim.retry_after_ms,
                )
                await self._send_json(
                    client,
                    {
                        "type": "session.close",
                        "reason": "wake_contention",
                        "owner_device_id": claim.owner_device_id,
                        "retry_after_ms": claim.retry_after_ms,
                    },
                )
                await self._close(client, 4429)
                return

            self.total_voice_pe_wake_claims += 1
            runtime_metrics.increment("voice_wake_claimed")
            self.last_voice_pe_wake_owner = device_id
            _LOGGER.info(
                "VOICE_PE_WAKE_CLAIMED device=%s session=%s",
                device_id,
                session_id,
            )

        duck_task: asyncio.Task[Any] | None = None
        if client_kind == "voice_pe" and self.voice_pe_session_started is not None:
            duck_task = asyncio.create_task(self.voice_pe_session_started(metadata))

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
                "protocol_version": REALTIME_PROTOCOL_VERSION,
            },
        )

        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        turn_tasks: set[asyncio.Task[Any]] = set()
        turn_terminal_event = asyncio.Event()
        turn_terminal_event.set()
        state: dict[str, Any] = {
            "generation": 0,
            "suppress_audio": False,
            "voice_pe_session_started_at": time.monotonic(),
            "provider_epoch": 0,
            "provider_renewal_pending": False,
            "provider_transitioning": False,
            "turn_terminal_event": turn_terminal_event,
        }
        self.active_sessions += 1
        self.total_sessions += 1
        try:
            session_transcription_prompt = self.config.transcription_prompt
            if self.transcription_prompt_provider is not None:
                try:
                    session_transcription_prompt = await self.transcription_prompt_provider(metadata)
                except Exception:
                    _LOGGER.exception("Dynamic transcription vocabulary failed")
            websocket_connect = _load_websocket_connect()
            provider_connection_index = 0
            while True:
                provider_epoch = self._next_provider_epoch()
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
                    provider_connection_index += 1
                    established_at = time.monotonic()
                    lease = ProviderSessionLease(
                        epoch=provider_epoch,
                        established_at=established_at,
                    )
                    state["provider_epoch"] = provider_epoch
                    state["provider_established_at"] = established_at
                    state["provider_renewal_at"] = lease.renewal_at
                    state["provider_renewal_pending"] = False
                    state["provider_transitioning"] = False
                    state["openai_response_turns"] = {}
                    await upstream.send(
                        json.dumps(build_session_update(
                            self.config,
                            voice,
                            conversation_mode,
                            eagerness,
                            transcription_prompt=session_transcription_prompt,
                        ))
                    )
                    await self._send_json(
                        client,
                        {
                            "type": "status",
                            "message": (
                                "Connecting Jarvis voice"
                                if provider_connection_index == 1
                                else "Renewing Jarvis voice session"
                            ),
                        },
                    )

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
                            provider_epoch=provider_epoch,
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
                            provider_epoch=provider_epoch,
                        )
                    )
                    renewal_task = asyncio.create_task(
                        self._wait_for_safe_provider_renewal(
                            state,
                            lease,
                        )
                    )
                    done, pending = await asyncio.wait(
                        {client_task, upstream_task, renewal_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    client_finished = client_task in done
                    renewal_requested = (
                        renewal_task in done
                        and not renewal_task.cancelled()
                        and renewal_task.exception() is None
                        and renewal_task.result() is True
                    )

                    # Invalidate the provider before cancelling its readers. Any
                    # delayed callback from this point is harmless by epoch.
                    state["provider_epoch"] = 0
                    state["provider_renewal_pending"] = False
                    if not renewal_requested:
                        # Invalidate turn generation in the same scheduler slice
                        # as the provider epoch. Generation-fenced TTS/background
                        # work cannot emit during reader/task cancellation.
                        state["generation"] = _safe_int(state.get("generation")) + 1
                        state["suppress_audio"] = True
                        for task in tuple(turn_tasks):
                            task.cancel()
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(
                        *pending,
                        *tuple(turn_tasks),
                        return_exceptions=True,
                    )

                    if renewal_task in done and renewal_task.exception() is not None:
                        raise renewal_task.exception()
                    if client_task in done and client_task.exception() is not None:
                        raise client_task.exception()
                    if upstream_task in done and upstream_task.exception() is not None:
                        self.last_error = str(upstream_task.exception())[:500]
                        _LOGGER.warning(
                            "Realtime provider connection ended; recovering with a new epoch: %s",
                            self.last_error,
                        )

                    if client_finished:
                        return

                    if not renewal_requested:
                        # Unexpected provider loss may have happened during a
                        # side-effecting turn. Never replay it automatically.
                        turn_tasks.clear()
                        _mark_turn_terminal(state)
                        await self._send_json(
                            client,
                            {
                                "type": "error",
                                "message": "Realtime provider connection was interrupted; the turn was not replayed.",
                            },
                        )
                        self.total_provider_recoveries += 1
                    else:
                        self.total_provider_renewals += 1

                    # Proactive renewal only reaches here at a terminal turn/audio
                    # boundary. Unexpected recovery has invalidated its old turn.
                    state["suppress_audio"] = False
                    state.pop("active_generation", None)
                    state.pop("active_client_turn_id", None)
                    state.pop("cancelled_client_turn_id", None)
                    state.pop("early_audio_done", None)
                    state.pop("early_speech_active", None)
                    state.pop("continuation_speech_active", None)
                    state.pop("queued_speech_remainder", None)
                    continue
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
            if duck_task is not None:
                await asyncio.gather(duck_task, return_exceptions=True)
            if client_kind == "voice_pe" and self.voice_pe_session_ended is not None:
                try:
                    await self.voice_pe_session_ended(metadata)
                except Exception:
                    _LOGGER.exception("Voice PE media restoration failed")
            await self._close(client, 1000)

    async def _speaker_identity_process(
        self,
        client: Any,
        upstream: Any,
        metadata: dict[str, Any],
        state: dict[str, Any],
        transcript: str,
        pcm: bytes,
        voice: str,
    ) -> bool:
        async def send(payload: dict[str, Any]) -> None:
            await self._send_json(client, payload)

        async def speak(text: str) -> None:
            cleaned = " ".join(str(text or "").split()).strip()
            if not cleaned:
                return
            state["speaker_prompt_started"] = True
            _mark_turn_started(state)
            state["suppress_audio"] = False
            await self._send_json(
                client,
                {"type": "speaker.prompt", "text": cleaned},
            )

            # Voice-ID prompts must use the same configured speech renderer
            # as normal Jarvis replies. Voice PE uses ElevenLabs when
            # JARVIS_TTS_PROVIDER=elevenlabs; only fall back to OpenAI
            # Realtime speech when the configured Jarvis renderer fails.
            turn_generation = _safe_int(state.get("generation"))
            turn_id = _safe_int(state.get("active_client_turn_id"))
            if self._use_direct_elevenlabs(metadata):
                handled = await self._stream_elevenlabs_response(
                    client,
                    cleaned,
                    state,
                    generation=turn_generation,
                    client_turn_id=turn_id,
                    finalize_turn=True,
                )
                if handled:
                    return

            await upstream.send(
                json.dumps(
                    speak_response_event(
                        cleaned,
                        voice,
                        generation=turn_generation,
                        client_turn_id=turn_id,
                    )
                )
            )

        return await self.speaker_identity_runtime.process(
            transcript,
            pcm,
            metadata,
            state,
            send,
            speak,
        )

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
        provider_epoch: int = 0,
    ) -> None:
        while True:
            queued_messages = state.get("queued_client_messages")
            if isinstance(queued_messages, list) and queued_messages:
                message = queued_messages.pop(0)
            else:
                message = await client.receive()
            if not _provider_epoch_is_current(state, provider_epoch):
                return
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                return
            if bool(state.get("provider_transitioning")):
                # Preserve the one message already removed from the client
                # socket while renewal owns the boundary. The replacement
                # provider consumes it before reading another client message.
                state.setdefault("queued_client_messages", []).append(message)
                await asyncio.Event().wait()

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

                if metadata.get("client_kind") == "voice_pe":
                    _speaker_capture_append_pcm(
                        state,
                        pcm,
                    )

                state["pcm_diagnostic_chunks"] = (
                    int(state.get("pcm_diagnostic_chunks", 0)) + 1
                )
                diagnostic_chunks = int(state["pcm_diagnostic_chunks"])

                if diagnostic_chunks == 1 or diagnostic_chunks % 100 == 0:
                    sample_values = [
                        int.from_bytes(
                            pcm[index:index + 2],
                            byteorder="little",
                            signed=True,
                        )
                        for index in range(0, len(pcm) - 1, 2)
                    ]

                    if sample_values:
                        sample_count = len(sample_values)

                        dc_mean = (
                            sum(sample_values) /
                            sample_count
                        )

                        raw_rms = int(
                            (
                                sum(
                                    sample * sample
                                    for sample in sample_values
                                ) /
                                sample_count
                            )
                            ** 0.5
                        )

                        centered_rms = int(
                            (
                                sum(
                                    (sample - dc_mean)
                                    * (sample - dc_mean)
                                    for sample in sample_values
                                ) /
                                sample_count
                            )
                            ** 0.5
                        )

                        raw_peak = max(
                            abs(sample)
                            for sample in sample_values
                        )

                        centered_peak = int(
                            max(
                                abs(sample - dc_mean)
                                for sample in sample_values
                            )
                        )

                        clipped_samples = sum(
                            1
                            for sample in sample_values
                            if abs(sample) >= 32760
                        )

                        _LOGGER.info(
                            "Voice PE PCM diagnostic: "
                            "chunks=%d bytes=%d samples=%d "
                            "dc_mean=%.1f raw_rms=%d "
                            "centered_rms=%d raw_peak=%d "
                            "centered_peak=%d clipped=%d",
                            diagnostic_chunks,
                            len(pcm),
                            sample_count,
                            dc_mean,
                            raw_rms,
                            centered_rms,
                            raw_peak,
                            centered_peak,
                            clipped_samples,
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
                cancelled_client_turn_id = _safe_int(
                    payload.get("client_turn_id"),
                    _safe_int(state.get("active_client_turn_id")),
                )
                cancelled_generation = _safe_int(
                    state.get("active_generation"),
                    _safe_int(state.get("generation")),
                )
                state["cancelled_client_turn_id"] = cancelled_client_turn_id
                state["cancelled_generation"] = cancelled_generation
                state["generation"] = max(
                    _safe_int(state.get("generation")),
                    cancelled_generation,
                ) + 1
                state["suppress_audio"] = True
                state.pop("active_generation", None)
                state.pop("active_client_turn_id", None)
                state.pop("queued_speech_remainder", None)
                state["early_audio_done"] = False
                state["early_speech_active"] = False
                state["continuation_speech_active"] = False
                _LOGGER.info(
                    "JARVIS DIAG | CANCEL ACCEPTED | generation=%s "
                    "client_turn_id=%s fence_generation=%s tasks=%s",
                    cancelled_generation,
                    cancelled_client_turn_id,
                    state["generation"],
                    len(turn_tasks),
                )
                await upstream.send(json.dumps({"type": "response.cancel"}))
                tasks_to_cancel = tuple(turn_tasks)
                for task in tasks_to_cancel:
                    task.cancel()
                if tasks_to_cancel:
                    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                _mark_turn_terminal(state, cancelled_generation)
                await self._send_json(
                    client,
                    _turn_payload(
                        cancelled_generation,
                        cancelled_client_turn_id,
                        {"type": "turn.cancelled", "status": "cancelled"},
                    ),
                )
                _LOGGER.info(
                    "JARVIS DIAG | CANCEL COMPLETE | generation=%s "
                    "client_turn_id=%s remaining_tasks=%s",
                    cancelled_generation,
                    cancelled_client_turn_id,
                    len(turn_tasks),
                )
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
                        client_turn_id=_safe_int(payload.get("client_turn_id")),
                        provider_epoch=provider_epoch,
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
        provider_epoch: int = 0,
    ) -> None:
        try:
            await self._consume_openai_events(
                client,
                upstream,
                brain_handler,
                metadata,
                voice_mode,
                conversation_mode,
                voice,
                turn_tasks,
                state,
                provider_epoch=provider_epoch,
            )
        finally:
            if _provider_epoch_is_current(state, provider_epoch):
                # Fence client input in the same task that observes provider
                # closure; the supervisor will either renew or recover next.
                state["provider_transitioning"] = True

    async def _consume_openai_events(
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
        provider_epoch: int = 0,
    ) -> None:
        async for raw in upstream:
            if not _provider_epoch_is_current(state, provider_epoch):
                return
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = str(event.get("type") or "")
            if kind == "session.updated":
                self.last_error = None
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
                runtime_metrics.increment("voice_speech_started")
                if metadata.get("client_kind") == "voice_pe":
                    _speaker_capture_mark_start(
                        state,
                        event,
                    )

                _LOGGER.info(
                    "JARVIS DIAG | SPEECH STARTED | generation=%s turn_in_progress=%s",
                    state.get("generation"),
                    state.get("turn_in_progress"),
                )
                if (
                    metadata.get("client_kind") == "voice_pe"
                    and bool(state.get("turn_in_progress"))
                ):
                    continue

                if metadata.get("client_kind") == "voice_pe":
                    state["voice_pe_speech_active"] = True
                    state["voice_pe_speech_start_count"] = (
                        int(state.get("voice_pe_speech_start_count", 0)) + 1
                    )
                    state["voice_pe_speech_started_at"] = time.monotonic()

                state["generation"] = int(state.get("generation", 0)) + 1
                state["suppress_audio"] = True
                await self._send_json(client, {"type": "speech.started"})
            elif kind == "input_audio_buffer.speech_stopped":
                if metadata.get("client_kind") == "voice_pe":
                    _speaker_capture_mark_stop(
                        state,
                        event,
                    )

                _LOGGER.info(
                    "JARVIS DIAG | SPEECH STOPPED | generation=%s turn_in_progress=%s",
                    state.get("generation"),
                    state.get("turn_in_progress"),
                )
                if (
                    metadata.get("client_kind") == "voice_pe"
                    and bool(state.get("turn_in_progress"))
                ):
                    continue

                if metadata.get("client_kind") == "voice_pe":
                    speech_started_at = state.get("voice_pe_speech_started_at")
                    if isinstance(speech_started_at, (int, float)):
                        state["voice_pe_last_completed_speech_ms"] = round(
                            (time.monotonic() - float(speech_started_at)) * 1000
                        )
                    state["voice_pe_speech_active"] = False

                await self._send_json(client, {"type": "speech.stopped"})
            elif kind == "conversation.item.input_audio_transcription.completed":
                transcript = str(
                    event.get("transcript") or ""
                ).strip()
                transcription_confidence = input_transcription_confidence(event)
                if transcription_confidence is not None:
                    metadata["transcription_confidence"] = transcription_confidence
                    runtime_metrics.set_gauge(
                        "voice_transcription_confidence",
                        transcription_confidence,
                    )
                    if transcription_confidence < 0.20:
                        runtime_metrics.increment("voice_low_confidence_transcripts")
                runtime_metrics.increment("voice_transcripts_completed")
                speech_started_at = state.get("voice_pe_speech_started_at")
                if isinstance(speech_started_at, (int, float)):
                    runtime_metrics.observe(
                        "speech_start_to_transcript_ms",
                        (time.monotonic() - float(speech_started_at)) * 1000,
                    )

                _LOGGER.info(
                    "JARVIS DIAG | STT FINAL | JARVIS HEARD: %r",
                    transcript,
                )

                speaker_pcm = b""

                if metadata.get("client_kind") == "voice_pe":
                    if SPEAKER_CAPTURE_DIAGNOSTICS:
                        _speaker_capture_write_segment(
                            state,
                            event,
                            transcript,
                        )
                    speaker_pcm = (
                        _speaker_capture_segment_pcm(
                            state,
                            event,
                        )
                    )
                    if (
                        speaker_pcm
                        and not SPEAKER_SUPPRESSION_ENABLED
                    ):
                        _speaker_verify_schedule(
                            state,
                            event,
                            transcript,
                            speaker_pcm,
                        )

                if (
                    transcript
                    and conversation_mode
                    == CONVERSATION_MODE_LIVE
                ):
                    # Own the utterance before any awaited identity/routing work
                    # so renewal cannot consume and then lose this transcript.
                    _mark_turn_started(state)
                    if metadata.get("client_kind") == "voice_pe":
                        transcript_index = (
                            int(state.get("voice_pe_transcripts_seen", 0)) + 1
                        )
                        state["voice_pe_transcripts_seen"] = transcript_index

                        last_speech_ms = state.get(
                            "voice_pe_last_completed_speech_ms"
                        )
                        session_started_at = state.get(
                            "voice_pe_session_started_at"
                        )
                        session_age_ms = (
                            round(
                                (
                                    time.monotonic()
                                    - float(session_started_at)
                                )
                                * 1000
                            )
                            if isinstance(
                                session_started_at,
                                (int, float),
                            )
                            else None
                        )

                        drop_wake_residue = (
                            transcript_index == 1
                            and not bool(
                                state.get("voice_pe_wake_guard_used")
                            )
                            and bool(
                                state.get("voice_pe_speech_active")
                            )
                            and int(
                                state.get(
                                    "voice_pe_speech_start_count",
                                    0,
                                )
                            )
                            >= 2
                            and isinstance(
                                last_speech_ms,
                                (int, float),
                            )
                            and float(last_speech_ms) <= 750
                            and isinstance(session_age_ms, int)
                            and session_age_ms <= 6000
                            and len(transcript) <= 24
                            and len(transcript.split()) <= 3
                        )

                        if drop_wake_residue:
                            runtime_metrics.increment("voice_wake_residue_dropped")
                            state["voice_pe_wake_guard_used"] = True
                            _LOGGER.info(
                                "JARVIS DIAG | VOICE PE WAKE RESIDUE DROPPED | "
                                "transcript=%r previous_speech_ms=%s "
                                "session_age_ms=%s speech_starts=%s",
                                transcript,
                                last_speech_ms,
                                session_age_ms,
                                state.get("voice_pe_speech_start_count"),
                            )
                            _mark_turn_terminal(state)
                            continue

                    if (
                        metadata.get("client_kind") == "voice_pe"
                        and self.speaker_identity.enabled
                    ):
                        state.pop("speaker_prompt_started", None)
                        speaker_flow_handled = await self._speaker_identity_process(
                            client,
                            upstream,
                            metadata,
                            state,
                            transcript,
                            speaker_pcm,
                            voice,
                        )
                        if speaker_flow_handled:
                            if not bool(state.pop("speaker_prompt_started", False)):
                                _mark_turn_terminal(state)
                            continue

                    # Core-side conservative speaker suppression.
                    #
                    # Do not use the biometric result for a short,
                    # wake/setup-like first transcript. This protects
                    # wake residue while still allowing a real longer
                    # first command to be evaluated.
                    if SPEAKER_SUPPRESSION_ENABLED:
                        setup_or_wake_bypass = (
                            transcript_index == 1
                            and isinstance(
                                session_age_ms,
                                int,
                            )
                            and session_age_ms <= 6000
                            and len(transcript) <= 24
                            and len(
                                transcript.split()
                            ) <= 3
                        )

                        if setup_or_wake_bypass:
                            _LOGGER.info(
                                "JARVIS SPEAKER GATE BYPASS | "
                                "item=%s "
                                "reason=FIRST_SHORT_SETUP_ALLOW "
                                "transcript=%r",
                                str(
                                    event.get(
                                        "item_id"
                                    )
                                    or ""
                                ),
                                transcript,
                            )

                        elif not speaker_pcm:
                            _LOGGER.warning(
                                "JARVIS SPEAKER GATE FAIL OPEN | "
                                "item=%s "
                                "reason=NO_PCM "
                                "transcript=%r",
                                str(
                                    event.get(
                                        "item_id"
                                    )
                                    or ""
                                ),
                                transcript,
                            )

                        else:
                            (
                                suppress_background,
                                speaker_gate_reason,
                            ) = (
                                await _speaker_verify_gate_decision(
                                    event,
                                    transcript,
                                    speaker_pcm,
                                )
                            )

                            if suppress_background:
                                _LOGGER.info(
                                    "JARVIS SPEAKER BACKGROUND DROPPED | "
                                    "item=%s "
                                    "reason=%s "
                                    "transcript=%r",
                                    str(
                                        event.get(
                                            "item_id"
                                        )
                                        or ""
                                    ),
                                    speaker_gate_reason,
                                    transcript,
                                )

                                _mark_turn_terminal(state)
                                continue

                    transcript_payload: dict[str, Any] = {
                        "type": "user.transcript",
                        "text": transcript,
                    }
                    if transcription_confidence is not None:
                        transcript_payload["confidence"] = transcription_confidence
                    await self._send_json(client, transcript_payload)

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
                            _mark_turn_terminal(state)
                        else:
                            _mark_turn_started(state)
                            state["active_generation"] = _safe_int(state.get("generation"))
                            state["active_client_turn_id"] = 0
                            state[
                                "close_after_response"
                            ] = closure_kind
                            state["suppress_audio"] = False

                            if self._use_direct_elevenlabs(metadata):
                                handled = await self._stream_elevenlabs_response(
                                    client,
                                    closure_response,
                                    state,
                                    generation=_safe_int(state.get("generation")),
                                    client_turn_id=0,
                                )
                                if not handled:
                                    await upstream.send(
                                        json.dumps(
                                            speak_response_event(
                                                closure_response,
                                                voice,
                                                generation=_safe_int(state.get("generation")),
                                            )
                                        )
                                    )
                            else:
                                await upstream.send(
                                    json.dumps(
                                        speak_response_event(
                                            closure_response,
                                            voice,
                                            generation=_safe_int(state.get("generation")),
                                        )
                                    )
                                )
                    else:
                        turn_args = (
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
                        if provider_epoch > 0:
                            await self._start_brain_turn(
                                *turn_args,
                                provider_epoch=provider_epoch,
                                turn_already_started=True,
                            )
                        else:
                            await self._start_brain_turn(*turn_args)
            elif kind == "response.created":
                response_object = (
                    event.get("response")
                    if isinstance(event.get("response"), dict)
                    else {}
                )
                response_id = str(response_object.get("id") or "").strip()
                response_metadata = (
                    response_object.get("metadata")
                    if isinstance(response_object.get("metadata"), dict)
                    else {}
                )
                bound_generation = _safe_int(
                    response_metadata.get("jarvis_generation"),
                    _safe_int(
                        state.get("active_generation"),
                        _safe_int(state.get("generation")),
                    ),
                )
                bound_client_turn_id = _safe_int(
                    response_metadata.get("jarvis_client_turn_id"),
                    _safe_int(state.get("active_client_turn_id")),
                )
                if response_id:
                    contexts = state.setdefault("openai_response_turns", {})
                    if isinstance(contexts, dict):
                        contexts[response_id] = {
                            "generation": bound_generation,
                            "client_turn_id": bound_client_turn_id,
                            "provider_epoch": provider_epoch,
                        }
                if bound_generation != _safe_int(state.get("generation")):
                    state["suppress_audio"] = True
                    continue
                state["suppress_audio"] = False
            elif kind == "response.output_audio.delta":
                response_id = str(event.get("response_id") or "").strip()
                contexts = state.get("openai_response_turns")
                context = (
                    contexts.get(response_id)
                    if isinstance(contexts, dict) and response_id
                    else None
                )
                if (
                    isinstance(context, dict)
                    and (
                        _safe_int(context.get("provider_epoch"), provider_epoch)
                        != provider_epoch
                        or _safe_int(context.get("generation"))
                        != _safe_int(state.get("generation"))
                    )
                ):
                    continue
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
                response_id = str(event.get("response_id") or "").strip()
                contexts = state.get("openai_response_turns")
                context = contexts.get(response_id) if isinstance(contexts, dict) and response_id else None
                if isinstance(context, dict) and (
                    _safe_int(context.get("provider_epoch"), provider_epoch) != provider_epoch
                    or _safe_int(context.get("generation")) != _safe_int(state.get("generation"))
                ):
                    continue
                delta = str(event.get("delta") or "")
                if delta and voice_mode == VOICE_MODE_REALTIME:
                    turn_generation = _safe_int(context.get("generation")) if isinstance(context, dict) else _safe_int(state.get("generation"))
                    turn_id = _safe_int(context.get("client_turn_id")) if isinstance(context, dict) else _safe_int(state.get("active_client_turn_id"))
                    await self._send_json(client, _turn_payload(turn_generation, turn_id, {"type": "assistant.transcript.delta", "text": delta}))
            elif kind == "response.output_audio_transcript.done":
                response_id = str(event.get("response_id") or "").strip()
                contexts = state.get("openai_response_turns")
                context = contexts.get(response_id) if isinstance(contexts, dict) and response_id else None
                if isinstance(context, dict) and (
                    _safe_int(context.get("provider_epoch"), provider_epoch) != provider_epoch
                    or _safe_int(context.get("generation")) != _safe_int(state.get("generation"))
                ):
                    continue
                transcript = str(event.get("transcript") or "").strip()
                if transcript and voice_mode == VOICE_MODE_REALTIME:
                    turn_generation = _safe_int(context.get("generation")) if isinstance(context, dict) else _safe_int(state.get("generation"))
                    turn_id = _safe_int(context.get("client_turn_id")) if isinstance(context, dict) else _safe_int(state.get("active_client_turn_id"))
                    await self._send_json(client, _turn_payload(turn_generation, turn_id, {"type": "assistant.transcript.done", "text": transcript}))
            elif kind == "response.output_audio.done":
                response_id = str(event.get("response_id") or "").strip()
                contexts = state.get("openai_response_turns")
                context = contexts.get(response_id) if isinstance(contexts, dict) and response_id else None
                if isinstance(context, dict) and (
                    _safe_int(context.get("provider_epoch"), provider_epoch) != provider_epoch
                    or _safe_int(context.get("generation")) != _safe_int(state.get("generation"))
                ):
                    continue
                if voice_mode == VOICE_MODE_REALTIME:
                    turn_generation = _safe_int(context.get("generation")) if isinstance(context, dict) else _safe_int(state.get("generation"))
                    turn_id = _safe_int(context.get("client_turn_id")) if isinstance(context, dict) else _safe_int(state.get("active_client_turn_id"))
                    await self._send_json(client, _turn_payload(turn_generation, turn_id, {"type": "audio.done"}))
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

                response_id = str(
                    response.get("id") or event.get("response_id") or ""
                ).strip()
                contexts = state.get("openai_response_turns")
                context = (
                    contexts.pop(response_id, None)
                    if isinstance(contexts, dict) and response_id
                    else None
                )
                completion_generation = (
                    _safe_int(context.get("generation"))
                    if isinstance(context, dict)
                    else _safe_int(state.get("generation"))
                )
                completion_turn_id = (
                    _safe_int(context.get("client_turn_id"))
                    if isinstance(context, dict)
                    else _safe_int(state.get("active_client_turn_id"))
                )
                completion_epoch = (
                    _safe_int(context.get("provider_epoch"), provider_epoch)
                    if isinstance(context, dict)
                    else provider_epoch
                )
                if (
                    completion_epoch != provider_epoch
                    or completion_generation != _safe_int(state.get("generation"))
                ):
                    continue
                await self._complete_audio_response(
                    client,
                    upstream,
                    state,
                    generation=completion_generation,
                    client_turn_id=completion_turn_id,
                    status=str(response.get("status", "completed")),
                    usage=usage,
                    provider_epoch=provider_epoch,
                )


            elif kind == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                message = str(error.get("message") or "OpenAI realtime error")
                if "no active response" in message.casefold():
                    continue
                self.last_error = message[:500]
                await self._send_json(client, {"type": "error", "message": self.last_error})
                lowered = message.casefold()
                if any(
                    marker in lowered
                    for marker in (
                        "maximum duration",
                        "session expired",
                        "session has expired",
                        "connection is closed",
                    )
                ):
                    raise RuntimeError(message)

    async def _complete_audio_response(
        self,
        client: Any,
        upstream: Any,
        state: dict[str, Any],
        *,
        generation: int | None = None,
        client_turn_id: int | None = None,
        status: str = "completed",
        usage: Any = None,
        provider_epoch: int = 0,
    ) -> None:
        """
        Finish the current audio response or start its queued
        continuation.

        The first streamed speech response may finish before the
        Jarvis brain has produced the complete final text. In that
        case completion is deferred until the remainder is known.
        """

        if not _provider_epoch_is_current(state, provider_epoch):
            return

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
                            generation=_safe_int(
                                generation, _safe_int(state.get("generation"))
                            ),
                            client_turn_id=_safe_int(
                                client_turn_id, _safe_int(state.get("active_client_turn_id"))
                            ),
                        )
                    )
                )

                await self._send_json(
                    client,
                    _turn_payload(
                        _safe_int(generation, _safe_int(state.get("generation"))),
                        _safe_int(client_turn_id, _safe_int(state.get("active_client_turn_id"))),
                        {"type": "speech.continuation", "characters": len(remainder)},
                    ),
                )

                return

        state[
            "continuation_speech_active"
        ] = False

        await self._send_json(
            client,
            _turn_payload(
                _safe_int(generation, _safe_int(state.get("generation"))),
                _safe_int(client_turn_id, _safe_int(state.get("active_client_turn_id"))),
                {"type": "turn.done", "status": status, "usage": usage},
            ),
        )

        closure_kind = state.pop(
            "close_after_response",
            None,
        )

        if closure_kind:
            await self._send_json(
                client,
                _turn_payload(
                    _safe_int(generation, _safe_int(state.get("generation"))),
                    _safe_int(client_turn_id, _safe_int(state.get("active_client_turn_id"))),
                    {
                        "type": "session.close",
                        "reason": "voice_closure",
                        "kind": closure_kind,
                    },
                ),
            )

        # Renewal may proceed only after every terminal event for this turn has
        # been handed to the client. Setting this earlier can cancel turn.done.
        _mark_turn_terminal(state)

    def _use_direct_elevenlabs(
        self,
        metadata: dict[str, Any],
    ) -> bool:
        return (
            self.config.tts_provider == "elevenlabs"
            and (
                metadata.get("client_kind") == "voice_pe"
                or (
                    metadata.get("client_kind") == "mobile"
                    and metadata.get("requested_voice")
                    in {"original", "home_assistant_original"}
                )
            )
        )

    async def _finish_direct_elevenlabs_turn(
        self,
        client: Any,
        state: dict[str, Any],
        spoken_text: str,
        *,
        generation: int | None = None,
        client_turn_id: int | None = None,
    ) -> None:
        resolved_generation = _safe_int(
            generation, _safe_int(state.get("generation"))
        )
        resolved_client_turn_id = _safe_int(
            client_turn_id, _safe_int(state.get("active_client_turn_id"))
        )
        if resolved_generation != _safe_int(state.get("generation")):
            return

        _LOGGER.info(
            "JARVIS DIAG | TURN COMPLETE | generation=%s "
            "spoken_characters=%d text=%r",
            state.get("generation"),
            len(spoken_text),
            spoken_text,
        )

        await self._send_json(
            client,
            _turn_payload(
                resolved_generation,
                resolved_client_turn_id,
                {"type": "assistant.transcript.done", "text": spoken_text},
            ),
        )

        await self._send_json(
            client,
            _turn_payload(
                resolved_generation, resolved_client_turn_id, {"type": "audio.done"}
            ),
        )

        await self._send_json(
            client,
            _turn_payload(
                resolved_generation,
                resolved_client_turn_id,
                {"type": "turn.done", "status": "completed", "usage": None},
            ),
        )

        closure_kind = state.pop(
            "close_after_response",
            None,
        )

        if closure_kind:
            await self._send_json(
                client,
                _turn_payload(
                    resolved_generation,
                    resolved_client_turn_id,
                    {
                        "type": "session.close",
                        "reason": "voice_closure",
                        "kind": closure_kind,
                    },
                ),
            )

        _mark_turn_terminal(state)


    async def _stream_elevenlabs_websocket(
        self,
        client: Any,
        text_queue: asyncio.Queue[str | None],
        state: dict[str, Any],
        generation: int,
    ) -> dict[str, Any]:
        api_key = self.config.elevenlabs_api_key
        voice_id = self.config.elevenlabs_voice_id
        model_id = self.config.elevenlabs_model_id
        output_format = self.config.elevenlabs_output_format

        if not api_key or not voice_id:
            return {
                "success": False,
                "audio_started": False,
                "error": "ElevenLabs API key or voice ID is not configured",
            }

        url = (
            "wss://api.elevenlabs.io/v1/text-to-speech/"
            f"{quote(voice_id, safe='')}/stream-input"
            f"?model_id={quote(model_id, safe='')}"
            f"&output_format={quote(output_format, safe='')}"
            "&inactivity_timeout=60"
            "&auto_mode=true"
        )

        started_at = time.monotonic()
        first_text_at: float | None = None
        first_audio_at: float | None = None
        audio_started = False

        # ElevenLabs audio must be drained independently from playback.
        # Otherwise real-time playback sleeps block the WebSocket receiver
        # and cause jitter, pauses and apparent jumps.
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=128
        )

        websocket_connect = _load_websocket_connect()

        try:
            async with websocket_connect(
                url,
                max_size=None,
                max_queue=128,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
                close_timeout=5,
            ) as tts_ws:
                _LOGGER.info(
                    "ELEVENLABS WS CONNECTED: latency_ms=%d",
                    round(
                        (time.monotonic() - started_at) * 1000
                    ),
                )

                # JARVIS LOW-LATENCY SENTENCE MODE:
                # send_text() now supplies complete sentences whenever
                # possible, so ElevenLabs auto_mode can generate them
                # directly without applying a second chunk buffer.
                await tts_ws.send(
                    json.dumps(
                        {
                            "text": " ",
                            "xi_api_key": api_key,
                            # JARVIS VOICE CONSISTENCY SETTINGS
                            "voice_settings": {
                                "stability": 0.75,
                                "similarity_boost": 0.80,
                                "style": 0.0,
                                "use_speaker_boost": True,
                                "speed": 1.0,
                            },
                        }
                    )
                )

                async def send_text() -> None:
                    nonlocal first_text_at

                    buffer = ""
                    total_characters = 0
                    text_chunk_count = 0

                    async def send_chunk(
                        chunk: str,
                        *,
                        flush: bool = False,
                    ) -> None:
                        nonlocal first_text_at
                        nonlocal total_characters
                        nonlocal text_chunk_count

                        if not chunk:
                            return

                        # Preserve GPT output exactly. Do not inject spaces
                        # or split words simply to hit a byte threshold.
                        payload: dict[str, Any] = {
                            "text": chunk,
                        }

                        if flush:
                            payload["flush"] = True

                        if first_text_at is None:
                            first_text_at = time.monotonic()

                            _LOGGER.info(
                                "ELEVENLABS WS FIRST TEXT: "
                                "latency_ms=%d characters=%d",
                                round(
                                    (
                                        first_text_at
                                        - started_at
                                    )
                                    * 1000
                                ),
                                len(chunk),
                            )

                        total_characters += len(chunk)
                        text_chunk_count += 1

                        _LOGGER.info(
                            "ELEVENLABS WS TEXT CHUNK: "
                            "index=%d characters=%d flush=%s "
                            "head=%r tail=%r",
                            text_chunk_count,
                            len(chunk),
                            flush,
                            chunk[:40],
                            chunk[-40:],
                        )

                        await tts_ws.send(
                            json.dumps(payload)
                        )

                    while True:
                        item = await text_queue.get()

                        if item is None:
                            # The final buffered text carries flush=True so
                            # ElevenLabs renders everything still pending.
                            if buffer:
                                await send_chunk(
                                    buffer,
                                    flush=True,
                                )
                                buffer = ""
                            else:
                                # Flush any text already buffered inside
                                # ElevenLabs without adding spoken content.
                                await tts_ws.send(
                                    json.dumps(
                                        {
                                            "text": " ",
                                            "flush": True,
                                        }
                                    )
                                )

                            _LOGGER.info(
                                "ELEVENLABS WS TEXT COMPLETE: "
                                "characters=%d",
                                total_characters,
                            )

                            # EOS after flush.
                            await tts_ws.send(
                                json.dumps(
                                    {
                                        "text": "",
                                    }
                                )
                            )
                            return

                        value = str(item or "")

                        if not value:
                            continue

                        buffer += value

                        # JARVIS VOICE CONSISTENCY:
                        # Send complete sentences whenever possible so
                        # ElevenLabs has natural linguistic context before
                        # committing to intonation, pace and tone.
                        while buffer:
                            cut = 0

                            # Use the FIRST complete sentence in the
                            # accumulated GPT stream. Leading whitespace
                            # in the next chunk is preserved naturally.
                            #
                            # A period immediately after a digit can be a
                            # streamed decimal such as 3.8. If the next
                            # character has not arrived yet, wait instead
                            # of incorrectly committing "3." to TTS.
                            for character_index, character in enumerate(buffer):
                                if character not in ".?!":
                                    continue

                                next_index = character_index + 1

                                if (
                                    character == "."
                                    and character_index > 0
                                    and buffer[
                                        character_index - 1
                                    ].isdigit()
                                ):
                                    # "3." at the current streaming edge may
                                    # become "3.8" in the next GPT delta.
                                    if next_index == len(buffer):
                                        continue

                                    # A digit on both sides proves this is
                                    # decimal punctuation, not sentence end.
                                    if buffer[next_index].isdigit():
                                        continue

                                if (
                                    next_index == len(buffer)
                                    or buffer[next_index].isspace()
                                ):
                                    cut = next_index
                                    break

                            # Exceptional very-long sentence protection.
                            # Do not revert to the old 80-180 character
                            # fragmentation. Wait for substantially more
                            # context, then choose a natural punctuation
                            # or whitespace boundary.
                            if not cut and len(buffer) >= 320:
                                soft_end = max(
                                    buffer.rfind(",", 180, 320),
                                    buffer.rfind(";", 180, 320),
                                    buffer.rfind(":", 180, 320),
                                )

                                if soft_end >= 180:
                                    cut = soft_end + 1
                                else:
                                    boundary = max(
                                        buffer.rfind(" ", 220, 320),
                                        buffer.rfind("\n", 220, 320),
                                        buffer.rfind("\t", 220, 320),
                                    )
                                    cut = (
                                        boundary + 1
                                        if boundary >= 220
                                        else 320
                                    )

                            if not cut:
                                break

                            chunk = buffer[:cut]
                            buffer = buffer[cut:]
                            await send_chunk(chunk)
                async def receive_audio() -> None:
                    previous_audio_at: float | None = None
                    audio_event_count = 0

                    try:
                        async for raw_message in tts_ws:
                            if isinstance(raw_message, bytes):
                                try:
                                    raw_message = (
                                        raw_message.decode("utf-8")
                                    )
                                except UnicodeDecodeError:
                                    continue

                            try:
                                event = json.loads(raw_message)
                            except Exception:
                                continue

                            if not isinstance(event, dict):
                                continue

                            encoded = event.get("audio")

                            if isinstance(encoded, str) and encoded:
                                try:
                                    audio = base64.b64decode(
                                        encoded,
                                        validate=True,
                                    )
                                except Exception:
                                    audio = b""

                                if audio:
                                    now = time.monotonic()
                                    audio_event_count += 1

                                    if previous_audio_at is not None:
                                        gap_ms = round(
                                            (now - previous_audio_at) * 1000
                                        )

                                        if gap_ms >= 100:
                                            _LOGGER.info(
                                                "ELEVENLABS WS AUDIO GAP: "
                                                "event=%d gap_ms=%d bytes=%d",
                                                audio_event_count,
                                                gap_ms,
                                                len(audio),
                                            )

                                    previous_audio_at = now

                                    if generation != int(
                                        state.get(
                                            "generation",
                                            0,
                                        )
                                    ):
                                        return

                                    # Drain ElevenLabs immediately. Playback
                                    # happens in a separate coroutine.
                                    await audio_queue.put(audio)

                            if bool(event.get("is_final")):
                                return

                    finally:
                        # Always release the player.
                        await audio_queue.put(None)

                async def play_audio() -> None:
                    nonlocal audio_started
                    nonlocal first_audio_at

                    pending = bytearray()
                    finished = False

                    # HTTP fallback already uses 4096-byte PCM frames.
                    frame_size = 4096

                    # Keep roughly 120 ms queued ahead on the Voice PE.
                    # 24 kHz * 2 bytes * 0.12 sec = 5760 bytes.
                    playback_lead_seconds = 0.120
                    playback_started_at: float | None = None
                    audio_seconds_sent = 0.0

                    while True:
                        while (
                            len(pending) < frame_size
                            and not finished
                        ):
                            queue_wait_started = time.monotonic()
                            item = await audio_queue.get()
                            queue_wait_ms = round(
                                (time.monotonic() - queue_wait_started) * 1000
                            )

                            if (
                                playback_started_at is not None
                                and queue_wait_ms >= 40
                            ):
                                _LOGGER.info(
                                    "ELEVENLABS PLAYER STARVATION: "
                                    "wait_ms=%d pending_bytes=%d",
                                    queue_wait_ms,
                                    len(pending),
                                )

                            if item is None:
                                finished = True
                                break

                            pending.extend(item)

                        if not pending and finished:
                            return

                        if len(pending) >= frame_size:
                            frame = bytes(
                                pending[:frame_size]
                            )
                            del pending[:frame_size]
                        elif finished:
                            frame = bytes(pending)
                            pending.clear()
                        else:
                            continue

                        if generation != int(
                            state.get("generation", 0)
                        ):
                            return

                        if not frame:
                            continue

                        if not audio_started:
                            audio_started = True
                            first_audio_at = time.monotonic()
                            playback_started_at = first_audio_at

                            _LOGGER.info(
                                "ELEVENLABS WS FIRST AUDIO: "
                                "latency_ms=%d",
                                round(
                                    (
                                        first_audio_at
                                        - started_at
                                    )
                                    * 1000
                                ),
                            )

                            _LOGGER.info(
                                "ELEVENLABS WS SMOOTH PLAYER: "
                                "frame_bytes=%d lead_ms=%d",
                                frame_size,
                                round(
                                    playback_lead_seconds
                                    * 1000
                                ),
                            )

                        await client.send_bytes(frame)

                        self.total_audio_output_bytes += len(
                            frame
                        )

                        audio_seconds_sent += (
                            len(frame) / 48_000.0
                        )

                        # Absolute pacing rather than sleeping for the
                        # complete chunk duration. This accounts for network
                        # send time and intentionally keeps audio queued ahead
                        # of the speaker to prevent underruns.
                        if playback_started_at is not None:
                            target = (
                                playback_started_at
                                + audio_seconds_sent
                                - playback_lead_seconds
                            )

                            delay = (
                                target
                                - time.monotonic()
                            )

                            if delay > 0:
                                await asyncio.sleep(delay)

                sender_task = asyncio.create_task(
                    send_text()
                )
                receiver_task = asyncio.create_task(
                    receive_audio()
                )
                player_task = asyncio.create_task(
                    play_audio()
                )

                try:
                    # Limit ElevenLabs text/audio generation, not the
                    # real-time duration of already-buffered playback.
                    #
                    # A long response can legitimately take more than
                    # 90 seconds to play even after ElevenLabs has
                    # finished delivering all of its PCM.
                    async with asyncio.timeout(90.0):
                        await asyncio.gather(
                            sender_task,
                            receiver_task,
                        )

                    # The player drains a finite PCM queue at real-time
                    # speed. Do not cancel valid speech simply because
                    # its playback duration exceeds the generation
                    # timeout above.
                    await player_task
                finally:
                    for task in (
                        sender_task,
                        receiver_task,
                        player_task,
                    ):
                        if not task.done():
                            task.cancel()

                    await asyncio.gather(
                        sender_task,
                        receiver_task,
                        player_task,
                        return_exceptions=True,
                    )

            return {
                "success": bool(audio_started),
                "audio_started": bool(audio_started),
                "first_text_ms": (
                    round(
                        (
                            first_text_at
                            - started_at
                        )
                        * 1000
                    )
                    if first_text_at is not None
                    else None
                ),
                "first_audio_ms": (
                    round(
                        (
                            first_audio_at
                            - started_at
                        )
                        * 1000
                    )
                    if first_audio_at is not None
                    else None
                ),
            }

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self.last_error = (
                f"ElevenLabs WebSocket TTS failed: {exc}"
            )[:500]

            _LOGGER.exception(
                "ElevenLabs streaming WebSocket failed"
            )

            return {
                "success": False,
                "audio_started": bool(audio_started),
                "error": str(exc)[:300],
            }


    async def _stream_elevenlabs_response(
        self,
        client: Any,
        text: str,
        state: dict[str, Any],
        *,
        generation: int | None = None,
        client_turn_id: int | None = None,
        finalize_turn: bool = True,
    ) -> bool:
        spoken_text = " ".join(str(text or "").split()).strip()
        if not spoken_text:
            return False
        resolved_generation = _safe_int(
            generation, _safe_int(state.get("generation"))
        )
        resolved_client_turn_id = _safe_int(
            client_turn_id, _safe_int(state.get("active_client_turn_id"))
        )
        if resolved_generation != _safe_int(state.get("generation")):
            return True

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
                        if resolved_generation != _safe_int(state.get("generation")):
                            return True
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

        if finalize_turn:
            if resolved_generation != _safe_int(state.get("generation")):
                return True
            await self._finish_direct_elevenlabs_turn(
                client,
                state,
                spoken_text,
                generation=resolved_generation,
                client_turn_id=resolved_client_turn_id,
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
        client_turn_id: int | None = None,
        provider_epoch: int = 0,
        turn_already_started: bool = False,
    ) -> None:
        if not _provider_epoch_is_current(state, provider_epoch):
            return
        command = " ".join(str(transcript or "").split()).strip()
        if not command:
            return
        if (
            metadata.get("client_kind") == "voice_pe"
            and bool(state.get("turn_in_progress"))
            and not turn_already_started
        ):
            await self._send_json(
                client,
                {
                    "type": "turn.ignored",
                    "reason": "voice_pe_turn_in_progress",
                },
            )
            return
        if not turn_already_started:
            _mark_turn_started(state)
        generation = int(state.get("generation", 0)) + 1
        state["generation"] = generation
        resolved_client_turn_id = _safe_int(client_turn_id)
        state["active_generation"] = generation
        state["active_client_turn_id"] = resolved_client_turn_id
        state["turn_in_progress_generation"] = generation
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
                client_turn_id=resolved_client_turn_id,
                provider_epoch=provider_epoch,
            )
        )
        turn_tasks.add(task)

        def finish_turn_task(completed: asyncio.Task[Any]) -> None:
            turn_tasks.discard(completed)
            if completed.cancelled():
                if _provider_epoch_is_current(state, provider_epoch):
                    _mark_turn_terminal(state, generation)
            elif completed.exception() is not None:
                if _provider_epoch_is_current(state, provider_epoch):
                    _mark_turn_terminal(state, generation)

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
        client_turn_id: int = 0,
        provider_epoch: int = 0,
    ) -> None:
        if not _provider_epoch_is_current(state, provider_epoch):
            return
        self.total_brain_turns += 1
        state["active_voice"] = voice

        _LOGGER.info(
            "JARVIS DIAG | BRAIN START | generation=%s command=%r "
            "client=%s voice_mode=%s",
            generation,
            command,
            metadata.get("client_kind"),
            voice_mode,
        )

        await self._send_json(
            client,
            _turn_payload(
                generation,
                client_turn_id,
                {"type": "brain.started", "command": command},
            ),
        )

        speech_buffer = ""
        early_speech_sent = False
        early_speech_text = ""
        early_speech_task: asyncio.Task[bool] | None = None

        direct_elevenlabs_stream = bool(
            speak
            and self._use_direct_elevenlabs(metadata)
            and voice_mode != VOICE_MODE_HOME_ASSISTANT
        )

        elevenlabs_text_queue: asyncio.Queue[str | None] | None = (
            asyncio.Queue()
            if direct_elevenlabs_stream
            else None
        )

        elevenlabs_stream_task: asyncio.Task[dict[str, Any]] | None = (
            asyncio.create_task(
                self._stream_elevenlabs_websocket(
                    client,
                    elevenlabs_text_queue,
                    state,
                    generation,
                )
            )
            if elevenlabs_text_queue is not None
            else None
        )

        elevenlabs_streamed_text = ""

        if direct_elevenlabs_stream:
            # OpenAI realtime audio must never mix with ElevenLabs PCM.
            state["suppress_audio"] = True

        async def on_delta(delta: str) -> None:
            nonlocal speech_buffer, early_speech_sent, early_speech_text
            nonlocal early_speech_task, elevenlabs_streamed_text
            if (
                not _provider_epoch_is_current(state, provider_epoch)
                or generation != int(state.get("generation", 0))
            ):
                return
            text = str(delta or "")
            if not text:
                return
            self.total_streamed_text_chunks += 1

            _LOGGER.info(
                "JARVIS DIAG | GPT DELTA | generation=%s text=%r",
                generation,
                text,
            )

            await self._send_json(
                client,
                _turn_payload(
                    generation,
                    client_turn_id,
                    {"type": "brain.delta", "text": text},
                ),
            )

            if (
                direct_elevenlabs_stream
                and elevenlabs_text_queue is not None
            ):
                # Stream the complete GPT response. Do not truncate at
                # 420 characters; the WebSocket can remain open for the
                # entire turn.
                elevenlabs_streamed_text += text
                elevenlabs_text_queue.put_nowait(text)

                # The dedicated Voice PE is now spoken by the
                # persistent ElevenLabs stream, not early HTTP TTS.
                return

            if not speak or early_speech_sent:
                return

            speech_buffer += text

            if self._use_direct_elevenlabs(metadata):
                # Dedicated hardware can begin speaking earlier than the
                # generic UI path. Still wait for a natural boundary so
                # Jarvis does not speak broken token fragments.
                segment = SpeechRenderPolicy.early_segment(
                    speech_buffer,
                    minimum_chars=16,
                    preferred_chars=48,
                    maximum_chars=96,
                )
            else:
                segment = SpeechRenderPolicy.early_segment(
                    speech_buffer
                )

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
                    _turn_payload(
                        generation,
                        client_turn_id,
                        {
                            "type": "original.tts",
                            "text": segment,
                            "streaming_preview": True,
                        },
                    ),
                )

            elif self._use_direct_elevenlabs(metadata):
                # Do NOT await this here. The brain must keep generating
                # the rest of the reply while ElevenLabs renders and the
                # Voice PE begins playing this first phrase.
                state["suppress_audio"] = True

                _LOGGER.info(
                    "VOICE PE EARLY TTS START: characters=%d",
                    len(segment),
                )

                early_speech_task = asyncio.create_task(
                    self._stream_elevenlabs_response(
                        client,
                        segment,
                        state,
                        generation=generation,
                        client_turn_id=client_turn_id,
                        finalize_turn=False,
                    )
                )

                await self._send_json(
                    client,
                    _turn_payload(
                        generation, client_turn_id,
                        {"type": "speech.early.started", "characters": len(segment)},
                    ),
                )

            else:
                state["suppress_audio"] = False
                await upstream.send(
                    json.dumps(
                        speak_response_event(
                            segment,
                            voice,
                            generation=generation,
                            client_turn_id=client_turn_id,
                        )
                    )
                )

        try:
            turn_metadata = dict(metadata)
            turn_metadata.update(
                trusted_local_context(self.config.timezone)
            )
            turn_metadata["speak"] = bool(speak)
            if client_turn_id > 0:
                turn_metadata["client_turn_id"] = client_turn_id
            raw_result = await brain_handler(command, turn_metadata, on_delta)
            if hasattr(raw_result, "model_dump"):
                raw_result = raw_result.model_dump()
            if not isinstance(raw_result, dict):
                raw_result = {"success": True, "response": str(raw_result)}
            response = str(raw_result.get("response") or "").strip()
            if not response:
                response = "I completed that, but Jarvis Core did not return a response."

            _LOGGER.info(
                "JARVIS DIAG | BRAIN COMPLETE | generation=%s "
                "success=%s tool_called=%s response=%r",
                generation,
                raw_result.get("success", True),
                raw_result.get("tool_called", False),
                response,
            )

            conversation_id = str(
                raw_result.get("conversation_id") or ""
            ).strip()
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
                "streamed": bool(
                    raw_result.get("streamed", False)
                ),
                "quiet_control": quiet_control,
                "memory_used": memory_used,
                "message_count": message_count,
                "user_name": user_name,
            }
        except asyncio.CancelledError:
            if early_speech_task is not None:
                early_speech_task.cancel()

                await asyncio.gather(
                    early_speech_task,
                    return_exceptions=True,
                )

            if elevenlabs_stream_task is not None:
                elevenlabs_stream_task.cancel()

                await asyncio.gather(
                    elevenlabs_stream_task,
                    return_exceptions=True,
                )

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

        if (
            not _provider_epoch_is_current(state, provider_epoch)
            or generation != int(state.get("generation", 0))
        ):
            if elevenlabs_stream_task is not None:
                elevenlabs_stream_task.cancel()

                await asyncio.gather(
                    elevenlabs_stream_task,
                    return_exceptions=True,
                )

            self.total_discarded_stale_turns += 1
            await self._send_json(
                client,
                _turn_payload(
                    generation, client_turn_id, {"type": "brain.discarded", "command": command}
                ),
            )
            return

        for tool_event in result.get("tool_events", []):
            await self._send_json(
                client,
                {
                    "type": "tool.completed",
                    "generation": generation,
                    **({"client_turn_id": client_turn_id} if client_turn_id > 0 else {}),
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
                "generation": generation,
                **({"client_turn_id": client_turn_id} if client_turn_id > 0 else {}),
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
            _turn_payload(
                generation,
                client_turn_id,
                {
                    "type": "session.context",
                    "conversation_id": result["conversation_id"],
                    "user_name": result.get("user_name"),
                    "message_count": _safe_int(result.get("message_count"), 0),
                },
            ),
        )
        self.total_context_syncs += 1

        await self._send_json(
            client,
            {
                "type": "turn.summary",
                "generation": generation,
                **({"client_turn_id": client_turn_id} if client_turn_id > 0 else {}),
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
                "generation": generation,
                **({"client_turn_id": client_turn_id} if client_turn_id > 0 else {}),
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
            await self._send_json(
                client,
                _turn_payload(
                    generation, client_turn_id,
                    {"type": "turn.done", "status": "completed", "usage": None},
                ),
            )
            _mark_turn_terminal(state)
            return
        if (
            direct_elevenlabs_stream
            and elevenlabs_text_queue is not None
            and elevenlabs_stream_task is not None
        ):
            # Voice PE should speak the full answer, not the generic
            # 520-character voice preview used by shorter UI surfaces.
            complete_spoken_response = (
                SpeechRenderPolicy.spoken_text(
                    result["response"],
                    maximum_chars=max(
                        520,
                        len(result["response"]) + 32,
                    ),
                )
            )

            if bool(result.get("streamed")):
                # AIEngine already delivered the complete answer through
                # on_delta. Do not reconstruct a remainder from normalised
                # text; doing so can repeat, skip or jump at the join.
                if not elevenlabs_streamed_text:
                    elevenlabs_text_queue.put_nowait(
                        complete_spoken_response
                    )
            else:
                # Tool turns intentionally do not stream provisional text.
                # Once tools are complete, speak the final answer once.
                elevenlabs_text_queue.put_nowait(
                    complete_spoken_response
                )

            # Flush and finalise this SAME ElevenLabs generation.
            elevenlabs_text_queue.put_nowait(None)

            try:
                ws_result = await elevenlabs_stream_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.exception(
                    "Voice PE ElevenLabs WS task failed"
                )

                ws_result = {
                    "success": False,
                    "audio_started": False,
                    "error": str(exc),
                }

            if bool(ws_result.get("success")):
                _LOGGER.info(
                    "ELEVENLABS WS TURN COMPLETE: "
                    "first_text_ms=%s first_audio_ms=%s",
                    ws_result.get("first_text_ms"),
                    ws_result.get("first_audio_ms"),
                )

                await self._finish_direct_elevenlabs_turn(
                    client,
                    state,
                    complete_spoken_response,
                    generation=generation,
                    client_turn_id=client_turn_id,
                )
                return

            if bool(ws_result.get("audio_started")):
                # Some of the answer has already been spoken.
                # Never replay it using another TTS provider.
                _LOGGER.warning(
                    "ElevenLabs WS ended after audio started; "
                    "not replaying spoken text"
                )

                await self._send_json(
                    client,
                    {
                        "type": "status",
                        "message": "Jarvis voice stream ended early",
                    },
                )

                await self._finish_direct_elevenlabs_turn(
                    client,
                    state,
                    complete_spoken_response,
                    generation=generation,
                    client_turn_id=client_turn_id,
                )
                return

            _LOGGER.warning(
                "ElevenLabs WS produced no audio; "
                "falling back to HTTP TTS"
            )

            handled = await self._stream_elevenlabs_response(
                client,
                complete_spoken_response,
                state,
                generation=generation,
                client_turn_id=client_turn_id,
            )

            if handled:
                return

            state["suppress_audio"] = False

            await upstream.send(
                json.dumps(
                    speak_response_event(
                        complete_spoken_response,
                        voice,
                        generation=generation,
                        client_turn_id=client_turn_id,
                    )
                )
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

            if (
                self._use_direct_elevenlabs(metadata)
                and voice_mode != VOICE_MODE_HOME_ASSISTANT
            ):
                state["brain_turn_complete"] = True
                state["early_speech_active"] = False

                early_ok = True

                if early_speech_task is not None:
                    try:
                        early_ok = await early_speech_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        _LOGGER.exception(
                            "Voice PE early ElevenLabs task failed"
                        )
                        early_ok = False

                if not early_ok:
                    # Preserve the existing OpenAI realtime fallback if
                    # original Jarvis TTS fails.
                    state["suppress_audio"] = False

                    await upstream.send(
                        json.dumps(
                            speak_response_event(
                                complete_spoken_response,
                                voice,
                                generation=generation,
                                client_turn_id=client_turn_id,
                            )
                        )
                    )
                    return

                if remainder:
                    _LOGGER.info(
                        "VOICE PE REMAINDER TTS START: characters=%d",
                        len(remainder),
                    )

                    remainder_ok = (
                        await self._stream_elevenlabs_response(
                            client,
                            remainder,
                            state,
                            generation=generation,
                            client_turn_id=client_turn_id,
                            finalize_turn=False,
                        )
                    )

                    if not remainder_ok:
                        state["suppress_audio"] = False

                        await upstream.send(
                            json.dumps(
                                speak_response_event(
                                    remainder,
                                    voice,
                                    generation=generation,
                                    client_turn_id=client_turn_id,
                                )
                            )
                        )
                        return

                await self._finish_direct_elevenlabs_turn(
                    client,
                    state,
                    complete_spoken_response,
                    generation=generation,
                    client_turn_id=client_turn_id,
                )

                return

            state[
                "queued_speech_remainder"
            ] = remainder

            state[
                "brain_turn_complete"
            ] = True

            await self._send_json(
                client,
                _turn_payload(
                    generation, client_turn_id,
                    {"type": "speech.remainder.ready", "characters": len(remainder)},
                ),
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
                    generation=generation,
                    client_turn_id=client_turn_id,
                    status="completed",
                    usage=None,
                    provider_epoch=provider_epoch,
                )

            return

        spoken_response = SpeechRenderPolicy.spoken_text(result["response"])
        state["suppress_audio"] = False
        if voice_mode == VOICE_MODE_HOME_ASSISTANT:
            await self._send_json(
                client,
                _turn_payload(
                    generation, client_turn_id,
                    {"type": "original.tts", "text": spoken_response},
                ),
            )
            _mark_turn_terminal(state)
            return
        if self._use_direct_elevenlabs(metadata):
            handled = await self._stream_elevenlabs_response(
                client,
                spoken_response,
                state,
                generation=generation,
                client_turn_id=client_turn_id,
            )
            if handled:
                return

        await upstream.send(
            json.dumps(
                speak_response_event(
                    spoken_response,
                    voice,
                    generation=generation,
                    client_turn_id=client_turn_id,
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
