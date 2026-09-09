from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.connectors import ExecutionStatus
from app.email_assistant import EmailAssistantPolicyEngine


class RetentionRegistry:
    def __init__(self, messages: dict[str, dict[str, Any]]) -> None:
        self.messages = messages
        self.requests = []
        self.trash_calls: list[str] = []
        self.trash_requests = []
        self.unknown_after_write: set[str] = set()
        self.unknown_without_write: set[str] = set()

    async def execute(self, request, *, refresh_health=False):
        self.requests.append(request)
        if request.capability_id == "gmail.search":
            return SimpleNamespace(
                success=True,
                data={"message_ids": list(self.messages)},
                error=None,
            )
        if request.capability_id == "gmail.read":
            message = self.messages.get(str(request.payload["message_id"]))
            if message is None:
                return SimpleNamespace(success=False, data={}, error="message disappeared")
            return SimpleNamespace(success=True, data=dict(message), error=None)
        if request.capability_id == "gmail.trash":
            message_id = str(request.payload["message_id"])
            self.trash_calls.append(message_id)
            self.trash_requests.append(request)
            suppress_write = message_id in self.unknown_without_write
            if suppress_write:
                self.unknown_without_write.remove(message_id)
            else:
                labels = set(self.messages[message_id]["label_ids"])
                labels.discard("INBOX")
                labels.add("TRASH")
                self.messages[message_id]["label_ids"] = sorted(labels)
            status = (
                ExecutionStatus.OUTCOME_UNKNOWN
                if message_id in self.unknown_after_write or suppress_write
                else ExecutionStatus.VERIFIED
            )
            receipt = SimpleNamespace(action_id=f"action-{message_id}")
            return SimpleNamespace(
                success=status is ExecutionStatus.VERIFIED,
                status=status,
                receipt=receipt,
                provider_reference=message_id,
                verification={"trash_label_present": status is ExecutionStatus.VERIFIED},
                error="provider result unknown"
                if status is ExecutionStatus.OUTCOME_UNKNOWN
                else None,
            )
        raise AssertionError(f"Unexpected capability: {request.capability_id}")


class BriefingRegistry:
    def __init__(self) -> None:
        self.requests = []
        self.observed_at = "2026-09-08T12:00:00+00:00"

    async def execute(self, request, *, refresh_health=False):
        assert request.capability_id == "gmail.briefing"
        self.requests.append(request)
        return SimpleNamespace(
            success=True,
            data={
                "observed_at": self.observed_at,
                "counts": {"total": 2, "unread": 1},
                "coverage": {"partial_details": False},
            },
            provider_reference="message-latest",
            error=None,
        )


def message(message_id: str, *, age_days: int, labels: set[str], now: datetime) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "thread_id": f"thread-{message_id}",
        "label_ids": sorted(labels),
        "internal_date_ms": int((now - timedelta(days=age_days)).timestamp() * 1000),
        "subject": message_id,
    }


