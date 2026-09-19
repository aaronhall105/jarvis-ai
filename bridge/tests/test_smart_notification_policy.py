from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.notification_policy import (
    decide_notification,
    notification_recipients,
)
from app.proactive_intelligence import Candidate, ProactiveEngine, Rules, SettingsModel


def engine(tmp_path: Path) -> ProactiveEngine:
    value = ProactiveEngine(str(tmp_path / "proactive.db"), cooldown=300)
    value.mobile_notify = AsyncMock()  # type: ignore[method-assign]
    return value


def candidate(
    *,
    category: str,
    kind: str,
    entity_id: str,
    message: str,
    importance: int,
    target_user: str = "all",
) -> Candidate:
    return Candidate(
        category,
        kind,
        entity_id,
        kind.replace("_", " ").title(),
        message,
        "grounded test evidence",
        importance,
        target_user,
    )


def no_quiet_hours(value: ProactiveEngine, user: str = "aaron") -> None:
    value.save_settings(
        SettingsModel(
            user_id=user,
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )


@pytest.mark.asyncio
async def test_important_only_keeps_washing_machine_door_in_activity(tmp_path: Path) -> None:
    value = engine(tmp_path)
    no_quiet_hours(value)
    event = await value.record(
        candidate(
            category="security",
            kind="door_open",
            entity_id="binary_sensor.washing_machine_door_is_open",
            message="Washing Machine Door is open has been open for 1481 minutes.",
            importance=86,
        )
    )

    assert event is not None
    assert event["status"] == "activity"
    assert event["notified_at"] is None
    decision = event["decision"]["recipient_decisions"]["aaron"]
    assert decision["reason_code"] == "important_only_appliance_opening"
    value.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_occupied_front_door_person_detection_does_not_push(tmp_path: Path) -> None:
    value = engine(tmp_path)
    no_quiet_hours(value)
    value.presence = {"aaron": "home", "amber": "home"}

    event = await value.record(
        candidate(
            category="cameras",
            kind="person_detected",
            entity_id="binary_sensor.front_door_person",
            message="Front door Person detected a person.",
            importance=82,
        )
    )

    assert event is not None
    assert event["status"] == "activity"
    assert (
        event["decision"]["recipient_decisions"]["aaron"]["reason_code"]
        == "important_only_occupied_house_person_detection"
    )
    value.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unknown_presence_never_invents_an_away_security_context(tmp_path: Path) -> None:
    value = engine(tmp_path)
    no_quiet_hours(value)
    value.presence = {"aaron": "unknown", "amber": "unknown"}
    event = await value.record(
        candidate(
            category="cameras",
            kind="person_detected",
            entity_id="binary_sensor.front_door_person",
            message="Front door Person detected a person.",
            importance=98,
        )
    )

    assert event is not None
    decision = event["decision"]["recipient_decisions"]["aaron"]
    assert decision["reason_code"] == "important_only_occupied_house_person_detection"
    value.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_away_person_detection_notifies_owner_once_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "proactive.db"
    first = ProactiveEngine(str(path), cooldown=300)
    first.mobile_notify = AsyncMock()  # type: ignore[method-assign]
    no_quiet_hours(first)
    first.presence = {"aaron": "not_home", "amber": "not_home"}
    item = candidate(
        category="cameras",
        kind="person_detected",
        entity_id="binary_sensor.front_door_person",
        message="A person was detected at the front door while nobody is home.",
        importance=98,
    )

    created = await first.record(item)
    assert created is not None
    assert created["notified_at"] is not None
    first.mobile_notify.assert_awaited_once()  # type: ignore[attr-defined]
    assert first.mobile_notify.await_args.args[0] == "notify.mobile_app_aaron_s_phone"  # type: ignore[attr-defined]

    restarted = ProactiveEngine(str(path), cooldown=300)
    restarted.mobile_notify = AsyncMock()  # type: ignore[method-assign]
    restarted.presence = {"aaron": "not_home", "amber": "not_home"}
    assert await restarted.record(item) is None
    restarted.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]
    incident = restarted.incidents(1)[0]
    assert incident["status"] == "active"
    assert incident["occurrence_count"] == 2
    assert incident["notification_count"] == 1
    assert incident["last_decision"]["reason_code"] == "incident_already_active"


