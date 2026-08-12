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


def _insert_transaction_candidate(
    engine: SelfImprovementEngine,
    *,
    code: str = "123456",
    with_binding: bool = True,
) -> int:
    now = engine._utc_now()  # noqa: SLF001

    with engine._connect() as connection:  # noqa: SLF001
        failure = connection.execute(
            """
            INSERT INTO improvement_failures (
                created_at,
                updated_at,
                last_seen_at,
                conversation_id,
                user_key,
                signature,
                category,
                severity,
                summary,
                evidence_json,
                occurrences,
                explicit,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                now,
                "usr:aaron:transaction",
                "aaron",
                "transaction-test-signature",
                "general",
                "low",
                "Transaction test",
                "{}",
                1,
                1,
                "candidate_ready",
            ),
        )

        failure_id = int(
            failure.lastrowid
        )

        candidate = connection.execute(
            """
            INSERT INTO improvement_candidates (
                failure_id,
                created_at,
                updated_at,
                status,
                approval_code,
                approval_code_expires_at,
                base_commit,
                candidate_commit,
                validated_patch_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure_id,
                now,
                now,
                "awaiting_approval",
                code,
                engine._utc_after(3600),  # noqa: SLF001
                (
                    "base-commit"
                    if with_binding
                    else None
                ),
                (
                    "candidate-commit"
                    if with_binding
                    else None
                ),
                (
                    "patch-sha"
                    if with_binding
                    else None
                ),
            ),
        )

        return int(
            candidate.lastrowid
        )


@pytest.mark.asyncio
async def test_approval_transaction_cannot_skip_approval(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = (
        _insert_transaction_candidate(
            engine
        )
    )

    result = await engine.request_deploy(
        candidate_id,
        "123456",
        "Aaron",
    )

    assert result.success is False
    assert (
        result.intent
        == "improvement_deploy_invalid_state"
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert (
        candidate["status"]
        == "awaiting_approval"
    )


@pytest.mark.asyncio
async def test_approval_transaction_rotates_to_one_time_deploy_ticket(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = (
        _insert_transaction_candidate(
            engine
        )
    )

    approved = await engine.approve_candidate(
        candidate_id,
        "123456",
        "Aaron",
    )

    assert approved.success is True
    assert approved.details is not None

    deploy_code = str(
        approved.details[
            "deploy_code"
        ]
    )

    assert (
        len(deploy_code) == 6
        and deploy_code.isdigit()
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert candidate["status"] == "approved"
    assert candidate["approval_code"] is None
    assert candidate["deploy_ticket_hash"]
    assert candidate["deploy_ticket_salt"]
    assert candidate["deploy_ticket_expires_at"]
    assert (
        candidate["deploy_ticket_consumed_at"]
        is None
    )

    old_code = await engine.request_deploy(
        candidate_id,
        "123456",
        "Aaron",
    )

    assert old_code.success is False
    assert (
        old_code.intent
        == "improvement_deploy_bad_code"
    )

    requested = await engine.request_deploy(
        candidate_id,
        deploy_code,
        "Aaron",
    )

    assert requested.success is True

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert (
        candidate["status"]
        == "deploy_requested"
    )
    assert (
        candidate["deploy_ticket_consumed_at"]
        is not None
    )
    assert candidate["deploy_ticket_hash"] is None
    assert candidate["deploy_ticket_salt"] is None
    assert candidate["deploy_lease_id"] is None
    assert candidate["deploy_lease_started_at"] is None
    assert candidate["deploy_lease_expires_at"] is None
    assert candidate["deploy_phase"] == "requested"


@pytest.mark.asyncio
async def test_approval_transaction_expired_ticket_fails_closed(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = (
        _insert_transaction_candidate(
            engine
        )
    )

    approved = await engine.approve_candidate(
        candidate_id,
        "123456",
        "Aaron",
    )

    assert approved.success is True
    assert approved.details is not None

    deploy_code = str(
        approved.details[
            "deploy_code"
        ]
    )

    with engine._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE improvement_candidates
            SET deploy_ticket_expires_at = ?
            WHERE candidate_id = ?
            """,
            (
                engine._utc_after(-1),  # noqa: SLF001
                candidate_id,
            ),
        )

    result = await engine.request_deploy(
        candidate_id,
        deploy_code,
        "Aaron",
    )

    assert result.success is False
    assert (
        result.intent
        == "improvement_deploy_ticket_expired"
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert candidate["status"] == "approved"


@pytest.mark.asyncio
async def test_approval_transaction_requires_validated_commit_binding(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = (
        _insert_transaction_candidate(
            engine,
            with_binding=False,
        )
    )

    approved = await engine.approve_candidate(
        candidate_id,
        "123456",
        "Aaron",
    )

    assert approved.success is True
    assert approved.details is not None

    result = await engine.request_deploy(
        candidate_id,
        str(
            approved.details[
                "deploy_code"
            ]
        ),
        "Aaron",
    )

    assert result.success is False
    assert (
        result.intent
        == "improvement_deploy_binding_missing"
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert candidate["status"] == "approved"
