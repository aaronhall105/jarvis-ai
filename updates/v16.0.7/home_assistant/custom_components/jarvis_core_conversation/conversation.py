from __future__ import annotations

import asyncio
import json
import time
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from aiohttp import ClientError, ClientResponseError
from homeassistant.components import conversation, tts
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, entity_registry as er, intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .audio_gate import SmartAudioGate
from .closure import closure_response, match_conversation_closure
from .streaming import AssistantStreamState

from .const import (
    CONF_AUDIO_GATE_ENABLED,
    CONF_FOLLOW_UP_MODE,
    CONF_FOLLOW_UP_WINDOW,
    CONF_SHOW_PROGRESS_TEXT,
    CONF_SPOKEN_PROGRESS,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_AUDIO_GATE_ENABLED,
    DEFAULT_FOLLOW_UP_MODE,
    DEFAULT_FOLLOW_UP_WINDOW,
    DEFAULT_SHOW_PROGRESS_TEXT,
    DEFAULT_SPOKEN_PROGRESS,
    FOLLOW_UP_ALWAYS,
    FOLLOW_UP_DISABLED,
    FOLLOW_UP_SMART,
    FOLLOW_UP_QUESTIONS,
)

_LOGGER = logging.getLogger(__name__)

_STOP_PHRASES = {
    "bye",
    "goodbye",
    "never mind",
    "nevermind",
    "stop",
    "stop listening",
    "end conversation",
    "end the conversation",
    "that's all",
    "that is all",
    "thanks",
    "thank you",
    "cancel",
}

_FOLLOW_UP_HINTS = (
    "would you like",
    "which one",
    "which device",
    "which room",
    "what do you mean",
    "could you clarify",
    "please clarify",
    "please specify",
    "please tell me",
    "say confirm",
    "say 'confirm'",
    "say cancel",
    "say 'cancel'",
)


def _normalise_phrase(value: str) -> str:
    """Normalise a short spoken phrase for exact matching."""

    value = value.casefold().strip()
    value = re.sub(r"[^a-z0-9'\s]", "", value)
    return re.sub(r"\s+", " ", value)


def _looks_like_follow_up(speech: str, payload: dict[str, Any]) -> bool:
    """Return whether the response clearly expects another user turn."""

    explicit = payload.get("continue_conversation")
    if explicit is True:
        return True

    if "?" in speech:
        return True

    lowered = speech.casefold()
    if any(hint in lowered for hint in _FOLLOW_UP_HINTS):
        return True

    intent_name = str(payload.get("intent") or "").casefold()
    return intent_name in {
        "admin_change",
        "clarification",
        "follow_up",
    }


