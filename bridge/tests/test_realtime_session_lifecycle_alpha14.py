from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from app.realtime_voice import (
    ProviderSessionLease,
    RealtimeVoiceConfig,
    RealtimeVoiceProxy,
    _mark_turn_started,
    _mark_turn_terminal,
)


def _proxy() -> RealtimeVoiceProxy:
    return RealtimeVoiceProxy(
        RealtimeVoiceConfig(
            enabled=True,
            api_key="test-key",
            mobile_token="test-token",
            voice_pe_token="voice-pe-token",
            model="gpt-realtime",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="",
        )
    )


class RecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.audio: list[bytes] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.messages.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.audio.append(payload)


class SessionClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        return json.dumps(
            {
                "type": "auth",
                "token": "test-token",
                "conversation_id": "persistent-conversation",
            }
        )

    async def close(self, *, code: int) -> None:
        self.close_codes.append(code)


class RecordingUpstream:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class ProviderContext(RecordingUpstream):
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


async def _events(*values: dict[str, Any]):
    for value in values:
        yield json.dumps(value)


def _state(*, epoch: int, generation: int = 1, active: bool = False) -> dict[str, Any]:
    terminal = asyncio.Event()
    if not active:
        terminal.set()
    return {
        "provider_epoch": epoch,
        "generation": generation,
        "active_generation": generation,
        "active_client_turn_id": 42,
        "turn_in_progress": active,
        "turn_terminal_event": terminal,
        "suppress_audio": False,
        "openai_response_turns": {},
    }


@pytest.mark.asyncio
async def test_idle_session_renews_when_lease_is_due() -> None:
    proxy = _proxy()
    state = _state(epoch=7)
    lease = ProviderSessionLease(
        epoch=7,
        established_at=100.0,
        lifetime_seconds=60.0,
        renewal_lead_seconds=10.0,
    )
    lease.established_at = -10_000.0

    assert await proxy._wait_for_safe_provider_renewal(state, lease) is True
    assert lease.renewal_pending is True
    assert state["provider_renewal_pending"] is True


@pytest.mark.asyncio
async def test_due_renewal_waits_for_active_turn_terminal_audio_boundary() -> None:
    proxy = _proxy()
    state = _state(epoch=8, active=True)
    lease = ProviderSessionLease(
        epoch=8,
        established_at=-10_000.0,
        lifetime_seconds=60.0,
        renewal_lead_seconds=10.0,
    )

    waiter = asyncio.create_task(proxy._wait_for_safe_provider_renewal(state, lease))
    await asyncio.sleep(0)
    assert waiter.done() is False
    assert state["provider_renewal_pending"] is True

    _mark_turn_terminal(state)
    assert await waiter is True
    assert state["provider_transitioning"] is True


@pytest.mark.asyncio
async def test_old_epoch_delayed_audio_is_discarded() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=2, generation=4, active=True)

    await proxy._openai_to_client(
        client,
        _events(
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"old").decode()}
        ),
        _unused_brain,
        {},
        "realtime",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=1,
    )

    assert client.audio == []
    assert state["turn_in_progress"] is True


@pytest.mark.asyncio
async def test_old_epoch_delayed_transcript_is_discarded() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=2, generation=4, active=True)

    await proxy._openai_to_client(
        client,
        _events({"type": "response.output_audio_transcript.delta", "delta": "old"}),
        _unused_brain,
        {},
        "realtime",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=1,
    )

    assert client.messages == []


@pytest.mark.asyncio
async def test_old_epoch_response_done_cannot_finalize_newer_turn() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=3, generation=9, active=True)

    await proxy._openai_to_client(
        client,
        _events({"type": "response.done", "response": {"id": "old", "status": "completed"}}),
        _unused_brain,
        {},
        "realtime",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=2,
    )

    assert state["turn_in_progress"] is True
    assert not any(item.get("type") == "turn.done" for item in client.messages)


@pytest.mark.asyncio
async def test_provider_lifetime_error_enters_common_recovery_path() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=6)

    with pytest.raises(RuntimeError, match="maximum duration"):
        await proxy._openai_to_client(
            client,
            _events(
                {
                    "type": "error",
                    "error": {"message": "Your session hit the maximum duration of 60 minutes."},
                }
            ),
            _unused_brain,
            {},
            "realtime",
            "live",
            "marin",
            set(),
            state,
            provider_epoch=6,
        )

    assert proxy.last_error == "Your session hit the maximum duration of 60 minutes."
    assert [item["type"] for item in client.messages] == ["error"]


