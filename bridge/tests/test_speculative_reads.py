from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.speculative_reads import (
    CORE_TOOL_EFFECTS,
    ProvisionalReadPlan,
    SemanticReadIdentity,
    SpeculationDisposition,
    SpeculativeReadCoordinator,
    ToolEffect,
    ToolEffectRegistry,
    classify_core_tools,
)


def identity(
    *,
    tool: str = "home_state",
    target: str = "sensor.alpha",
    generation: int = 7,
    operation: str = "READ",
    scope: str = "HOME",
) -> SemanticReadIdentity:
    return SemanticReadIdentity(
        session_id="session-1",
        conversation_id="conversation-1",
        generation=generation,
        task_family="HOME",
        semantic_operation=operation,
        tool_name=tool,
        arguments={"target": target},
        reference_scope=scope,
        provider_identity="home-assistant",
    )


def plan(**overrides: Any) -> ProvisionalReadPlan:
    return ProvisionalReadPlan(
        identity=identity(**overrides),
        input_version=2,
        effect=ToolEffect.READ_ONLY_EXTERNAL,
    )


@pytest.mark.asyncio
async def test_matching_final_plan_reuses_result_without_duplicate_read() -> None:
    invocations: list[dict[str, Any]] = []

    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append(arguments)
        return {"success": True, "state": "on"}

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read, provider="home-assistant")
    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)

    await coordinator.start(plan())
    await asyncio.sleep(0)
    resolution = await coordinator.resolve(identity(), wait_for_in_flight=True)

    assert resolution.disposition is SpeculationDisposition.REUSED
    assert resolution.result == {"success": True, "state": "on"}
    assert invocations == [{"target": "sensor.alpha"}]


@pytest.mark.asyncio
async def test_target_and_tool_mismatches_are_never_reused() -> None:
    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        return {"arguments": arguments}

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read)
    registry.register_read("weather_read", read)
    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
    await coordinator.start(plan())
    await asyncio.sleep(0)

    target = await coordinator.resolve(identity(target="sensor.beta"))
    assert target.disposition is SpeculationDisposition.MISMATCH

    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
    await coordinator.start(plan())
    await asyncio.sleep(0)
    tool = await coordinator.resolve(identity(tool="weather_read"))
    assert tool.disposition is SpeculationDisposition.MISMATCH


@pytest.mark.asyncio
async def test_generation_and_reference_scope_mismatch_fall_back() -> None:
    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        return dict(arguments)

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read)
    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
    await coordinator.start(plan())
    await asyncio.sleep(0)

    generation = await coordinator.resolve(identity(generation=8))
    assert generation.disposition is SpeculationDisposition.GENERATION_MISMATCH
    scope = await coordinator.resolve(identity(scope="CURRENT_OBJECT"))
    assert scope.disposition is SpeculationDisposition.MISMATCH


@pytest.mark.asyncio
async def test_write_unknown_and_malicious_wrapper_cannot_reach_executor() -> None:
    write_invocations = 0

    async def write(arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal write_invocations
        write_invocations += 1
        return dict(arguments)

    registry = ToolEffectRegistry()
    registry.classify("home_write", ToolEffect.WRITE, speculation_enabled=True)
    registry.classify("unknown_tool", ToolEffect.UNKNOWN, speculation_enabled=True)
    with pytest.raises(ValueError):
        registry.register_read("malicious_read_wrapper", write, effect=ToolEffect.WRITE)

    projection = registry.read_projection()
    assert projection.names == frozenset()
    with pytest.raises(PermissionError):
        await projection.execute("home_write", {"state": "on"})
    assert write_invocations == 0


@pytest.mark.asyncio
async def test_every_representative_write_family_is_absent_from_read_projection() -> None:
    registry = ToolEffectRegistry()
    classify_core_tools(registry)
    projection = registry.read_projection()
    representative_writes = {
        "control_device",  # Home
        "prepare_gmail_message",  # mail
        "create_external_monitor",  # scheduler/monitor
        "create_personal_plan",  # task planning
        "propose_admin_change",  # configuration/calendar-class mutation policy
        "forget_memory",  # destructive
        "google_integration",  # unknown effect wrapper
    }
    for name in representative_writes:
        with pytest.raises(PermissionError):
            await projection.execute(name, {"malicious": True})
    assert representative_writes <= CORE_TOOL_EFFECTS.keys()


@pytest.mark.asyncio
async def test_mixed_or_write_semantics_are_rejected_before_invocation() -> None:
    invocations = 0

    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal invocations
        invocations += 1
        return dict(arguments)

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read)
    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
    rejected = await coordinator.start(plan(operation="CONTROL"))
    assert rejected is None
    assert invocations == 0


