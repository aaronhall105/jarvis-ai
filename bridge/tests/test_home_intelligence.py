from __future__ import annotations

import pytest

from app.ai_engine import AIEngine
from app.dialogue_manager import DialogueManager
from app.home_intelligence import (
    DeviceAvailability,
    GroundedHomeEntity,
    HomeIntelligenceEngine,
    HomeQueryPlan,
    roll_up_physical_devices,
)
from app.response_presentation import render_home_query_evidence
from app.working_context import (
    ReferenceStatus,
    WorkingContextService,
    reference_query,
    tool_call_projection,
)


AREAS = (
    {"area_id": "living_room", "name": "Living Room"},
    {"area_id": "bedroom", "name": "Bedroom"},
    {"area_id": "hallway", "name": "Hallway"},
)

STATES = (
    {
        "entity_id": "light.living_room",
        "domain": "light",
        "name": "Living Room",
        "area_id": "living_room",
        "area_name": "Living Room",
        "state": "on",
        "available": True,
    },
    {
        "entity_id": "light.bedroom",
        "domain": "light",
        "name": "Bedroom",
        "area_id": "bedroom",
        "area_name": "Bedroom",
        "state": "off",
        "available": True,
    },
    {
        "entity_id": "light.hallway",
        "domain": "light",
        "name": "Hallway",
        "area_id": "hallway",
        "area_name": "Hallway",
        "state": "on",
        "available": True,
    },
    {
        "entity_id": "camera.hallway",
        "domain": "camera",
        "name": "Hallway Camera",
        "area_id": "hallway",
        "area_name": "Hallway",
        "state": "unavailable",
        "available": False,
    },
    {
        "entity_id": "camera.bedroom",
        "domain": "camera",
        "name": "Bedroom Camera",
        "area_id": "bedroom",
        "area_name": "Bedroom",
        "state": "off",
        "available": True,
    },
    {
        "entity_id": "person.aaron",
        "domain": "person",
        "name": "Aaron",
        "state": "home",
        "available": True,
    },
    {
        "entity_id": "person.amber",
        "domain": "person",
        "name": "Amber",
        "state": "not_home",
        "available": True,
    },
    {
        "entity_id": "media_player.tv",
        "domain": "media_player",
        "name": "Living Room TV",
        "area_id": "living_room",
        "area_name": "Living Room",
        "state": "playing",
        "available": True,
    },
)


def _engine(states=STATES) -> HomeIntelligenceEngine:
    async def areas():
        return AREAS

    async def live_states():
        return states

    return HomeIntelligenceEngine(area_loader=areas, state_loader=live_states)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wording",
    (
        "Which lights are on?",
        "What lights are currently on?",
        "Are any lights still on?",
        "Tell me what lights I've left on.",
    ),
)
async def test_whole_home_light_variants_share_one_complete_semantic_plan(wording: str) -> None:
    del wording  # Natural language is interpreted by the model into this same plan.
    plan = HomeQueryPlan.from_mapping(
        {
            "operation": "QUERY",
            "scope": "HOME",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
            "area_id": None,
        }
    )
    result = (await _engine().query(plan)).as_result()

    assert [item["entity_id"] for item in result["entities"]] == [
        "light.hallway",
        "light.living_room",
    ]
    assert result["complete"] is True
    assert result["context_projection"]["result_set"]["filters"] == plan.as_dict()


@pytest.mark.asyncio
async def test_area_scope_changes_without_changing_light_predicate() -> None:
    living = HomeQueryPlan.from_mapping(
        {
            "scope": "AREA",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
            "area_id": "living_room",
        }
    )
    bedroom = HomeQueryPlan.from_mapping({**living.as_dict(), "area_id": "bedroom"})
    assert [item.entity_id for item in (await _engine().query(living)).entities] == [
        "light.living_room"
    ]
    assert (await _engine().query(bedroom)).entities == ()


