from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from app.home_experience import (
    HomeExperienceService,
    OccupancyState,
    SourceFreshness,
    project_home_experience,
)
from app.home_intelligence import GroundedHomeEntity, HomeSnapshot
from app.response_presentation import render_home_query_evidence


OBSERVED = "2026-10-08T12:00:00+00:00"
ROOT = Path(__file__).resolve().parents[2]
AREAS = (
    {"area_id": "living_room", "name": "Living Room"},
    {"area_id": "bedroom", "name": "Bedroom"},
)


def _entity(**values) -> GroundedHomeEntity:
    return GroundedHomeEntity.from_state(values, OBSERVED)


def _snapshot(*, person_detected: str = "on") -> HomeSnapshot:
    entities = (
        _entity(
            entity_id="person.aaron",
            domain="person",
            name="Aaron",
            state="home",
            available=True,
        ),
        _entity(
            entity_id="person.amber",
            domain="person",
            name="Amber",
            state="not_home",
            available=True,
        ),
        *(
            _entity(
                entity_id=f"light.light_{index}",
                domain="light",
                name=f"Living Light {index}",
                area_id="living_room",
                area_name="Living Room",
                device_id=f"light-device-{index}",
                state="on",
                available=True,
            )
            for index in range(1, 4)
        ),
        _entity(
            entity_id="camera.living_room",
            domain="camera",
            name="Living Room Camera",
            area_id="living_room",
            area_name="Living Room",
            device_id="camera-device-1",
            device_name="Living Room Camera",
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="binary_sensor.living_room_person",
            domain="binary_sensor",
            name="Living Room Person",
            area_id="living_room",
            area_name="Living Room",
            device_id="camera-device-1",
            device_name="Living Room Camera",
            state=person_detected,
            available=True,
        ),
        _entity(
            entity_id="camera.hallway",
            domain="camera",
            name="Hallway Camera Stream",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="camera-device-2",
            device_name="Hallway Camera",
            state="unavailable",
            available=False,
        ),
        *(
            _entity(
                entity_id=f"binary_sensor.hallway_diagnostic_{index}",
                domain="binary_sensor",
                name=f"Hallway Diagnostic {index}",
                area_id="bedroom",
                area_name="Bedroom",
                device_id="camera-device-2",
                device_name="Hallway Camera",
                entity_category="diagnostic",
                state="unavailable",
                available=False,
            )
            for index in range(1, 8)
        ),
    )
    presentation = (
        *entities,
        _entity(
            entity_id="sensor.house_power",
            domain="sensor",
            name="House Power",
            state="742",
            available=True,
            device_class="power",
            unit="W",
            display_value="742 W",
        ),
        _entity(
            entity_id="sensor.washing_machine_state",
            domain="sensor",
            name="Washing Machine State",
            area_id="living_room",
            area_name="Living Room",
            state="running",
            available=True,
        ),
    )
    return HomeSnapshot(
        observed_at=OBSERVED,
        entities=entities,
        presentation_entities=presentation,
        areas=AREAS,
    )


def _events() -> Sequence[dict[str, object]]:
    return (
        {
            "id": "event-1",
            "target_user": "aaron",
            "title": "Washing machine finished",
            "message": "The washing machine has finished.",
            "kind": "cycle_finished",
            "category": "appliances",
            "status": "notified",
            "created_at": 1_760_000_000,
            "updated_at": 1_760_000_000,
            "notified_at": 1_760_000_000,
            "spoken_at": None,
            "reason": "Validated running-to-finished transition.",
            "evidence": ["sensor.washing_machine_state changed from running to finished"],
            "decision": {"incident_id": "incident-appliance"},
            "room": "Living Room",
            "entity_id": "sensor.washing_machine_state",
            "actions": ["dismiss"],
        },
        {
            "id": "suppressed",
            "target_user": "aaron",
            "title": "Suppressed chatter",
            "notified_at": None,
            "spoken_at": None,
        },
        {
            "id": "amber-only",
            "target_user": "amber",
            "title": "Amber only",
            "notified_at": 1_760_000_000,
        },
    )