@pytest.mark.asyncio
async def test_multiple_partial_updates_have_one_generation_budget() -> None:
    gate = asyncio.Event()
    invocations = 0

    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal invocations
        invocations += 1
        await gate.wait()
        return dict(arguments)

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read)
    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)

    assert coordinator.reserve_planning("session-1", 7) is True
    assert coordinator.reserve_planning("session-1", 7) is False
    first = await coordinator.start(plan())
    second = await coordinator.start(plan())
    assert first is second
    gate.set()
    await asyncio.sleep(0)
    assert invocations == 1


@pytest.mark.asyncio
async def test_provider_error_and_timeout_leave_authoritative_fallback_available() -> None:
    async def broken(arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("provider detail must remain bounded")

    async def slow(arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(1)
        return dict(arguments)

    for callback, tool in ((broken, "broken"), (slow, "slow")):
        registry = ToolEffectRegistry()
        registry.register_read(tool, callback)
        coordinator = SpeculativeReadCoordinator(
            registry.read_projection(), enabled=True, timeout_seconds=0.01
        )
        provisional = ProvisionalReadPlan(
            identity=identity(tool=tool),
            input_version=1,
            effect=ToolEffect.READ_ONLY_EXTERNAL,
        )
        await coordinator.start(provisional)
        await asyncio.sleep(0.02)
        resolution = await coordinator.resolve(identity(tool=tool))
        assert resolution.disposition is SpeculationDisposition.NOT_READY
        assert resolution.result is None


@pytest.mark.asyncio
async def test_ttl_expiry_and_session_invalidation_prevent_stale_reuse() -> None:
    now = [100.0]

    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        return dict(arguments)

    registry = ToolEffectRegistry()
    registry.register_read("home_state", read)
    coordinator = SpeculativeReadCoordinator(
        registry.read_projection(), enabled=True, ttl_seconds=1, clock=lambda: now[0]
    )
    await coordinator.start(plan())
    await asyncio.sleep(0)
    now[0] = 102.0
    expired = await coordinator.resolve(identity())
    assert expired.disposition is SpeculationDisposition.EXPIRED

    coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
    await coordinator.start(plan())
    coordinator.invalidate_session("session-1", generation=8)
    invalidated = await coordinator.resolve(identity())
    assert invalidated.disposition is SpeculationDisposition.FALLBACK


def test_fingerprint_is_semantic_canonical_and_contains_no_raw_transcript() -> None:
    left = SemanticReadIdentity(
        session_id="s",
        conversation_id="c",
        generation=1,
        task_family="home",
        semantic_operation="query",
        tool_name="read",
        arguments={"b": 2.0, "a": ["x"]},
        provider_identity="provider",
    )
    right = SemanticReadIdentity(
        session_id="s",
        conversation_id="c",
        generation=1,
        task_family="HOME",
        semantic_operation="QUERY",
        tool_name="read",
        arguments={"a": ["x"], "b": 2},
        provider_identity="provider",
    )
    assert left.fingerprint == right.fingerprint
    assert "x" not in left.fingerprint
    assert len(left.fingerprint) == 64


def test_unclassified_tools_default_to_unknown_and_feature_flag_is_off() -> None:
    registry = ToolEffectRegistry()
    assert registry.definition("future_tool").effect is ToolEffect.UNKNOWN
    coordinator = SpeculativeReadCoordinator(registry.read_projection())
    assert coordinator.reserve_planning("session", 1) is False
    assert coordinator.snapshot()["enabled"] is False