@pytest.mark.asyncio
async def test_new_epoch_turn_works_with_monotonic_generation() -> None:
    proxy = _proxy()
    client = RecordingClient()
    upstream = RecordingUpstream()
    state = _state(epoch=5, generation=12)
    tasks: set[asyncio.Task[Any]] = set()
    terminal_state_when_done_sent: list[bool] = []
    original_send_json = client.send_json

    async def record_terminal_order(payload: dict[str, Any]) -> None:
        if payload.get("type") == "turn.done":
            terminal_state_when_done_sent.append(state["turn_terminal_event"].is_set())
        await original_send_json(payload)

    client.send_json = record_terminal_order  # type: ignore[method-assign]

    async def brain(command: str, metadata: dict[str, Any], on_delta):
        await on_delta("new answer")
        return {
            "success": True,
            "response": "new answer",
            "conversation_id": "kept-conversation",
            "intent": "general",
            "model": "test",
        }

    await proxy._start_brain_turn(
        "hello",
        False,
        client,
        upstream,
        brain,
        {"conversation_id": "kept-conversation"},
        "realtime",
        "marin",
        tasks,
        state,
        client_turn_id=77,
        provider_epoch=5,
    )
    await asyncio.gather(*tuple(tasks))

    assert state["generation"] == 13
    assert [item["type"] for item in client.messages].count("brain.response") == 1
    assert [item["type"] for item in client.messages].count("turn.done") == 1
    assert state["turn_in_progress"] is False
    assert terminal_state_when_done_sent == [False]


@pytest.mark.asyncio
async def test_old_and_new_epoch_events_cannot_duplicate_output() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=11, generation=21, active=True)
    encoded = base64.b64encode(b"new").decode()

    old_events = _events(
        {"type": "response.output_audio_transcript.delta", "delta": "duplicate"},
        {"type": "response.output_audio.delta", "delta": encoded},
        {"type": "response.done", "response": {"id": "response-1", "status": "completed"}},
    )
    await proxy._openai_to_client(
        client,
        old_events,
        _unused_brain,
        {},
        "realtime",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=10,
    )

    new_events = _events(
        {
            "type": "response.created",
            "response": {
                "id": "response-2",
                "metadata": {"jarvis_generation": "21", "jarvis_client_turn_id": "42"},
            },
        },
        {
            "type": "response.output_audio_transcript.delta",
            "response_id": "response-2",
            "delta": "new",
        },
        {"type": "response.output_audio.delta", "response_id": "response-2", "delta": encoded},
        {"type": "response.done", "response": {"id": "response-2", "status": "completed"}},
    )
    await proxy._openai_to_client(
        client,
        new_events,
        _unused_brain,
        {},
        "realtime",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=11,
    )

    assert client.audio == [b"new"]
    deltas = [item for item in client.messages if item.get("type") == "assistant.transcript.delta"]
    assert [item["text"] for item in deltas] == ["new"]
    assert [item["type"] for item in client.messages].count("turn.done") == 1


@pytest.mark.asyncio
async def test_home_assistant_mode_still_suppresses_provider_audio() -> None:
    proxy = _proxy()
    client = RecordingClient()
    state = _state(epoch=4, generation=6, active=True)

    await proxy._openai_to_client(
        client,
        _events(
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"audio").decode()}
        ),
        _unused_brain,
        {},
        "home_assistant",
        "live",
        "marin",
        set(),
        state,
        provider_epoch=4,
    )

    assert client.audio == []


def test_normal_session_below_threshold_is_unchanged() -> None:
    lease = ProviderSessionLease(
        epoch=1,
        established_at=1_000.0,
        lifetime_seconds=3_600.0,
        renewal_lead_seconds=120.0,
    )

    assert lease.renewal_required(4_479.9) is False
    assert lease.renewal_pending is False


def test_turn_terminal_event_tracks_barge_in_safe_boundary() -> None:
    state = _state(epoch=1)
    _mark_turn_started(state)
    assert state["turn_terminal_event"].is_set() is False
    _mark_turn_terminal(state)
    assert state["turn_terminal_event"].is_set() is True


