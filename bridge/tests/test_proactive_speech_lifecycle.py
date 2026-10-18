from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.proactive_speech import (
    EndpointActivity,
    ProactiveSpeechCoordinator,
    ProactiveSpeechState,
    SpeechQueueRequest,
    SpeechStartResult,
)
from app.proactive_intelligence import Candidate, ProactiveEngine, SettingsModel


@dataclass
class Clock:
    value: int = 1_000

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds


class InertSpeechEndpoint:
    def __init__(self) -> None:
        self.activity = EndpointActivity()
        self.started: list[tuple[str, bool]] = []
        self.stopped: list[str] = []

    async def status(self, endpoint: str) -> EndpointActivity:
        del endpoint
        return self.activity

    async def start(self, item: dict[str, Any], replay: bool) -> SpeechStartResult:
        self.started.append((str(item["event_id"]), replay))
        self.activity = EndpointActivity(assistant_speaking=True, turn_in_progress=True)
        return SpeechStartResult(True, f"session-{item['event_id']}")

    async def stop(self, item: dict[str, Any]) -> bool:
        self.stopped.append(str(item["event_id"]))
        self.activity = EndpointActivity(user_speaking=True, turn_in_progress=True)
        return True


def request(
    event_id: str,
    clock: Clock,
    *,
    importance: int = 80,
    dedupe_key: str | None = None,
    supersession_key: str = "condition:one",
) -> SpeechQueueRequest:
    return SpeechQueueRequest(
        event_id=event_id,
        event_type="synthetic_condition",
        message="An inert test condition changed.",
        importance=importance,
        endpoint="script.inert_test_speaker",
        freshness_deadline=clock.now() + 60,
        dedupe_key=dedupe_key or f"event:{event_id}",
        supersession_key=supersession_key,
        provenance={
            "source_type": "OTHER_GROUNDED_SOURCE",
            "source_reference": {"test": "inert"},
            "observed_at": clock.now(),
        },
        trigger_evidence={"version": event_id},
        target_user="all",
    )


def coordinator(
    tmp_path: Path,
    clock: Clock,
    endpoint: InertSpeechEndpoint,
    *,
    fresh: list[bool] | None = None,
) -> ProactiveSpeechCoordinator:
    freshness = fresh if fresh is not None else [True]

    async def revalidate(_: dict[str, Any]) -> bool:
        return freshness[0]

    return ProactiveSpeechCoordinator(
        tmp_path / "proactive.db",
        endpoint_status=endpoint.status,
        start_speech=endpoint.start,
        stop_speech=endpoint.stop,
        revalidate=revalidate,
        now_fn=clock.now,
        completion_grace_seconds=1,
        resume_window_seconds=20,
    )


@pytest.mark.asyncio
async def test_queued_waiting_speaking_completed_has_explicit_transitions(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock))

    await lifecycle.process_ready()
    lifecycle.mark_completed("event-1")

    item = lifecycle.get("event-1")
    assert item is not None
    assert item["lifecycle_state"] == ProactiveSpeechState.COMPLETED.value
    assert item["completed_at"] == clock.now()
    assert [row["to_state"] for row in lifecycle.transitions("event-1")] == [
        "QUEUED",
        "WAITING_FOR_GAP",
        "SPEAKING",
        "COMPLETED",
    ]


@pytest.mark.asyncio
async def test_completion_waits_for_endpoint_playback_boundary_and_is_idempotent(
    tmp_path: Path,
) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock))
    await lifecycle.process_ready()

    clock.advance(2)
    await lifecycle.process_ready()
    assert lifecycle.get("event-1")["lifecycle_state"] == "SPEAKING"

    endpoint.activity = EndpointActivity()
    await lifecycle.process_ready()
    first = lifecycle.mark_completed("event-1")
    second = lifecycle.mark_completed("event-1")

    assert first == second
    assert second["lifecycle_state"] == "COMPLETED"
    assert [row["to_state"] for row in lifecycle.transitions("event-1")].count("COMPLETED") == 1


@pytest.mark.asyncio
async def test_active_user_speech_keeps_event_waiting_for_gap(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    endpoint.activity = EndpointActivity(user_speaking=True, turn_in_progress=True)
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock))

    await lifecycle.process_ready()
    assert lifecycle.get("event-1")["lifecycle_state"] == "WAITING_FOR_GAP"
    assert endpoint.started == []

    endpoint.activity = EndpointActivity()
    await lifecycle.process_ready()
    assert lifecycle.get("event-1")["lifecycle_state"] == "SPEAKING"


