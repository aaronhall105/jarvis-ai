from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai_engine import (
    AIEngine,
    _home_action_turn_envelope,
    _pending_grounded_home_execution,
    _semantic_control_mode,
)
from app.connectors.base import ConnectorResult
from app.home_assistant_connector import (
    HOME_ASSISTANT_CAPABILITIES,
    HomeAssistantConnector,
)
from app.home_parameters import match_parameter
from app.home_semantic import HomeSemanticEngine
from app.user_context import UserContext


def entity(
    entity_id: str,
    *,
    name: str,
    area: str = "studio",
    state: str = "on",
    attributes: dict[str, Any] | None = None,
    supported_features: int = 0,
    device_id: str | None = None,
) -> dict[str, Any]:
    domain = entity_id.split(".", 1)[0]
    return {
        "entity_id": entity_id,
        "domain": domain,
        "name": name,
        "state": state,
        "available": state not in {"unknown", "unavailable"},
        "area_id": area,
        "area_name": area.title(),
        "device_id": device_id or entity_id,
        "device_name": name,
        "device_name_by_user": name,
        "device_identifiers": (("test", device_id or entity_id),),
        "device_connections": (),
        "supported_features": supported_features,
        "attributes": {"supported_features": supported_features, **(attributes or {})},
    }


def field(
    selector: dict[str, Any],
    *,
    supported_features: list[int] | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"selector": selector}
    filters: dict[str, Any] = {}
    if supported_features:
        filters["supported_features"] = supported_features
    if attributes:
        filters["attribute"] = attributes
    if filters:
        result["filter"] = filters
    return result


def services() -> list[dict[str, Any]]:
    percent = {"number": {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"}}
    return [
        {
            "domain": "light",
            "services": {
                "turn_on": {
                    "fields": {
                        "brightness_pct": field(
                            percent,
                            attributes={"supported_color_modes": ["brightness"]},
                        ),
                        "brightness_step_pct": field(
                            {
                                "number": {
                                    "min": -100,
                                    "max": 100,
                                    "step": 1,
                                    "unit_of_measurement": "%",
                                }
                            },
                            attributes={"supported_color_modes": ["brightness"]},
                        ),
                    },
                    "target": {"entity": [{"domain": ["light"]}]},
                }
            },
        },
        {
            "domain": "media_player",
            "services": {
                "volume_set": {
                    "fields": {
                        "volume_level": field(
                            {"number": {"min": 0, "max": 1, "step": 0.01}},
                            supported_features=[4],
                        )
                    },
                    "target": {"entity": [{"domain": ["media_player"], "supported_features": [4]}]},
                }
            },
        },
        {
            "domain": "fan",
            "services": {
                "set_percentage": {
                    "fields": {"percentage": field(percent, supported_features=[1])},
                    "target": {"entity": [{"domain": ["fan"], "supported_features": [1]}]},
                }
            },
        },
        {
            "domain": "climate",
            "services": {
                "set_temperature": {
                    "fields": {
                        "temperature": field(
                            {"number": {"min": 0, "max": 250, "step": 0.1}},
                            supported_features=[1],
                        )
                    },
                    "target": {"entity": [{"domain": ["climate"], "supported_features": [1]}]},
                }
            },
        },
        {
            "domain": "cover",
            "services": {
                "set_cover_position": {
                    "fields": {"position": field(percent, supported_features=[4])},
                    "target": {"entity": [{"domain": ["cover"], "supported_features": [4]}]},
                }
            },
        },
        {
            "domain": "number",
            "services": {
                "set_value": {
                    "fields": {"value": field({"text": {}})},
                    "target": {"entity": [{"domain": ["number"]}]},
                }
            },
        },
        {
            "domain": "select",
            "services": {
                "select_option": {
                    "fields": {"option": field({"state": {}})},
                    "target": {"entity": [{"domain": ["select"]}]},
                }
            },
        },
    ]


class FakeHome:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = deepcopy(rows)
        self.calls: list[dict[str, Any]] = []
        self.schema_calls = 0

    async def states(self) -> list[dict[str, Any]]:
        return deepcopy(self.rows)

    async def schemas(self) -> list[dict[str, Any]]:
        self.schema_calls += 1
        return services()

    async def call_service(
        self,
        *,
        domain: str,
        service: str,
        entity_ids: list[str],
        service_data: dict[str, Any],
    ) -> None:
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "entity_ids": entity_ids,
                "service_data": service_data,
            }
        )
        for row in self.rows:
            if row["entity_id"] not in entity_ids:
                continue
            attrs = row["attributes"]
            if "brightness_pct" in service_data:
                row["state"] = "on" if service_data["brightness_pct"] else "off"
                attrs["brightness"] = round(float(service_data["brightness_pct"]) * 255 / 100)
            elif "brightness_step_pct" in service_data:
                current = round(float(attrs.get("brightness") or 0) * 100 / 255)
                target = max(0, min(100, current + float(service_data["brightness_step_pct"])))
                row["state"] = "on" if target else "off"
                attrs["brightness"] = round(target * 255 / 100)
            elif "volume_level" in service_data:
                attrs["volume_level"] = service_data["volume_level"]
            elif domain == "climate" and "temperature" in service_data:
                attrs["temperature"] = service_data["temperature"]
            elif domain == "fan" and "percentage" in service_data:
                attrs["percentage"] = service_data["percentage"]
            elif domain == "cover" and "position" in service_data:
                attrs["current_position"] = service_data["position"]
            elif domain == "number" and "value" in service_data:
                row["state"] = str(service_data["value"])
            elif domain == "select" and "option" in service_data:
                row["state"] = str(service_data["option"])

    def engine(self) -> HomeSemanticEngine:
        return HomeSemanticEngine(
            state_loader=self.states,
            service_caller=self.call_service,
            service_schema_loader=self.schemas,
        )