def _should_continue_conversation(
    *,
    mode: str,
    user_text: str,
    speech: str,
    payload: dict[str, Any],
) -> bool:
    """Decide whether Home Assistant should reopen the microphone."""

    if mode == FOLLOW_UP_DISABLED:
        return False

    if mode in {FOLLOW_UP_SMART, FOLLOW_UP_QUESTIONS}:
        return _looks_like_follow_up(speech, payload)

    return mode == FOLLOW_UP_ALWAYS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Jarvis Core conversation entity."""

    async_add_entities(
        [
            JarvisCoreConversationEntity(
                hass=hass,
                entry=entry,
            )
        ]
    )


class JarvisCoreConversationEntity(ConversationEntity):
    """Conversation agent backed by Jarvis Core."""

    _attr_has_entity_name = True
    _attr_supports_streaming = True
    _attr_name = "Conversation"
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_conversation"
        self._audio_gate = SmartAudioGate()

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""

        return ["en"]

    async def _request_user_context(
        self,
        user_input: ConversationInput,
    ) -> dict[str, Any]:
        """Resolve the authenticated Home Assistant user for this request."""

        context = user_input.context
        user_id = getattr(context, "user_id", None)
        user = (
            await self.hass.auth.async_get_user(user_id)
            if user_id
            else None
        )
        user_name = (
            str(getattr(user, "name", "") or "").strip()
            or "Aaron"
        )
        device_id = (
            getattr(user_input, "device_id", None)
            or getattr(context, "device_id", None)
        )
        satellite_id = getattr(user_input, "satellite_id", None)

        return {
            "user_id": user_id,
            "user_name": user_name,
            "user_is_admin": bool(
                getattr(user, "is_admin", False)
            ),
            "device_id": device_id,
            "satellite_id": satellite_id,
            # A real satellite ID distinguishes a voice pipeline from typed Assist.
            # Typed chat may still carry a Companion App device ID.
            "voice_mode": bool(satellite_id),
        }

    def _progress_media_player(self, device_id: str | None) -> str | None:
        """Resolve the media player belonging to the originating voice device."""

        if not device_id:
            return None

        registry = er.async_get(self.hass)
        candidates = [
            entry
            for entry in er.async_entries_for_device(registry, device_id)
            if entry.domain == "media_player"
            and not entry.disabled
            and not entry.hidden
            and self.hass.states.get(entry.entity_id) is not None
        ]
        if not candidates:
            return None

        def score(entry: Any) -> tuple[int, str]:
            entity_id = entry.entity_id.casefold()
            platform = str(entry.platform or "").casefold()
            points = 0
            if "home_assistant_voice" in entity_id:
                points += 100
            if "voice" in entity_id:
                points += 40
            if platform == "esphome":
                points += 30
            if "media_player" in entity_id:
                points += 5
            return points, entry.entity_id

        return max(candidates, key=score).entity_id

    async def _wait_for_progress_audio(
        self,
        media_player_entity_id: str,
        message: str,
    ) -> None:
        """Wait briefly for the filler phrase to finish before returning the answer."""

        word_count = max(1, len(message.split()))
        timeout_seconds = min(6.0, max(2.0, 0.55 * word_count + 0.8))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        saw_playing = False

        while asyncio.get_running_loop().time() < deadline:
            state = self.hass.states.get(media_player_entity_id)
            state_value = str(getattr(state, "state", "") or "").casefold()
            if state_value in {"playing", "buffering"}:
                saw_playing = True
            elif saw_playing:
                return
            await asyncio.sleep(0.10)

        if not saw_playing:
            # Some ESPHome media players do not expose a reliable playing state.
            # A short phrase-length delay still prevents the final TTS overlapping.
            await asyncio.sleep(min(2.2, max(0.7, word_count * 0.28)))

    async def _speak_progress(
        self,
        message: str,
        device_id: str | None,
        context: Any,
    ) -> bool:
        """Speak an interim phrase on the originating Voice Preview device."""

        media_player_entity_id = self._progress_media_player(device_id)
        tts_entity_id = tts.async_default_engine(self.hass)
        if tts_entity_id and not str(tts_entity_id).startswith("tts."):
            # The generic tts.speak service targets modern TTS entities. A legacy
            # provider name cannot be passed as an entity_id safely.
            tts_entity_id = None
        if not media_player_entity_id or not tts_entity_id:
            _LOGGER.debug(
                "Spoken Jarvis progress unavailable device_id=%s player=%s tts=%s",
                device_id,
                media_player_entity_id,
                tts_entity_id,
            )
            return False

        try:
            await self.hass.services.async_call(
                "tts",
                "speak",
                {
                    "entity_id": tts_entity_id,
                    "media_player_entity_id": media_player_entity_id,
                    "message": message,
                    "cache": True,
                },
                blocking=True,
                context=context,
            )
            await self._wait_for_progress_audio(media_player_entity_id, message)
            return True
        except Exception:  # noqa: BLE001 - filler failure must not fail the answer
            _LOGGER.warning(
                "Could not speak Jarvis progress on %s",
                media_player_entity_id,
                exc_info=True,
            )
            return False

    async def async_prepare(
        self,
        language: str | None = None,
    ) -> None:
        """Warm the local Jarvis HTTP connection before a voice request."""

        base_url = str(self.entry.data[CONF_URL]).rstrip("/")
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(2):
                async with session.get(f"{base_url}/health") as response:
                    response.release()
        except (TimeoutError, ClientError):
            # Preparation is best-effort. The real request reports any error.
            _LOGGER.debug("Jarvis prepare warm-up did not complete")

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Send the transcript to Jarvis Core."""

        response = intent.IntentResponse(
            language=user_input.language,
        )
        request_user = await self._request_user_context(user_input)
        request_conversation_id = (
            user_input.conversation_id
            or chat_log.conversation_id
        )

        # End-of-conversation instructions are handled locally before Jarvis Core,
        # so there is no filler phrase, LLM delay, tool call or reopened microphone.
        closure = match_conversation_closure(user_input.text)
        if closure is not None:
            self._audio_gate.clear(
                conversation_id=request_conversation_id,
                satellite_id=str(request_user.get("satellite_id") or "") or None,
                device_id=str(request_user.get("device_id") or "") or None,
            )
            speech = closure_response(
                closure,
                str(request_user.get("user_name") or ""),
            )
            if speech:
                response.async_set_speech(speech)
            chat_log.async_add_assistant_content_without_tools(
                AssistantContent(
                    agent_id=user_input.agent_id,
                    content=speech or None,
                )
            )
            _LOGGER.debug(
                "Jarvis conversation closed locally kind=%s phrase=%s user=%s",
                closure.kind,
                closure.normalised_text,
                request_user.get("user_name"),
            )
            return ConversationResult(
                conversation_id=(
                    user_input.conversation_id
                    or chat_log.conversation_id
                ),
                response=response,
                continue_conversation=False,
            )

        audio_gate_enabled = bool(
            self.entry.options.get(
                CONF_AUDIO_GATE_ENABLED,
                DEFAULT_AUDIO_GATE_ENABLED,
            )
        )
        follow_up_window = int(
            self.entry.options.get(
                CONF_FOLLOW_UP_WINDOW,
                DEFAULT_FOLLOW_UP_WINDOW,
            )
        )
        if audio_gate_enabled and request_user.get("voice_mode"):
            gate = self._audio_gate.evaluate(
                transcript=user_input.text,
                conversation_id=request_conversation_id,
                satellite_id=str(request_user.get("satellite_id") or "") or None,
                device_id=str(request_user.get("device_id") or "") or None,
            )
            if not gate.accepted:
                _LOGGER.info(
                    "Jarvis audio gate rejected follow-up reason=%s confidence=%.2f "
                    "kind=%s conversation_id=%s satellite=%s words=%s",
                    gate.reason,
                    gate.confidence,
                    gate.expectation_kind,
                    request_conversation_id,
                    request_user.get("satellite_id"),
                    len(str(user_input.text or "").split()),
                )
                chat_log.async_add_assistant_content_without_tools(
                    AssistantContent(
                        agent_id=user_input.agent_id,
                        content=None,
                    )
                )
                return ConversationResult(
                    conversation_id=request_conversation_id,
                    response=response,
                    continue_conversation=False,
                )

        base_url = str(self.entry.data[CONF_URL]).rstrip("/")
        timeout_seconds = int(self.entry.data[CONF_TIMEOUT])
        follow_up_mode = str(
            self.entry.options.get(
                CONF_FOLLOW_UP_MODE,
                DEFAULT_FOLLOW_UP_MODE,
            )
        )
        spoken_progress = bool(
            self.entry.options.get(
                CONF_SPOKEN_PROGRESS,
                DEFAULT_SPOKEN_PROGRESS,
            )
        )
        show_progress_text = bool(
            self.entry.options.get(
                CONF_SHOW_PROGRESS_TEXT,
                DEFAULT_SHOW_PROGRESS_TEXT,
            )
        )

        # Home Assistant supplies one conversation ID for the whole voice/chat
        # session. Passing it to Jarvis keeps Jarvis Core's SQLite history in
        # step with Home Assistant's conversation.
        session = aiohttp_client.async_get_clientsession(self.hass)

        request_started = time.monotonic()
        final_payload: dict[str, Any] = {}
        stream_state = AssistantStreamState()
        first_delta_ms: int | None = None
        final_received = False

        async def jarvis_delta_stream(
            http_response: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            """Convert Jarvis NDJSON events into Home Assistant chat deltas."""

            nonlocal final_payload, first_delta_ms, final_received

            while True:
                raw_line = await http_response.content.readline()
                if not raw_line:
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = json.loads(line)
                event_type = str(event.get("type") or "")

                if event_type in {"start", "ping"}:
                    continue

                if event_type == "progress":
                    message = str(event.get("message") or "").strip()
                    if not message:
                        continue
                    if first_delta_ms is None:
                        first_delta_ms = round(
                            (time.monotonic() - request_started) * 1000
                        )
                    if spoken_progress and request_user.get("voice_mode"):
                        await self._speak_progress(
                            message,
                            str(request_user.get("device_id") or "") or None,
                            user_input.context,
                        )
                    if show_progress_text:
                        for progress_event in stream_state.progress_events(message):
                            yield progress_event
                    continue

                if event_type == "delta":
                    delta = str(event.get("delta") or "")
                    if not delta:
                        continue
                    if first_delta_ms is None:
                        first_delta_ms = round(
                            (time.monotonic() - request_started) * 1000
                        )
                    for answer_event in stream_state.answer_events(delta):
                        yield answer_event
                    continue

                if event_type == "final":
                    final_received = True
                    result = event.get("result")
                    if isinstance(result, dict):
                        final_payload = result
                    speech = str(final_payload.get("response") or "").strip()
                    if not speech and not stream_state.answer_text:
                        speech = (
                            "The request completed, but Jarvis did not return "
                            "a spoken response."
                        )
                    for final_event in stream_state.final_events(speech):
                        yield final_event
                    continue

                if event_type == "error":
                    raise ValueError(
                        str(event.get("message") or "Jarvis streaming failed.")
                    )

            if not final_received:
                raise ValueError(
                    "Jarvis streaming ended before the final event."
                )
            if stream_state.current_message is None:
                raise ValueError("Jarvis streaming ended without a response.")

        try:
            async with asyncio.timeout(timeout_seconds):
                async with session.post(
                    f"{base_url}/api/assistant/ai/stream",
                    json={
                        "text": user_input.text,
                        "conversation_id": request_conversation_id,
                        **{
                            key: value
                            for key, value in request_user.items()
                            if key != "satellite_id"
                        },
                    },
                ) as http_response:
                    http_response.raise_for_status()
                    content_stream = chat_log.async_add_delta_content_stream(
                        user_input.agent_id,
                        jarvis_delta_stream(http_response),
                    )
                    async for _content in content_stream:
                        pass

            speech = str(
                final_payload.get("response")
                or stream_state.answer_text
                or stream_state.progress_text
            ).strip()

            returned_conversation_id = str(
                final_payload.get("conversation_id")
                or request_conversation_id
                or ""
            ).strip() or None

            continue_conversation = _should_continue_conversation(
                mode=follow_up_mode,
                user_text=user_input.text,
                speech=speech,
                payload=final_payload,
            )

            if audio_gate_enabled and request_user.get("voice_mode"):
                if continue_conversation:
                    expectation = self._audio_gate.arm(
                        conversation_id=(
                            returned_conversation_id
                            or request_conversation_id
                        ),
                        satellite_id=(
                            str(request_user.get("satellite_id") or "")
                            or None
                        ),
                        device_id=(
                            str(request_user.get("device_id") or "")
                            or None
                        ),
                        assistant_speech=speech,
                        intent_name=str(final_payload.get("intent") or ""),
                        timeout_seconds=follow_up_window,
                    )
                    _LOGGER.debug(
                        "Jarvis audio gate armed kind=%s timeout=%ss "
                        "conversation_id=%s",
                        expectation.kind,
                        follow_up_window,
                        expectation.conversation_id,
                    )
                else:
                    self._audio_gate.clear(
                        conversation_id=(
                            returned_conversation_id
                            or request_conversation_id
                        ),
                        satellite_id=(
                            str(request_user.get("satellite_id") or "")
                            or None
                        ),
                        device_id=(
                            str(request_user.get("device_id") or "")
                            or None
                        ),
                    )

            result = conversation.async_get_result_from_chat_log(
                user_input,
                chat_log,
            )

            total_ms = round((time.monotonic() - request_started) * 1000)
            _LOGGER.debug(
                "Jarvis streamed reply conversation_id=%s continue=%s mode=%s "
                "intent=%s user=%s voice=%s first_delta_ms=%s total_ms=%s",
                returned_conversation_id,
                continue_conversation,
                follow_up_mode,
                final_payload.get("intent"),
                request_user.get("user_name"),
                request_user.get("voice_mode"),
                first_delta_ms,
                total_ms,
            )

            return ConversationResult(
                conversation_id=(
                    returned_conversation_id
                    or result.conversation_id
                ),
                response=result.response,
                continue_conversation=continue_conversation,
            )

        except TimeoutError:
            _LOGGER.exception(
                "Jarvis Core request timed out after %s seconds",
                timeout_seconds,
            )
            return self._error_result(
                user_input=user_input,
                chat_log=chat_log,
                response=response,
                speech=(
                    "Jarvis Core took too long to respond. Please try again."
                ),
            )

        except ClientResponseError as exc:
            _LOGGER.exception(
                "Jarvis Core returned HTTP status %s",
                exc.status,
            )
            return self._error_result(
                user_input=user_input,
                chat_log=chat_log,
                response=response,
                speech=(
                    "Jarvis Core returned an error. Please check the "
                    "Jarvis server logs."
                ),
            )

        except ValueError:
            _LOGGER.exception("Jarvis streaming reply ended unexpectedly")
            return self._error_result(
                user_input=user_input,
                chat_log=chat_log,
                response=response,
                speech=(
                    "Jarvis's reply was interrupted. Please try again."
                ),
            )

        except (ClientError, TypeError):
            _LOGGER.exception("Unable to communicate with Jarvis Core")
            return self._error_result(
                user_input=user_input,
                chat_log=chat_log,
                response=response,
                speech=(
                    "I could not reach Jarvis Core. Please check the "
                    "Jarvis server."
                ),
            )

    def _error_result(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
        response: intent.IntentResponse,
        speech: str,
    ) -> ConversationResult:
        """Build a spoken error response."""

        response.async_set_error(
            intent.IntentResponseErrorCode.UNKNOWN,
            speech,
        )

        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(
                agent_id=user_input.agent_id,
                content=speech,
            )
        )

        return ConversationResult(
            conversation_id=(
                user_input.conversation_id
                or chat_log.conversation_id
            ),
            response=response,
            continue_conversation=False,
        )