@pytest.mark.asyncio
async def test_zero_one_and_many_cardinality_are_preserved() -> None:
    one = HomeQueryPlan.from_mapping(
        {
            "scope": "AREA",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
            "area_id": "hallway",
        }
    )
    assert [item.entity_id for item in (await _engine().query(one)).entities] == ["light.hallway"]

    none = HomeQueryPlan.from_mapping(
        {
            "scope": "AREA",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
            "area_id": "bedroom",
        }
    )
    assert (await _engine().query(none)).entities == ()

    many = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
        }
    )
    assert len((await _engine().query(many)).entities) == 2


@pytest.mark.asyncio
async def test_referenced_set_is_exact_and_fails_if_member_disappears() -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "REFERENCED_ENTITY_SET",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
            "entity_ids": ["light.living_room", "light.bedroom"],
            "reference_result_set_id": "home:set-1",
        }
    )
    result = await _engine().query(plan)
    assert [item.entity_id for item in result.entities] == ["light.living_room"]

    stale_states = tuple(item for item in STATES if item["entity_id"] != "light.bedroom")
    with pytest.raises(ValueError, match="no longer exists"):
        await _engine(stale_states).query(plan)

    explicit = HomeQueryPlan.from_mapping(
        {
            "scope": "EXPLICIT_ENTITY_SET",
            "category": "lights",
            "predicate": "ANY",
            "aggregation": "LIST",
            "entity_ids": ["light.hallway"],
        }
    )
    assert [item.entity_id for item in (await _engine().query(explicit)).entities] == [
        "light.hallway"
    ]
    invented = HomeQueryPlan.from_mapping(
        {
            **explicit.as_dict(),
            "entity_ids": ["light.model_invented"],
        }
    )
    with pytest.raises(ValueError, match="no longer exists"):
        await _engine().query(invented)


@pytest.mark.asyncio
async def test_unavailable_is_not_conflated_with_off() -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "cameras",
            "predicate": "UNAVAILABLE",
            "aggregation": "LIST",
        }
    )
    states = (
        *STATES,
        {
            "entity_id": "camera.unknown",
            "domain": "camera",
            "name": "Unknown Camera",
            "state": "unknown",
            "available": False,
        },
    )
    result = await _engine(states).query(plan)
    assert [item.entity_id for item in result.entities] == ["camera.hallway"]
    assert "camera.bedroom" not in {item.entity_id for item in result.entities}
    assert "camera.unknown" not in {item.entity_id for item in result.entities}


@pytest.mark.asyncio
async def test_complete_unavailable_device_query_uses_explicit_unavailable_state() -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "devices",
            "predicate": "UNAVAILABLE",
            "aggregation": "LIST",
        }
    )
    result = (await _engine().query(plan)).as_result()

    assert [item["entity_id"] for item in result["entities"]] == ["camera.hallway"]
    assert (
        render_home_query_evidence(
            [{"tool": "query_home", "result": result}],
            request_text="Which devices are unavailable?",
        )
        == "1 device is unavailable: Hallway Camera."
    )


def test_physical_device_rollup_collapses_child_entities_without_losing_evidence() -> None:
    observed_at = "2026-10-08T12:00:00+00:00"
    entities = tuple(
        GroundedHomeEntity.from_state(item, observed_at)
        for item in (
            {
                "entity_id": "camera.hallway",
                "domain": "camera",
                "name": "Hallway Camera Stream",
                "device_id": "camera-device-1",
                "device_name": "Hallway Camera",
                "state": "unavailable",
            },
            *(
                {
                    "entity_id": f"sensor.hallway_camera_diagnostic_{index}",
                    "domain": "sensor",
                    "name": f"Hallway Camera Diagnostic {index}",
                    "device_id": "camera-device-1",
                    "device_name": "Hallway Camera",
                    "entity_category": "diagnostic",
                    "state": "unavailable",
                }
                for index in range(1, 8)
            ),
        )
    )

    devices = roll_up_physical_devices(entities, observed_at=observed_at)

    assert len(devices) == 1
    assert devices[0].availability is DeviceAvailability.UNAVAILABLE
    assert devices[0].name == "Hallway Camera"
    assert devices[0].unavailable_entity_count == 8
    assert len(devices[0].member_entities) == 8


