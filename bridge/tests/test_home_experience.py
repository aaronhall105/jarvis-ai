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
    assert "3 lights on" in home.overall_status["detail"]
    assert len(home.energy) == 1
    assert home.energy[0]["display_value"] == "742 W"
    assert len(home.appliances) == 1


def test_room_person_detection_is_occupied_but_inactive_camera_is_not_clear() -> None:
    occupied = project_home_experience(_snapshot(), principal_id="aaron")
    living = next(room for room in occupied.rooms if room.area_id == "living_room")
    assert living.occupancy_state is OccupancyState.OCCUPIED
    assert living.occupancy_summary == "Occupied"
    assert living.occupancy_detail.startswith("Person detected by")

    inactive = project_home_experience(_snapshot(person_detected="off"), principal_id="aaron")
    living = next(room for room in inactive.rooms if room.area_id == "living_room")
    assert living.occupancy_state is OccupancyState.UNKNOWN
    assert living.occupancy_summary == "Occupancy unknown"
    assert living.occupancy_detail == "Evidence is insufficient to claim the room is clear"


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
    assert all(
        room.occupancy_freshness == SourceFreshness.RECENT_CACHED.value
        and room.occupancy_source_health == "CACHED"
        for room in cached.rooms
    )
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
        assert "Aaron and Amber are home" in home.overall_status["detail"]


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
    assert "Front door open" in home.overall_status["detail"]


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

    assert rendered == home.overall_status["spoken_summary"]
    assert "3 lights on" in rendered
    assert "Hallway Camera unavailable" in rendered


def test_camera_variants_canonicalize_once_and_retain_every_source() -> None:
    variants = (
        "camera.living_room",
        "camera.living_room_clear",
        "camera.living_room_fluent",
        "camera.living_room_snapshots_clear",
        "camera.living_room_snapshots_fluent",
    )
    entities = tuple(
        _entity(
            entity_id=entity_id,
            domain="camera",
            name=entity_id.removeprefix("camera.").replace("_", " ").title(),
            area_id="living_room",
            area_name="Living Room",
            device_id="camera-device",
            device_name="Living Room",
            device_connections=(("mac", "aa:bb:cc:dd:ee:ff"),),
            state="streaming",
            available=True,
        )
        for entity_id in variants
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=entities, areas=(AREAS[0],)),
        principal_id="aaron",
    )

    assert len(home.cameras) == 1
    assert home.cameras[0]["name"] == "Living Room Camera"
    diagnostics = home.cameras[0]["diagnostics"]
    source_ids = {
        diagnostics["primary_entity_id"],
        *diagnostics["alternate_entity_ids"],
        *diagnostics["diagnostic_entity_ids"],
    }
    assert source_ids == set(variants)


def test_camera_activity_uses_registry_device_identity_before_room_fallback() -> None:
    entities = (
        _entity(
            entity_id="camera.living_room_alpha",
            domain="camera",
            name="Alpha Clear",
            area_id="living_room",
            area_name="Living Room",
            device_id="alpha-camera",
            device_name="Alpha",
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="camera.living_room_beta",
            domain="camera",
            name="Beta Clear",
            area_id="living_room",
            area_name="Living Room",
            device_id="beta-camera",
            device_name="Beta",
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="binary_sensor.living_room_beta_person",
            domain="binary_sensor",
            name="Beta Person",
            area_id="living_room",
            area_name="Living Room",
            device_id="beta-camera",
            device_class="occupancy",
            state="on",
            available=True,
        ),
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=entities, areas=(AREAS[0],)),
        principal_id="aaron",
    )

    cameras = {item["name"]: item for item in home.cameras}
    assert cameras["Alpha Camera"]["person_status"] == "UNKNOWN"
    assert cameras["Beta Camera"]["person_status"] == "DETECTED"


def test_light_canonicalization_hides_technical_surface_and_freezes_friendly_target() -> None:
    entities = (
        _entity(
            entity_id="light.living_room_ceiling",
            domain="light",
            name="Livingroom Lights",
            registry_name="Livingroom Lights",
            area_id="living_room",
            area_name="Living Room",
            device_id="friendly-light",
            state="on",
            available=True,
        ),
        _entity(
            entity_id="sensor.living_room_ceiling_power",
            domain="sensor",
            name="Livingroom Lights Power",
            area_id="living_room",
            area_name="Living Room",
            device_id="friendly-light",
            state="9.3",
            available=True,
            device_class="power",
            unit="W",
        ),
        _entity(
            entity_id="light.shellydimmerg4_e8f60a7ac9e8",
            domain="light",
            name="shellydimmerg4-e8f60a7ac9e8",
            device_name="shellydimmerg4-e8f60a7ac9e8",
            area_id="living_room",
            area_name="Living Room",
            device_id="unnamed-light",
            state="off",
            available=True,
        ),
    )
    home = project_home_experience(
        HomeSnapshot(
            observed_at=OBSERVED,
            entities=entities[:1] + entities[2:],
            presentation_entities=entities,
            areas=(AREAS[0],),
        ),
        principal_id="aaron",
    )

    assert home.lights["total_count"] == 1
    assert [item["name"] for item in home.lights["items"]] == ["Livingroom Lights"]
    action = next(item for item in home.quick_actions if item["action_id"].startswith("lights-off"))
    assert action["target_entity_ids"] == ["light.living_room_ceiling"]


