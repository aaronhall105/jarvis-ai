from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai_engine import AIEngine
from app.dialogue_manager import DialogueManager
from app.external_agent_runtime import ExternalAgentRuntime


CONVERSATION = "usr:aaron:pending-mail"
AUTHORIZATION = "Send her another one saying I'll call later."


def _pending_slots(**updates: object) -> dict[str, object]:
    slots: dict[str, object] = {
        "principal_id": "aaron",
        "conversation_id": CONVERSATION,
        "operation": "send",
        "original_authorization_text": AUTHORIZATION,
        "pending_request_id": "original-mobile-request",
        "subject": "Later",
        "body": "I'll call later.",
    }
    slots.update(updates)
    return slots


@pytest.fixture
def pending_engine(tmp_path):
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    conversations = SimpleNamespace(
        add_user_message=AsyncMock(),
        add_assistant_message=AsyncMock(),
    )
    runtime = SimpleNamespace(
        _gmail_message_operation=lambda text: ExternalAgentRuntime._gmail_message_operation(text),
        _write_authorized=lambda capability, text: ExternalAgentRuntime._write_authorized(
            capability, text
        ),
        _resolve_gmail_message_recipient=AsyncMock(),
        execute_resolved_gmail_message=AsyncMock(
            return_value={
                "handled": True,
                "operation": "send",
                "success": True,
                "status": "verified",
                "sent": True,
                "recipient": "amber.gill1992@outlook.com",
                "recipient_name": "Amber",
                "recipient_source": "pending_exact_target",
                "receipt": {"status": "verified"},
            }
        ),
    )
    engine = AIEngine.__new__(AIEngine)
    engine.dialogue = dialogue
    engine.conversations = conversations
    engine.external_runtime = runtime
    engine.model = "fixture-model"
    actor = SimpleNamespace(user_key="aaron")
    return engine, dialogue, runtime, actor


async def _answer(engine, dialogue, actor, text: str):
    resolution = await dialogue.resolve_pending(CONVERSATION, text)
    assert resolution.handled is True
    return await engine._continue_pending_gmail_message(
        resolution=resolution,
        conversation_id=CONVERSATION,
        actor=actor,
        raw_user_text=text,
    )


@pytest.mark.asyncio
async def test_affirmative_candidate_completes_pending_email_before_presence(
    pending_engine,
) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(
            candidate_recipient="amber.gill1992@outlook.com",
            candidate_recipient_name="Amber",
        ),
        missing_slots=["recipient"],
    )

    result = await _answer(engine, dialogue, actor, "Yes")

    assert result["intent"] == "gmail_message_follow_up"
    assert result["response"] == "Done — I sent that email to Amber."
    runtime.execute_resolved_gmail_message.assert_awaited_once()
    sent = runtime.execute_resolved_gmail_message.await_args.kwargs
    assert sent["authorization_text"] == AUTHORIZATION
    assert sent["recipient"] == "amber.gill1992@outlook.com"
    assert sent["body"] == "I'll call later."
    assert sent["request_id"] == "original-mobile-request"
    assert (await dialogue.get(CONVERSATION)).active_goal is None


@pytest.mark.asyncio
async def test_unbacked_yes_stays_in_gmail_flow_without_presence_or_write(
    pending_engine,
) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    result = await _answer(engine, dialogue, actor, "Yes")

    assert result["intent"] == "gmail_message_awaiting_recipient"
    assert result["response"] == "Who do you mean?"
    runtime._resolve_gmail_message_recipient.assert_not_awaited()
    runtime.execute_resolved_gmail_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_candidate_keeps_original_body_and_asks_who(pending_engine) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(
            candidate_recipient="amber.gill1992@outlook.com",
            candidate_recipient_name="Amber",
        ),
        missing_slots=["recipient"],
    )

    result = await _answer(engine, dialogue, actor, "No")

    assert result["response"] == "Who do you mean?"
    runtime.execute_resolved_gmail_message.assert_not_awaited()
    state = await dialogue.get(CONVERSATION)
    assert state.slots["body"] == "I'll call later."
    assert "candidate_recipient" not in state.slots


@pytest.mark.asyncio
async def test_negative_without_candidate_is_not_treated_as_a_contact_name(pending_engine) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    result = await _answer(engine, dialogue, actor, "No")

    assert result["response"] == "Who do you mean?"
    runtime._resolve_gmail_message_recipient.assert_not_awaited()
    runtime.execute_resolved_gmail_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_answer_fills_only_recipient_and_preserves_original_body(
    pending_engine,
) -> None:
    engine, dialogue, runtime, actor = pending_engine
    runtime._resolve_gmail_message_recipient.return_value = {
        "resolved": True,
        "recipient": "amber.gill1992@outlook.com",
        "recipient_name": "Amber",
        "source": "google_contacts",
    }
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    result = await _answer(engine, dialogue, actor, "Amber")

    assert result["response"] == "Done — I sent that email to Amber."
    resolved = runtime._resolve_gmail_message_recipient.await_args.kwargs
    assert resolved["user_text"] == "Send an email to Amber"
    sent = runtime.execute_resolved_gmail_message.await_args.kwargs
    assert sent["body"] == "I'll call later."