@pytest.mark.asyncio
async def test_idle_renewal_rotates_provider_without_closing_client(monkeypatch) -> None:
    proxy = _proxy()
    client = SessionClient()
    providers: list[ProviderContext] = []

    def connect(*args, **kwargs):
        provider = ProviderContext()
        providers.append(provider)
        return provider

    async def client_reader(*args, provider_epoch=0, **kwargs):
        if provider_epoch == 1:
            await asyncio.Event().wait()

    async def provider_reader(*args, provider_epoch=0, **kwargs):
        await asyncio.Event().wait()

    async def renewal_waiter(state, lease):
        if lease.epoch == 1:
            return True
        await asyncio.Event().wait()

    monkeypatch.setattr("app.realtime_voice._load_websocket_connect", lambda: connect)
    monkeypatch.setattr(proxy, "_client_to_openai", client_reader)
    monkeypatch.setattr(proxy, "_openai_to_client", provider_reader)
    monkeypatch.setattr(proxy, "_wait_for_safe_provider_renewal", renewal_waiter)

    await proxy.handle(client, _unused_brain)

    assert len(providers) == 2
    assert proxy.total_provider_renewals == 1
    assert proxy.total_provider_recoveries == 0
    assert client.close_codes == [1000]
    assert all(provider.messages[0]["type"] == "session.update" for provider in providers)


@pytest.mark.asyncio
async def test_unexpected_provider_close_rotates_with_same_epoch_fencing(monkeypatch) -> None:
    proxy = _proxy()
    client = SessionClient()
    providers: list[ProviderContext] = []

    def connect(*args, **kwargs):
        provider = ProviderContext()
        providers.append(provider)
        return provider

    async def client_reader(*args, provider_epoch=0, **kwargs):
        if provider_epoch == 1:
            await asyncio.Event().wait()

    async def provider_reader(*args, provider_epoch=0, **kwargs):
        if provider_epoch == 1:
            return
        await asyncio.Event().wait()

    async def renewal_waiter(state, lease):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.realtime_voice._load_websocket_connect", lambda: connect)
    monkeypatch.setattr(proxy, "_client_to_openai", client_reader)
    monkeypatch.setattr(proxy, "_openai_to_client", provider_reader)
    monkeypatch.setattr(proxy, "_wait_for_safe_provider_renewal", renewal_waiter)

    await proxy.handle(client, _unused_brain)

    assert len(providers) == 2
    assert proxy.total_provider_recoveries == 1
    assert proxy.total_provider_renewals == 0
    assert any(item.get("type") == "error" for item in client.messages)


@pytest.mark.asyncio
async def test_command_racing_renewal_boundary_is_queued_for_new_provider() -> None:
    proxy = _proxy()
    upstream = RecordingUpstream()
    state = _state(epoch=15)
    state["provider_transitioning"] = True

    class TextClient(RecordingClient):
        async def receive(self) -> dict[str, str]:
            return {"text": json.dumps({"type": "text", "text": "hello", "client_turn_id": 91})}

    reader = asyncio.create_task(
        proxy._client_to_openai(
            TextClient(),
            upstream,
            _unused_brain,
            {},
            "realtime",
            "live",
            "marin",
            set(),
            state,
            provider_epoch=15,
        )
    )
    await asyncio.sleep(0)

    queued = state["queued_client_messages"]
    assert json.loads(queued[0]["text"])["client_turn_id"] == 91
    assert state["generation"] == 1
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_audio_racing_renewal_boundary_never_reaches_old_provider() -> None:
    proxy = _proxy()
    upstream = RecordingUpstream()
    state = _state(epoch=16)
    state["provider_transitioning"] = True

    class AudioClient(RecordingClient):
        async def receive(self) -> dict[str, bytes]:
            return {"bytes": b"new-session-audio"}

    reader = asyncio.create_task(
        proxy._client_to_openai(
            AudioClient(),
            upstream,
            _unused_brain,
            {},
            "realtime",
            "live",
            "marin",
            set(),
            state,
            provider_epoch=16,
        )
    )
    await asyncio.sleep(0)

    assert upstream.messages == []
    assert state["queued_client_messages"] == [{"bytes": b"new-session-audio"}]
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


async def _unused_brain(command: str, metadata: dict[str, Any], on_delta):
    return {"success": True, "response": "unused"}