def test_available_primary_device_with_failed_diagnostic_is_partial_not_offline() -> None:
    observed_at = "2026-10-08T12:00:00+00:00"
    entities = tuple(
        GroundedHomeEntity.from_state(item, observed_at)
        for item in (
            {
                "entity_id": "camera.hallway",
                "domain": "camera",
                "name": "Hallway Camera",
                "device_id": "camera-device-1",
                "state": "streaming",
            },
            {
                "entity_id": "sensor.hallway_camera_temperature",
                "domain": "sensor",
                "name": "Hallway Camera Temperature",
                "device_id": "camera-device-1",
                "entity_category": "diagnostic",
                "state": "unavailable",
            },
        )
    )

    device = roll_up_physical_devices(entities, observed_at=observed_at)[0]

    assert device.availability is DeviceAvailability.PARTIAL
    assert device.unavailable_entity_count == 1


def test_device_rollup_never_groups_unlinked_entities_by_similar_names() -> None:
    observed_at = "2026-10-08T12:00:00+00:00"
    entities = tuple(
        GroundedHomeEntity.from_state(item, observed_at)
        for item in (
            {
                "entity_id": "camera.hallway",
                "domain": "camera",
                "name": "Hallway Camera",
                "state": "unavailable",
            },
            {
                "entity_id": "sensor.hallway_camera_status",
                "domain": "sensor",
                "name": "Hallway Camera Status",
                "state": "unavailable",
            },
        )
    )

    devices = roll_up_physical_devices(entities, observed_at=observed_at)

    assert len(devices) == 2
    assert {item.device_key for item in devices} == {
        "entity:camera.hallway",
        "entity:sensor.hallway_camera_status",
    }
    assert all(item.device_id is None for item in devices)


@pytest.mark.asyncio
async def test_two_unavailable_physical_devices_render_as_two_devices() -> None:
    states = (
        {
            "entity_id": "camera.hallway",
            "domain": "camera",
            "name": "Hallway Camera Stream",
            "device_id": "camera-device-1",
            "device_name": "Hallway Camera",
            "state": "unavailable",
        },
        {
            "entity_id": "media_player.bedroom_echo",
            "domain": "media_player",
            "name": "Bedroom Echo Player",
            "device_id": "echo-device-1",
            "device_name": "Bedroom Echo",
            "state": "unavailable",
        },
    )
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "devices",
            "predicate": "UNAVAILABLE",
            "aggregation": "LIST",
        }
    )
    result = (await _engine(states).query(plan)).as_result()

    assert result["count"] == 2
    assert [item["name"] for item in result["devices"]] == [
        "Bedroom Echo",
        "Hallway Camera",
    ]
    assert len(result["entities"]) == 2


@pytest.mark.asyncio
async def test_presence_and_snapshot_are_registry_grounded_and_deduplicated() -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "people",
            "predicate": "HOME",
            "aggregation": "LIST",
        }
    )
    people = await _engine().query(plan)
    assert [item.name for item in people.entities] == ["Aaron"]
    all_people = await _engine().query(
        HomeQueryPlan.from_mapping(
            {
                "scope": "HOME",
                "category": "people",
                "predicate": "ANY",
                "aggregation": "SUMMARY",
            }
        )
    )
    assert (
        render_home_query_evidence(
            [{"tool": "query_home", "result": all_people.as_result()}],
            request_text="Who is home?",
        )
        == "Aaron is home. Amber is away."
    )
    snapshot = (await _engine().snapshot()).as_dict()
    assert [item["entity_id"] for item in snapshot["unavailable_entities"]] == ["camera.hallway"]
    assert len({item.entity_id for item in (await _engine().snapshot()).entities}) == len(STATES)
    assert [item["entity_id"] for item in snapshot["offline_cameras"]] == ["camera.hallway"]


