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
        "entity_category": None,
        "device_manufacturer": manufacturer,
        "device_model": model,
        "device_connections": ([["mac", connection]] if connection else []),
        "device_identifiers": [["fixture", device_id]],
        "supported_features": supported_features,
        "attributes": {},
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
) -> dict[str, Any]:
    return await fixture.engine.search(
        query=query,
        semantic_terms=terms,
        area_id=area_id,
        capability=capability,
        domain=None,
        state=None,
        aggregation=aggregation,
        operation=operation,
        requested_action=action,
        include_diagnostics=False,
        limit=20,
        principal_id="aaron",
        conversation_id="usr:aaron:semantic",
        request_id="request-1",
    )


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