@pytest.mark.asyncio
async def test_incident_resolves_and_recurrence_inside_cooldown_does_not_push(
    tmp_path: Path,
) -> None:
    value = engine(tmp_path)
    no_quiet_hours(value)
    value.presence = {"aaron": "not_home", "amber": "not_home"}
    item = candidate(
        category="cameras",
        kind="person_detected",
        entity_id="binary_sensor.front_door_person",
        message="Person detected while away.",
        importance=98,
    )
    assert await value.record(item) is not None
    value._resolve_inactive_incidents(  # noqa: SLF001 - lifecycle regression
        {"entity_id": item.entity_id, "state": "off"},
        1_800_000_000,
    )
    # The current wall clock remains inside the persisted notification cooldown.
    assert await value.record(item) is None
    value.mobile_notify.assert_awaited_once()  # type: ignore[attr-defined]
    assert value.incidents(1)[0]["last_decision"]["reason_code"] == "incident_cooldown"


@pytest.mark.asyncio
async def test_failed_delivery_retries_boundedly_without_duplicate_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_800_000_000
    monkeypatch.setattr("app.proactive_intelligence.time.time", lambda: now)
    value = ProactiveEngine(str(tmp_path / "proactive.db"), cooldown=300)
    value.mobile_notify = AsyncMock(side_effect=RuntimeError("transport down"))  # type: ignore[method-assign]
    value.presence = {"aaron": "not_home", "amber": "not_home"}
    no_quiet_hours(value)
    item = candidate(
        category="cameras",
        kind="person_detected",
        entity_id="binary_sensor.front_door_person",
        message="Person detected while away.",
        importance=98,
    )

    first = await value.record(item)
    assert first is not None
    assert first["decision"]["notification_outcome"] == "delivery_failed"
    assert await value.record(item) is None

    for expected_attempts in (2, 3):
        now += 31
        retried = await value.record(item)
        assert retried is not None
        assert retried["decision"]["notification_outcome"] == "delivery_failed"
        assert value.incidents(1)[0]["delivery_attempts"] == expected_attempts

    now += 31
    assert await value.record(item) is None
    assert value.mobile_notify.await_count == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_critical_safety_notifies_both_and_bypasses_quiet_hours(
    tmp_path: Path,
) -> None:
    value = engine(tmp_path)
    value.quiet = lambda settings, now=None: True  # type: ignore[method-assign]
    event = await value.record(
        candidate(
            category="security",
            kind="smoke_detected",
            entity_id="binary_sensor.kitchen_smoke",
            message="Kitchen Smoke reports smoke.",
            importance=100,
        )
    )

    assert event is not None
    assert value.mobile_notify.await_count == 2  # type: ignore[attr-defined]
    assert {
        call.args[0]
        for call in value.mobile_notify.await_args_list  # type: ignore[attr-defined]
    } == {
        "notify.mobile_app_aaron_s_phone",
        "notify.mobile_app_amber_phone",
    }
    for item in event["decision"]["recipient_decisions"].values():
        assert item["critical"] is True
        assert item["quiet_hours_bypassed"] is True