async def parameter_plan(
    engine: HomeSemanticEngine,
    *,
    query: str,
    area: str | None,
    parameter: str,
    value: float | str | None,
    mode: str = "ABSOLUTE",
    direction: str = "NONE",
    selection: str = "SINGLE",
) -> dict[str, Any]:
    return await engine.search(
        query=query,
        semantic_terms=(),
        area_id=area,
        inventory_kind="CAPABILITY",
        capability_role_filter="ANY",
        operation="CONTROL",
        aggregation="LIST",
        requested_parameter=parameter,
        parameter_mode=mode,
        parameter_direction=direction,
        parameter_value=value,
        parameter_unit="%"
        if parameter in {"brightness", "volume", "fan speed", "position"}
        else None,
        target_selection=selection,
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )


@pytest.mark.asyncio
async def test_live_schema_projects_standard_parameter_capabilities_and_caches() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 128},
            ),
            entity(
                "media_player.studio",
                name="Studio Audio",
                attributes={"volume_level": 0.25},
                supported_features=4,
            ),
            entity(
                "fan.studio",
                name="Studio Airflow",
                attributes={"percentage": 30},
                supported_features=1,
            ),
            entity(
                "climate.studio",
                name="Studio Heating",
                attributes={
                    "temperature": 20,
                    "min_temp": 7,
                    "max_temp": 35,
                    "target_temp_step": 0.5,
                    "temperature_unit": "°C",
                },
                supported_features=1,
            ),
            entity(
                "cover.studio",
                name="Studio Shade",
                attributes={"current_position": 70},
                supported_features=4,
            ),
            entity(
                "number.studio",
                name="Aether Bloom Limit",
                state="60",
                attributes={"min": 20, "max": 90, "step": 5},
            ),
            entity(
                "select.studio",
                name="Aether Bloom Mode",
                state="Normal",
                attributes={"options": ["Normal", "Eco", "Boost"]},
            ),
        ]
    )
    engine = home.engine()
    first = await engine.graph()
    second = await engine.graph()
    assert home.schema_calls == 1
    assert len(first.physical_objects) == len(second.physical_objects) == 7
    projected = {
        parameter.parameter
        for item in first.physical_objects
        for parameter in item.parameterized_capabilities
    }
    assert {
        "brightness",
        "volume",
        "fan_percentage",
        "target_temperature",
        "position",
        "value",
        "option",
    } <= projected


