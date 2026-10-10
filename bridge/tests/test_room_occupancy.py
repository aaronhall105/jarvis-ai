from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.home_intelligence import GroundedHomeEntity, HomeSnapshot
from app.home_experience import project_home_experience
from app.response_presentation import render_home_query_evidence
from app.room_occupancy import (
    RoomOccupancyEngine,
    RoomOccupancyPolicy,
    RoomOccupancyValue,
)
from app.tool_engine import ToolEngine


BASE = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
AREA = ({"area_id": "living_room", "name": "Living Room"},)
POLICY = RoomOccupancyPolicy(
    strong_persistence_seconds=300,
    motion_persistence_seconds=120,
    clear_confirmation_seconds=300,
    disconnected_stale_seconds=120,
)


def _entity(
    name: str,
    *,
    state: str,
    device_class: str = "occupancy",
    available: bool = True,
    changed: datetime = BASE,
    domain: str = "binary_sensor",
) -> GroundedHomeEntity:
    slug = name.casefold().replace(" ", "_")
    return GroundedHomeEntity(
        entity_id=f"{domain}.{slug}",
        name=name,
        domain=domain,
        state=state,
        available=available,
        observed_at=changed.isoformat(),
        area_id="living_room",
        area_name="Living Room",
        device_id="living-room-sensor",
        device_class=device_class,
        source_last_changed=changed.isoformat(),
        source_last_updated=changed.isoformat(),
    )


def _snapshot(at: datetime, *entities: GroundedHomeEntity) -> HomeSnapshot:
    return HomeSnapshot(observed_at=at.isoformat(), entities=tuple(entities), areas=AREA)


def test_person_enters_then_camera_loss_is_likely_not_clear() -> None:
    engine = RoomOccupancyEngine(policy=POLICY, clock=lambda: BASE)
    person = _entity("Living Room Person", state="off")
    assert (
        engine.reconcile(_snapshot(BASE, person))["living_room"].state is RoomOccupancyValue.UNKNOWN
    )

    entered = replace(
        person,
        state="on",
        source_last_changed=(BASE + timedelta(seconds=1)).isoformat(),
    )
    occupied = engine.reconcile(_snapshot(BASE + timedelta(seconds=1), entered))["living_room"]
    assert occupied.state is RoomOccupancyValue.OCCUPIED
    assert occupied.last_strong_evidence_at == (BASE + timedelta(seconds=1)).isoformat()

    lost = replace(
        entered,
        state="off",
        source_last_changed=(BASE + timedelta(seconds=4)).isoformat(),
    )
    likely = engine.reconcile(_snapshot(BASE + timedelta(seconds=4), lost))["living_room"]
    assert likely.state is RoomOccupancyValue.LIKELY_OCCUPIED
    assert likely.clear_candidate_since == (BASE + timedelta(seconds=4)).isoformat()


def test_person_returns_without_bogus_clear_transition() -> None:
    engine = RoomOccupancyEngine(policy=POLICY)
    on = _entity("Living Room Person", state="on")
    engine.reconcile(_snapshot(BASE, on))
    off = replace(on, state="off", source_last_changed=(BASE + timedelta(seconds=2)).isoformat())
    engine.reconcile(_snapshot(BASE + timedelta(seconds=2), off))
    returned = replace(on, source_last_changed=(BASE + timedelta(seconds=5)).isoformat())
    result = engine.reconcile(_snapshot(BASE + timedelta(seconds=5), returned))["living_room"]
    assert result.state is RoomOccupancyValue.OCCUPIED
    assert [item["new_state"] for item in engine.transitions("living_room")] == [
        "OCCUPIED",
        "LIKELY_OCCUPIED",
        "OCCUPIED",
    ][::-1]
    assert "PROBABLY_CLEAR" not in {item["new_state"] for item in engine.transitions("living_room")}


def test_clear_requires_conservative_interval() -> None:
    now = [BASE]
    engine = RoomOccupancyEngine(policy=POLICY, clock=lambda: now[0])
    person = _entity("Living Room Person", state="on")
    engine.reconcile(_snapshot(now[0], person))
    now[0] += timedelta(seconds=1)
    off = replace(person, state="off", source_last_changed=now[0].isoformat())
    engine.reconcile(_snapshot(now[0], off))

    now[0] += timedelta(seconds=299)
    assert engine.evaluate()["living_room"].state is RoomOccupancyValue.LIKELY_OCCUPIED
    now[0] += timedelta(seconds=1)
    assert engine.evaluate()["living_room"].state is RoomOccupancyValue.PROBABLY_CLEAR


