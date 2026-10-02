from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.dialogue_manager import DialogueManager
from app.pending_interactions import PendingInteractionKind, PendingInteractionService


def service(tmp_path, *, dialogue: DialogueManager | None = None):
    manager = dialogue or DialogueManager(str(tmp_path / "dialogue.db"))
    proposals = SimpleNamespace(resolve=AsyncMock(return_value=None))
    return (
        PendingInteractionService(dialogue=manager, action_proposals=proposals),
        manager,
        proposals,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ("home", "calendar", "research", "contact", "device"))
async def test_generic_clarification_survives_restart_and_resolves_any_domain(tmp_path, domain):
    first, manager, _ = service(tmp_path)
    handler = AsyncMock(
        return_value={
            "success": True,
            "turn_handled": True,
            "action_outcome": "completed",
            "response": f"Used the selected {domain} option.",
        }
    )
    first.register_handler("continue_generic", handler)
    created = await first.begin(
        principal_id="aaron",
        conversation_id=f"usr:aaron:{domain}",
        kind=PendingInteractionKind.CLARIFICATION,
        goal=f"Resolve a {domain} reference",
        prompt=f"Do you mean the selected {domain} option?",
        unresolved_slot=f"{domain}_identity",
        handler_id="continue_generic",
        context={"domain": domain},
        proposed_value=f"trusted-{domain}-id",
    )

    restarted, _, _ = service(tmp_path, dialogue=DialogueManager(str(manager.database_path)))
    restarted.register_handler("continue_generic", handler)
    resolved = await restarted.resolve(
        principal_id="aaron",
        conversation_id=f"usr:aaron:{domain}",
        answer="Yes",
        request_id=f"resolve-{domain}",
    )

    assert resolved is not None and resolved.handled is True
    assert resolved.interaction_id == created["interaction_id"]
    assert resolved.answer == f"trusted-{domain}-id"
    handler.assert_awaited_once()
    assert handler.await_args.args[0]["context"] == {"domain": domain}
    assert (
        await restarted.current(principal_id="aaron", conversation_id=f"usr:aaron:{domain}") is None
    )


@pytest.mark.asyncio
async def test_selection_is_scoped_and_duplicate_safe(tmp_path):
    pending, _, _ = service(tmp_path)
    handler = AsyncMock(
        return_value={"success": True, "response": "Selected Outlook.", "intent": "selected"}
    )
    pending.register_handler("select_provider", handler)
    await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        kind=PendingInteractionKind.SELECTION,
        goal="Choose a mailbox",
        prompt="Which mailbox — Gmail or Outlook?",
        unresolved_slot="provider",
        handler_id="select_provider",
        options=(
            {"option_id": "gmail", "label": "Gmail", "value": "google_gmail"},
            {"option_id": "outlook", "label": "Outlook", "value": "microsoft_outlook"},
        ),
    )

    assert (
        await pending.resolve(
            principal_id="mallory",
            conversation_id="usr:aaron:mail",
            answer="Outlook",
            request_id="wrong-principal",
        )
        is None
    )
    assert (
        await pending.resolve(
            principal_id="aaron",
            conversation_id="usr:aaron:other",
            answer="Outlook",
            request_id="wrong-conversation",
        )
        is None
    )
    resolved = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        answer="the second one",
        request_id="right-answer",
    )
    duplicate = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:mail",
        answer="the second one",
        request_id="duplicate",
    )

    assert resolved is not None and resolved.answer == "microsoft_outlook"
    assert duplicate is None
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_negative_declines_without_calling_handler(tmp_path):
    pending, _, _ = service(tmp_path)
    handler = AsyncMock()
    pending.register_handler("clarify", handler)
    await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:decline",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Clarify a reference",
        prompt="Do you mean the first one?",
        unresolved_slot="reference",
        handler_id="clarify",
        proposed_value="first",
    )

    resolution = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:decline",
        answer="Never mind",
        request_id="decline-1",
    )

    assert resolution is not None
    assert "won’t continue" in str(resolution.response)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_interaction_cannot_be_consumed(tmp_path):
    pending, manager, _ = service(tmp_path)
    handler = AsyncMock(return_value={"success": True, "response": "continued"})
    pending.register_handler("clarify", handler)
    await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:expired",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Clarify an old reference",
        prompt="Do you mean the old option?",
        unresolved_slot="reference",
        handler_id="clarify",
        proposed_value="old-option",
    )
    state = await manager.get("usr:aaron:expired")
    state.goal_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await manager.save(state)

    resolution = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:expired",
        answer="Yes",
        request_id="expired-answer",
    )

    assert resolution is None
    handler.assert_not_awaited()
    expired_state = await manager.get("usr:aaron:expired")
    assert expired_state.working_context["active_interaction_id"] is None


