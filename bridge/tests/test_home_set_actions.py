from __future__ import annotations

import json

import pytest

from app.ai_engine import AIEngine
from app.dialogue_manager import DialogueManager
from app.home_intelligence import HomeIntelligenceEngine, HomeQueryPlan
from app.user_context import UserContext
from app.working_context import WorkingContextService, tool_call_projection


class _Tools:
    SAFE_CONTROL_DOMAINS = {"light", "switch"}

    def __init__(self, entity_ids: tuple[str, ...]) -> None:
        self.entity_ids = entity_ids

    async def controllable_devices(self):
        return [
            {
                "entity_id": entity_id,
                "domain": entity_id.partition(".")[0],
            }
            for entity_id in self.entity_ids
        ]

    async def query_home(self, plan, *, principal_id="aaron"):
        assert principal_id == "aaron"
        return {"success": True, "query_plan": dict(plan), "entities": []}


class _Registry:
    async def areas(self):
        return []


class _Runtime:
    def __init__(self, outcomes: dict[str, str]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def execute(self, _capability_id, payload, **_kwargs):
        entity_id = str(payload["entity_id"])
        self.calls.append(entity_id)
        outcome = self.outcomes.get(entity_id, "verified")
        if outcome == "failed":
            return {
                "success": False,
                "accepted": False,
                "status": "failed",
                "data": {"entity_id": entity_id, "verified": False},
            }
        if outcome == "unknown":
            return {
                "success": False,
                "accepted": True,
                "status": "outcome_unknown",
                "data": {"entity_id": entity_id, "verified": False},
            }
        return {
            "success": True,
            "accepted": True,
            "status": "verified",
            "data": {"entity_id": entity_id, "verified": True},
            "receipt": {"status": "verified"},
        }


def _actor() -> UserContext:
    return UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        device_id=None,
        voice_mode=False,
    )


async def _project_set(tmp_path) -> DialogueManager:
    states = (
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
            "state": "on",
            "available": True,
        },
    )

    async def areas():
        return (
            {"area_id": "hallway", "name": "Hallway"},
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "bedroom", "name": "Bedroom"},
        )

    async def live_states():
        return states

    plan = HomeQueryPlan.from_mapping(
        {
            "scope": "HOME",
            "category": "lights",
            "predicate": "ON",
            "aggregation": "LIST",
        }
    )
    result = (
        await HomeIntelligenceEngine(area_loader=areas, state_loader=live_states).query(plan)
    ).as_result()
    objects, result_set = tool_call_projection(
        intent="state_query",
        calls=[{"tool": "query_home", "result": result}],
    )
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    await WorkingContextService(dialogue).project(
        principal_id="aaron",
        conversation_id="usr:aaron:home",
        objects=objects,
        result_set=result_set,
        focus_refs=[item.reference_id for item in objects],
    )
    return dialogue


def _engine(dialogue: DialogueManager, tools: _Tools, runtime: _Runtime) -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine.dialogue = dialogue
    engine.tools = tools
    engine.external_runtime = runtime
    engine.code_awareness = None
    engine.registry = _Registry()
    return engine


async def _execute(engine: AIEngine, text: str, selection: str, exclusions=()):
    return await engine._execute_function(
        name="control_referenced_set",
        arguments_json=json.dumps(
            {
                "action": "turn_off",
                "selection": selection,
                "exclude_names": list(exclusions),
            }
        ),
        user_text=text,
        authorised_tools={"control_referenced_set"},
        conversation_id="usr:aaron:home",
        actor=_actor(),
        request_id="set-action",
    )


@pytest.mark.asyncio
async def test_exact_referenced_set_and_set_subtraction_are_frozen(tmp_path) -> None:
    engine = _engine(
        await _project_set(tmp_path),
        _Tools(("light.hallway", "light.living_room", "light.bedroom")),
        _Runtime({}),
    )

    result = await _execute(
        engine,
        "Leave the hallway one on and turn the rest off",
        "REST_EXCLUDING",
        ("hallway",),
    )

    assert result["result"]["requested_entity_ids"] == [
        "light.bedroom",
        "light.living_room",
    ]
    assert set(engine.external_runtime.calls) == {
        "light.bedroom",
        "light.living_room",
    }
    assert result["result"]["verified_count"] == 2


@pytest.mark.asyncio
async def test_partial_set_write_never_claims_complete_success(tmp_path) -> None:
    engine = _engine(
        await _project_set(tmp_path),
        _Tools(("light.hallway", "light.living_room", "light.bedroom")),
        _Runtime({"light.bedroom": "failed", "light.hallway": "unknown"}),
    )

    result = await _execute(engine, "Turn those off", "ALL")

    assert result["result"]["success"] is False
    assert result["result"]["verified_count"] == 1
    assert result["result"]["failed_count"] == 1
    assert result["result"]["unknown_count"] == 1
    assert "confirmed 1 of 3" in result["result"]["response_message"]


@pytest.mark.asyncio
async def test_stale_member_and_unreferenced_broad_action_fail_closed(tmp_path) -> None:
    engine = _engine(
        await _project_set(tmp_path),
        _Tools(("light.hallway", "light.living_room")),
        _Runtime({}),
    )

    stale = await _execute(engine, "Turn those off", "ALL")
    assert stale["result"]["success"] is False
    assert stale["result"]["error"]["code"] == "stale_or_unsupported_grounded_set"
    assert engine.external_runtime.calls == []

    broad = await _execute(engine, "Turn all lights off", "ALL")
    assert broad["result"]["success"] is False
    assert broad["result"]["error"]["code"] == "explicit_grounded_set_reference_required"
    assert engine.external_runtime.calls == []


@pytest.mark.asyncio
async def test_referenced_set_read_uses_only_durable_grounded_identities(tmp_path) -> None:
    engine = _engine(
        await _project_set(tmp_path),
        _Tools(("light.hallway", "light.living_room", "light.bedroom")),
        _Runtime({}),
    )
    engine.external_runtime = None

    result = await engine._execute_function(
        name="query_home",
        arguments_json=json.dumps(
            {
                "operation": "QUERY",
                "scope": "REFERENCED_ENTITY_SET",
                "category": "lights",
                "predicate": "ON",
                "aggregation": "LIST",
                "area_id": None,
            }
        ),
        user_text="Which of those are still on?",
        authorised_tools={"query_home"},
        conversation_id="usr:aaron:home",
        actor=_actor(),
        request_id="referenced-read",
    )

    plan = result["result"]["query_plan"]
    assert plan["scope"] == "REFERENCED_ENTITY_SET"
    assert plan["entity_ids"] == (
        "light.bedroom",
        "light.hallway",
        "light.living_room",
    )
    assert plan["reference_result_set_id"]
