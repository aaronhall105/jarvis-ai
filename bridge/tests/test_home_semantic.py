from __future__ import annotations

from types import MethodType
from typing import Any

import pytest

from app.home_semantic import HomeSemanticEngine
from app.response_presentation import render_home_search_evidence
from app.tool_engine import ToolEngine
from app.dialogue_manager import DialogueManager
from app.working_context import (
    ReferenceQuery,
    ResultIntelligence,
    WorkingContextService,
    tool_call_projection,
)


def entity(
    entity_id: str,
    name: str,
    *,
    domain: str,
    state: str,
    area_id: str,
    area_name: str,
    device_id: str,
    device_name: str,
    device_class: str | None = None,
    supported_features: int = 0,
    manufacturer: str = "Synthetic",
    model: str = "Fixture",
    connection: str | None = None,
    entity_category: str | None = None,
    platform: str = "fixture",
    via_device_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "name": name,
        "domain": domain,
        "state": state,
        "available": state not in {"unknown", "unavailable", ""},
        "area_id": area_id,
        "area_name": area_name,
        "device_id": device_id,
        "device_name": device_name,
        "device_name_by_user": device_name,
        "device_class": device_class,
        "entity_category": entity_category,
        "device_manufacturer": manufacturer,
        "device_model": model,
        "device_connections": ([["mac", connection]] if connection else []),
        "device_identifiers": [["fixture", device_id]],
        "supported_features": supported_features,
        "platform": platform,
        "via_device_id": via_device_id,
        "attributes": attributes or {},
    }


class SemanticFixture:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []
        self.engine = HomeSemanticEngine(
            state_loader=self.load,
            service_caller=self.call_service,
        )
        self.engine.VERIFY_DELAYS = (0,)

    async def load(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.rows]

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_ids: list[str],
        service_data: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "entity_ids": entity_ids,
                "service_data": service_data,
            }
        )
        target = "on" if service == "turn_on" else "off" if service == "turn_off" else None
        for item in self.rows:
            if item["entity_id"] in entity_ids and target:
                item["state"] = target
                item["available"] = True


async def search(
    fixture: SemanticFixture,
    query: str,
    *,
    terms: list[str],
    area_id: str | None = None,
    capability: str | None = "state",
    aggregation: str = "LIST",
    operation: str = "QUERY",
    action: str | None = None,
    inventory_kind: str = "PHYSICAL_DEVICE",
    state: str | None = None,
    include_virtual: bool = False,
    domain: str | None = None,
    capability_role_filter: str = "ANY",
    parent_purpose: str | None = None,
) -> dict[str, Any]:
    return await fixture.engine.search(
        query=query,
        semantic_terms=terms,
        area_id=area_id,
        capability=capability,
        domain=domain,
        state=state,
        inventory_kind=inventory_kind,
        include_virtual=include_virtual,
        capability_role_filter=capability_role_filter,
        parent_purpose=parent_purpose,
        aggregation=aggregation,
        operation=operation,
        requested_action=action,
        include_diagnostics=False,
        limit=20,
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )


