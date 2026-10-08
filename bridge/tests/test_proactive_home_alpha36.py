from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.notification_policy import decide_notification
from app.house_awareness import AwarenessEvent, HouseAwarenessEngine
from app.proactive_intelligence import (
    Candidate,
    NotificationOutcomeUnknown,
    ProactiveEngine,
    Rules,
    SettingsModel,
)


def _camera_states(state: str, *, child_count: int = 7) -> list[dict]:
    rows = [
        {
            "entity_id": "camera.hallway",
            "domain": "camera",
            "name": "Hallway Camera Stream",
            "device_id": "camera-device-1",
            "device_name": "Hallway Camera",
            "area_id": "hallway",
            "area_name": "Hallway",
            "state": state,
        }
    ]
    rows.extend(
        {
            "entity_id": f"sensor.hallway_camera_diagnostic_{index}",
            "domain": "sensor",
            "name": f"Hallway Camera Diagnostic {index}",
            "device_id": "camera-device-1",
            "device_name": "Hallway Camera",
            "area_id": "hallway",
            "area_name": "Hallway",
            "entity_category": "diagnostic",
            "state": state,
        }
        for index in range(1, child_count + 1)
    )
    return rows


def _engine(tmp_path, *, principal_id: str = "aaron") -> ProactiveEngine:
    engine = ProactiveEngine(
        str(tmp_path / "proactive.db"),
        enabled=True,
        min_importance=80,
        cooldown=30,
        principal_id=principal_id,
    )
    engine.device_unavailable_seconds = 60
    engine.mobile_notify = AsyncMock(return_value={"accepted": True})
    engine.save_settings(
        SettingsModel(
            user_id=principal_id,
            notification_mode="all_useful",
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )
    return engine


@pytest.mark.asyncio
async def test_transient_device_outage_is_durable_but_never_notified(tmp_path) -> None:
    engine = _engine(tmp_path)

    await engine._process_device_availability(_camera_states("unavailable"), now=100)
    await engine._process_device_availability(_camera_states("streaming"), now=130)

    assert engine.feed("aaron") == []
    assert engine.mobile_notify.await_count == 0
    condition = engine.conditions()[0]
    assert condition["status"] == "suppressed"
    assert condition["suppression_reason"] == "recovered_before_threshold"


@pytest.mark.asyncio
async def test_persistent_multi_entity_camera_outage_creates_one_notification(
    tmp_path, monkeypatch
) -> None:
    clock = [100]
    monkeypatch.setattr("app.proactive_intelligence.time.time", lambda: clock[0])
    engine = _engine(tmp_path)

    await engine._process_device_availability(_camera_states("unavailable"), now=clock[0])
    clock[0] = 161
    await engine._process_device_availability(_camera_states("unavailable"), now=clock[0])
    clock[0] = 190
    await engine._process_device_availability(_camera_states("unavailable"), now=clock[0])

    events = engine.feed("aaron")
    assert len(events) == 1
    assert events[0]["kind"] == "device_unavailable"
    assert len(events[0]["evidence"]) == 8
    assert engine.mobile_notify.await_count == 1
    assert engine.conditions()[0]["status"] == "qualified"
    assert engine.pipeline_report()["counts"] == {
        "raw_observations": 24,
        "normalized_events": 1,
        "semantic_candidates": 1,
        "notifications": 1,
    }
    assert engine.pipeline_report()["llm_calls_per_raw_observation"] == 0.0


@pytest.mark.asyncio
async def test_online_camera_with_failed_diagnostic_does_not_create_outage(tmp_path) -> None:
    engine = _engine(tmp_path)
    states = _camera_states("streaming", child_count=1)
    states[1]["state"] = "unavailable"

    await engine._process_device_availability(states, now=100)
    await engine._process_device_availability(states, now=200)

    assert engine.conditions() == []
    assert engine.feed("aaron") == []


@pytest.mark.asyncio
async def test_restart_preserves_qualified_dedupe_and_partial_timer(tmp_path, monkeypatch) -> None:
    clock = [100]
    monkeypatch.setattr("app.proactive_intelligence.time.time", lambda: clock[0])
    first = _engine(tmp_path)
    await first._process_device_availability(_camera_states("unavailable"), now=100)

    restarted_during_timer = _engine(tmp_path)
    clock[0] = 161
    await restarted_during_timer._process_device_availability(
        _camera_states("unavailable"), now=161
    )
    assert restarted_during_timer.mobile_notify.await_count == 1

    restarted_after_notification = _engine(tmp_path)
    clock[0] = 300
    await restarted_after_notification._process_device_availability(
        _camera_states("unavailable"), now=300
    )

    assert restarted_after_notification.mobile_notify.await_count == 0
    assert len(restarted_after_notification.feed("aaron")) == 1
    assert restarted_after_notification.conditions()[0]["first_seen"] == 100


@pytest.mark.asyncio
async def test_recovery_is_emitted_once_only_after_reported_condition(
    tmp_path, monkeypatch
) -> None:
    clock = [100]
    monkeypatch.setattr("app.proactive_intelligence.time.time", lambda: clock[0])
    engine = _engine(tmp_path)
    await engine._process_device_availability(_camera_states("unavailable"), now=100)
    clock[0] = 161
    await engine._process_device_availability(_camera_states("unavailable"), now=161)
    clock[0] = 200
    await engine._process_device_availability(_camera_states("streaming"), now=200)
    clock[0] = 220
    await engine._process_device_availability(_camera_states("streaming"), now=220)

    assert [item["kind"] for item in engine.feed("aaron")] == [
        "device_recovered",
        "device_unavailable",
    ]
    assert engine.mobile_notify.await_count == 2
    assert engine.conditions()[0]["status"] == "recovered"


def test_appliance_completion_and_battery_threshold_are_transition_based() -> None:
    rules = Rules()
    rules.battery_low_percent = 20
    running = {
        "entity_id": "sensor.washing_machine_state",
        "state": "running",
        "attributes": {"friendly_name": "Washing Machine"},
    }
    finished = {**running, "state": "finished"}
    assert [
        item.kind
        for item in rules.evaluate(
            running,
            finished,
            first_seen=100,
            now=101,
            presence={},
        )
    ] == ["cycle_finished"]
    assert (
        rules.evaluate(
            finished,
            finished,
            first_seen=101,
            now=102,
            presence={},
        )
        == []
    )

    def battery(value: int) -> dict:
        return {
            "entity_id": "sensor.phone_battery",
            "state": str(value),
            "attributes": {
                "friendly_name": "Phone Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        }

    transitions = []
    for previous, current in ((21, 20), (20, 19), (19, 18)):
        transitions.extend(
            rules.evaluate(
                battery(previous),
                battery(current),
                first_seen=100,
                now=101,
                presence={},
            )
        )
    assert [item.kind for item in transitions] == ["battery_low"]


def test_quiet_hours_suppress_noncritical_qualified_event() -> None:
    settings = {
        "enabled": True,
        "notify_enabled": True,
        "notification_mode": "all_useful",
        "min_importance": 0,
        "categories": {"cameras": True},
    }
    decision = decide_notification(
        {
            "category": "cameras",
            "kind": "device_unavailable",
            "importance": 85,
        },
        settings=settings,
        presence={"aaron": "home"},
        recipient="aaron",
        quiet_hours=True,
    )

    assert decision.notify is False
    assert decision.reason_code == "quiet_hours"


@pytest.mark.asyncio
async def test_followups_use_persisted_evidence_and_live_grounded_state(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.set_state_provider(lambda: _camera_states("streaming"))
    event = await engine.record(
        Candidate(
            category="cameras",
            kind="device_unavailable",
            entity_id="camera.hallway",
            title="Device unavailable",
            message="Hallway Camera is unavailable.",
            reason="The primary camera entity remained unavailable for 120 seconds.",
            importance=85,
            target_user="aaron",
            evidence=("camera.hallway", "sensor.hallway_camera_diagnostic_1"),
            device_id="camera-device-1",
            device_name="Hallway Camera",
            current_state="unavailable",
            persistence_seconds=120,
        )
    )
    assert event is not None

    why = await engine.handle_reply("Why did you tell me that?", "aaron")
    current = await engine.handle_reply("Is it back yet?", "aaron")
    brief = await engine.handle_reply("Anything I need to know?", "aaron")
    isolated = await engine.handle_reply("Why did you tell me that?", "amber")

    assert why is not None and "120 seconds" in why["response"]
    assert "2 Home Assistant entities" in why["response"]
    assert current is not None and current["response"] == "Yes. Hallway Camera is back online."
    assert brief is not None and "One thing is worth noting" in brief["response"]
    assert isolated is None


@pytest.mark.asyncio
async def test_principal_scoped_conditions_and_events_do_not_leak(tmp_path, monkeypatch) -> None:
    clock = [100]
    monkeypatch.setattr("app.proactive_intelligence.time.time", lambda: clock[0])
    aaron = _engine(tmp_path, principal_id="aaron")
    amber = _engine(tmp_path, principal_id="amber")
    await aaron._process_device_availability(_camera_states("unavailable"), now=100)
    await amber._process_device_availability(_camera_states("unavailable"), now=100)
    clock[0] = 161
    await aaron._process_device_availability(_camera_states("unavailable"), now=161)
    await amber._process_device_availability(_camera_states("unavailable"), now=161)

    assert {item["principal_id"] for item in aaron.conditions()} == {"aaron"}
    assert {item["principal_id"] for item in amber.conditions()} == {"amber"}
    assert len(aaron.feed("aaron")) == 1
    assert len(amber.feed("amber")) == 1
    assert all(item["target_user"] == "aaron" for item in aaron.feed("aaron"))
    assert all(item["target_user"] == "amber" for item in amber.feed("amber"))

    clock[0] = 200
    await aaron._process_device_availability(_camera_states("streaming"), now=200)

    assert aaron.conditions()[0]["status"] == "recovered"
    assert amber.conditions()[0]["status"] == "qualified"
    amber_incidents = [item for item in amber.incidents() if item["target_user"] == "amber"]
    assert amber_incidents[0]["status"] == "active"


@pytest.mark.asyncio
async def test_unknown_notification_outcome_is_not_scheduled_for_blind_retry(
    tmp_path,
) -> None:
    engine = _engine(tmp_path)
    engine.mobile_notify = AsyncMock(side_effect=NotificationOutcomeUnknown("unknown"))
    event = await engine.record(
        Candidate(
            category="cameras",
            kind="device_unavailable",
            entity_id="camera.hallway",
            title="Device unavailable",
            message="Hallway Camera is unavailable.",
            reason="Grounded persistent outage.",
            importance=85,
            target_user="aaron",
            device_id="camera-device-1",
        )
    )

    assert event is not None
    assert event["decision"]["notification_outcome"] == "outcome_unknown"
    incident = engine.incidents()[0]
    assert incident["delivery_attempts"] == 1
    assert incident["next_retry_at"] is None


@pytest.mark.asyncio
async def test_global_notification_rate_limit_suppresses_burst(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.global_notification_limit = 1
    first = await engine.record(
        Candidate(
            category="cameras",
            kind="device_unavailable",
            entity_id="camera.hallway",
            title="Device unavailable",
            message="Hallway Camera is unavailable.",
            reason="Grounded persistent outage.",
            importance=85,
            target_user="aaron",
            device_id="camera-device-1",
        )
    )
    second = await engine.record(
        Candidate(
            category="cameras",
            kind="device_unavailable",
            entity_id="camera.bedroom",
            title="Device unavailable",
            message="Bedroom Camera is unavailable.",
            reason="Grounded persistent outage.",
            importance=85,
            target_user="aaron",
            device_id="camera-device-2",
        )
    )

    assert first is not None and first["notified_at"] is not None
    assert second is not None and second["notified_at"] is None
    assert second["decision"]["suppressed_reason"] == "global_notification_rate_limit"
    assert engine.mobile_notify.await_count == 1


def test_proactive_device_candidate_never_authorizes_autonomous_control() -> None:
    candidate = Candidate(
        category="cameras",
        kind="device_unavailable",
        entity_id="camera.hallway",
        title="Device unavailable",
        message="Hallway Camera is unavailable.",
        reason="Grounded persistent outage.",
        importance=85,
        device_id="camera-device-1",
    )

    assert candidate.actions == ("dismiss", "remind_later")
    assert "turn_off" not in candidate.actions
    assert "turn_on" not in candidate.actions


def test_quiet_period_calculation_remains_timezone_grounded(tmp_path) -> None:
    engine = _engine(tmp_path)
    settings = engine.settings("aaron")
    settings["quiet_start_hour"] = 22
    settings["quiet_end_hour"] = 7
    london = ZoneInfo("Europe/London")

    assert engine.quiet(settings, datetime(2026, 10, 8, 23, 0, tzinfo=london)) is True
    assert engine.quiet(settings, datetime(2026, 10, 8, 12, 0, tzinfo=london)) is False


def test_house_awareness_snapshot_carries_authoritative_device_identity() -> None:
    awareness = HouseAwarenessEngine.__new__(HouseAwarenessEngine)
    awareness._state_cache = {
        "camera.hallway": {
            "entity_id": "camera.hallway",
            "state": "unavailable",
            "attributes": {"friendly_name": "Hallway Camera Stream"},
        }
    }
    awareness._entity_meta = {
        "camera.hallway": {
            "device_id": "camera-device-1",
            "area_id": "hallway",
            "platform": "synthetic",
        }
    }
    awareness._device_meta = {"camera-device-1": {"name": "Hallway Camera", "area_id": "hallway"}}
    awareness._area_names = {"hallway": "Hallway"}

    snapshot = awareness.grounded_state_snapshot()

    assert snapshot == [
        {
            "entity_id": "camera.hallway",
            "state": "unavailable",
            "attributes": {"friendly_name": "Hallway Camera Stream"},
            "domain": "camera",
            "name": "Hallway Camera Stream",
            "area_id": "hallway",
            "area_name": "Hallway",
            "device_id": "camera-device-1",
            "device_name": "Hallway Camera",
            "device_class": None,
            "entity_category": None,
            "platform": "synthetic",
            "available": False,
            "unit": None,
            "display_value": "unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_house_awareness_direct_delivery_can_be_disabled_for_single_authority() -> None:
    awareness = HouseAwarenessEngine.__new__(HouseAwarenessEngine)
    awareness.proactive_enabled = True
    awareness.direct_proactive_delivery = False
    awareness.tools = type(
        "Tools",
        (),
        {"announce_message": AsyncMock(side_effect=AssertionError("must not deliver"))},
    )()
    event = AwarenessEvent(
        event_id=1,
        occurred_at="2026-10-08T12:00:00+00:00",
        entity_id="camera.hallway",
        domain="camera",
        event_type="safety_alert",
        category="security",
        summary="Synthetic event",
        old_state="off",
        new_state="on",
        area_id="hallway",
        area_name="Hallway",
        person_key=None,
        importance=100,
        user_visible=True,
        proactive_candidate=True,
        context_user_id="aaron",
        payload={},
    )

    await awareness._maybe_deliver_proactive(1, event)

    awareness.tools.announce_message.assert_not_awaited()