@pytest.mark.asyncio
async def test_noncritical_important_event_is_activity_only_during_quiet_hours(
    tmp_path: Path,
) -> None:
    value = engine(tmp_path)
    value.presence = {"aaron": "not_home", "amber": "not_home"}
    value.quiet = lambda settings, now=None: True  # type: ignore[method-assign]
    event = await value.record(
        candidate(
            category="cameras",
            kind="person_detected",
            entity_id="binary_sensor.front_door_person",
            message="Person detected while away.",
            importance=98,
        )
    )

    assert event is not None
    assert event["decision"]["recipient_decisions"]["aaron"]["reason_code"] == "quiet_hours"
    value.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_all_useful_mode_allows_cycle_finished_but_important_only_does_not(
    tmp_path: Path,
) -> None:
    important = engine(tmp_path / "important")
    no_quiet_hours(important)
    item = candidate(
        category="appliances",
        kind="cycle_finished",
        entity_id="sensor.washing_machine",
        message="The washing machine has finished.",
        importance=82,
    )
    assert (await important.record(item))["status"] == "activity"
    important.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]

    useful = engine(tmp_path / "useful")
    useful.save_settings(
        SettingsModel(
            user_id="aaron",
            notification_mode="all_useful",
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )
    assert (await useful.record(item))["notified_at"] is not None
    useful.mobile_notify.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_critical_only_suppresses_away_camera_but_keeps_safety(tmp_path: Path) -> None:
    value = engine(tmp_path)
    value.save_settings(
        SettingsModel(
            user_id="aaron",
            notification_mode="critical_only",
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )
    value.presence = {"aaron": "not_home", "amber": "not_home"}
    camera = await value.record(
        candidate(
            category="cameras",
            kind="person_detected",
            entity_id="binary_sensor.front_door_person",
            message="Person detected while away.",
            importance=98,
        )
    )
    assert camera is not None
    assert camera["status"] == "activity"
    value.mobile_notify.assert_not_awaited()  # type: ignore[attr-defined]


def test_recipient_routing_does_not_notify_aaron_about_amber_arrival() -> None:
    assert notification_recipients({"kind": "arrival", "target_user": "all"}) == ()
    assert notification_recipients({"kind": "battery_low", "target_user": "amber"}) == ("amber",)


def test_rules_keep_routine_presence_and_washer_door_as_grounded_activity() -> None:
    rules = Rules(door_seconds=600)
    washer = rules.evaluate(
        {"entity_id": "binary_sensor.washing_machine_door_is_open", "state": "on"},
        {
            "entity_id": "binary_sensor.washing_machine_door_is_open",
            "state": "on",
            "attributes": {"friendly_name": "Washing Machine Door is open"},
        },
        first_seen=100,
        now=1000,
        presence={"aaron": "home", "amber": "home"},
    )
    assert washer[0].kind == "appliance_door_open"
    assert washer[0].category == "appliances"
    assert washer[0].importance == 35
    decision = decide_notification(
        {
            "category": washer[0].category,
            "kind": washer[0].kind,
            "entity_id": washer[0].entity_id,
            "title": washer[0].title,
            "message": washer[0].message,
            "reason": washer[0].reason,
            "importance": washer[0].importance,
        },
        settings={
            "notification_mode": "important_only",
            "enabled": True,
            "notify_enabled": True,
            "min_importance": 80,
            "categories": {"appliances": True},
        },
        presence={"aaron": "home", "amber": "home"},
        recipient="aaron",
        quiet_hours=False,
    )
    assert decision.notify is False
    assert decision.reason_code == "important_only_appliance_opening"


def test_existing_settings_migrate_to_important_only_without_losing_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE proactive_settings ("
            "user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,min_importance INTEGER NOT NULL,"
            "notify_enabled INTEGER NOT NULL,speak_enabled INTEGER NOT NULL,"
            "quiet_start_hour INTEGER NOT NULL,quiet_end_hour INTEGER NOT NULL,"
            "categories_json TEXT NOT NULL,updated_at INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO proactive_settings VALUES (?,?,?,?,?,?,?,?,?)",
            ("aaron", 1, 83, 1, 0, 23, 6, "{}", 1),
        )

    value = ProactiveEngine(str(path))
    settings = value.settings("aaron")
    assert settings["notification_mode"] == "important_only"
    assert settings["min_importance"] == 83
    assert settings["quiet_start_hour"] == 23


def test_saving_legacy_android_payload_preserves_selected_mode(tmp_path: Path) -> None:
    value = engine(tmp_path)
    value.save_settings(SettingsModel(user_id="aaron", notification_mode="all_useful"))
    value.save_settings(SettingsModel(user_id="aaron", min_importance=84))
    assert value.settings("aaron")["notification_mode"] == "all_useful"