def capability_role_fixture() -> SemanticFixture:
    return SemanticFixture(
        [
            entity(
                "light.studio_ceiling",
                "Studio Ceiling",
                domain="light",
                state="on",
                area_id="studio",
                area_name="Studio",
                device_id="ceiling-dimmer",
                device_name="Studio Ceiling",
            ),
            entity(
                "sensor.studio_ceiling_power",
                "Studio Ceiling Power",
                domain="sensor",
                state="12",
                area_id="studio",
                area_name="Studio",
                device_id="ceiling-dimmer",
                device_name="Studio Ceiling",
                device_class="power",
            ),
            entity(
                "camera.patio_clear",
                "Patio Clear",
                domain="camera",
                state="streaming",
                area_id="patio",
                area_name="Patio",
                device_id="patio-sentinel",
                device_name="Patio Sentinel",
            ),
            entity(
                "binary_sensor.patio_person",
                "Patio Person",
                domain="binary_sensor",
                state="off",
                area_id="patio",
                area_name="Patio",
                device_id="patio-sentinel",
                device_name="Patio Sentinel",
                device_class="occupancy",
            ),
            entity(
                "light.patio_flood",
                "Patio Flood",
                domain="light",
                state="on",
                area_id="patio",
                area_name="Patio",
                device_id="patio-sentinel",
                device_name="Patio Sentinel",
            ),
            entity(
                "switch.kitchen_appliance",
                "Kitchen Appliance",
                domain="switch",
                state="on",
                area_id="kitchen",
                area_name="Kitchen",
                device_id="kitchen-appliance",
                device_name="Kitchen Appliance",
                device_class="appliance",
            ),
            entity(
                "sensor.kitchen_appliance_temperature",
                "Kitchen Appliance Temperature",
                domain="sensor",
                state="180",
                area_id="kitchen",
                area_name="Kitchen",
                device_id="kitchen-appliance",
                device_name="Kitchen Appliance",
                device_class="temperature",
            ),
            entity(
                "light.kitchen_appliance_cavity",
                "Kitchen Appliance Cavity",
                domain="light",
                state="off",
                area_id="kitchen",
                area_name="Kitchen",
                device_id="kitchen-appliance",
                device_name="Kitchen Appliance",
            ),
            entity(
                "light.utility_machine_indicator",
                "Utility Machine Indicator",
                domain="light",
                state="off",
                area_id="utility",
                area_name="Utility",
                device_id="utility-machine",
                device_name="Utility Machine",
            ),
            entity(
                "binary_sensor.utility_machine_door",
                "Utility Machine Door",
                domain="binary_sensor",
                state="off",
                area_id="utility",
                area_name="Utility",
                device_id="utility-machine",
                device_name="Utility Machine",
                device_class="opening",
            ),
            entity(
                "sensor.utility_machine_temperature",
                "Utility Machine Temperature",
                domain="sensor",
                state="42",
                area_id="utility",
                area_name="Utility",
                device_id="utility-machine",
                device_name="Utility Machine",
                device_class="temperature",
            ),
            entity(
                "fan.conservatory_combo",
                "Conservatory Airflow",
                domain="fan",
                state="on",
                area_id="conservatory",
                area_name="Conservatory",
                device_id="conservatory-combo",
                device_name="Conservatory Combo",
                device_class="fan",
            ),
            entity(
                "light.conservatory_combo",
                "Conservatory Illumination",
                domain="light",
                state="on",
                area_id="conservatory",
                area_name="Conservatory",
                device_id="conservatory-combo",
                device_name="Conservatory Combo",
                device_class="light",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_capability_roles_follow_parent_structure_not_capability_presence() -> None:
    graph = await capability_role_fixture().engine.graph()
    by_entity = {
        entity_id: capability
        for capability in graph.capabilities
        for entity_id in capability.member_entity_ids
    }

    assert by_entity["light.studio_ceiling"].capability_role == "PRIMARY"
    assert by_entity["light.patio_flood"].capability_role == "AUXILIARY"
    assert by_entity["light.kitchen_appliance_cavity"].capability_role == "AUXILIARY"
    assert by_entity["light.utility_machine_indicator"].capability_role == "AUXILIARY"
    assert by_entity["binary_sensor.patio_person"].capability_role == "OBSERVATIONAL"
    assert by_entity["sensor.kitchen_appliance_temperature"].capability_role == "MEASUREMENT"
    assert by_entity["fan.conservatory_combo"].capability_role == "PRIMARY"
    assert by_entity["light.conservatory_combo"].capability_role == "PRIMARY"


@pytest.mark.asyncio
async def test_primary_light_collection_excludes_but_does_not_hide_auxiliary_lights() -> None:
    fixture = capability_role_fixture()
    primary = await search(
        fixture,
        "light",
        terms=[],
        domain="light",
        inventory_kind="CAPABILITY",
        capability_role_filter="PRIMARY_ONLY",
        aggregation="COUNT",
        capability=None,
    )
    all_lights = await search(
        fixture,
        "light",
        terms=[],
        domain="light",
        inventory_kind="CAPABILITY",
        capability_role_filter="PRIMARY_OR_AUXILIARY",
        aggregation="COUNT",
        capability=None,
    )
    auxiliary = await search(
        fixture,
        "light",
        terms=[],
        domain="light",
        inventory_kind="CAPABILITY",
        capability_role_filter="AUXILIARY_ONLY",
        aggregation="LIST",
        capability=None,
    )

    assert primary["count"] == 2
    assert all_lights["count"] == 5
    assert auxiliary["count"] == 3
    assert {item["parent_canonical_id"] for item in auxiliary["items"]} <= {
        item.canonical_id for item in (await fixture.engine.graph()).physical_objects
    }
    response = render_home_search_evidence(
        [{"tool": "search_home", "arguments": {}, "result": primary}],
        request_text="How many are there?",
    )
    assert response and response.startswith("I found 2 main lights:")


@pytest.mark.asyncio
async def test_attached_capabilities_filter_by_free_form_parent_purpose_and_state() -> None:
    result = await search(
        capability_role_fixture(),
        "light",
        terms=[],
        domain="light",
        state="on",
        inventory_kind="CAPABILITY",
        capability_role_filter="AUXILIARY_ONLY",
        parent_purpose="camera",
        aggregation="LIST",
        capability=None,
    )

    assert result["count"] == 1
    assert result["items"][0]["entity_roles"] == {"light.patio_flood": "PRIMARY_CONTROL"}
    assert result["items"][0]["capability_role"] == "AUXILIARY"
    assert result["items"][0]["primary_purposes"] == ["camera"]
    assert result["parent_purpose"] == "camera"


def television_fixture() -> SemanticFixture:
    power_features = 128 | 256 | 4096 | 16384
    return SemanticFixture(
        [
            entity(
                "media_player.bedroom_display",
                "Bedroom TV",
                domain="media_player",
                state="on",
                area_id="bedroom",
                area_name="Bedroom",
                device_id="bedroom-display",
                device_name="Bedroom TV",
                device_class="tv",
                supported_features=power_features,
                connection="00:00:00:00:00:01",
            ),
            entity(
                "remote.bedroom_display",
                "Bedroom TV Remote",
                domain="remote",
                state="on",
                area_id="bedroom",
                area_name="Bedroom",
                device_id="bedroom-display",
                device_name="Bedroom TV",
                connection="00:00:00:00:00:01",
            ),
            entity(
                "media_player.lounge_display",
                "Lounge Screen",
                domain="media_player",
                state="off",
                area_id="lounge",
                area_name="Lounge",
                device_id="lounge-display",
                device_name="Lounge Screen",
                device_class="tv",
                supported_features=power_features,
            ),
            entity(
                "binary_sensor.bed_tv_occupancy",
                "Bed TV occupancy",
                domain="binary_sensor",
                state="off",
                area_id="bedroom",
                area_name="Bedroom",
                device_id="bed-observer",
                device_name="Bed Observer",
                device_class="occupancy",
            ),
            entity(
                "sensor.bed_tv_count",
                "Bed TV count",
                domain="sensor",
                state="0",
                area_id="bedroom",
                area_name="Bedroom",
                device_id="bed-observer",
                device_name="Bed Observer",
            ),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wording", "semantic_terms"),
    (
        ("how many TVs do I have", ["tv"]),
        ("how many televisions are there", ["television", "tv"]),
        ("number of TVs in the house", ["tv"]),
        ("TV count", ["tv"]),
        ("how many tv are in flat", ["tv"]),
    ),
)
async def test_collection_variations_count_canonical_objects_not_observation_entities(
    wording: str,
    semantic_terms: list[str],
) -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        wording,
        terms=semantic_terms,
        aggregation="COUNT",
    )

    assert result["count"] == 2
    assert {item["display_name"] for item in result["items"]} == {
        "Bedroom TV",
        "Lounge Screen",
    }
    assert all("occupancy" not in item["display_name"].casefold() for item in result["items"])


@pytest.mark.asyncio
async def test_semantic_phrases_do_not_match_partial_vendor_or_name_tokens() -> None:
    fixture = television_fixture()
    fixture.rows.extend(
        [
            entity(
                "binary_sensor.mobile_connected",
                "Mobile Connected",
                domain="binary_sensor",
                state="on",
                area_id="study",
                area_name="Study",
                device_id="mobile-device",
                device_name="Mobile Device",
                device_class="connectivity",
                manufacturer="Samsung",
            ),
            entity(
                "sensor.sun_next_rising",
                "Sun Next Rising",
                domain="sensor",
                state="tomorrow",
                area_id="outside",
                area_name="Outside",
                device_id="sun-device",
                device_name="Sun",
            ),
        ]
    )

    result = await search(
        fixture,
        "television",
        terms=["tv", "smart tv", "samsung tv"],
        aggregation="COUNT",
    )

    assert result["count"] == 2
    assert {item["display_name"] for item in result["items"]} == {
        "Bedroom TV",
        "Lounge Screen",
    }
    assert HomeSemanticEngine._score_values(("Living Room TV (Cast)",), ("tv",)) == 0.9


@pytest.mark.asyncio
async def test_bedroom_target_is_canonical_and_capability_filtered() -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        "television",
        terms=["television", "tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )

    assert result["resolution"] == "unique"
    assert [item["display_name"] for item in result["items"]] == ["Bedroom TV"]
    assert result["action_plan"]["candidate_ids"] == [result["items"][0]["canonical_id"]]


@pytest.mark.asyncio
async def test_area_words_filter_scope_without_matching_every_item_in_that_area() -> None:
    fixture = television_fixture()
    fixture.rows.append(
        entity(
            "media_player.bedroom_speaker",
            "Bedroom Speaker",
            domain="media_player",
            state="idle",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="bedroom-speaker",
            device_name="Bedroom Speaker",
            device_class="speaker",
            supported_features=1 | 4096 | 16384,
        )
    )
    result = await search(
        fixture,
        "bedroom tv",
        terms=["bedroom television", "tv in bedroom", "bedroom media player"],
        area_id="bedroom",
        capability=None,
    )

    assert [item["display_name"] for item in result["items"]] == ["Bedroom TV"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wording",
    (
        "switch off the bedroom television",
        "bedroom TV off",
        "can you turn off the TV in my bedroom",
        "kill the bedroom telly",
    ),
)
async def test_language_variations_share_one_semantic_control_plan(wording: str) -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        wording,
        terms=["television", "tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )

    assert result["resolution"] == "unique"
    assert result["items"][0]["display_name"] == "Bedroom TV"


@pytest.mark.asyncio
async def test_unrelated_off_observation_cannot_produce_already_done() -> None:
    fixture = television_fixture()
    discovered = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    canonical_id = discovered["items"][0]["canonical_id"]
    result = await fixture.engine.execute(
        handle=discovered["action_plan"]["handle"],
        canonical_ids=[canonical_id],
        action="turn_off",
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )

    assert result["verified"] is True
    assert result["already_in_target_state"] is False
    assert fixture.calls == [
        {
            "domain": "media_player",
            "service": "turn_off",
            "entity_ids": ["media_player.bedroom_display"],
            "service_data": None,
        }
    ]


@pytest.mark.asyncio
async def test_already_satisfied_requires_exact_authoritative_target_state() -> None:
    fixture = television_fixture()
    fixture.rows[0]["state"] = "off"
    discovered = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    canonical_id = discovered["items"][0]["canonical_id"]
    result = await fixture.engine.execute(
        handle=discovered["action_plan"]["handle"],
        canonical_ids=[canonical_id],
        action="turn_off",
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )

    assert result["verified"] is True
    assert result["already_in_target_state"] is True
    assert result["changed"] is False
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_novel_supported_device_needs_no_noun_specific_implementation() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "fan.studio_fixture",
                "Studio Air Purifier",
                domain="fan",
                state="on",
                area_id="studio",
                area_name="Studio",
                device_id="novel-device",
                device_name="Studio Air Purifier",
                device_class=None,
                manufacturer="Arbitrary Labs",
                model="Clean Sphere",
            )
        ]
    )
    state_result = await search(
        fixture,
        "air purifier",
        terms=["air purifier"],
        area_id="studio",
        aggregation="STATE",
    )
    assert state_result["items"][0]["state"] == "on"

    control = await search(
        fixture,
        "air purifier",
        terms=["air purifier"],
        area_id="studio",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    result = await fixture.engine.execute(
        handle=control["action_plan"]["handle"],
        canonical_ids=[control["items"][0]["canonical_id"]],
        action="turn_off",
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )
    assert result["verification_status"] == "VERIFIED"
    assert fixture.calls[0]["domain"] == "fan"


@pytest.mark.asyncio
async def test_unknown_friendly_name_and_read_only_capabilities_are_dynamic() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "switch.aether_bloom",
                "Aether Bloom",
                domain="switch",
                state="on",
                area_id="studio",
                area_name="Studio",
                device_id="aether-bloom",
                device_name="Aether Bloom",
            ),
            entity(
                "camera.north_portal",
                "North Portal",
                domain="camera",
                state="idle",
                area_id="hall",
                area_name="Hall",
                device_id="north-portal",
                device_name="North Portal",
            ),
        ]
    )
    novel = await search(fixture, "Aether Bloom", terms=["Aether Bloom"])
    assert novel["items"][0]["display_name"] == "Studio Aether Bloom"
    camera_write = await search(
        fixture,
        "North Portal",
        terms=["North Portal"],
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    assert camera_write["count"] == 0
    camera_read = await search(
        fixture,
        "North Portal",
        terms=["North Portal"],
        capability="view",
    )
    assert camera_read["count"] == 1


@pytest.mark.asyncio
async def test_shared_physical_connection_collapses_multiple_control_surfaces() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "media_player.surface_one",
                "Family Display",
                domain="media_player",
                state="on",
                area_id="family_room",
                area_name="Family Room",
                device_id="integration-one",
                device_name="Family Display",
                device_class="tv",
                supported_features=128 | 256,
                connection="00:00:00:00:00:09",
            ),
            entity(
                "media_player.surface_two",
                "Family Display Renderer",
                domain="media_player",
                state="on",
                area_id="family_room",
                area_name="Family Room",
                device_id="integration-two",
                device_name="Family Display",
                device_class="tv",
                supported_features=128 | 256,
                connection="00:00:00:00:00:09",
            ),
        ]
    )
    result = await search(fixture, "display", terms=["display"], aggregation="COUNT")
    assert result["count"] == 1
    inspected = await fixture.engine.inspect(result["items"][0]["canonical_id"])
    assert len(inspected["item"]["member_entities"]) == 2