@pytest.mark.asyncio
async def test_exact_address_answer_preserves_body_and_completes(pending_engine) -> None:
    engine, dialogue, runtime, actor = pending_engine
    runtime._resolve_gmail_message_recipient.return_value = {
        "resolved": True,
        "recipient": "amber.gill1992@outlook.com",
        "recipient_name": None,
        "source": "current_literal_address",
    }
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    await _answer(engine, dialogue, actor, "Use amber.gill1992@outlook.com")

    sent = runtime.execute_resolved_gmail_message.await_args.kwargs
    assert sent["recipient"] == "amber.gill1992@outlook.com"
    assert sent["body"] == "I'll call later."


@pytest.mark.asyncio
async def test_recipient_then_body_asks_once_and_sends_without_signoff(pending_engine) -> None:
    engine, dialogue, runtime, actor = pending_engine
    runtime._resolve_gmail_message_recipient.return_value = {
        "resolved": True,
        "recipient": "amber.gill1992@outlook.com",
        "recipient_name": "Amber",
        "source": "google_contacts",
    }
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(subject="", body=""),
        missing_slots=["recipient", "body"],
    )

    first = await _answer(engine, dialogue, actor, "Amber")
    assert first["response"] == "What would you like it to say?"
    runtime.execute_resolved_gmail_message.assert_not_awaited()

    second = await _answer(engine, dialogue, actor, "Have a good day")
    assert second["response"] == "Done — I sent that email to Amber."
    sent = runtime.execute_resolved_gmail_message.await_args.kwargs
    assert sent["subject"] == "A quick note"
    assert sent["body"] == "Have a good day"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected_body"),
    [
        ("Yes", "Have a good day\n\nLove, Aaron"),
        ("No", "Have a good day"),
    ],
)
async def test_explicit_signoff_slot_consumes_yes_or_no(
    pending_engine, answer: str, expected_body: str
) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(
            recipient="amber.gill1992@outlook.com",
            recipient_name="Amber",
            recipient_source="google_contacts",
            body="Have a good day",
            suggested_signoff="Love, Aaron",
        ),
        missing_slots=["signoff"],
    )

    await _answer(engine, dialogue, actor, answer)

    assert runtime.execute_resolved_gmail_message.await_args.kwargs["body"] == expected_body


@pytest.mark.asyncio
async def test_cancel_and_later_yes_cannot_send(pending_engine) -> None:
    _, dialogue, runtime, _ = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    cancelled = await dialogue.resolve_pending(CONVERSATION, "Cancel")
    assert cancelled.kind == "cancel_goal"
    await dialogue.clear_goal(CONVERSATION, outcome="cancelled")
    later = await dialogue.resolve_pending(CONVERSATION, "Yes")

    assert later.handled is False
    runtime.execute_resolved_gmail_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_goal_survives_restart_and_expires_safely(tmp_path) -> None:
    path = str(tmp_path / "dialogue.db")
    original = DialogueManager(path)
    await original.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    restarted = DialogueManager(path)
    assert (await restarted.resolve_pending(CONVERSATION, "Amber")).kind == "gmail_message"
    state = await restarted.get(CONVERSATION)
    state.goal_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await restarted.save(state)

    expired = DialogueManager(path)
    assert (await expired.resolve_pending(CONVERSATION, "Yes")).handled is False


@pytest.mark.asyncio
async def test_pending_goal_is_principal_and_conversation_scoped(pending_engine) -> None:
    engine, dialogue, runtime, _ = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(),
        missing_slots=["recipient"],
    )

    other_conversation = await dialogue.resolve_pending("usr:aaron:other", "Yes")
    assert other_conversation.handled is False
    resolution = await dialogue.resolve_pending(CONVERSATION, "Yes")
    result = await engine._continue_pending_gmail_message(
        resolution=resolution,
        conversation_id=CONVERSATION,
        actor=SimpleNamespace(user_key="mallory"),
        raw_user_text="Yes",
    )

    assert result["response"] == "That email request is no longer active."
    runtime.execute_resolved_gmail_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_releases_later_presence_turn(pending_engine) -> None:
    engine, dialogue, runtime, actor = pending_engine
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(
            recipient="amber.gill1992@outlook.com",
            recipient_name="Amber",
            recipient_source="google_contacts",
        ),
        missing_slots=[],
    )

    await _answer(engine, dialogue, actor, "Yes")
    presence = await dialogue.resolve_pending(CONVERSATION, "Where's Amber?")

    assert presence.handled is False
    runtime.execute_resolved_gmail_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_completion_clears_authority_instead_of_retrying_on_later_text(
    pending_engine,
) -> None:
    engine, dialogue, runtime, actor = pending_engine
    runtime.execute_resolved_gmail_message.return_value = {
        "handled": True,
        "operation": "send",
        "success": False,
        "status": "outcome_unknown",
        "error": "Provider outcome could not be verified",
    }
    await dialogue.begin_goal(
        CONVERSATION,
        "gmail_message",
        slots=_pending_slots(
            recipient="amber.gill1992@outlook.com",
            recipient_name="Amber",
            recipient_source="google_contacts",
        ),
        missing_slots=[],
    )

    first = await _answer(engine, dialogue, actor, "Yes")
    later = await dialogue.resolve_pending(CONVERSATION, "Yes")

    assert first["success"] is False
    assert "won’t claim" in first["response"]
    assert later.handled is False
    runtime.execute_resolved_gmail_message.assert_awaited_once()


def test_pending_gmail_precedes_understanding_and_presence_routing() -> None:
    source = inspect.getsource(AIEngine.ask)
    pending = source.index('dialogue_resolution.kind == "gmail_message"')
    understanding = source.index("self.understanding.interpret")
    assert pending < understanding