@pytest.mark.asyncio
async def test_stale_event_is_cancelled_without_tts(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    fresh = [True]
    lifecycle = coordinator(tmp_path, clock, endpoint, fresh=fresh)
    lifecycle.queue(request("event-1", clock))
    endpoint.activity = EndpointActivity(user_speaking=True, turn_in_progress=True)
    await lifecycle.process_ready()

    fresh[0] = False
    endpoint.activity = EndpointActivity()
    await lifecycle.process_ready()

    assert lifecycle.get("event-1")["lifecycle_state"] == "CANCELLED"
    assert endpoint.started == []


@pytest.mark.asyncio
async def test_interruption_stops_audio_and_drops_normal_event(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock, importance=80))
    await lifecycle.process_ready()

    result = await lifecycle.interrupt(event_id="event-1", topic_changed=False)

    assert result is not None
    assert result["lifecycle_state"] == "DROPPED"
    assert endpoint.stopped == ["event-1"]
    assert [row["to_state"] for row in lifecycle.transitions("event-1")][-2:] == [
        "INTERRUPTED",
        "DROPPED",
    ]


@pytest.mark.asyncio
async def test_critical_interruption_replays_when_little_was_delivered(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock, importance=100))
    await lifecycle.process_ready()

    result = await lifecycle.interrupt(
        event_id="event-1", topic_changed=False, delivered_fraction=0.05
    )
    assert result is not None
    assert result["lifecycle_state"] == "REPLAY_PENDING"

    endpoint.activity = EndpointActivity()
    await lifecycle.process_ready()
    assert lifecycle.get("event-1")["lifecycle_state"] == "SPEAKING"
    assert endpoint.started == [("event-1", False), ("event-1", True)]
    lifecycle.mark_completed("event-1")
    assert lifecycle.get("event-1")["lifecycle_state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_explicit_continuation_resumes_from_safe_boundary(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock, importance=100))
    await lifecycle.process_ready()
    await lifecycle.interrupt(
        event_id="event-1", topic_changed=True, explicit_continuation=True, delivered_fraction=0.5
    )
    endpoint.activity = EndpointActivity()

    result = await lifecycle.request_continuation("event-1")

    assert result is not None
    assert result["lifecycle_state"] == "SPEAKING"
    assert endpoint.started[-1] == ("event-1", False)
    lifecycle.mark_completed("event-1")
    assert lifecycle.get("event-1")["lifecycle_state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_interrupted_event_expires_instead_of_becoming_a_zombie(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock, importance=100))
    await lifecycle.process_ready()
    await lifecycle.interrupt(
        event_id="event-1",
        topic_changed=False,
        delivered_fraction=0.5,
        defer_decision=True,
    )

    clock.advance(21)
    result = await lifecycle.request_continuation("event-1")

    assert result is not None
    assert result["lifecycle_state"] == "DROPPED"
    assert result["state_reason"] == "resume_window_expired"


@pytest.mark.asyncio
async def test_topic_change_drops_interrupted_event(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock, importance=100))
    await lifecycle.process_ready()

    result = await lifecycle.interrupt(event_id="event-1", topic_changed=True)

    assert result is not None
    assert result["lifecycle_state"] == "DROPPED"


def test_supersession_and_deduplication_are_structural(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    first = lifecycle.queue(request("event-1", clock, dedupe_key="same-generation"))
    duplicate = lifecycle.queue(request("event-duplicate", clock, dedupe_key="same-generation"))
    newer = lifecycle.queue(request("event-2", clock, dedupe_key="new-generation"))

    assert duplicate["event_id"] == first["event_id"]
    assert lifecycle.get("event-1")["lifecycle_state"] == "CANCELLED"
    assert lifecycle.get("event-1")["state_reason"] == "superseded"
    assert newer["lifecycle_state"] == "QUEUED"


def test_restart_does_not_replay_completed_or_uncertain_speaking_item(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock))
    # Persist a speaking state without requiring an event loop.
    first._transition("event-1", ProactiveSpeechState.WAITING_FOR_GAP, "test")
    first._transition(
        "event-1",
        ProactiveSpeechState.SPEAKING,
        "test",
        fields={"speech_started_at": clock.now()},
    )

    restarted = coordinator(tmp_path, clock, endpoint)
    restarted.initialise()

    item = restarted.get("event-1")
    assert item is not None
    assert item["lifecycle_state"] == "DROPPED"
    assert item["state_reason"] == "restart_delivery_outcome_unknown"


@pytest.mark.asyncio
async def test_queued_event_survives_restart_and_revalidates(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock))

    restarted = coordinator(tmp_path, clock, endpoint)
    await restarted.process_ready()

    assert restarted.get("event-1")["lifecycle_state"] == "SPEAKING"