@pytest.mark.asyncio
async def test_similar_names_without_grounded_identity_remain_distinct() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "switch.first_surface",
                "Shared Label",
                domain="switch",
                state="on",
                area_id="studio",
                area_name="Studio",
                device_id="first-identity",
                device_name="Shared Label",
            ),
            entity(
                "switch.second_surface",
                "Shared Label",
                domain="switch",
                state="off",
                area_id="studio",
                area_name="Studio",
                device_id="second-identity",
                device_name="Shared Label",
            ),
        ]
    )

    result = await search(fixture, "Shared Label", terms=["Shared Label"], aggregation="COUNT")
    assert result["count"] == 2
    assert len({item["canonical_id"] for item in result["items"]}) == 2


@pytest.mark.asyncio
async def test_one_device_exposes_independent_capabilities_without_count_inflation() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "light.workshop_main",
                "Workshop Main",
                domain="light",
                state="on",
                area_id="workshop",
                area_name="Workshop",
                device_id="multi-surface-controller",
                device_name="Dimming Controller",
            ),
            entity(
                "sensor.workshop_power",
                "Workshop Power",
                domain="sensor",
                state="12.4",
                area_id="workshop",
                area_name="Workshop",
                device_id="multi-surface-controller",
                device_name="Dimming Controller",
                device_class="power",
            ),
            entity(
                "sensor.workshop_energy",
                "Workshop Energy",
                domain="sensor",
                state="3.2",
                area_id="workshop",
                area_name="Workshop",
                device_id="multi-surface-controller",
                device_name="Dimming Controller",
                device_class="energy",
            ),
            entity(
                "event.workshop_input",
                "Workshop Input",
                domain="event",
                state="2026-10-11T00:00:00Z",
                area_id="workshop",
                area_name="Workshop",
                device_id="multi-surface-controller",
                device_name="Dimming Controller",
            ),
            entity(
                "update.workshop_firmware",
                "Workshop Firmware",
                domain="update",
                state="off",
                area_id="workshop",
                area_name="Workshop",
                device_id="multi-surface-controller",
                device_name="Dimming Controller",
                entity_category="config",
            ),
        ]
    )

    graph = await fixture.engine.graph()
    assert len(graph.physical_objects) == 1
    assert {item.capability_kind for item in graph.capabilities} == {"energy", "light", "power"}
    inspected = graph.physical_objects[0].inspected()
    assert {item["role"] for item in inspected["member_entities"]} == {
        "PRIMARY_CONTROL",
        "MEASUREMENT",
        "EVENT",
        "CONFIGURATION",
    }

    lights = await search(
        fixture,
        "light",
        terms=["light"],
        inventory_kind="CAPABILITY",
        state="on",
    )
    assert lights["count"] == 1
    assert lights["items"][0]["capability_kind"] == "light"
    assert lights["items"][0]["parent_canonical_id"] == graph.physical_objects[0].canonical_id


