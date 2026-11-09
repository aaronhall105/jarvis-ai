"""Regression coverage for explanation-question feedback attribution.

These tests deliberately create no production tasks, approvals or home actions.
"""

from __future__ import annotations

import pytest

from app.self_improvement import SelfImprovementEngine
from app.user_context import UserContext


@pytest.fixture
def actor() -> UserContext:
    return UserContext.from_request(
        user_id="aaron-user-id",
        user_name="Test Owner",
        user_is_admin=True,
        device_id="test-phone",
        voice_mode=False,
        privilege_verified=True,
    )


@pytest.fixture
def engine(tmp_path) -> SelfImprovementEngine:
    return SelfImprovementEngine(
        str(tmp_path / "improvement.db"),
        enabled=True,
        auto_prepare=True,
        repeat_threshold=2,
        latency_failure_ms=7000,
        core_version="test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "calls", "question"),
    [
        ("general", [], "Why did you set the living room lights to 30%?"),
        (
            "general",
            [{"tool": "search_home", "result": {"success": True}}],
            "Why did you set the living room lights to 30%?",
        ),
        (
            "general",
            [
                {"tool": "search_home", "result": {"success": True}},
                {"tool": "explain_home_action_receipt", "result": {"success": True}},
            ],
            "Why did you set the living room lights to 30%?",
        ),
        ("general", [], "No, why did you send one?"),
        ("notification_awaiting_message", [], "No, why did you send one?"),
    ],
)
async def test_explanation_question_not_automatically_logged_as_failure(
    engine: SelfImprovementEngine,
    actor: UserContext,
    intent: str,
    calls: list[dict],
    question: str,
) -> None:
    conversation_id = "usr:test:explanation"
    await engine.observe_interaction(
        conversation_id=conversation_id,
        actor=actor,
        raw_text="What happened?",
        result={
            "success": True,
            "response": "Here is the current information.",
            "intent": intent,
            "calls": calls,
            "timings": {"jarvis_request_total_ms": 100},
        },
    )
    failure_id = await engine.capture_feedback_before_request(
        conversation_id=conversation_id,
        actor=actor,
        raw_text=question,
    )
    assert failure_id is None
    assert await engine.list_failures() == []
    assert await engine.list_candidates() == []


@pytest.mark.parametrize(
    ("intent", "raw_text", "calls", "expected"),
    [
        (
            "control_now",
            "Household vocabulary: notification, light, TV, bedroom, volume",
            [{"tool": "set_media_volume", "result": {"success": False}}],
            "device_resolution",
        ),
        ("notification_awaiting_message", "Provide a message", [], "notification"),
        (
            "general",
            "Send a message",
            [{"tool": "send_mobile_notification", "result": {"success": False}}],
            "notification",
        ),
    ],
)
def test_notification_category_requires_actual_intent_or_tool_provenance(
    intent: str, raw_text: str, calls: list[dict], expected: str
) -> None:
    assert (
        SelfImprovementEngine._category_for_interaction(
            {"intent": intent, "raw_text": raw_text, "tool_calls": calls}
        )
        == expected
    )


@pytest.mark.asyncio
async def test_unambiguous_correction_still_records_failure(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    conversation_id = "usr:test:explicit-feedback"
    await engine.observe_interaction(
        conversation_id=conversation_id,
        actor=actor,
        raw_text="Turn on the bedroom floor light",
        result={
            "success": True,
            "response": "Kitchen light on.",
            "intent": "control_now",
            "calls": [{"tool": "control_device", "result": {"success": True}}],
            "timings": {"jarvis_request_total_ms": 100},
        },
    )
    failure_id = await engine.capture_feedback_before_request(
        conversation_id=conversation_id,
        actor=actor,
        raw_text="That was wrong, I meant the bedroom floor light.",
    )
    assert failure_id is not None
    failure = await engine.get_failure(failure_id)
    assert failure is not None
    assert failure["category"] == "device_resolution"
    assert failure["explicit"] is True
