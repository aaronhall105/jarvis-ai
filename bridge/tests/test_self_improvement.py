from __future__ import annotations

from pathlib import Path

import pytest

from app.self_improvement import SelfImprovementEngine
from app.user_context import UserContext


@pytest.fixture
def actor() -> UserContext:
    return UserContext.from_request(
        user_id="aaron-user-id",
        user_name="Aaron",
        user_is_admin=True,
        device_id="aaron-phone",
        voice_mode=False,
    )


@pytest.fixture
def amber() -> UserContext:
    return UserContext.from_request(
        user_id="amber-user-id",
        user_name="Amber",
        user_is_admin=False,
        device_id="amber-phone",
        voice_mode=False,
    )


@pytest.fixture
def engine(tmp_path: Path) -> SelfImprovementEngine:
    return SelfImprovementEngine(
        str(tmp_path / "improvement.db"),
        enabled=True,
        auto_prepare=True,
        repeat_threshold=2,
        latency_failure_ms=7000,
        core_version="test",
    )


@pytest.mark.asyncio
async def test_explicit_correction_records_and_queues_failure(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    conversation_id = "usr:aaron:test"
    await engine.observe_interaction(
        conversation_id=conversation_id,
        actor=actor,
        raw_text="Turn on the bedroom floor light",
        result={
            "success": True,
            "response": "Kitchen Light is now on.",
            "intent": "control_now",
            "calls": [
                {
                    "tool": "control_device",
                    "result": {
                        "success": True,
                        "entity_id": "light.kitchen_light",
                    },
                }
            ],
            "understanding": {"interpreted_text": "Turn on the bedroom floodlight"},
            "timings": {"jarvis_request_total_ms": 220},
        },
    )

    failure_id = await engine.capture_feedback_before_request(
        conversation_id=conversation_id,
        actor=actor,
        raw_text="That was wrong, I meant the bedroom floodlight.",
    )

    assert failure_id is not None
    failure = await engine.get_failure(failure_id)
    assert failure is not None
    assert failure["category"] == "device_resolution"
    assert failure["explicit"] is True
    candidates = await engine.list_candidates()
    assert len(candidates) == 1
    assert candidates[0]["status"] == "queued"


@pytest.mark.asyncio
async def test_failed_tool_is_recorded(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    await engine.observe_interaction(
        conversation_id="usr:aaron:tool-failure",
        actor=actor,
        raw_text="Turn off the bedroom light",
        result={
            "success": False,
            "response": "I couldn’t control Bedroom Light.",
            "intent": "control_now",
            "calls": [
                {
                    "tool": "control_device",
                    "result": {"success": False, "error": "unavailable"},
                }
            ],
            "timings": {"jarvis_request_total_ms": 400},
        },
    )
    failures = await engine.list_failures()
    assert len(failures) == 1
    assert failures[0]["status"] == "recorded"


@pytest.mark.asyncio
async def test_non_admin_cannot_control_improvement(
    engine: SelfImprovementEngine,
    amber: UserContext,
) -> None:
    result = await engine.handle_command(
        text="Show self-improvement status",
        actor=amber,
        conversation_id="usr:amber:test",
    )
    assert result.handled is True
    assert result.success is False
    assert "only to Aaron" in result.response


@pytest.mark.asyncio
async def test_emergency_stop_and_resume(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    stopped = await engine.handle_command(
        text="Emergency stop self-improvement",
        actor=actor,
        conversation_id="usr:aaron:test",
    )
    assert stopped.success is True
    assert await engine.enabled() is False

    resumed = await engine.handle_command(
        text="Resume self-improvement",
        actor=actor,
        conversation_id="usr:aaron:test",
    )
    assert resumed.success is True
    assert await engine.enabled() is True


@pytest.mark.asyncio
async def test_approval_code_is_required(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    source_id = await engine.observe_interaction(
        conversation_id="usr:aaron:approval",
        actor=actor,
        raw_text="Bad request",
        result={
            "success": False,
            "response": "Failed.",
            "intent": "general_error",
            "calls": [],
            "timings": {},
        },
    )
    source = await engine.get_interaction(source_id)
    assert source is not None
    failure_id = await engine.record_failure(
        source=source,
        correction="That was wrong.",
        explicit=True,
        summary="Explicit failure",
    )
    candidates = await engine.list_candidates()
    candidate_id = candidates[0]["candidate_id"]

    # Simulate the host worker completing validation.
    def mark_ready() -> None:
        with engine._connect() as connection:  # noqa: SLF001 - focused DB contract test
            connection.execute(
                """
                UPDATE improvement_candidates
                SET status = 'awaiting_approval', approval_code = '123456'
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            )

    mark_ready()
    bad = await engine.approve_candidate(candidate_id, "111111", actor.display_name)
    assert bad.success is False
    good = await engine.approve_candidate(candidate_id, "123456", actor.display_name)
    assert good.success is True
    candidate = await engine.get_candidate(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "approved"
