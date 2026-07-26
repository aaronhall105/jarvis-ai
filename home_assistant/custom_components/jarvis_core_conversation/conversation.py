from __future__ import annotations

import asyncio
import json
import time
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from aiohttp import ClientError, ClientResponseError
from homeassistant.components import conversation
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
from homeassistant.helpers import aiohttp_client, intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .streaming import AssistantStreamState

from .const import (
    CONF_FOLLOW_UP_MODE,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_FOLLOW_UP_MODE,
    FOLLOW_UP_ALWAYS,
    FOLLOW_UP_DISABLED,
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
    if isinstance(explicit, bool):
        return explicit

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

    if _normalise_phrase(user_text) in _STOP_PHRASES:
        return False

    if mode == FOLLOW_UP_DISABLED:
        return False

    if mode == FOLLOW_UP_QUESTIONS:
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

        return {
            "user_id": user_id,
            "user_name": user_name,
            "user_is_admin": bool(
                getattr(user, "is_admin", False)
            ),
            "device_id": device_id,
            # Voice pipeline requests normally carry a device ID. Typed Assist
            # chat usually does not, so it can keep fuller text responses.
            "voice_mode": bool(device_id),
        }

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

        base_url = str(self.entry.data[CONF_URL]).rstrip("/")
        timeout_seconds = int(self.entry.data[CONF_TIMEOUT])
        follow_up_mode = str(
            self.entry.options.get(
                CONF_FOLLOW_UP_MODE,
                DEFAULT_FOLLOW_UP_MODE,
            )
        )

        # Home Assistant supplies one conversation ID for the whole voice/chat
        # session. Passing it to Jarvis keeps Jarvis Core's SQLite history in
        # step with Home Assistant's conversation.
        request_conversation_id = (
            user_input.conversation_id
            or chat_log.conversation_id
        )

        session = aiohttp_client.async_get_clientsession(self.hass)
        request_user = await self._request_user_context(user_input)

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
                        **request_user,
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