@pytest.mark.asyncio
async def test_retention_only_trashes_freshly_verified_old_inbox_mail(tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    registry = RetentionRegistry(
        {
            "age-29": message("age-29", age_days=29, labels={"INBOX"}, now=now),
            "age-30": message("age-30", age_days=30, labels={"INBOX"}, now=now),
            "archived": message("archived", age_days=45, labels={"IMPORTANT"}, now=now),
            "sent": message("sent", age_days=45, labels={"INBOX", "SENT"}, now=now),
            "draft": message("draft", age_days=45, labels={"INBOX", "DRAFT"}, now=now),
            "trashed": message("trashed", age_days=45, labels={"TRASH"}, now=now),
        }
    )
    engine = EmailAssistantPolicyEngine(tmp_path / "email.db", registry)  # type: ignore[arg-type]
    policy = await engine.create_retention_policy(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        retention_days=30,
        request_id="retention-30",
    )

    result = await engine.run_policy(policy["policy_id"], now=now)

    assert result["trashed"] == 1
    assert registry.trash_calls == ["age-30"]
    action_request = registry.trash_requests[0]
    assert action_request.conversation_id == "usr:aaron:mail"
    assert action_request.principal_id == "aaron"
    assert action_request.operation == "retention_inbox_to_trash"
    assert action_request.target == "age-30"
    assert action_request.confirmed is True
    assert action_request.standing_permission is True
    assert action_request.idempotency_key.startswith("email-retention:")
    search_request = next(
        request for request in registry.requests if request.capability_id == "gmail.search"
    )
    assert search_request.payload["query"] == "in:inbox before:1786276801"
    assert "TRASH" in registry.messages["age-30"]["label_ids"]
    assert "INBOX" not in registry.messages["age-30"]["label_ids"]
    assert set(registry.messages["archived"]["label_ids"]) == {"IMPORTANT"}
    assert set(registry.messages["sent"]["label_ids"]) == {"INBOX", "SENT"}
    assert set(registry.messages["draft"]["label_ids"]) == {"DRAFT", "INBOX"}
    assert set(registry.messages["trashed"]["label_ids"]) == {"TRASH"}
    audit = await engine.audit(policy["policy_id"], principal_id="aaron")
    assert audit[0]["evidence"]["permanent_delete"] is False
    assert audit[-1]["evidence"]["trashed"] == 1

    with sqlite3.connect(tmp_path / "email.db") as connection:
        item = connection.execute(
            "SELECT status,action_id,evidence_json FROM email_policy_items"
        ).fetchone()
    assert item is not None
    assert item[0] == "verified"
    assert item[1] == "action-age-30"
    assert '"permanent_delete":false' in item[2]


@pytest.mark.asyncio
async def test_retention_rechecks_stale_candidate_before_mutation(tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    registry = RetentionRegistry(
        {"stale": message("stale", age_days=60, labels={"IMPORTANT"}, now=now)}
    )
    engine = EmailAssistantPolicyEngine(tmp_path / "email.db", registry)  # type: ignore[arg-type]
    policy = await engine.create_retention_policy(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        request_id="stale-candidate",
    )

    result = await engine.run_policy(policy["policy_id"], now=now)

    assert result["skipped"] == 1
    assert registry.trash_calls == []


@pytest.mark.asyncio
async def test_retention_unknown_outcome_reads_back_and_never_trashes_twice(tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    registry = RetentionRegistry(
        {"unknown": message("unknown", age_days=60, labels={"INBOX"}, now=now)}
    )
    registry.unknown_after_write.add("unknown")
    path = tmp_path / "email.db"
    engine = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    policy = await engine.create_retention_policy(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        request_id="unknown-outcome",
    )

    first = await engine.run_policy(policy["policy_id"], now=now)
    assert first["trashed"] == 1
    assert first["outcome_unknown"] == 0
    assert registry.trash_calls == ["unknown"]

    # A new engine proves the durable policy and reconciled item survive restart.
    restarted = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    persisted = await restarted.get(policy["policy_id"], principal_id="aaron")
    assert persisted is not None
    await restarted.run_policy(policy["policy_id"], now=now + timedelta(days=1))
    assert registry.trash_calls == ["unknown"]
    with sqlite3.connect(path) as connection:
        status = connection.execute(
            "SELECT status FROM email_policy_items WHERE message_id='unknown'"
        ).fetchone()
    assert status == ("verified_after_unknown",)


@pytest.mark.asyncio
async def test_retention_unknown_without_effect_retries_only_after_fresh_readback(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    registry = RetentionRegistry(
        {"retry": message("retry", age_days=60, labels={"INBOX"}, now=now)}
    )
    registry.unknown_without_write.add("retry")
    engine = EmailAssistantPolicyEngine(tmp_path / "email.db", registry)  # type: ignore[arg-type]
    policy = await engine.create_retention_policy(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        request_id="unknown-no-effect",
    )

    first = await engine.run_policy(policy["policy_id"], now=now)
    assert first["status"] == "partial"
    assert first["outcome_unknown"] == 1
    assert registry.trash_calls == ["retry"]
    assert "INBOX" in registry.messages["retry"]["label_ids"]

    second = await engine.run_policy(policy["policy_id"], now=now)
    assert second["status"] == "completed"
    assert second["reconciled"] == 1
    assert second["trashed"] == 1
    assert registry.trash_calls == ["retry", "retry"]
    assert "TRASH" in registry.messages["retry"]["label_ids"]
    with sqlite3.connect(tmp_path / "email.db") as connection:
        item = connection.execute(
            "SELECT status,attempts,evidence_json FROM email_policy_items WHERE message_id='retry'"
        ).fetchone()
    assert item is not None
    assert item[0:2] == ("verified", 2)
    assert '"action_status":"verified"' in item[2]


@pytest.mark.asyncio
async def test_retention_restart_during_item_reconciles_without_duplicate_trash(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    registry = RetentionRegistry(
        {"crash": message("crash", age_days=60, labels={"TRASH"}, now=now)}
    )
    path = tmp_path / "email.db"
    engine = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    policy = await engine.create_retention_policy(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        request_id="crash-during-item",
    )
    cutoff = now - timedelta(days=30)
    eligibility_key = engine._eligibility_key(policy, "crash", cutoff)
    engine._record_item(
        policy_id=policy["policy_id"],
        message_id="crash",
        eligibility_key=eligibility_key,
        status="executing",
        attempts=1,
        evidence={"provider_call_may_have_started": True},
    )

    restarted = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    result = await restarted.run_policy(policy["policy_id"], now=now)

    assert result["reconciled"] == 1
    assert registry.trash_calls == []
    with sqlite3.connect(path) as connection:
        status = connection.execute(
            "SELECT status FROM email_policy_items WHERE message_id='crash'"
        ).fetchone()
    assert status == ("verified_after_unknown",)


@pytest.mark.asyncio
async def test_retention_policy_is_idempotent_scoped_and_manageable(tmp_path: Path) -> None:
    registry = RetentionRegistry({})
    path = tmp_path / "email.db"
    engine = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    request = {
        "principal_id": "aaron",
        "conversation_id": "usr:aaron:mail",
        "retention_days": 30,
        "request_id": "stable-policy",
    }
    created = await engine.create_retention_policy(**request)
    replay = await engine.create_retention_policy(**request)
    assert replay["policy_id"] == created["policy_id"]
    assert replay["reused"] is True
    assert await engine.get(created["policy_id"], principal_id="amber") is None
    with pytest.raises(ValueError, match="already exists"):
        await engine.create_retention_policy(
            principal_id="aaron",
            conversation_id="usr:aaron:other-chat",
            retention_days=45,
            request_id="conflicting-policy",
        )

    paused = await engine.pause(created["policy_id"], principal_id="aaron")
    assert paused is not None and paused["status"] == "paused"
    changed = await engine.change(created["policy_id"], principal_id="aaron", retention_days=45)
    assert changed is not None and changed["retention_days"] == 45
    resumed = await engine.resume(created["policy_id"], principal_id="aaron")
    assert resumed is not None and resumed["status"] == "active"
    disabled = await engine.disable(created["policy_id"], principal_id="aaron")
    assert disabled is not None and disabled["status"] == "disabled"

    restarted = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    persisted = await restarted.get(created["policy_id"], principal_id="aaron")
    assert persisted is not None
    assert persisted["status"] == "disabled"
    assert persisted["retention_days"] == 45

    stopped_health = await restarted.health_snapshot()
    assert stopped_health["healthy"] is False
    assert stopped_health["database_healthy"] is True
    await restarted.start()
    running_health = await restarted.health_snapshot()
    assert running_health["healthy"] is True
    assert running_health["worker_running"] is True
    await restarted.stop()


@pytest.mark.asyncio
async def test_briefing_watermark_is_verified_scoped_and_survives_restart(tmp_path: Path) -> None:
    registry = BriefingRegistry()
    path = tmp_path / "email.db"
    engine = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]

    first = await engine.briefing(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
    )
    assert first["success"] is True
    assert first["since_previous_briefing"] is False
    assert first["watermark_persisted"] is True
    assert "since_epoch" not in registry.requests[0].payload

    registry.observed_at = "2026-09-09T12:00:00+00:00"
    restarted = EmailAssistantPolicyEngine(path, registry)  # type: ignore[arg-type]
    second = await restarted.briefing(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
    )
    assert second["since_previous_briefing"] is True
    assert second["previous_briefing_epoch"] == 1_788_868_800
    assert registry.requests[1].payload["since_epoch"] == 1_788_868_800

    other = await restarted.briefing(
        principal_id="aaron",
        conversation_id="usr:aaron:other",
    )
    assert other["since_previous_briefing"] is False
    assert "since_epoch" not in registry.requests[2].payload