def test_light_uses_grounded_capability_name_when_device_name_is_only_the_area() -> None:
    floodlight = _entity(
        entity_id="light.living_room_floodlight",
        domain="light",
        name="Living Room Floodlight",
        device_name_by_user="Living Room",
        area_id="living_room",
        area_name="Living Room",
        device_id="living-camera",
        state="off",
        available=True,
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=(floodlight,), areas=(AREAS[0],)),
        principal_id="aaron",
    )

    assert [item["name"] for item in home.lights["items"]] == ["Living Room Floodlight"]


def test_attention_excludes_diagnostics_and_unavailable_secondary_media() -> None:
    camera = _entity(
        entity_id="camera.bedroom_clear",
        domain="camera",
        name="Bedroom Clear",
        area_id="bedroom",
        area_name="Bedroom",
        device_id="bedroom-camera",
        device_name="Bedroom",
        state="unavailable",
        available=False,
    )
    diagnostics = tuple(
        _entity(
            entity_id=f"binary_sensor.bedroom_diagnostic_{index}",
            domain="binary_sensor",
            name=f"Bedroom diagnostic {index}",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="bedroom-camera",
            entity_category="diagnostic",
            state="unavailable",
            available=False,
        )
        for index in range(10)
    )
    secondary = tuple(
        _entity(
            entity_id=f"media_player.secondary_{index}",
            domain="media_player",
            name=f"Secondary control {index}",
            area_id="bedroom",
            area_name="Bedroom",
            device_id=f"secondary-{index}",
            state="unavailable",
            available=False,
        )
        for index in range(2)
    )
    entities = (camera, *diagnostics, *secondary)
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=entities, areas=(AREAS[1],)),
        principal_id="aaron",
    )

    assert home.diagnostics["raw_unavailable_device_count"] == 3
    assert home.devices["unavailable_count"] == 1
    assert home.overall_status["attention_count"] == 1
    assert home.overall_status["headline"] == "1 thing needs attention"


def test_camera_and_floodlight_on_one_device_do_not_duplicate_device_incident() -> None:
    camera = _entity(
        entity_id="camera.bedroom_clear",
        domain="camera",
        name="Bedroom Clear",
        area_id="bedroom",
        area_name="Bedroom",
        device_id="bedroom-device",
        device_name="Bedroom",
        device_connections=(("mac", "ec:71:db:ba:60:b2"),),
        state="unavailable",
        available=False,
    )
    floodlight = _entity(
        entity_id="light.bedroom_floodlight",
        domain="light",
        name="Bedroom Floodlight",
        area_id="bedroom",
        area_name="Bedroom",
        device_id="bedroom-device",
        device_name="Bedroom",
        device_connections=(("mac", "ec:71:db:ba:60:b2"),),
        state="unavailable",
        available=False,
    )
    incident = {
        "incident_id": "bedroom-outage",
        "target_user": "aaron",
        "kind": "device_unavailable",
        "status": "active",
        "device_id": "bedroom-device",
        "device_name": "Bedroom",
        "area_id": "bedroom",
        "room": "Bedroom",
        "last_decision": {"title": "Device Unavailable"},
    }
    home = project_home_experience(
        HomeSnapshot(
            observed_at=OBSERVED,
            entities=(camera, floodlight),
            areas=(AREAS[1],),
        ),
        principal_id="aaron",
        proactive_incidents=(incident,),
    )

    assert home.devices["unavailable_count"] == 1
    assert len(home.current_incidents) == 1
    assert home.current_incidents[0]["device_name"] == "Bedroom Camera"
    assert home.overall_status["attention_count"] == 1
    assert home.overall_status["detail"].count("Bedroom Camera unavailable") == 1