@pytest.mark.asyncio
async def test_snapshot_derives_only_grounded_active_and_health_categories() -> None:
    states = (
        *STATES,
        {
            "entity_id": "vacuum.downstairs",
            "domain": "vacuum",
            "name": "Downstairs Vacuum",
            "state": "cleaning",
            "available": True,
        },
        {
            "entity_id": "switch.coffee_machine",
            "domain": "switch",
            "name": "Coffee Machine",
            "state": "on",
            "available": True,
        },
        {
            "entity_id": "sensor.door_battery",
            "domain": "sensor",
            "name": "Door Battery",
            "state": "15",
            "available": True,
            "device_class": "battery",
            "unit": "%",
        },
    )
    snapshot = (await _engine(states).snapshot()).as_dict()

    assert [item["entity_id"] for item in snapshot["running_appliances"]] == ["vacuum.downstairs"]
    assert [item["entity_id"] for item in snapshot["switches_on"]] == ["switch.coffee_machine"]
    assert [item["entity_id"] for item in snapshot["low_batteries"]] == ["sensor.door_battery"]


def test_plan_rejects_unscoped_area_and_invalid_presence_predicate() -> None:
    with pytest.raises(ValueError, match="exact configured area"):
        HomeQueryPlan.from_mapping({"scope": "AREA", "category": "lights"})
    with pytest.raises(ValueError, match="apply only to people"):
        HomeQueryPlan.from_mapping({"scope": "HOME", "category": "lights", "predicate": "HOME"})
    with pytest.raises(ValueError, match="grounded entity set"):
        HomeQueryPlan.from_mapping(
            {
                "scope": "REFERENCED_ENTITY_SET",
                "category": "lights",
                "predicate": "ON",
            }
        )


@pytest.mark.asyncio
async def test_whole_home_renderer_handles_zero_one_many_and_house_status() -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
        }
    )
    result = (await _engine().query(plan)).as_result()
    rendered = render_home_query_evidence(
        [{"tool": "query_home", "result": result}], request_text="Which lights are on?"
    )
    assert rendered == "2 lights are on: Hallway and Living Room."

    hallway = HomeQueryPlan.from_mapping({**plan.as_dict(), "scope": "AREA", "area_id": "hallway"})
    one = (await _engine().query(hallway)).as_result()
    assert (
        render_home_query_evidence(
            [{"tool": "query_home", "result": one}],
            request_text="Which light is on in the hallway?",
        )
        == "1 light is on in the hallway: Hallway."
    )

    bedroom = HomeQueryPlan.from_mapping({**plan.as_dict(), "scope": "AREA", "area_id": "bedroom"})
    empty = (await _engine().query(bedroom)).as_result()
    assert (
        render_home_query_evidence(
            [{"tool": "query_home", "result": empty}], request_text="What about the bedroom?"
        )
        == "The Bedroom lights are off."
    )

    snapshot = await _engine().snapshot()
    status = render_home_query_evidence(
        [
            {
                "tool": "query_home",
                "result": {
                    "success": True,
                    "query_plan": {
                        "operation": "SNAPSHOT",
                        "scope": "HOME",
                        "category": "devices",
                        "predicate": "ANY",
                        "aggregation": "SUMMARY",
                        "area_id": None,
                    },
                    "snapshot": snapshot.as_dict(),
                },
            }
        ],
        request_text="Give me a house status.",
    )
    assert status is not None
    assert status.startswith("One thing needs attention: Hallway Camera")
    assert "2 lights are on" in status