@pytest.mark.asyncio
async def test_camera_streams_roll_up_once_while_attached_light_remains_a_capability() -> None:
    common = {
        "device_name": "Observatory",
    }
    fixture = SemanticFixture(
        [
            entity(
                "camera.observatory_clear",
                "Observatory Clear",
                domain="camera",
                state="idle",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                connection="00:00:00:00:00:42",
                **common,
            ),
            entity(
                "camera.observatory_fluent",
                "Observatory Fluent",
                domain="camera",
                state="idle",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                connection="00:00:00:00:00:42",
                **common,
            ),
            entity(
                "camera.observatory_snapshots_clear",
                "Observatory Snapshots Clear",
                domain="camera",
                state="idle",
                area_id="aggregate",
                area_name="Aggregate",
                device_id="analytics-device",
                via_device_id="analytics-gateway",
                **common,
            ),
            entity(
                "camera.observatory_snapshots_fluent",
                "Observatory Snapshots Fluent",
                domain="camera",
                state="idle",
                area_id="aggregate",
                area_name="Aggregate",
                device_id="analytics-device",
                via_device_id="analytics-gateway",
                **common,
            ),
            entity(
                "binary_sensor.observatory_person",
                "Observatory Person",
                domain="binary_sensor",
                state="off",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                device_class="occupancy",
                connection="00:00:00:00:00:42",
                **common,
            ),
            entity(
                "binary_sensor.observatory_motion",
                "Observatory Motion",
                domain="binary_sensor",
                state="off",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                device_class="motion",
                connection="00:00:00:00:00:42",
                **common,
            ),
            entity(
                "light.observatory_flood",
                "Observatory Flood",
                domain="light",
                state="on",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                connection="00:00:00:00:00:42",
                **common,
            ),
            entity(
                "sensor.observatory_signal",
                "Observatory Signal",
                domain="sensor",
                state="-54",
                area_id="observatory",
                area_name="Observatory",
                device_id="camera-device",
                entity_category="diagnostic",
                connection="00:00:00:00:00:42",
                **common,
            ),
        ]
    )

    cameras = await search(fixture, "camera", terms=["camera"], aggregation="COUNT")
    assert cameras["count"] == 1
    assert cameras["items"][0]["kind"] == "camera"
    assert cameras["items"][0]["area_id"] == "observatory"
    inspected = await fixture.engine.inspect(cameras["items"][0]["canonical_id"])
    assert len(inspected["item"]["member_entities"]) == 8

    lights = await search(
        fixture,
        "light",
        terms=["light"],
        inventory_kind="CAPABILITY",
        state="on",
    )
    assert lights["count"] == 1
    assert lights["items"][0]["capability_kind"] == "light"


