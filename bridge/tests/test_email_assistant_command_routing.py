from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("JARVIS_DATA_DIR", "/tmp/jarvis-email-assistant-command-tests")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app import main
from app.dialogue_manager import DialogueManager
from app.user_context import UserContext


def actor(name: str = "aaron") -> UserContext:
    return UserContext(
        user_id=name,
        user_key=name,
        display_name=name.title(),
        is_admin=name == "aaron",
    )


@pytest.mark.parametrize(
    "text",
    (
        "Delete emails after 30 days if not archived",
        "Trash Gmail messages older than 60 days",
        "Clean up promotional emails",
        "Get rid of newsletters",
    ),
)
def test_broad_cleanup_phrases_use_confirmation_route(text: str) -> None:
    assert main._email_cleanup_policy_request(text) is True


@pytest.mark.parametrize(
    "text",
    ("Delete that email", "Move this email to trash", "What would you delete?"),
)
def test_message_level_and_preview_requests_are_not_bulk_policy_intent(text: str) -> None:
    assert main._email_cleanup_policy_request(text) is False


@pytest.fixture(autouse=True)
def isolated_dialogue(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "dialogue", DialogueManager(str(tmp_path / "dialogue.db")))


@pytest.mark.asyncio
async def test_cleanup_preview_is_read_only_and_natural(monkeypatch) -> None:
    engine = SimpleNamespace(
        assistant_status=AsyncMock(return_value=None),
        preview_cleanup=AsyncMock(
            return_value={"dry_run_candidates": 18, "coverage_partial": False}
        ),
    )
    monkeypatch.setattr(main, "email_policies", engine)

    result = await main._try_handle_email_assistant(
        "What would you delete?",
        actor=actor(),
        conversation_id="usr:aaron:mail",
        request_id="preview-1",
    )

    assert result is not None
    assert result["success"] is True
    assert "18 old low-value emails" in str(result["response"])
    engine.preview_cleanup.assert_awaited_once_with(
        principal_id="aaron", conversation_id="usr:aaron:mail"
    )


@pytest.mark.asyncio
async def test_explicit_cleanup_command_configures_safe_policy_not_legacy_retention(
    monkeypatch,
) -> None:
    engine = SimpleNamespace(
        assistant_status=AsyncMock(return_value=None),
        preview_cleanup=AsyncMock(
            return_value={"dry_run_candidates": 7, "coverage_partial": False}
        ),
        configure_assistant=AsyncMock(
            return_value={"cleanup_mode": "trash", "cleanup_age_days": 30}
        ),
    )
    monkeypatch.setattr(main, "email_policies", engine)

    proposed = await main._try_handle_email_assistant(
        "Delete old newsletters after 30 days.",
        actor=actor(),
        conversation_id="usr:aaron:mail",
        request_id="cleanup-1",
    )
    confirmed = await main._try_handle_email_assistant(
        "Yes",
        actor=actor(),
        conversation_id="usr:aaron:mail",
        request_id="cleanup-2",
    )

    assert proposed is not None
    assert proposed["intent"] == "email_cleanup_awaiting_confirmation"
    assert "7 old low-value emails" in str(proposed["response"])
    assert confirmed is not None
    assert "only read, low-value" in str(confirmed["response"])
    engine.preview_cleanup.assert_awaited_once_with(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        cleanup_mode="trash",
        cleanup_age_days=30,
    )
    engine.configure_assistant.assert_awaited_once_with(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        inbox_cleanup=True,
        cleanup_dry_run=False,
        cleanup_mode="trash",
        cleanup_age_days=30,
    )


@pytest.mark.asyncio
async def test_bulk_cleanup_cancellation_never_enables_mutations(monkeypatch) -> None:
    engine = SimpleNamespace(
        assistant_status=AsyncMock(return_value=None),
        preview_cleanup=AsyncMock(
            return_value={"dry_run_candidates": 3, "coverage_partial": False}
        ),
        configure_assistant=AsyncMock(),
    )
    monkeypatch.setattr(main, "email_policies", engine)

    await main._try_handle_email_assistant(
        "Clean up my inbox.",
        actor=actor(),
        conversation_id="usr:aaron:cleanup-cancel",
        request_id="cleanup-3",
    )
    cancelled = await main._try_handle_email_assistant(
        "No",
        actor=actor(),
        conversation_id="usr:aaron:cleanup-cancel",
        request_id="cleanup-4",
    )

    assert cancelled is not None
    assert cancelled["intent"] == "email_cleanup_cancelled"
    engine.configure_assistant.assert_not_awaited()
    state = await main.dialogue.get("usr:aaron:cleanup-cancel")
    assert state.active_goal is None


@pytest.mark.asyncio
async def test_email_assistant_router_never_hijacks_an_existing_pending_goal(monkeypatch) -> None:
    engine = SimpleNamespace(
        assistant_status=AsyncMock(return_value=None),
        configure_assistant=AsyncMock(),
    )
    monkeypatch.setattr(main, "email_policies", engine)
    await main.dialogue.begin_goal(
        "usr:aaron:pending-message",
        "gmail_message",
        missing_slots=("body",),
        slots={"original_authorization_text": "Send Amber an email"},
    )

    result = await main._try_handle_email_assistant(
        "Pause email alerts",
        actor=actor(),
        conversation_id="usr:aaron:pending-message",
        request_id="pending-1",
    )

    assert result is None
    engine.configure_assistant.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_assistant_status_is_principal_scoped_and_raw_only_when_requested(
    monkeypatch,
) -> None:
    status = {
        "principal_id": "aaron",
        "status": "active",
        "important_email_alerts": True,
        "reply_alerts": True,
        "inbox_cleanup": False,
        "cleanup_dry_run": True,
        "last_gmail_check": None,
        "provider_error": None,
    }
    engine = SimpleNamespace(assistant_status=AsyncMock(return_value=status))
    monkeypatch.setattr(main, "email_policies", engine)

    natural = await main._try_handle_email_assistant(
        "Is my email assistant running?",
        actor=actor(),
        conversation_id="usr:aaron:mail",
        request_id=None,
    )
    raw = await main._try_handle_email_assistant(
        "Show me the raw technical email assistant status",
        actor=actor(),
        conversation_id="usr:aaron:mail",
        request_id=None,
    )

    assert natural is not None
    assert natural["response"] == (
        "Your email assistant is running with important-email alerts, reply alerts."
    )
    assert raw is not None
    assert '"principal_id": "aaron"' in str(raw["response"])
    assert engine.assistant_status.await_count == 2
