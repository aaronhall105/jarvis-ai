from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("JARVIS_DATA_DIR", "/tmp/jarvis-task-centre-routing-tests")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app import main
from app.conversation_engine import ConversationEngine
from app.dialogue_manager import DialogueManager


def actor() -> SimpleNamespace:
    return SimpleNamespace(user_key="aaron")


@pytest.mark.asyncio
async def test_screenshot_notify_when_done_binds_inbox_cleanup_before_generic_notification(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    centre = SimpleNamespace(
        bind_notification_from_conversation=AsyncMock(
            return_value={
                "bound": True,
                "task": {
                    "task_id": "email_group:cleanup-1",
                    "title": "Inbox cleanup",
                    "notification_on_completion": True,
                },
                "response": "Yes — I’ll let you know when the inbox cleanup finishes.",
            }
        ),
        retry_from_conversation=AsyncMock(),
    )
    monkeypatch.setattr(main, "task_centre", centre)
    monkeypatch.setattr(
        main, "conversations", ConversationEngine(str(tmp_path / "conversations.db"))
    )
    monkeypatch.setattr(main, "dialogue", DialogueManager(str(tmp_path / "dialogue.db")))
    generic = AsyncMock(side_effect=AssertionError("generic notification flow must not run"))
    monkeypatch.setattr(main.ai, "ask", generic)

    result = await main._execute_ai_request(
        main.TextCommandRequest(
            text="Notify me when your done",
            conversation_id="cleanup-chat",
            request_id="notify-cleanup-1",
            user_id="aaron",
            user_name="Aaron",
        )
    )

    assert result["intent"] == "task_notification_bound"
    assert result["response"] == ("Yes — I’ll let you know when the inbox cleanup finishes.")
    assert "what should the notification say" not in str(result["response"]).casefold()
    centre.bind_notification_from_conversation.assert_awaited_once_with(
        principal_id="aaron",
        conversation_id="usr:aaron:cleanup-chat",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    generic.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_standalone_notification_is_not_claimed_as_task_subscription() -> None:
    result = await main._try_handle_task_notification(
        "Send a notification to my phone",
        actor=actor(),  # type: ignore[arg-type]
        conversation_id="usr:aaron:chat",
    )
    assert result is None


@pytest.mark.asyncio
async def test_retry_response_reports_durable_waiting_state_not_fake_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centre = SimpleNamespace(
        retry_from_conversation=AsyncMock(
            return_value={
                "handled": True,
                "retried": True,
                "response": "Inbox cleanup is still waiting — Gmail unavailable.",
                "task": {
                    "task_id": "email_group:cleanup-1",
                    "task_type": "email_cleanup",
                    "status": "WAITING_FOR_JARVIS",
                },
            }
        )
    )
    monkeypatch.setattr(main, "task_centre", centre)

    result = await main._try_handle_task_retry(
        "Try again",
        actor=actor(),  # type: ignore[arg-type]
        conversation_id="usr:aaron:cleanup-chat",
    )

    assert result is not None
    assert result["action_outcome"] == "blocked"
    assert "still waiting" in str(result["response"]).casefold()
    assert "starting" not in str(result["response"]).casefold()


@pytest.mark.asyncio
async def test_screenshot_retry_then_notify_binds_the_same_durable_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centre = SimpleNamespace(
        retry_from_conversation=AsyncMock(
            return_value={
                "handled": True,
                "retried": True,
                "response": "Inbox cleanup is still waiting — Gmail unavailable.",
                "task": {
                    "task_id": "email_group:cleanup-1",
                    "task_type": "email_cleanup",
                    "status": "WAITING_FOR_JARVIS",
                },
            }
        ),
        bind_notification_from_conversation=AsyncMock(
            return_value={
                "bound": True,
                "task": {
                    "task_id": "email_group:cleanup-1",
                    "notification_on_completion": True,
                },
                "response": "Yes — I’ll let you know when the inbox cleanup finishes.",
            }
        ),
    )
    monkeypatch.setattr(main, "task_centre", centre)

    retry = await main._try_handle_task_retry(
        "Try again",
        actor=actor(),  # type: ignore[arg-type]
        conversation_id="usr:aaron:cleanup-chat",
    )
    notification = await main._try_handle_task_notification(
        "Notify me when your done",
        actor=actor(),  # type: ignore[arg-type]
        conversation_id="usr:aaron:cleanup-chat",
    )

    assert retry is not None
    assert retry["action_outcome"] == "blocked"
    assert "starting" not in str(retry["response"]).casefold()
    assert notification is not None
    assert notification["intent"] == "task_notification_bound"
    assert notification["response"] == (
        "Yes — I’ll let you know when the inbox cleanup finishes."
    )
    assert "what should the notification say" not in str(notification["response"]).casefold()
    centre.bind_notification_from_conversation.assert_awaited_once_with(
        principal_id="aaron",
        conversation_id="usr:aaron:cleanup-chat",
        notify_on_completion=True,
        notify_on_failure=False,
    )