@pytest.mark.asyncio
async def test_service_schema_cache_loads_during_fresh_process_uptime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 128},
            )
        ]
    )
    monkeypatch.setattr("app.home_semantic.time.monotonic", lambda: 42.0)

    graph = await home.engine().graph()

    assert home.schema_calls == 1
    assert graph.physical_objects[0].parameterized_capabilities


@pytest.mark.asyncio
async def test_service_schema_failure_does_not_break_existing_home_inventory() -> None:
    rows = [entity("light.studio", name="Studio Light")]

    async def failed_schemas() -> list[dict[str, Any]]:
        raise RuntimeError("temporary HA metadata failure")

    engine = HomeSemanticEngine(
        state_loader=AsyncMock(return_value=rows),
        service_caller=AsyncMock(),
        service_schema_loader=failed_schemas,
    )
    graph = await engine.graph()
    assert len(graph.physical_objects) == 1
    assert "turn_on" in graph.physical_objects[0].capabilities
    assert graph.physical_objects[0].parameterized_capabilities == ()


@pytest.mark.asyncio
async def test_absolute_brightness_uses_opaque_plan_and_verified_readback() -> None:
    home = FakeHome(
        [
            entity(
                "light.living_room_ceiling",
                name="Living Room Lights",
                area="living_room",
                state="off",
                attributes={"supported_color_modes": ["brightness"], "brightness": None},
            )
        ]
    )
    engine = home.engine()
    search = await parameter_plan(
        engine,
        query="light",
        area="living_room",
        parameter="brightness",
        value=30,
    )
    assert search["count"] == 1
    assert search["action_plan"]["parameter"] == "brightness"
    canonical_ids = search["action_plan"]["candidate_ids"]
    result = await engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=canonical_ids,
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "VERIFIED"
    assert result["outcomes"][0]["after_value"] == pytest.approx(30, abs=1)
    assert home.calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "entity_ids": ["light.living_room_ceiling"],
            "service_data": {"brightness_pct": 30.0},
        }
    ]


@pytest.mark.asyncio
async def test_non_dimmable_light_fails_without_a_service_call() -> None:
    home = FakeHome(
        [
            entity(
                "light.porch",
                name="Porch Light",
                attributes={"supported_color_modes": ["onoff"]},
            )
        ]
    )
    engine = home.engine()
    search = await parameter_plan(
        engine, query="light", area="studio", parameter="brightness", value=30
    )
    result = await engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "FAILED"
    assert result["outcomes"][0]["status"] == "UNSUPPORTED"
    assert home.calls == []


