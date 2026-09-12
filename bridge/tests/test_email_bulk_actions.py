from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.connectors import ExecutionStatus
from app.email_assistant import EmailAssistantPolicyEngine


class BulkRegistry:
    def __init__(self, count: int, *, provider: str = "google_gmail") -> None:
        self.provider = provider
        self.messages = {
            f"message-{index}": {
                "message_id": f"message-{index}",
                "thread_id": f"thread-{index}",
                "from": f"sender-{index}@example.com",
                "subject": f"Subject {index}",
                "label_ids": ["INBOX", "UNREAD"],
                "parent_folder_id": "inbox-id",
            }
            for index in range(count)
        }
        self.writes: list[tuple[str, str]] = []
        self.fail_once: set[str] = set()

    @staticmethod
    def result(
        data: dict[str, Any],
        *,
        status: ExecutionStatus = ExecutionStatus.VERIFIED,
        error: str | None = None,
        reference: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            success=status is ExecutionStatus.VERIFIED,
            data=data,
            error=error,
            provider_reference=reference,
            status=status,
            receipt=(
                SimpleNamespace(action_id=f"receipt-{reference}") if reference is not None else None
            ),
            verification={"provider_verified": status is ExecutionStatus.VERIFIED},
        )

    async def execute(self, request, *, refresh_health=False):
        del refresh_health
        capability = request.capability_id
        if capability in {"gmail.search", "outlook.search"}:
            values = [dict(item) for item in self.messages.values()]
            if request.payload.get("unread") is True or "is:unread" in str(
                request.payload.get("query") or ""
            ):
                values = [item for item in values if "UNREAD" in item["label_ids"]]
            return self.result(
                {
                    "message_ids": [item["message_id"] for item in values],
                    "messages": values,
                    "count": len(values),
                    "pages": (len(values) + 49) // 50,
                    "truncated": False,
                }
            )
        if capability in {"gmail.read", "outlook.read"}:
            message = self.messages.get(str(request.payload["message_id"]))
            if message is None:
                return self.result({}, status=ExecutionStatus.FAILED, error="not found")
            data = dict(message)
            return self.result({"message": data} if capability == "outlook.read" else data)
        if capability == "outlook.folders":
            return self.result(
                {
                    "folders": [
                        {"id": "inbox-id", "displayName": "Inbox"},
                        {"id": "deleted-id", "displayName": "Deleted Items"},
                        {"id": "archive-id", "displayName": "Archive"},
                    ]
                }
            )
        if capability in {"gmail.trash", "gmail.archive", "outlook.trash", "outlook.archive"}:
            message_id = str(request.payload["message_id"])
            self.writes.append((capability, message_id))
            if message_id in self.fail_once:
                self.fail_once.remove(message_id)
                return self.result(
                    {}, status=ExecutionStatus.FAILED, error="temporary provider failure"
                )
            message = self.messages[message_id]
            if capability == "gmail.trash":
                message["label_ids"] = ["TRASH"]
            elif capability == "gmail.archive":
                message["label_ids"] = []
            elif capability == "outlook.trash":
                message["parent_folder_id"] = "deleted-id"
            else:
                message["parent_folder_id"] = "archive-id"
            return self.result({"message_id": message_id}, reference=message_id)
        raise AssertionError(f"Unexpected capability: {capability}")


def account_resolver(provider: str):
    async def resolve(principal: str):
        return [
            {
                "provider": provider,
                "account_id": f"{provider}-account",
                "account_email": f"{principal}@example.com",
            }
        ]

    return resolve


def engine(path: Path, registry: BulkRegistry) -> EmailAssistantPolicyEngine:
    return EmailAssistantPolicyEngine(
        path,
        registry,  # type: ignore[arg-type]
        account_resolver=account_resolver(registry.provider),
    )


async def snapshot(
    service: EmailAssistantPolicyEngine,
    *,
    request_id: str = "bulk-request",
    principal: str = "aaron",
    conversation: str = "usr:aaron:bulk",
) -> dict[str, Any]:
    provider = "google_gmail"
    accounts = await service.account_resolver(principal)  # type: ignore[misc]
    if accounts:
        provider = str(accounts[0]["provider"])
    return await service.snapshot_bulk_action(
        principal_id=principal,
        conversation_id=conversation,
        provider=provider,
        account_id=f"{provider}-account",
        operation="trash",
        filter_kind="unread_inbox",
        original_authorization_text="Move all unread emails to the bin",
        request_id=request_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_count", [0, 1, 10, 50, 137])
async def test_bulk_action_freezes_and_processes_every_candidate(
    tmp_path: Path, candidate_count: int
) -> None:
    registry = BulkRegistry(candidate_count)
    service = engine(tmp_path / "bulk.db", registry)

    action = await snapshot(service, request_id=f"bulk-{candidate_count}")
    assert action["intended_count"] == candidate_count
    result = await service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )

    assert result["status"] == "completed"
    assert result["succeeded_count"] == candidate_count
    assert len(registry.writes) == candidate_count
    assert len({message_id for _, message_id in registry.writes}) == candidate_count


