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
        privilege_verified=True,
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


def _mark_transaction_candidate_deployed(
    engine: SelfImprovementEngine,
    candidate_id: int,
) -> None:
    now = engine._utc_now()  # noqa: SLF001

    with engine._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE improvement_candidates
            SET
                status = 'deployed',
                updated_at = ?,
                deployed_at = ?,
                approval_code = NULL,
                approval_code_expires_at = NULL,
                deploy_phase = 'deployed'
            WHERE candidate_id = ?
            """,
            (
                now,
                now,
                candidate_id,
            ),
        )


@pytest.mark.asyncio
async def test_rollback_transaction_requires_deployed_state(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = _insert_transaction_candidate(
        engine
    )

    result = await engine.issue_rollback_ticket(
        candidate_id,
        "Aaron",
    )

    assert result.success is False

    assert (
        result.intent
        == "improvement_rollback_ticket_invalid_state"
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert (
        candidate[
            "status"
        ]
        == "awaiting_approval"
    )


@pytest.mark.asyncio
async def test_rollback_transaction_rotates_and_consumes_ticket_once(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = _insert_transaction_candidate(
        engine
    )

    _mark_transaction_candidate_deployed(
        engine,
        candidate_id,
    )

    issued = await engine.issue_rollback_ticket(
        candidate_id,
        "Aaron",
    )

    assert issued.success is True
    assert issued.details is not None

    rollback_code = str(
        issued.details[
            "rollback_code"
        ]
    )

    assert len(
        rollback_code
    ) == 6

    assert rollback_code.isdigit()

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert candidate["status"] == "deployed"
    assert candidate["approval_code"] is None
    assert candidate["rollback_ticket_hash"]
    assert candidate["rollback_ticket_salt"]
    assert candidate["rollback_ticket_expires_at"]

    assert (
        candidate[
            "rollback_ticket_hash"
        ]
        != rollback_code
    )

    assert (
        candidate[
            "rollback_ticket_consumed_at"
        ]
        is None
    )

    wrong = await engine.request_rollback(
        candidate_id,
        "000000",
        "Aaron",
    )

    assert wrong.success is False

    assert (
        wrong.intent
        == "improvement_rollback_bad_code"
    )

    requested = await engine.request_rollback(
        candidate_id,
        rollback_code,
        "Aaron",
    )

    assert requested.success is True

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None

    assert (
        candidate[
            "status"
        ]
        == "rollback_requested"
    )

    assert (
        candidate[
            "rollback_ticket_consumed_at"
        ]
        is not None
    )

    assert candidate["rollback_ticket_hash"] is None
    assert candidate["rollback_ticket_salt"] is None
    assert candidate["rollback_ticket_expires_at"] is None

    assert (
        candidate[
            "deploy_phase"
        ]
        == "manual_rollback_requested"
    )

    # Simulate only the state value being restored by an
    # administrator. The consumed ticket must still be dead.
    with engine._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE improvement_candidates
            SET status = 'deployed'
            WHERE candidate_id = ?
            """,
            (
                candidate_id,
            ),
        )

    replay = await engine.request_rollback(
        candidate_id,
        rollback_code,
        "Aaron",
    )

    assert replay.success is False

    assert (
        replay.intent
        == "improvement_rollback_ticket_used"
    )


@pytest.mark.asyncio
async def test_rollback_transaction_expired_ticket_fails_closed(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = _insert_transaction_candidate(
        engine
    )

    _mark_transaction_candidate_deployed(
        engine,
        candidate_id,
    )

    issued = await engine.issue_rollback_ticket(
        candidate_id,
        "Aaron",
    )

    assert issued.success is True
    assert issued.details is not None

    rollback_code = str(
        issued.details[
            "rollback_code"
        ]
    )

    with engine._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE improvement_candidates
            SET rollback_ticket_expires_at = ?
            WHERE candidate_id = ?
            """,
            (
                engine._utc_after(-1),  # noqa: SLF001
                candidate_id,
            ),
        )

    result = await engine.request_rollback(
        candidate_id,
        rollback_code,
        "Aaron",
    )

    assert result.success is False

    assert (
        result.intent
        == "improvement_rollback_ticket_expired"
    )

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None
    assert candidate["status"] == "deployed"

    assert (
        candidate[
            "rollback_ticket_consumed_at"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_rollback_transaction_reissue_invalidates_old_code(
    engine: SelfImprovementEngine,
) -> None:
    candidate_id = _insert_transaction_candidate(
        engine
    )

    _mark_transaction_candidate_deployed(
        engine,
        candidate_id,
    )

    first = await engine.issue_rollback_ticket(
        candidate_id,
        "Aaron",
    )

    assert first.success is True
    assert first.details is not None

    first_code = str(
        first.details[
            "rollback_code"
        ]
    )

    second = await engine.issue_rollback_ticket(
        candidate_id,
        "Aaron",
    )

    assert second.success is True
    assert second.details is not None

    second_code = str(
        second.details[
            "rollback_code"
        ]
    )

    assert first_code != second_code

    old_result = await engine.request_rollback(
        candidate_id,
        first_code,
        "Aaron",
    )

    assert old_result.success is False

    assert (
        old_result.intent
        == "improvement_rollback_bad_code"
    )

    new_result = await engine.request_rollback(
        candidate_id,
        second_code,
        "Aaron",
    )

    assert new_result.success is True

    candidate = await engine.get_candidate(
        candidate_id
    )

    assert candidate is not None

    assert (
        candidate[
            "status"
        ]
        == "rollback_requested"
    )


@pytest.mark.asyncio
async def test_mixed_read_only_inspection_miss_is_not_recorded_as_failure(
    engine: SelfImprovementEngine,
    actor: UserContext,
) -> None:
    interaction_id = await engine.observe_interaction(
        conversation_id="usr:aaron:failure99",
        actor=actor,
        raw_text=(
            "Inspect your own live code and tell me "
            "your current Git status."
        ),
        result={
            "success": True,
            "response": (
                "The Jarvis repository is on conversation-engine "
                "and clean. The optional Voice PE Git status "
                "could not be read."
            ),
            "intent": "general",
            "calls": [
                {
                    "tool": "code_roots",
                    "result": {
                        "success": True,
                        "mode": "read_only",
                    },
                },
                {
                    "tool": "git_status",
                    "arguments": {
                        "root": "jarvis",
                    },
                    "result": {
                        "success": True,
                        "branch": "conversation-engine",
                    },
                },
                {
                    "tool": "git_status",
                    "arguments": {
                        "root": "voice_pe",
                    },
                    "result": {
                        "success": False,
                        "error": "fatal: not a git repository",
                    },
                },
            ],
            "timings": {
                "jarvis_request_total_ms": 400,
            },
        },
    )

    interaction = await engine.get_interaction(
        interaction_id
    )

    assert interaction is not None
    assert interaction["success"] is True
    assert interaction["failure_like"] is False

    failures = await engine.list_failures()

    assert failures == []