@pytest.mark.asyncio
async def test_physical_counts_exclude_groups_while_virtual_targets_remain_searchable() -> None:
    media_features = 1 | 4096 | 16384
    fixture = SemanticFixture(
        [
            entity(
                "media_player.kitchen_endpoint",
                "Kitchen Sound",
                domain="media_player",
                state="idle",
                area_id="kitchen",
                area_name="Kitchen",
                device_id="kitchen-endpoint",
                device_name="Kitchen Sound",
                device_class="speaker",
                supported_features=media_features,
            ),
            entity(
                "media_player.lounge_endpoint",
                "Lounge Sound",
                domain="media_player",
                state="idle",
                area_id="lounge",
                area_name="Lounge",
                device_id="lounge-endpoint",
                device_name="Lounge Sound",
                device_class="speaker",
                supported_features=media_features,
            ),
            entity(
                "media_player.whole_home",
                "Whole Home Audio",
                domain="media_player",
                state="idle",
                area_id="home",
                area_name="Home",
                device_id="",
                device_name="Speaker Group",
                device_class="speaker",
                supported_features=media_features,
                attributes={
                    "entity_id": [
                        "media_player.kitchen_endpoint",
                        "media_player.lounge_endpoint",
                    ]
                },
            ),
        ]
    )

    physical = await search(fixture, "speaker", terms=["speaker"], aggregation="COUNT")
    assert physical["count"] == 2
    assert all(item["object_role"] == "PHYSICAL" for item in physical["items"])

    groups = await search(
        fixture,
        "speaker group",
        terms=["speaker group"],
        inventory_kind="VIRTUAL_TARGET",
    )
    assert groups["count"] == 1
    assert groups["items"][0]["object_role"] == "GROUP"

    group_action = await search(
        fixture,
        "speaker group",
        terms=["speaker group"],
        capability="play",
        operation="CONTROL",
        action="play",
        inventory_kind="VIRTUAL_TARGET",
    )
    assert group_action["resolution"] == "unique"
    assert group_action["action_plan"]["candidate_ids"] == [groups["items"][0]["canonical_id"]]