def test_occupancy_normal_view_is_concise_and_diagnostics_retain_raw_evidence() -> None:
    labels = ("person", "motion", "animal", "vehicle", "cell_phone", "laptop", "remote", "tv")
    entities = tuple(
        _entity(
            entity_id=f"binary_sensor.living_room_{label}",
            domain="binary_sensor",
            name=f"Living Room {label.replace('_', ' ').title()}",
            area_id="living_room",
            area_name="Living Room",
            device_id="camera-device",
            device_class="motion" if label == "motion" else "occupancy",
            state="off",
            available=True,
        )
        for label in labels
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=entities, areas=(AREAS[0],)),
        principal_id="aaron",
    )
    room = home.rooms[0]

    assert room.occupancy_state is OccupancyState.UNKNOWN
    assert room.occupancy_summary == "Occupancy unknown"
    assert len(room.occupancy_evidence) <= 3
    assert len({item["kind"] for item in room.occupancy_evidence}) == len(room.occupancy_evidence)
    assert {item["name"] for item in room.occupancy_evidence} <= {
        "Living Room Person",
        "Living Room Motion",
    }
    assert len(room.diagnostics["occupancy_evidence"]) == len(labels)


def test_shared_hardware_connection_groups_tv_control_surfaces_without_name_guessing() -> None:
    entities = (
        _entity(
            entity_id="media_player.samsung_tv",
            domain="media_player",
            name="TV",
            area_id="living_room",
            area_name="Living Room",
            device_id="samsung-device",
            device_name="TV",
            device_name_by_user="Samsung 7 Series TV",
            device_connections=(("mac", "a0:d0:5b:05:24:f7"),),
            state="on",
            available=True,
        ),
        _entity(
            entity_id="media_player.samsung_dlna",
            domain="media_player",
            name="TV [TV] Samsung 7 Series (55)",
            area_id="living_room",
            area_name="Living Room",
            device_id="dlna-device",
            device_name="TV",
            device_connections=(
                ("upnp", "uuid:example"),
                ("mac", "a0:d0:5b:05:24:f7"),
            ),
            state="idle",
            available=True,
        ),
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=entities, areas=(AREAS[0],)),
        principal_id="aaron",
    )

    assert len(home.rooms[0].media) == 1
    assert home.rooms[0].media[0]["name"] == "Samsung 7 Series TV"
    assert set(home.rooms[0].media[0]["physical_device_ids"]) == {
        "samsung-device",
        "dlna-device",
    }


def test_energy_is_curated_and_human_formatted() -> None:
    values = (
        ("current_demand", "Current Demand", "242", "power", "W"),
        (
            "current_accumulative_consumption",
            "Current Accumulative Consumption",
            "13.601",
            "energy",
            "kWh",
        ),
        ("current_accumulative_cost", "Current Accumulative Cost", "3.15", "monetary", "GBP"),
        ("current_rate", "Current Rate", "0.31348", None, "GBP/kWh"),
        ("next_rate", "Next Rate", "0.139743", None, "GBP/kWh"),
        ("appliance_energy", "Refrigerator Power energy", "0.00938205333471298", "energy", "kWh"),
    )
    entities = tuple(
        _entity(
            entity_id=f"sensor.{slug}",
            domain="sensor",
            name=name,
            state=state,
            available=True,
            device_class=device_class,
            unit=unit,
            area_id="kitchen" if slug == "appliance_energy" else None,
            area_name="Kitchen" if slug == "appliance_energy" else None,
        )
        for slug, name, state, device_class, unit in values
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=(), presentation_entities=entities),
        principal_id="aaron",
    )

    display = {item["role"]: item["display_value"] for item in home.energy}
    assert display["CURRENT_POWER"] == "242 W"
    assert display["CURRENT_CONSUMPTION"] == "13.601 kWh"
    assert display["CURRENT_COST"] == "£3.15"
    assert display["CURRENT_RATE"] == "31.35 p/kWh"
    assert display["NEXT_RATE"] == "13.97 p/kWh"
    assert display["APPLIANCE_ENERGY"] == "9.4 Wh"


def test_explicit_aggregate_area_is_not_a_room() -> None:
    entities = (
        _entity(
            entity_id="camera.aggregate",
            domain="camera",
            name="Aggregate Camera",
            area_id="whole_home",
            area_name="Whole Home",
            device_id="aggregate-camera",
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="light.bedroom",
            domain="light",
            name="Bedroom Light",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="bedroom-light",
            state="off",
            available=True,
        ),
    )
    home = project_home_experience(
        HomeSnapshot(
            observed_at=OBSERVED,
            entities=entities,
            areas=(
                {"area_id": "whole_home", "name": "Whole Home", "presentation_kind": "aggregate"},
                AREAS[1],
            ),
        ),
        principal_id="aaron",
    )

    assert [room.name for room in home.rooms] == ["Bedroom"]