def test_motion_extends_recent_person_but_not_unknown_baseline() -> None:
    engine = RoomOccupancyEngine(policy=POLICY)
    motion = _entity("Living Room Motion", state="on", device_class="motion")
    initial = engine.reconcile(_snapshot(BASE, motion))["living_room"]
    assert initial.state is RoomOccupancyValue.UNKNOWN

    person = _entity("Living Room Person", state="on")
    engine.reconcile(_snapshot(BASE + timedelta(seconds=1), person, motion))
    off = replace(
        person,
        state="off",
        source_last_changed=(BASE + timedelta(seconds=2)).isoformat(),
    )
    supported = engine.reconcile(_snapshot(BASE + timedelta(seconds=2), off, motion))["living_room"]
    assert supported.state is RoomOccupancyValue.LIKELY_OCCUPIED
    assert supported.reason_code == "RECENT_STRONG_WITH_SUPPORTING_EVIDENCE"
    assert supported.clear_candidate_since is None


def test_clear_interval_starts_after_supporting_motion_stops() -> None:
    now = [BASE]
    engine = RoomOccupancyEngine(policy=POLICY, clock=lambda: now[0])
    person = _entity("Living Room Person", state="on")
    motion = _entity("Living Room Motion", state="on", device_class="motion")
    engine.reconcile(_snapshot(now[0], person, motion))
    now[0] += timedelta(seconds=1)
    person_off = replace(person, state="off", source_last_changed=now[0].isoformat())
    engine.reconcile(_snapshot(now[0], person_off, motion))

    now[0] += timedelta(minutes=10)
    motion_off = replace(motion, state="off", source_last_changed=now[0].isoformat())
    state = engine.reconcile(_snapshot(now[0], person_off, motion_off))["living_room"]
    assert state.state is RoomOccupancyValue.LIKELY_OCCUPIED
    assert state.clear_candidate_since == now[0].isoformat()

    now[0] += timedelta(seconds=POLICY.clear_confirmation_seconds)
    assert engine.evaluate()["living_room"].state is RoomOccupancyValue.PROBABLY_CLEAR


def test_unavailable_sources_are_unknown_not_clear() -> None:
    engine = RoomOccupancyEngine(policy=POLICY)
    unavailable = _entity("Living Room Person", state="unavailable", available=False)
    state = engine.reconcile(_snapshot(BASE, unavailable))["living_room"]
    assert state.state is RoomOccupancyValue.UNKNOWN
    assert state.source_health == "UNAVAILABLE"


def test_disconnected_source_cannot_leave_room_occupied_indefinitely() -> None:
    now = [BASE]
    engine = RoomOccupancyEngine(policy=POLICY, clock=lambda: now[0])
    person = _entity("Living Room Person", state="on")
    assert (
        engine.reconcile(_snapshot(now[0], person))["living_room"].state
        is RoomOccupancyValue.OCCUPIED
    )
    engine.set_source_connected(False)
    now[0] += timedelta(seconds=POLICY.disconnected_stale_seconds + 1)

    state = engine.evaluate()["living_room"]
    assert state.state is RoomOccupancyValue.UNKNOWN
    assert state.freshness.value == "UNAVAILABLE"


def test_animal_vehicle_and_objects_do_not_create_human_occupancy() -> None:
    entities = tuple(
        _entity(f"Living Room {name}", state="on")
        for name in ("Animal", "Vehicle", "Cell Phone", "Laptop", "Remote", "TV")
    )
    engine = RoomOccupancyEngine(policy=POLICY)
    state = engine.reconcile(_snapshot(BASE, *entities))["living_room"]
    assert state.state is RoomOccupancyValue.UNKNOWN
    assert all(item.strength.value == "WEAK" for item in state.evidence)


def test_future_mmwave_is_strong_extensible_evidence() -> None:
    camera = _entity("Living Room Person", state="off")
    motion = _entity("Living Room Motion", state="off", device_class="motion")
    mmwave = _entity("Living Room mmWave Occupancy", state="on", device_class="occupancy")
    engine = RoomOccupancyEngine(policy=POLICY)
    state = engine.reconcile(_snapshot(BASE, camera, motion, mmwave))["living_room"]
    assert state.state is RoomOccupancyValue.OCCUPIED
    assert any(item.entity_id == mmwave.entity_id for item in state.evidence)