def test_stale_queued_event_is_cancelled_during_restart(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock))
    clock.advance(61)

    restarted = coordinator(tmp_path, clock, endpoint)
    restarted.initialise()

    item = restarted.get("event-1")
    assert item is not None
    assert item["lifecycle_state"] == "CANCELLED"
    assert item["state_reason"] == "expired_during_restart"


@pytest.mark.asyncio
async def test_interrupted_restart_respects_resume_window(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock, importance=100))
    await first.process_ready()
    await first.interrupt(
        event_id="event-1",
        topic_changed=False,
        delivered_fraction=0.5,
        defer_decision=True,
    )

    restarted = coordinator(tmp_path, clock, endpoint)
    restarted.initialise()
    assert restarted.get("event-1")["lifecycle_state"] == "RESUME_PENDING"


@pytest.mark.asyncio
async def test_interrupted_restart_drops_after_resume_window(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock, importance=100))
    await first.process_ready()
    await first.interrupt(
        event_id="event-1",
        topic_changed=False,
        delivered_fraction=0.5,
        defer_decision=True,
    )
    clock.advance(21)

    restarted = coordinator(tmp_path, clock, endpoint)
    restarted.initialise()
    item = restarted.get("event-1")
    assert item["lifecycle_state"] == "DROPPED"
    assert item["state_reason"] == "restart_resume_expired"