@pytest.mark.asyncio
async def test_broad_area_device_query_resets_prior_semantic_constraints() -> None:
    fixture = television_fixture()
    fixture.rows.append(
        entity(
            "media_player.bedroom_audio",
            "Bedroom Audio",
            domain="media_player",
            state="idle",
            area_id="bedroom",
            area_name="Bedroom",
            device_id="bedroom-audio",
            device_name="Bedroom Audio",
            device_class="speaker",
            supported_features=1 | 4096 | 16384,
        )
    )

    result = await search(
        fixture,
        "*",
        terms=[],
        area_id="bedroom",
        capability=None,
    )
    assert result["count"] == 3
    assert {item["display_name"] for item in result["items"]} == {
        "Bedroom Audio",
        "Bedroom Bed Observer",
        "Bedroom TV",
    }

    structural_label = await search(
        fixture,
        "devices",
        terms=[
            "bedroom devices",
            "devices in bedroom",
            "things in bedroom",
            "bedroom appliances",
        ],
        area_id="bedroom",
        capability=None,
    )
    assert {item["canonical_id"] for item in structural_label["items"]} == {
        item["canonical_id"] for item in result["items"]
    }


@pytest.mark.asyncio
async def test_distinct_same_named_physical_objects_are_disambiguated_not_merged() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "media_player.first_display",
                "Studio Display",
                domain="media_player",
                state="off",
                area_id="studio",
                area_name="Studio",
                device_id="first-display",
                device_name="Studio Display",
                device_class="tv",
                model="Model One",
            ),
            entity(
                "media_player.second_display",
                "Studio Display",
                domain="media_player",
                state="off",
                area_id="studio",
                area_name="Studio",
                device_id="second-display",
                device_name="Studio Display",
                device_class="tv",
                model="Model Two",
            ),
        ]
    )

    result = await search(fixture, "tv", terms=["television"], aggregation="COUNT")

    assert result["count"] == 2
    assert {item["display_name"] for item in result["items"]} == {
        "Studio Display (Model One)",
        "Studio Display (Model Two)",
    }