@pytest.mark.asyncio
async def test_relative_and_qualitative_brightness_use_bounded_generic_policy() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 102},
            )
        ]
    )
    engine = home.engine()
    relative = await parameter_plan(
        engine,
        query="light",
        area="studio",
        parameter="brightness",
        value=20,
        mode="RELATIVE",
        direction="INCREASE",
    )
    result = await engine.execute_parameter(
        handle=relative["action_plan"]["handle"],
        canonical_ids=relative["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["outcomes"][0]["after_value"] == pytest.approx(60, abs=1)
    qualitative = await parameter_plan(
        engine,
        query="light",
        area="studio",
        parameter="brightness",
        value=None,
        mode="RELATIVE",
        direction="DECREASE",
    )
    result = await engine.execute_parameter(
        handle=qualitative["action_plan"]["handle"],
        canonical_ids=qualitative["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["outcomes"][0]["qualitative_delta"] == 10
    assert result["outcomes"][0]["after_value"] == pytest.approx(50, abs=1)


@pytest.mark.asyncio
async def test_out_of_range_value_is_rejected_not_clamped() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 100},
            )
        ]
    )
    engine = home.engine()
    search = await parameter_plan(
        engine, query="light", area="studio", parameter="brightness", value=170
    )
    result = await engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "FAILED"
    assert "at most 100%" in result["response_message"]
    assert home.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "query", "parameter", "value", "expected_field", "expected_value"),
    [
        (
            entity(
                "media_player.studio",
                name="Studio Audio",
                attributes={"volume_level": 0.2},
                supported_features=4,
            ),
            "audio",
            "volume",
            35,
            "volume_level",
            0.35,
        ),
        (
            entity(
                "fan.studio",
                name="Studio Airflow",
                attributes={"percentage": 20},
                supported_features=1,
            ),
            "airflow",
            "fan speed",
            40,
            "percentage",
            40,
        ),
        (
            entity(
                "climate.studio",
                name="Studio Heating",
                attributes={
                    "temperature": 19,
                    "min_temp": 7,
                    "max_temp": 35,
                    "target_temp_step": 0.5,
                    "temperature_unit": "°C",
                },
                supported_features=1,
            ),
            "heating",
            "temperature",
            21,
            "temperature",
            21,
        ),
        (
            entity(
                "cover.studio",
                name="Studio Shade",
                attributes={"current_position": 20},
                supported_features=4,
            ),
            "shade",
            "position",
            50,
            "position",
            50,
        ),
    ],
)
async def test_parameter_architecture_generalizes_across_standard_ha_domains(
    row: dict[str, Any],
    query: str,
    parameter: str,
    value: float,
    expected_field: str,
    expected_value: float,
) -> None:
    home = FakeHome([row])
    engine = home.engine()
    search = await parameter_plan(
        engine, query=query, area="studio", parameter=parameter, value=value
    )
    result = await engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "VERIFIED"
    assert home.calls[0]["service_data"] == {expected_field: expected_value}


@pytest.mark.asyncio
async def test_number_step_and_select_options_are_grounded_in_entity_metadata() -> None:
    number_home = FakeHome(
        [
            entity(
                "number.aether_bloom_limit",
                name="Aether Bloom",
                state="50",
                attributes={"min": 20, "max": 90, "step": 5},
            )
        ]
    )
    number_engine = number_home.engine()
    search = await parameter_plan(
        number_engine, query="aether bloom", area="studio", parameter="limit", value=83
    )
    result = await number_engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["outcomes"][0]["requested_value"] == 85
    assert result["outcomes"][0]["normalized_to_step"] is True

    select_home = FakeHome(
        [
            entity(
                "select.aether_bloom_mode",
                name="Aether Bloom",
                state="Normal",
                attributes={"options": ["Normal", "Eco", "Boost"]},
            )
        ]
    )
    select_engine = select_home.engine()
    search = await parameter_plan(
        select_engine, query="aether bloom", area="studio", parameter="mode", value="eco"
    )
    result = await select_engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "VERIFIED"
    assert select_home.calls[0]["service_data"] == {"option": "Eco"}


@pytest.mark.asyncio
async def test_multi_target_parameter_action_reports_partial_support() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio_dimmable",
                name="Studio Dimmable",
                attributes={"supported_color_modes": ["brightness"], "brightness": 255},
            ),
            entity(
                "light.studio_basic",
                name="Studio Basic",
                attributes={"supported_color_modes": ["onoff"]},
            ),
        ]
    )
    engine = home.engine()
    search = await parameter_plan(
        engine,
        query="light",
        area="studio",
        parameter="brightness",
        value=40,
        selection="ALL_MATCHES",
    )
    assert len(search["action_plan"]["candidate_ids"]) == 2
    result = await engine.execute_parameter(
        handle=search["action_plan"]["handle"],
        canonical_ids=search["action_plan"]["candidate_ids"],
        principal_id="aaron",
        conversation_id="conversation",
        request_id="request",
    )
    assert result["verification_status"] == "PARTIAL"
    assert {value["status"] for value in result["outcomes"]} == {"VERIFIED", "UNSUPPORTED"}
    assert len(home.calls) == 1