@pytest.mark.asyncio
async def test_house_status_normal_and_active_appliance_fixtures_stay_grounded() -> None:
    normal_states = tuple(
        {
            **item,
            "state": (
                "off"
                if item["domain"] == "light"
                else "idle"
                if item["domain"] == "media_player"
                else "on"
                if item["entity_id"] == "camera.hallway"
                else item["state"]
            ),
            "available": True,
        }
        for item in STATES
    )
    normal_snapshot = await _engine(normal_states).snapshot()
    normal = render_home_query_evidence(
        [
            {
                "tool": "query_home",
                "result": {
                    "success": True,
                    "query_plan": {
                        "operation": "SNAPSHOT",
                        "scope": "HOME",
                        "category": "devices",
                        "predicate": "ANY",
                        "aggregation": "SUMMARY",
                    },
                    "snapshot": normal_snapshot.as_dict(),
                },
            }
        ],
        request_text="Give me a house status.",
    )
    assert normal is not None and normal.startswith("Everything looks normal.")
    assert "all lights are off" in normal

    active_states = (
        *normal_states,
        {
            "entity_id": "vacuum.downstairs",
            "domain": "vacuum",
            "name": "Downstairs Vacuum",
            "state": "cleaning",
            "available": True,
        },
    )
    active_snapshot = await _engine(active_states).snapshot()
    active = render_home_query_evidence(
        [
            {
                "tool": "query_home",
                "result": {
                    "success": True,
                    "query_plan": {
                        "operation": "SNAPSHOT",
                        "scope": "HOME",
                        "category": "devices",
                        "predicate": "ANY",
                        "aggregation": "SUMMARY",
                    },
                    "snapshot": active_snapshot.as_dict(),
                },
            }
        ],
        request_text="Give me a house status.",
    )
    assert active is not None and "1 appliance is running" in active


def test_generic_continuous_set_result_keeps_only_registry_ids() -> None:
    with pytest.raises(ValueError, match="invalid entity identity"):
        from app.home_intelligence import GroundedHomeEntity

        GroundedHomeEntity.from_state({"entity_id": "invented"}, "now")


@pytest.mark.asyncio
async def test_grounded_result_set_survives_restart_and_is_principal_scoped(tmp_path) -> None:
    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
        }
    )
    result = (await _engine().query(plan)).as_result()
    objects, result_set = tool_call_projection(
        intent="state_query",
        calls=[{"tool": "query_home", "result": result}],
    )
    database = tmp_path / "dialogue.db"
    service = WorkingContextService(DialogueManager(str(database)))
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:home-set",
        objects=objects,
        result_set=result_set,
        focus_refs=[item.reference_id for item in objects],
    )

    restarted = WorkingContextService(DialogueManager(str(database)))
    resolution = await restarted.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:home-set",
        query=reference_query("turn those off", object_types=("device",)),
    )
    assert resolution.status is ReferenceStatus.RESOLVED
    assert [item.canonical_id for item in resolution.objects] == [
        "light.hallway",
        "light.living_room",
    ]

    isolated = await restarted.resolve(
        principal_id="amber",
        conversation_id="usr:amber:home-set",
        query=reference_query("turn those off", object_types=("device",)),
    )
    assert isolated.status is ReferenceStatus.MISSING
    with pytest.raises(ValueError, match="scope does not match"):
        await restarted.resolve(
            principal_id="amber",
            conversation_id="usr:aaron:home-set",
            query=reference_query("turn those off", object_types=("device",)),
        )


@pytest.mark.asyncio
async def test_model_semantics_can_request_sets_but_cannot_supply_entity_ids() -> None:
    class Registry:
        async def areas(self):
            return AREAS

    class Tools:
        READABLE_DOMAINS = {"light", "person", "camera"}

    engine = AIEngine.__new__(AIEngine)
    engine.registry = Registry()
    engine.tools = Tools()
    definitions = await engine._home_read_tools(type("Actor", (), {"area_id": None})())
    search_home = next(item for item in definitions if item["name"] == "search_home")
    properties = search_home["parameters"]["properties"]

    assert {"PHYSICAL_DEVICE", "ROOM", "HOME_SUMMARY"} == set(properties["inventory_kind"]["enum"])
    assert {
        "OCCUPIED",
        "LIKELY_OCCUPIED",
        "PROBABLY_CLEAR",
        "UNKNOWN",
        "ANY",
        None,
    } == set(properties["occupancy_state"]["enum"])
    assert "query_home" not in {item["name"] for item in definitions}
    assert "entity_ids" not in properties
    assert "area_id" in properties
    assert set(properties["scope"]["enum"]) == {"HOME", "AREA", "CURRENT_REFERENCE"}
    assert "semantic_target" in properties
    assert "query" not in properties
    assert "use_current_reference" not in properties