@pytest.mark.asyncio
async def test_restart_recovers_claimed_non_authoritative_interaction_safely(tmp_path):
    first, manager, _ = service(tmp_path)
    first.register_handler("clarify", AsyncMock())
    created = await first.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:recover",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Clarify a read filter",
        prompt="Do you mean Outlook?",
        unresolved_slot="provider",
        handler_id="clarify",
        proposed_value="microsoft_outlook",
    )
    assert await first._claim(
        conversation_id="usr:aaron:recover",
        interaction_id=created["interaction_id"],
        request_id="interrupted-request",
        answer="microsoft_outlook",
    )

    restarted, _, _ = service(tmp_path, dialogue=DialogueManager(str(manager.database_path)))
    handler = AsyncMock(return_value={"success": True, "response": "Outlook selected."})
    restarted.register_handler("clarify", handler)
    resolution = await restarted.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:recover",
        answer="Yes",
        request_id="retry-after-restart",
    )

    assert resolution is not None and resolution.answer == "microsoft_outlook"
    handler.assert_awaited_once()
    assert (
        await restarted.current(principal_id="aaron", conversation_id="usr:aaron:recover") is None
    )


@pytest.mark.asyncio
async def test_new_interaction_supersedes_old_interaction_without_consuming_it(tmp_path):
    pending, _, _ = service(tmp_path)
    handler = AsyncMock(return_value={"success": True, "response": "continued"})
    pending.register_handler("clarify", handler)
    first = await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:superseded",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Choose a calendar",
        prompt="Do you mean Work?",
        unresolved_slot="calendar",
        handler_id="clarify",
        proposed_value="work-calendar",
    )
    second = await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:superseded",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Choose a device",
        prompt="Do you mean Hallway camera?",
        unresolved_slot="device",
        handler_id="clarify",
        proposed_value="hallway-camera",
    )

    current = await pending.current(principal_id="aaron", conversation_id="usr:aaron:superseded")
    assert current is not None
    assert current["interaction_id"] == second["interaction_id"]
    assert current["interaction_id"] != first["interaction_id"]
    resolution = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:superseded",
        answer="Yes",
        request_id="new-only",
    )
    assert resolution is not None and resolution.answer == "hallway-camera"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_interaction_delegates_only_bare_confirmation_to_action_proposals(tmp_path):
    pending, _, proposals = service(tmp_path)
    proposals.resolve.return_value = SimpleNamespace(
        handled=True,
        response="The registered action ran.",
        proposal_id="proposal-1",
        as_result=lambda: {"success": True, "response": "The registered action ran."},
    )

    assert (
        await pending.resolve(
            principal_id="aaron",
            conversation_id="usr:aaron:proposal",
            answer="an unrelated sentence",
            request_id="ordinary",
        )
        is None
    )
    resolved = await pending.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:proposal",
        answer="Go ahead",
        request_id="proposal-confirm",
    )

    assert resolved is not None and resolved.kind == "action_proposal"
    proposals.resolve.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    (PendingInteractionKind.ACTION_PROPOSAL, PendingInteractionKind.AUTHORITY_CONFIRMATION),
)
async def test_clarification_store_cannot_create_execution_authority(tmp_path, kind):
    pending, _, _ = service(tmp_path)
    pending.register_handler("unsafe", AsyncMock())

    with pytest.raises(ValueError, match="planner-backed proposal"):
        await pending.begin(
            principal_id="aaron",
            conversation_id="usr:aaron:authority",
            kind=kind,
            goal="Run an action",
            prompt="Should I do it?",
            unresolved_slot="authority",
            handler_id="unsafe",
            proposed_value="execute",
        )