def test_gateway_camera_sources_do_not_create_an_aggregate_room_or_duplicate_cameras() -> None:
    entities = (
        _entity(
            entity_id="camera.living_room_clear",
            domain="camera",
            name="Living Room Clear",
            area_id="living_room",
            area_name="Living Room",
            device_id="living-camera",
            device_name="Living Room",
            device_connections=(("mac", "aa:bb:cc:dd:ee:01"),),
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="camera.bedroom_clear",
            domain="camera",
            name="Bedroom Clear",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="bedroom-camera",
            device_name="Bedroom",
            device_connections=(("mac", "aa:bb:cc:dd:ee:02"),),
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="camera.gateway_living_room",
            domain="camera",
            name="Living Room",
            area_id="apartment",
            area_name="Apartment",
            device_id="gateway-living",
            device_name="Living Room",
            via_device_id="camera-gateway",
            state="streaming",
            available=True,
        ),
        _entity(
            entity_id="camera.gateway_bedroom",
            domain="camera",
            name="Bedroom",
            area_id="apartment",
            area_name="Apartment",
            device_id="gateway-bedroom",
            device_name="Bedroom",
            via_device_id="camera-gateway",
            state="streaming",
            available=True,
        ),
    )
    home = project_home_experience(
        HomeSnapshot(
            observed_at=OBSERVED,
            entities=entities,
            areas=(*AREAS, {"area_id": "apartment", "name": "Apartment"}),
        ),
        principal_id="aaron",
    )

    assert len(home.cameras) == 2
    assert {item["name"] for item in home.cameras} == {
        "Bedroom Camera",
        "Living Room Camera",
    }
    assert {room.name for room in home.rooms} == {"Bedroom", "Living Room"}
    assert all(item["diagnostics"]["diagnostic_entity_ids"] for item in home.cameras)


def test_repeated_surfaced_observations_collapse_to_one_named_activity() -> None:
    camera = _entity(
        entity_id="camera.bedroom_clear",
        domain="camera",
        name="Bedroom Clear",
        area_id="bedroom",
        area_name="Bedroom",
        device_id="bedroom-camera",
        device_name="Bedroom",
        state="unavailable",
        available=False,
    )
    events = tuple(
        {
            "id": f"event-{index}",
            "target_user": "aaron",
            "title": "Device Unavailable",
            "message": "A device is unavailable.",
            "kind": "device_unavailable",
            "status": "notified",
            "created_at": 1_760_000_000 + index,
            "updated_at": 1_760_000_000 + index,
            "notified_at": 1_760_000_000 + index,
            "entity_id": camera.entity_id,
            "decision": {"incident_id": "bedroom-camera-outage"},
        }
        for index in range(3)
    )
    home = project_home_experience(
        HomeSnapshot(observed_at=OBSERVED, entities=(camera,), areas=(AREAS[1],)),
        principal_id="aaron",
        proactive_events=events,
    )

    assert len(home.recent_events) == 1
    assert home.recent_events[0]["title"] == "Bedroom Camera unavailable"


def test_normal_presentation_never_contains_serialized_null_words() -> None:
    value = project_home_experience(_snapshot(), principal_id="aaron").as_dict()
    value.pop("diagnostics", None)
    for room in value["rooms"]:
        room.pop("diagnostics", None)

    def strings(item):
        if isinstance(item, dict):
            for child in item.values():
                yield from strings(child)
        elif isinstance(item, list):
            for child in item:
                yield from strings(child)
        elif isinstance(item, str):
            yield item

    assert not ({item.casefold() for item in strings(value)} & {"null", "none", "undefined"})


def test_android_and_ha_mappings_consume_shared_counts_without_rederiving_them() -> None:
    android = (
        ROOT
        / "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/HomeActivity.java"
    ).read_text()
    ha = (ROOT / "home_assistant/custom_components/jarvis_core_conversation/sensor.py").read_text()

    assert 'lights.optInt("on_count", 0)' in android
    assert 'devices.optInt("unavailable_count", 0)' in android
    assert "home.incidents()" in android
    assert 'safe(person, "presence", "UNKNOWN")' in android
    assert '(data.get("lights") or {}).get("on_count", 0)' in ha
    assert '(data.get("devices") or {}).get("unavailable_count", 0)' in ha
    assert '_array(data, "current_incidents")' in ha
    assert 'item.get("presence") == "HOME"' in ha
    assert "state_changed" not in ha
    assert "entity_registry" not in ha