@pytest.mark.asyncio
async def test_parameter_query_exposes_compact_current_value_without_execution_ids() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 128},
            )
        ]
    )
    engine = home.engine()
    result = await engine.search(
        query="light",
        semantic_terms=(),
        area_id="studio",
        inventory_kind="CAPABILITY",
        operation="QUERY",
        aggregation="STATE",
        requested_parameter="brightness",
    )
    assert result["count"] == 1
    parameter = match_parameter(
        (await engine.graph()).capabilities[0].parameterized_capabilities, "brightness"
    )
    assert parameter is not None
    assert parameter.current_value == pytest.approx(50, abs=1)
    public = result["items"][0]["parameterized_capabilities"][0]
    assert public["current_value"] == pytest.approx(50, abs=1)
    assert "entity_id" not in public
    assert "service" not in public


@pytest.mark.asyncio
async def test_parameter_executor_rejects_modified_frozen_target_set() -> None:
    home = FakeHome(
        [
            entity(
                "light.studio",
                name="Studio Light",
                attributes={"supported_color_modes": ["brightness"], "brightness": 128},
            )
        ]
    )
    engine = home.engine()
    search = await parameter_plan(
        engine, query="light", area="studio", parameter="brightness", value=30
    )
    with pytest.raises(ValueError, match="exact frozen"):
        await engine.execute_parameter(
            handle=search["action_plan"]["handle"],
            canonical_ids=["invented:entity"],
            principal_id="aaron",
            conversation_id="conversation",
            request_id="request",
        )
    assert home.calls == []


@pytest.mark.asyncio
async def test_llm_tools_expose_semantic_parameter_intent_but_not_ha_execution_fields() -> None:
    engine = AIEngine.__new__(AIEngine)
    engine.registry = SimpleNamespace(areas=AsyncMock(return_value=[]))
    engine.tools = SimpleNamespace(
        READABLE_DOMAINS={"light", "number", "select"},
        MEDIA_SHORTCUTS={},
        MEDIA_PLAYER_ENTITIES={},
        MEDIA_ACTION_SERVICES={},
        NOTIFICATION_SERVICES={},
        ANNOUNCEMENT_TARGETS={},
    )
    actor = UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        device_id="test",
        voice_mode=False,
    )
    read_tools = await engine._home_read_tools(actor)
    search = next(item for item in read_tools if item["name"] == "search_home")
    properties = search["parameters"]["properties"]
    assert properties["control_mode"]["enum"] == [
        "NONE",
        "DISCRETE",
        "PARAMETERIZED",
    ]
    assert "control_mode" in search["parameters"]["required"]
    assert properties["requested_control"]["type"] == ["string", "null"]
    assert "requested_action" not in properties
    assert "requested_parameter" not in properties
    assert properties["parameter_mode"]["enum"] == ["NONE", "ABSOLUTE", "RELATIVE"]
    assert "entity_id" not in properties
    assert "service" not in properties
    assert "service_data" not in properties

    control_tools = await engine._home_control_tools(actor=actor)
    execute = next(item for item in control_tools if item["name"] == "execute_home_parameter")
    assert set(execute["parameters"]["properties"]) == {"handle", "canonical_ids"}


@pytest.mark.parametrize(
    ("arguments", "expected_mode"),
    [
        (
            {
                "semantic_operation": "CONTROL",
                "control_mode": "PARAMETERIZED",
                "requested_control": "level",
                "parameter_mode": "ABSOLUTE",
                "parameter_value": 30,
            },
            "PARAMETERIZED",
        ),
        (
            {
                "semantic_operation": "CONTROL",
                "control_mode": "DISCRETE",
                "requested_control": "turn_off",
                "parameter_mode": "NONE",
                "parameter_value": None,
            },
            "DISCRETE",
        ),
        (
            {
                "semantic_operation": "CONTROL",
                "control_mode": "PARAMETERIZED",
                "requested_control": "level",
                "parameter_mode": "RELATIVE",
                "parameter_value": 10,
            },
            "PARAMETERIZED",
        ),
    ],
)
def test_semantic_control_mode_accepts_exactly_one_coherent_branch(
    arguments: dict[str, Any], expected_mode: str
) -> None:
    mode, error = _semantic_control_mode(arguments)
    assert mode == expected_mode
    assert error is None