def _incidents() -> Sequence[dict[str, object]]:
    return (
        {
            "incident_id": "camera-outage",
            "target_user": "aaron",
            "kind": "device_unavailable",
            "status": "active",
            "device_id": "camera-device-2",
            "device_name": "Hallway Camera",
            "area_id": "bedroom",
            "room": "Bedroom",
            "last_seen": 1_760_000_000,
            "occurrence_count": 1,
            "last_decision": {
                "title": "Device unavailable",
                "message": "Hallway Camera is unavailable.",
            },
        },
    )


def test_projection_agrees_on_people_lights_devices_and_events() -> None:
    home = project_home_experience(
        _snapshot(),
        principal_id="aaron",
        proactive_events=_events(),
        proactive_incidents=_incidents(),
    )

    assert {item["name"]: item["presence"] for item in home.people} == {
        "Aaron": "HOME",
        "Amber": "AWAY",
    }
    assert home.lights["on_count"] == 3
    assert home.devices["unavailable_count"] == 1
    assert home.devices["unavailable"][0]["name"] == "Hallway Camera"
    assert home.devices["unavailable"][0]["unavailable_entity_count"] == 8
    assert len(home.current_incidents) == 1
    assert len(home.recent_events) == 1
    assert "1 thing needs attention" in home.overall_status["headline"]
    assert "3 lights are on" in home.overall_status["headline"]
    assert len(home.energy) == 1
    assert home.energy[0]["display_value"] == "742 W"
    assert len(home.appliances) == 1


def test_room_person_detection_is_occupied_but_inactive_camera_is_not_clear() -> None:
    occupied = project_home_experience(_snapshot(), principal_id="aaron")
    living = next(room for room in occupied.rooms if room.area_id == "living_room")
    assert living.occupancy_state is OccupancyState.OCCUPIED
    assert living.occupancy_summary == "Person detected"

    inactive = project_home_experience(_snapshot(person_detected="off"), principal_id="aaron")
    living = next(room for room in inactive.rooms if room.area_id == "living_room")
    assert living.occupancy_state is OccupancyState.UNKNOWN
    assert living.occupancy_summary == "No current occupancy evidence"


def test_action_targets_are_exact_revisioned_and_principal_scoped() -> None:
    snapshot = _snapshot()

    async def load() -> HomeSnapshot:
        return snapshot

    service = HomeExperienceService(
        snapshot_loader=load,
        event_loader=lambda _principal: (),
        incident_loader=lambda: (),
    )
    home = service.project(snapshot, "aaron")
    action = next(item for item in home.quick_actions if item["kind"] == "TURN_OFF_EXACT_LIGHT_SET")
    frozen = service.resolve_action("aaron", str(action["action_id"]))

    assert frozen.revision == home.revision
    assert frozen.target_entity_ids == (
        "light.light_1",
        "light.light_2",
        "light.light_3",
    )
    with pytest.raises(ValueError, match="no longer available"):
        service.resolve_action("amber", str(action["action_id"]))


@pytest.mark.asyncio
async def test_cached_projection_is_marked_degraded_and_actions_are_disabled() -> None:
    calls = 0

    async def load() -> HomeSnapshot:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("Core cannot reach Home Assistant")
        return _snapshot()

    service = HomeExperienceService(
        snapshot_loader=load,
        event_loader=lambda _principal: (),
        incident_loader=lambda: (),
    )
    live = await service.get("aaron")
    cached = await service.get("aaron")

    assert live.source_freshness["status"] == SourceFreshness.LIVE.value
    assert cached.source_freshness["status"] == SourceFreshness.RECENT_CACHED.value
    assert cached.source_freshness["actions_allowed"] is False
    assert all(item["enabled"] is False for item in cached.quick_actions)
    with pytest.raises(ValueError, match="no longer available"):
        service.resolve_action("aaron", str(live.quick_actions[0]["action_id"]))