@pytest.mark.asyncio
async def test_structured_and_metadata_named_endpoints_share_one_physical_collection() -> None:
    media_features = 1 | 4096 | 16384
    fixture = SemanticFixture(
        [
            entity(
                "media_player.voice_satellite",
                "Voice Satellite",
                domain="media_player",
                state="idle",
                area_id="lounge",
                area_name="Lounge",
                device_id="voice-satellite",
                device_name="Voice Satellite",
                device_class="speaker",
                supported_features=media_features,
            ),
            entity(
                "media_player.echo_endpoint",
                "Kitchen Speaker",
                domain="media_player",
                state="idle",
                area_id="kitchen",
                area_name="Kitchen",
                device_id="echo-endpoint",
                device_name="Kitchen Speaker",
                supported_features=media_features,
            ),
        ]
    )

    result = await search(fixture, "speaker", terms=["speaker"], aggregation="COUNT")

    assert result["count"] == 2
    assert {item["display_name"] for item in result["items"]} == {
        "Kitchen Speaker",
        "Lounge Voice Satellite",
    }


@pytest.mark.asyncio
async def test_observation_name_does_not_reclassify_observer_as_observed_object() -> None:
    fixture = television_fixture()
    graph = await fixture.engine.graph()
    observer = next(item for item in graph.physical_objects if "Observer" in item.display_name)
    assert observer.kind == "occupancy"
    assert {role for _, role in observer.entity_roles} == {"OBSERVATION", "MEASUREMENT"}

    result = await search(fixture, "television", terms=["tv"])
    assert "Bed Observer" not in {item["display_name"] for item in result["items"]}


@pytest.mark.asyncio
async def test_observation_only_device_name_cannot_inflate_structural_category_count() -> None:
    fixture = SemanticFixture(
        [
            entity(
                "camera.primary_view",
                "Primary View",
                domain="camera",
                state="idle",
                area_id="hall",
                area_name="Hall",
                device_id="view-device",
                device_name="Primary View",
            ),
            entity(
                "binary_sensor.observer_motion",
                "Observer Motion",
                domain="binary_sensor",
                state="off",
                area_id="hall",
                area_name="Hall",
                device_id="observer-device",
                device_name="Camera",
                device_class="motion",
                manufacturer="Synthetic",
                model="Camera observer",
            ),
        ]
    )

    result = await search(fixture, "camera", terms=["camera"], aggregation="COUNT")
    assert result["count"] == 1
    assert result["items"][0]["kind"] == "camera"


@pytest.mark.asyncio
async def test_multiple_plausible_physical_targets_require_clarification() -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        "television",
        terms=["tv"],
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )

    assert result["resolution"] == "ambiguous"
    assert result["clarification_required"] is True
    assert "action_plan" not in result


@pytest.mark.asyncio
async def test_action_plan_rejects_an_invented_or_different_canonical_target() -> None:
    fixture = television_fixture()
    discovered = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    with pytest.raises(ValueError, match="not in the grounded action plan"):
        await fixture.engine.execute(
            handle=discovered["action_plan"]["handle"],
            canonical_ids=["home:model-invented"],
            action="turn_off",
            principal_id="aaron",
            conversation_id="usr:aaron:semantic",
            request_id="request-1",
        )
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_unknown_authoritative_state_never_becomes_already_satisfied() -> None:
    fixture = television_fixture()
    fixture.rows[0]["state"] = "unknown"
    fixture.rows[0]["available"] = False
    discovered = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    result = await fixture.engine.execute(
        handle=discovered["action_plan"]["handle"],
        canonical_ids=[discovered["items"][0]["canonical_id"]],
        action="turn_off",
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )

    assert result["verification_status"] == "UNKNOWN"
    assert result["verified"] is False
    assert result["already_in_target_state"] is False
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_canonical_identity_and_evidence_project_into_working_context() -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
    )
    objects, result_set = tool_call_projection(
        intent="state_query",
        calls=[{"tool": "search_home", "arguments": {}, "result": result}],
    )

    assert len(objects) == 1
    assert objects[0].canonical_id == result["items"][0]["canonical_id"]
    assert objects[0].metadata["evidence"]
    assert result_set and result_set["object_refs"] == [objects[0].reference_id]


@pytest.mark.asyncio
async def test_count_presentation_uses_only_canonical_results() -> None:
    fixture = television_fixture()
    result = await search(
        fixture,
        "television",
        terms=["tv"],
        aggregation="COUNT",
    )
    response = render_home_search_evidence(
        [{"tool": "search_home", "arguments": {}, "result": result}],
        request_text="how many tv are in flat",
    )

    assert response and response.startswith("I found 2 matching television:")
    assert "Bedroom TV" in response
    assert "Lounge Screen" in response
    assert "occupancy" not in response.casefold()