def test_restart_reconciles_recent_state_and_rejects_stale_occupied(tmp_path: Path) -> None:
    database = tmp_path / "occupancy.db"
    first = RoomOccupancyEngine(database_path=database, policy=POLICY)
    person = _entity("Living Room Person", state="on")
    first.reconcile(_snapshot(BASE, person))
    off_time = BASE + timedelta(seconds=10)
    off = replace(person, state="off", source_last_changed=off_time.isoformat())
    assert (
        first.reconcile(_snapshot(off_time, off))["living_room"].state
        is RoomOccupancyValue.LIKELY_OCCUPIED
    )

    restarted = RoomOccupancyEngine(database_path=database, policy=POLICY)
    reconciled = restarted.reconcile(_snapshot(off_time + timedelta(seconds=10), off))[
        "living_room"
    ]
    assert reconciled.state is RoomOccupancyValue.LIKELY_OCCUPIED

    hours_later = off_time + timedelta(hours=3)
    unavailable = replace(
        person,
        state="unavailable",
        available=False,
        source_last_changed=hours_later.isoformat(),
    )
    stale = restarted.reconcile(_snapshot(hours_later, unavailable))["living_room"]
    assert stale.state is RoomOccupancyValue.UNKNOWN


def test_incremental_update_recomputes_only_affected_room() -> None:
    engine = RoomOccupancyEngine(policy=POLICY)
    person = _entity("Living Room Person", state="off")
    engine.reconcile(_snapshot(BASE, person))
    changed = replace(
        person,
        state="on",
        source_last_changed=(BASE + timedelta(seconds=1)).isoformat(),
    )
    engine.observe(changed, observed_at=BASE + timedelta(seconds=1))
    assert engine.state("living_room").state is RoomOccupancyValue.OCCUPIED


async def test_occupancy_query_preserves_likely_uncertainty_and_context() -> None:
    person = _entity("Living Room Person", state="on")
    engine = RoomOccupancyEngine(policy=POLICY)
    engine.reconcile(_snapshot(BASE, person))
    off = replace(
        person,
        state="off",
        source_last_changed=(BASE + timedelta(seconds=1)).isoformat(),
    )
    snapshot = _snapshot(BASE + timedelta(seconds=1), off)

    class SnapshotLoader:
        async def snapshot(self) -> HomeSnapshot:
            return snapshot

    tool = object.__new__(ToolEngine)
    tool.home_intelligence = SnapshotLoader()
    tool._home_experience_projector = lambda value, principal: project_home_experience(
        value,
        principal_id=principal,
        occupancy_states=engine.reconcile(value),
    )
    result = await tool.query_home(
        {
            "operation": "QUERY",
            "scope": "HOME",
            "category": "rooms",
            "predicate": "OCCUPIED",
            "aggregation": "LIST",
        },
        principal_id="aaron",
    )

    assert [room["occupancy_state"] for room in result["rooms"]] == ["LIKELY_OCCUPIED"]
    assert result["context_projection"]["objects"][0]["object_type"] == "room"
    rendered = render_home_query_evidence(
        [{"tool": "query_home", "result": result}],
        request_text="Which rooms are occupied?",
    )
    assert rendered is not None
    assert "Living Room is likely occupied" in rendered
    assert "Person last detected" in rendered


async def test_clear_query_never_includes_unknown() -> None:
    person = _entity("Living Room Person", state="unavailable", available=False)
    snapshot = _snapshot(BASE, person)
    engine = RoomOccupancyEngine(policy=POLICY)

    class SnapshotLoader:
        async def snapshot(self) -> HomeSnapshot:
            return snapshot

    tool = object.__new__(ToolEngine)
    tool.home_intelligence = SnapshotLoader()
    tool._home_experience_projector = lambda value, principal: project_home_experience(
        value,
        principal_id=principal,
        occupancy_states=engine.reconcile(value),
    )
    result = await tool.query_home(
        {
            "operation": "QUERY",
            "scope": "HOME",
            "category": "rooms",
            "predicate": "CLEAR",
            "aggregation": "LIST",
        },
        principal_id="aaron",
    )

    assert result["rooms"] == []
    rendered = render_home_query_evidence(
        [{"tool": "query_home", "result": result}],
        request_text="Which rooms are empty?",
    )
    assert rendered == "No rooms have enough evidence to be called probably clear."