def test_proactive_store_failure_does_not_hide_current_grounded_home_state() -> None:
    async def load() -> HomeSnapshot:
        return _snapshot()

    def unavailable_events(_principal: str):
        raise OSError("event store unavailable")

    service = HomeExperienceService(
        snapshot_loader=load,
        event_loader=unavailable_events,
        incident_loader=lambda: (),
    )

    home = service.project(_snapshot(), "aaron")

    assert home.lights["on_count"] == 3
    assert home.recent_events == ()
    assert home.diagnostics["proactive_evidence_status"] == "UNAVAILABLE"


def test_energy_unavailable_does_not_create_empty_or_judgmental_energy_copy() -> None:
    snapshot = _snapshot()
    without_energy = HomeSnapshot(
        observed_at=snapshot.observed_at,
        entities=snapshot.entities,
        presentation_entities=tuple(
            item
            for item in snapshot.presentation_entities
            if item.entity_id != "sensor.house_power"
        ),
        areas=snapshot.areas,
    )

    home = project_home_experience(without_energy, principal_id="aaron")

    assert home.energy == ()
    assert "high" not in home.overall_status["headline"].casefold()


@pytest.mark.parametrize(
    ("aaron_state", "amber_state", "expected_home"),
    [
        ("not_home", "not_home", set()),
        ("home", "home", {"Aaron", "Amber"}),
        ("unavailable", "unknown", set()),
    ],
)
def test_presence_matrix_never_guesses(
    aaron_state: str,
    amber_state: str,
    expected_home: set[str],
) -> None:
    snapshot = _snapshot()
    states = {"person.aaron": aaron_state, "person.amber": amber_state}
    values = tuple(
        replace(
            item,
            state=states[item.entity_id],
            available=states[item.entity_id] != "unavailable",
        )
        if item.entity_id in states
        else item
        for item in snapshot.presentation_entities
    )
    home = project_home_experience(
        replace(snapshot, entities=values[: len(snapshot.entities)], presentation_entities=values),
        principal_id="aaron",
    )

    assert {item["name"] for item in home.people if item["presence"] == "HOME"} == expected_home
    assert all(item["presence"] in {"HOME", "AWAY", "UNKNOWN"} for item in home.people)
    if expected_home == {"Aaron", "Amber"}:
        assert "Aaron and Amber are home" in home.overall_status["headline"]


def test_attention_headline_mentions_incident_not_already_counted_as_device() -> None:
    incidents = (
        *_incidents(),
        {
            "incident_id": "incident-door",
            "kind": "door_open",
            "status": "active",
            "target_user": "aaron",
            "entity_id": "binary_sensor.front_door",
            "last_seen": 1_760_000_100,
            "last_decision": {"title": "Front door open"},
        },
    )

    home = project_home_experience(
        _snapshot(),
        principal_id="aaron",
        proactive_events=_events(),
        proactive_incidents=incidents,
    )

    assert home.overall_status["attention_count"] == 2
    assert "1 other incident is active" in home.overall_status["headline"]


def test_child_entity_failure_is_partial_not_an_unavailable_physical_device() -> None:
    snapshot = _snapshot()
    restored_primary = tuple(
        replace(item, state="streaming", available=True)
        if item.entity_id == "camera.hallway"
        else item
        for item in snapshot.entities
    )
    presentation = tuple(
        replace(item, state="streaming", available=True)
        if item.entity_id == "camera.hallway"
        else item
        for item in snapshot.presentation_entities
    )

    home = project_home_experience(
        replace(snapshot, entities=restored_primary, presentation_entities=presentation),
        principal_id="aaron",
    )

    assert home.devices["unavailable_count"] == 0
    assert home.devices["partial_count"] == 1
    assert home.devices["partial"][0]["name"] == "Hallway Camera"