@pytest.mark.asyncio
async def test_bulk_action_partial_failure_is_truthful_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    registry = BulkRegistry(12)
    registry.fail_once = {"message-3", "message-9"}
    service = engine(tmp_path / "bulk.db", registry)
    action = await snapshot(service)

    first = await service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert first["status"] == "partial"
    assert first["succeeded_count"] == 10
    assert first["failed_count"] == 2

    second = await service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert second["status"] == "completed"
    assert second["succeeded_count"] == 12
    attempts = [message_id for _, message_id in registry.writes]
    assert attempts.count("message-3") == 2
    assert attempts.count("message-9") == 2
    assert all(
        attempts.count(f"message-{index}") == 1 for index in range(12) if index not in {3, 9}
    )


@pytest.mark.asyncio
async def test_bulk_action_restart_resumes_exact_remaining_frozen_set(tmp_path: Path) -> None:
    registry = BulkRegistry(70)
    path = tmp_path / "bulk.db"
    first_service = engine(path, registry)
    action = await snapshot(first_service)
    first = await first_service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
        max_items=25,
    )
    assert first["status"] == "interrupted"
    assert first["succeeded_count"] == 25

    registry.messages["new-after-confirmation"] = {
        "message_id": "new-after-confirmation",
        "thread_id": "new-thread",
        "from": "new@example.com",
        "subject": "Arrived later",
        "label_ids": ["INBOX", "UNREAD"],
        "parent_folder_id": "inbox-id",
    }
    restarted = engine(path, registry)
    final = await restarted.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert final["status"] == "completed"
    assert final["succeeded_count"] == 70
    assert len(registry.writes) == 70
    assert "new-after-confirmation" not in {item[1] for item in registry.writes}


@pytest.mark.asyncio
async def test_restart_reconciles_executing_item_without_duplicate_move(tmp_path: Path) -> None:
    registry = BulkRegistry(2)
    path = tmp_path / "bulk.db"
    first_service = engine(path, registry)
    action = await snapshot(first_service)
    registry.messages["message-0"]["label_ids"] = ["TRASH"]
    with first_service._db() as connection:
        connection.execute(
            "UPDATE email_bulk_actions SET status='running' WHERE bulk_action_id=?",
            (action["bulk_action_id"],),
        )
        connection.execute(
            "UPDATE email_bulk_action_items SET status='executing',attempts=1 "
            "WHERE bulk_action_id=? AND provider_message_id='message-0'",
            (action["bulk_action_id"],),
        )

    restarted = engine(path, registry)
    final = await restarted.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert final["status"] == "completed"
    assert registry.writes == [("gmail.trash", "message-1")]


@pytest.mark.asyncio
async def test_bulk_cancellation_and_scope_isolation_cause_zero_writes(tmp_path: Path) -> None:
    registry = BulkRegistry(8)
    service = engine(tmp_path / "bulk.db", registry)
    action = await snapshot(service)

    assert (
        await service.bulk_action(
            principal_id="amber",
            conversation_id="usr:aaron:bulk",
            bulk_action_id=action["bulk_action_id"],
        )
        is None
    )
    assert (
        await service.bulk_action(
            principal_id="aaron",
            conversation_id="usr:aaron:different",
            bulk_action_id=action["bulk_action_id"],
        )
        is None
    )
    assert await service.cancel_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    result = await service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert result["status"] == "cancelled"
    assert registry.writes == []


@pytest.mark.asyncio
async def test_outlook_bulk_uses_deleted_items_and_exact_provider_identity(tmp_path: Path) -> None:
    registry = BulkRegistry(53, provider="microsoft_outlook")
    service = engine(tmp_path / "outlook-bulk.db", registry)
    action = await snapshot(service, request_id="outlook-bulk")
    result = await service.execute_bulk_action(
        principal_id="aaron",
        conversation_id="usr:aaron:bulk",
        bulk_action_id=action["bulk_action_id"],
    )
    assert result["status"] == "completed"
    assert result["provider"] == "microsoft_outlook"
    assert registry.writes == [("outlook.trash", f"message-{index}") for index in range(53)]