@pytest.mark.asyncio
async def test_completed_event_is_never_replayed_after_restart(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    first = coordinator(tmp_path, clock, endpoint)
    first.queue(request("event-1", clock))
    await first.process_ready()
    first.mark_completed("event-1")

    restarted_endpoint = InertSpeechEndpoint()
    restarted = coordinator(tmp_path, clock, restarted_endpoint)
    await restarted.process_ready()

    assert restarted.get("event-1")["lifecycle_state"] == "COMPLETED"
    assert restarted_endpoint.started == []


@pytest.mark.asyncio
async def test_continuation_is_scoped_to_target_principal(tmp_path: Path) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    private_request = replace(
        request("event-1", clock, importance=100),
        target_user="aaron",
    )
    lifecycle.queue(private_request)
    await lifecycle.process_ready()
    await lifecycle.interrupt(
        event_id="event-1",
        topic_changed=False,
        delivered_fraction=0.5,
        defer_decision=True,
    )

    assert await lifecycle.request_continuation(principal_id="amber") is None
    assert await lifecycle.request_continuation(principal_id="aaron") is not None


def test_provenance_is_preserved_without_message_payload_in_transition_log(
    tmp_path: Path,
) -> None:
    clock = Clock()
    endpoint = InertSpeechEndpoint()
    lifecycle = coordinator(tmp_path, clock, endpoint)
    lifecycle.queue(request("event-1", clock))

    item = lifecycle.get("event-1")
    assert item is not None
    assert item["provenance"]["source_type"] == "OTHER_GROUNDED_SOURCE"
    assert item["provenance"]["source_reference"] == {"test": "inert"}
    assert "message" not in lifecycle.transitions("event-1")[0]["metadata"]


def speech_enabled_engine(tmp_path: Path) -> ProactiveEngine:
    engine = ProactiveEngine(
        str(tmp_path / "engine.db"),
        enabled=True,
        min_importance=80,
        speaker_entity="script.inert_test_speaker",
        principal_id="aaron",
    )
    engine.mobile_notify = AsyncMock(return_value={"accepted": True})
    engine.ha_service = AsyncMock(return_value={"accepted": True})
    engine.presence["aaron"] = "home"
    engine.save_settings(
        SettingsModel(
            user_id="aaron",
            speak_enabled=True,
            notification_mode="all_useful",
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )
    return engine


@pytest.mark.asyncio
async def test_v19_delivery_queues_lifecycle_instead_of_speaking_outside_it(
    tmp_path: Path,
) -> None:
    engine = speech_enabled_engine(tmp_path)
    event = await engine.record(
        Candidate(
            category="appliances",
            kind="cycle_finished",
            entity_id="sensor.synthetic",
            title="Synthetic condition",
            message="An inert condition changed.",
            reason="Synthetic grounded evidence changed.",
            importance=85,
            target_user="aaron",
            evidence=("sensor.synthetic",),
        )
    )

    assert event is not None
    lifecycle = engine.speech.get(str(event["id"]))
    assert lifecycle is not None
    assert lifecycle["lifecycle_state"] == "SPEAKING"
    assert lifecycle["provenance"]["source_type"] == "HOME_STATE"
    engine.ha_service.assert_awaited_once_with(
        "script",
        "turn_on",
        {
            "entity_id": "script.inert_test_speaker",
            "variables": {"message": "An inert condition changed."},
        },
    )


@pytest.mark.asyncio
async def test_v19_stale_trigger_is_cancelled_before_home_assistant_speech(
    tmp_path: Path,
) -> None:
    engine = speech_enabled_engine(tmp_path)
    engine.states = {"sensor.synthetic": {"entity_id": "sensor.synthetic", "state": "off"}}
    event = await engine.record(
        Candidate(
            category="appliances",
            kind="cycle_finished",
            entity_id="sensor.synthetic",
            title="Synthetic condition",
            message="An inert condition changed.",
            reason="Synthetic grounded evidence changed.",
            importance=85,
            target_user="aaron",
            current_state="on",
            observed_at=1_000,
        )
    )

    assert event is not None
    lifecycle = engine.speech.get(str(event["id"]))
    assert lifecycle is not None
    assert lifecycle["lifecycle_state"] == "CANCELLED"
    engine.ha_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_realtime_voice_interrupts_only_after_user_speech_starts(
    tmp_path: Path,
) -> None:
    engine = speech_enabled_engine(tmp_path)
    event = await engine.record(
        Candidate(
            category="appliances",
            kind="cycle_finished",
            entity_id="sensor.synthetic",
            title="Synthetic condition",
            message="An inert condition changed.",
            reason="Synthetic grounded evidence changed.",
            importance=85,
            target_user="aaron",
        )
    )
    assert event is not None
    engine.ha_service.reset_mock()

    await engine.voice_session_started({"session_id": "voice-session-1"})

    lifecycle = engine.speech.get(str(event["id"]))
    assert lifecycle is not None
    assert lifecycle["lifecycle_state"] == "SPEAKING"
    engine.ha_service.assert_not_awaited()

    await engine.voice_user_speech_started({"session_id": "voice-session-1"})

    lifecycle = engine.speech.get(str(event["id"]))
    assert lifecycle is not None
    assert lifecycle["lifecycle_state"] == "INTERRUPTED"
    engine.ha_service.assert_awaited_once_with(
        "script",
        "turn_off",
        {"entity_id": "script.inert_test_speaker"},
    )

    await engine.voice_session_ended({"session_id": "voice-session-1"})
    assert engine.speech.get(str(event["id"]))["lifecycle_state"] == "DROPPED"


@pytest.mark.asyncio
async def test_explicit_semantic_continuation_survives_interactive_turn_boundary(
    tmp_path: Path,
) -> None:
    engine = speech_enabled_engine(tmp_path)
    event = await engine.record(
        Candidate(
            category="appliances",
            kind="cycle_finished",
            entity_id="sensor.synthetic",
            title="Synthetic condition",
            message="An inert condition changed.",
            reason="Synthetic grounded evidence changed.",
            importance=85,
            target_user="aaron",
        )
    )
    assert event is not None
    await engine.voice_session_started({"session_id": "voice-session-1"})
    await engine.voice_user_speech_started({"session_id": "voice-session-1"})

    pending = await engine.speech.request_continuation(str(event["id"]))
    assert pending is not None
    assert pending["lifecycle_state"] == "REPLAY_PENDING"

    await engine.voice_session_ended({"session_id": "voice-session-1"})
    assert engine.speech.get(str(event["id"]))["lifecycle_state"] == "SPEAKING"