def test_semantic_control_mode_rejects_genuinely_conflicting_dual_plan() -> None:
    mode, error = _semantic_control_mode(
        {
            "semantic_operation": "CONTROL",
            "control_mode": "PARAMETERIZED",
            "requested_action": "turn_off",
            "requested_parameter": "level",
            "parameter_mode": "ABSOLUTE",
            "parameter_value": 30,
        }
    )
    assert mode == "PARAMETERIZED"
    assert error == "A parameterized control requires only a semantic parameter."


def test_exact_grounded_parameter_plan_gets_one_bounded_execution_continuation() -> None:
    calls = [
        {
            "tool": "search_home",
            "result": {
                "success": True,
                "operation": "CONTROL",
                "resolution": "unique",
                "clarification_required": False,
                "action_plan": {
                    "handle": "opaque",
                    "parameter": "level",
                    "candidate_ids": ["capability:studio"],
                },
            },
        }
    ]
    assert _pending_grounded_home_execution(calls) == "execute_home_parameter"
    assert (
        _pending_grounded_home_execution(
            [
                *calls,
                {
                    "tool": "execute_home_parameter",
                    "result": {"success": True, "verification_status": "VERIFIED"},
                },
            ]
        )
        is None
    )


def test_ambiguous_grounded_control_never_forces_execution() -> None:
    assert (
        _pending_grounded_home_execution(
            [
                {
                    "tool": "search_home",
                    "result": {
                        "success": True,
                        "operation": "CONTROL",
                        "resolution": "ambiguous",
                        "clarification_required": True,
                        "action_plan": {
                            "handle": "opaque",
                            "parameter": "level",
                            "candidate_ids": ["one", "two"],
                        },
                    },
                }
            ]
        )
        is None
    )


def test_historical_receipt_status_is_not_current_turn_action_status() -> None:
    envelope = _home_action_turn_envelope(
        [
            {
                "tool": "explain_home_action_receipt",
                "result": {
                    "success": True,
                    "referenced_action_status": "VERIFIED",
                    "action_attempted": False,
                },
            }
        ]
    )
    assert envelope == {
        "home_action_attempted": False,
        "home_action_status": None,
        "referenced_home_action_status": "VERIFIED",
    }


@pytest.mark.asyncio
async def test_parameter_verification_evidence_is_retained_in_action_receipt_contract() -> None:
    connector = HomeAssistantConnector.__new__(HomeAssistantConnector)
    metadata = next(
        item
        for item in HOME_ASSISTANT_CAPABILITIES
        if item.capability_id == "homeassistant.control"
    )
    result = ConnectorResult.succeeded(
        {
            "verified": True,
            "canonical_ids": ["capability:test"],
            "action": "set_parameter",
            "parameter": "brightness",
            "parameter_mode": "ABSOLUTE",
            "requested_value": 30,
            "requested_unit": "%",
            "verification_status": "VERIFIED",
            "outcomes": [{"after_value": 30, "verified": True}],
        }
    )
    verification = await connector.verify(metadata, SimpleNamespace(), result)
    assert verification.evidence["parameter"] == "brightness"
    assert verification.evidence["requested_value"] == 30
    assert verification.evidence["outcomes"][0]["after_value"] == 30


@pytest.mark.asyncio
async def test_partial_parameter_receipt_keeps_specific_response_message() -> None:
    engine = AIEngine.__new__(AIEngine)
    engine.external_runtime = SimpleNamespace(
        execute=AsyncMock(
            return_value={
                "success": False,
                "accepted": True,
                "status": "accepted_unverified",
                "provider_reference": None,
                "receipt": {"action_id": "receipt"},
                "data": {
                    "success": False,
                    "verification_status": "PARTIAL",
                    "response_message": "Verified 1 of 2 parameter changes.",
                },
            }
        )
    )
    actor = UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        device_id="test",
        voice_mode=False,
    )
    result = await engine._execute_registered_home_action(
        capability_id="homeassistant.control",
        operation="execute_home_parameter",
        arguments={"handle": "opaque", "canonical_ids": ["capability:test"]},
        conversation_id="conversation",
        actor=actor,
        request_id="request",
        target=["capability:test"],
    )
    assert result["response_message"] == "Verified 1 of 2 parameter changes."
