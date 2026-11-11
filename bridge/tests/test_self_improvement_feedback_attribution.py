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
    assert failure["category"] == "feedback_review"
    assert failure["explicit"] is True


def receipt_call(
    identifier,
    *,
    operation="execute_home_parameter",
    parameter="brightness",
    conversation="feedback",
):
    return {
        "tool": operation,
        "result": {
            "success": True,
            "receipt": {
                "action_id": identifier,
                "conversation_id": conversation,
                "requested_operation": operation,
                "status": "verified",
                "target": "media_player.lounge" if parameter == "volume" else "light.lounge",
                "result": {"parameter": parameter, "requested_value": 30, "command_sent": True},
            },
        },
    }


async def observe(
    engine,
    actor,
    calls,
    text="Household vocabulary: notification, light, volume",
    conversation="feedback",
):
    return await engine.observe_interaction(
        conversation_id=conversation,
        actor=actor,
        raw_text=text,
        result={"success": True, "intent": "general", "response": "Done", "calls": calls},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "parameter", "category"),
    [
        ("execute_home_parameter", "brightness", "device_resolution"),
        ("execute_home_parameter", "volume", "media_volume"),
        ("send_mobile_notification", None, "notification"),
    ],
)
async def test_receipt_establishes_actual_action(engine, actor, operation, parameter, category):
    action = await observe(
        engine, actor, [receipt_call("actual", operation=operation, parameter=parameter)]
    )
    failure_id = await engine.capture_feedback_before_request(
        conversation_id="feedback", actor=actor, raw_text="That was wrong, I meant 20."
    )
    failure = await engine.get_failure(failure_id)
    assert failure["source_interaction_id"] == action
    assert failure["category"] == category
    assert failure["evidence"]["feedback_attribution"]["receipt_id"] == "actual"


@pytest.mark.asyncio
async def test_explanation_links_exact_recorded_receipt_not_read_only_turn(engine, actor):
    action = await observe(engine, actor, [receipt_call("light-action")])
    await observe(
        engine,
        actor,
        [
            {
                "tool": "explain_home_action_receipt",
                "result": {"success": True, "receipt_id": "light-action", "write_performed": False},
            }
        ],
    )
    failure_id = await engine.capture_feedback_before_request(
        conversation_id="feedback", actor=actor, raw_text="That was wrong, I meant 20 percent."
    )
    failure = await engine.get_failure(failure_id)
    assert failure["source_interaction_id"] == action
    assert failure["evidence"]["source"]["tool_calls"][0]["tool"] == "execute_home_parameter"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calls",
    [
        [],
        [{"tool": "search_home", "result": {"success": True}}],
        [receipt_call("one"), receipt_call("two")],
        [
            {
                "tool": "explain_home_action_receipt",
                "result": {"success": True, "receipt_id": "missing"},
            }
        ],
        [receipt_call("foreign", conversation="another-conversation")],
    ],
)
async def test_ambiguous_feedback_preserved_without_action_attribution(engine, actor, calls):
    await observe(engine, actor, [receipt_call("old-unrelated")])
    context_id = await observe(engine, actor, calls)
    failure_id = await engine.capture_feedback_before_request(
        conversation_id="feedback",
        actor=actor,
        raw_text="That was wrong, you should have sent a notification.",
    )
    failure = await engine.get_failure(failure_id)
    assert failure["explicit"] is True
    assert failure["source_interaction_id"] is None
    assert failure["category"] == "feedback_review"
    attribution = failure["evidence"]["feedback_attribution"]
    assert attribution["status"] == "review_required"
    assert attribution["feedback_context_interaction_id"] == context_id
    assert attribution["context"]["tool_calls"] == calls


@pytest.mark.asyncio
async def test_feedback_dedup_keeps_distinct_interactions_actions_and_values(engine, actor):
    await observe(engine, actor, [receipt_call("one")])

    async def feedback(text="That was wrong, I meant 20 percent."):
        return await engine.capture_feedback_before_request(
            conversation_id="feedback", actor=actor, raw_text=text
        )

    first = await feedback()
    original = await engine.get_failure(first)
    assert await feedback() == first
    assert await engine.get_failure(first) == original
    assert await feedback("That was wrong, I meant 30 percent.") != first
    await observe(engine, actor, [receipt_call("two")])
    assert await feedback() != first
    assert (await engine.get_failure(first))["evidence"] == original["evidence"]


@pytest.mark.asyncio
async def test_feedback_cannot_cross_principal(engine, actor):
    await observe(engine, actor, [receipt_call("one")])
    other = UserContext.from_request(
        user_id="other",
        user_name="Other",
        user_is_admin=False,
        device_id="other-phone",
        voice_mode=False,
    )
    assert (
        await engine.capture_feedback_before_request(
            conversation_id="feedback", actor=other, raw_text="That was wrong"
        )
        is None
    )


@pytest.mark.asyncio
async def test_concurrent_feedback_retry_is_idempotent(engine, actor):
    import asyncio

    await observe(engine, actor, [receipt_call("one")])
    identifiers = await asyncio.gather(
        *[
            engine.capture_feedback_before_request(
                conversation_id="feedback",
                actor=actor,
                raw_text="That was wrong, I meant 20 percent.",
            )
            for _ in range(8)
        ]
    )
    assert len(set(identifiers)) == 1
    assert (await engine.get_failure(identifiers[0]))["occurrences"] == 1
    with engine._connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM improvement_candidates WHERE failure_id = ?",
                (identifiers[0],),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_identical_independent_improvement_requests_remain_separate(engine):
    first = await engine.request_improvement("Improve latency", "owner")
    second = await engine.request_improvement("Improve latency", "owner")
    assert first[0] and second[0] and first[1] != second[1]


@pytest.mark.asyncio
async def test_conflicting_receipt_snapshots_require_review(engine, actor):
    first = receipt_call("one")
    second = receipt_call("one", parameter="volume")
    await observe(engine, actor, [first, second])
    failure_id = await engine.capture_feedback_before_request(
        conversation_id="feedback", actor=actor, raw_text="That was wrong"
    )
    assert (await engine.get_failure(failure_id))["category"] == "feedback_review"


@pytest.mark.parametrize("calls", [None, {}, "malformed"])
def test_malformed_historical_tools_remain_unattributed(calls):
    from app.feedback_attribution import attribute_feedback

    result = attribute_feedback(
        {
            "interaction_id": 1,
            "conversation_id": "fixture",
            "user_key": "owner",
            "tool_calls": calls,
        },
        [],
        "That was wrong",
    )
    assert result["feedback_attribution"]["status"] == "review_required"
    assert result["tool_calls"] == []