def test_state_presentation_deduplicates_identical_canonical_statements() -> None:
    result = {
        "success": True,
        "operation": "QUERY",
        "aggregation": "STATE",
        "query": "display",
        "count": 2,
        "items": [
            {"canonical_id": "home:first", "display_name": "Lounge Display", "state": "off"},
            {"canonical_id": "home:second", "display_name": "Lounge Display", "state": "off"},
        ],
    }

    response = render_home_search_evidence(
        [{"tool": "search_home", "arguments": {}, "result": result}],
        request_text="What about the lounge display?",
    )

    assert response == "Lounge Display is off."


@pytest.mark.asyncio
async def test_structural_domain_term_cannot_broaden_semantic_count() -> None:
    fixture = television_fixture()
    fixture.rows.append(
        entity(
            "media_player.studio_speaker",
            "Studio Sound Hub",
            domain="media_player",
            state="playing",
            area_id="studio",
            area_name="Studio",
            device_id="studio-sound-hub",
            device_name="Studio Sound Hub",
            device_class="speaker",
            supported_features=1 | 4 | 8 | 4096 | 16384,
        )
    )

    result = await fixture.engine.search(
        query="televisions",
        semantic_terms=["tv", "screen", "media_player"],
        area_id=None,
        capability="state",
        domain="media_player",
        state=None,
        aggregation="COUNT",
        operation="QUERY",
        requested_action=None,
        include_diagnostics=False,
        limit=20,
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-structural-term",
    )

    assert result["count"] == 2
    assert {item["display_name"] for item in result["items"]} == {
        "Bedroom TV",
        "Lounge Screen",
    }


@pytest.mark.asyncio
async def test_room_inventory_preserves_exact_alpha39_occupancy_states() -> None:
    engine = ToolEngine.__new__(ToolEngine)

    async def query_home(
        _self: ToolEngine,
        plan: dict[str, Any],
        *,
        principal_id: str,
    ) -> dict[str, Any]:
        assert plan["predicate"] == "ANY"
        return {
            "success": True,
            "observed_at": "2026-10-10T00:00:00Z",
            "rooms": [
                {
                    "area_id": "studio",
                    "name": "Studio",
                    "occupancy_state": "LIKELY_OCCUPIED",
                    "occupancy_evidence": [],
                },
                {
                    "area_id": "hall",
                    "name": "Hall",
                    "occupancy_state": "PROBABLY_CLEAR",
                    "occupancy_evidence": [],
                },
                {
                    "area_id": "unknown",
                    "name": "Unknown Room",
                    "occupancy_state": "UNKNOWN",
                    "occupancy_evidence": [],
                },
            ],
        }

    engine.query_home = MethodType(query_home, engine)
    result = await engine.search_home(
        query="clear rooms",
        semantic_terms=["clear rooms"],
        scope="HOME",
        inventory_kind="ROOM",
        occupancy_state="PROBABLY_CLEAR",
        area_id=None,
        capability=None,
        domain=None,
        state=None,
        aggregation="LIST",
        operation="QUERY",
        requested_action=None,
        include_diagnostics=False,
        limit=20,
    )

    assert [item["display_name"] for item in result["items"]] == ["Hall"]
    assert result["occupancy_state"] == "PROBABLY_CLEAR"
    assert all(item["state"] != "UNKNOWN" for item in result["items"])


@pytest.mark.asyncio
async def test_verified_home_action_projects_grounded_explanation(tmp_path) -> None:
    fixture = television_fixture()
    fixture.rows[0]["state"] = "off"
    discovered = await search(
        fixture,
        "television",
        terms=["tv"],
        area_id="bedroom",
        capability="turn_off",
        operation="CONTROL",
        action="turn_off",
    )
    executed = await fixture.engine.execute(
        handle=discovered["action_plan"]["handle"],
        canonical_ids=[discovered["items"][0]["canonical_id"]],
        action="turn_off",
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    calls = [
        {"tool": "search_home", "arguments": {}, "result": discovered},
        {"tool": "execute_home_action", "arguments": {}, "result": executed},
    ]
    await dialogue.record_result(
        "usr:aaron:semantic",
        intent="control_now",
        success=True,
        response="already satisfied",
        calls=calls,
    )
    context_service = WorkingContextService(dialogue)
    context = await context_service.get(
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
    )
    resolution = await context_service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        query=ReferenceQuery(plural=True),
    )
    answer = ResultIntelligence.explain(context["derived_results"][0], resolution.objects)

    assert "exact canonical target" in answer
    assert "authoritative state" in answer
    assert "Bedroom TV" in answer