def test_recovered_incident_is_not_current_but_recovery_event_is_recent() -> None:
    recovered = {
        **_events()[0],
        "id": "event-recovered",
        "kind": "device_recovered",
        "title": "Hallway Camera restored",
        "message": "Hallway Camera is available again.",
        "decision": {"incident_id": "camera-outage"},
    }
    resolved_incident = {
        **_incidents()[0],
        "status": "resolved",
        "resolved_at": 1_760_000_100,
    }

    home = project_home_experience(
        _snapshot(),
        principal_id="aaron",
        proactive_events=(recovered,),
        proactive_incidents=(resolved_incident,),
    )

    assert home.current_incidents == ()
    assert len(home.recent_events) == 1
    assert home.recent_events[0]["recovery"] is True


def test_semantic_revision_and_counts_refresh_after_state_change() -> None:
    before_snapshot = _snapshot()
    before = project_home_experience(before_snapshot, principal_id="aaron")
    updated_entities = tuple(
        replace(item, state="off") if item.entity_id == "light.light_3" else item
        for item in before_snapshot.entities
    )
    updated_presentation = tuple(
        replace(item, state="off") if item.entity_id == "light.light_3" else item
        for item in before_snapshot.presentation_entities
    )
    after = project_home_experience(
        replace(
            before_snapshot,
            entities=updated_entities,
            presentation_entities=updated_presentation,
        ),
        principal_id="aaron",
    )

    assert before.lights["on_count"] == 3
    assert after.lights["on_count"] == 2
    assert after.revision != before.revision
    assert len(after.quick_actions[0]["target_entity_ids"]) == 2


def test_semantic_revision_ignores_refresh_time_when_ha_state_is_unchanged() -> None:
    snapshot = _snapshot()
    sourced_entities = tuple(
        replace(item, source_last_updated=OBSERVED) for item in snapshot.entities
    )
    sourced_presentation = tuple(
        replace(item, source_last_updated=OBSERVED) for item in snapshot.presentation_entities
    )
    before = project_home_experience(
        replace(
            snapshot,
            entities=sourced_entities,
            presentation_entities=sourced_presentation,
        ),
        principal_id="aaron",
    )
    refreshed_at = "2026-10-08T12:00:01+00:00"
    after = project_home_experience(
        replace(
            snapshot,
            observed_at=refreshed_at,
            entities=tuple(replace(item, observed_at=refreshed_at) for item in sourced_entities),
            presentation_entities=tuple(
                replace(item, observed_at=refreshed_at) for item in sourced_presentation
            ),
        ),
        principal_id="aaron",
    )

    assert before.revision == after.revision
    assert before.lights == after.lights
    assert before.devices == after.devices


def test_conversation_house_status_uses_shared_home_experience_summary() -> None:
    home = project_home_experience(
        _snapshot(),
        principal_id="aaron",
        proactive_events=_events(),
        proactive_incidents=_incidents(),
    )

    rendered = render_home_query_evidence(
        [
            {
                "tool": "query_home",
                "result": {
                    "success": True,
                    "query_plan": {"operation": "SNAPSHOT"},
                    "home_experience": home.as_dict(),
                },
            }
        ],
        request_text="Give me a house status.",
    )

    assert rendered == home.overall_status["headline"]
    assert "3 lights are on" in rendered
    assert "Hallway Camera is unavailable" in rendered


def test_android_and_ha_mappings_consume_shared_counts_without_rederiving_them() -> None:
    android = (
        ROOT
        / "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/HomeActivity.java"
    ).read_text()
    ha = (ROOT / "home_assistant/custom_components/jarvis_core_conversation/sensor.py").read_text()

    assert 'lights.optInt("on_count", 0)' in android
    assert 'devices.optInt("unavailable_count", 0)' in android
    assert "home.incidents()" in android
    assert 'person.optString("presence", "UNKNOWN")' in android
    assert '(data.get("lights") or {}).get("on_count", 0)' in ha
    assert '(data.get("devices") or {}).get("unavailable_count", 0)' in ha
    assert '_array(data, "current_incidents")' in ha
    assert 'item.get("presence") == "HOME"' in ha
    assert "state_changed" not in ha
    assert "entity_registry" not in ha
