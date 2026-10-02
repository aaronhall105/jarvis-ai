from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("JARVIS_DATA_DIR", "/tmp/jarvis-working-context-routing-tests")
os.environ.setdefault("OPENAI_API_KEY", "synthetic-openai-key-for-tests")

from app import main
from app.dialogue_manager import DialogueManager
from app.pending_interactions import PendingInteractionService
from app.user_context import UserContext
from app.working_context import WorkingContextService, make_context_object


def _actor(principal: str = "aaron") -> UserContext:
    return UserContext(
        user_id=principal,
        user_key=principal,
        display_name=principal.title(),
        is_admin=principal == "aaron",
    )


@pytest.fixture
def context_runtime(tmp_path, monkeypatch):
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    service = WorkingContextService(dialogue)
    proposals = SimpleNamespace(resolve=AsyncMock(return_value=None))
    interactions = PendingInteractionService(
        dialogue=dialogue,
        action_proposals=proposals,
        ttl_seconds=600,
    )
    interactions.register_handler(
        "working_context_selection", main._continue_working_context_selection
    )
    interactions.register_handler(
        "document_attachment_selection", main._continue_document_attachment_selection
    )
    monkeypatch.setattr(main, "dialogue", dialogue)
    monkeypatch.setattr(main, "working_context", service)
    monkeypatch.setattr(main, "pending_interactions", interactions)
    return dialogue, service, interactions


@pytest.mark.asyncio
async def test_email_followups_answer_sender_date_previous_and_compare(context_runtime) -> None:
    dialogue, service, _ = context_runtime
    conversation = "usr:aaron:wageslip-continuation"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "query_kind": "topic_search",
            "topic_query": "wage slip",
            "messages": [
                {
                    "message_id": "september",
                    "sender_name": "Joseph Scott",
                    "from": "Joseph Scott <payroll@example.invalid>",
                    "subject": "WAGE SLIP",
                    "received_at": "2026-09-24T14:11:47+00:00",
                },
                {
                    "message_id": "august",
                    "sender_name": "Joseph Scott",
                    "from": "Joseph Scott <payroll@example.invalid>",
                    "subject": "Wage slip August",
                    "received_at": "2026-08-24T14:11:47+00:00",
                },
            ],
        },
    )

    sender = await main._try_handle_working_context_followup(
        "Who sent it?", actor=_actor(), conversation_id=conversation
    )
    date = await main._try_handle_working_context_followup(
        "What date is it?", actor=_actor(), conversation_id=conversation
    )
    previous = await main._try_handle_working_context_followup(
        "What about the previous one?", actor=_actor(), conversation_id=conversation
    )
    compared = await main._try_handle_working_context_followup(
        "Compare them", actor=_actor(), conversation_id=conversation
    )

    assert sender and sender["response"] == "It’s from Joseph Scott."
    assert date and date["response"] == "It’s dated 24 September."
    assert previous and "Wage slip August" in str(previous["response"])
    assert compared and "WAGE SLIP" in str(compared["response"])
    assert "Wage slip August" in str(compared["response"])
    stored = await service.get(principal_id="aaron", conversation_id=conversation)
    assert stored["derived_results"][0]["type"] == "comparison"


@pytest.mark.asyncio
async def test_ambiguous_open_it_uses_pending_interaction_and_task_centre_path(
    context_runtime,
) -> None:
    _dialogue, service, interactions = context_runtime
    conversation = "usr:aaron:ambiguous-open"
    first = make_context_object(
        object_type="document", display_name="Quote A", source="files", canonical_id="a"
    )
    second = make_context_object(
        object_type="document", display_name="Quote B", source="files", canonical_id="b"
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[first, second],
        result_set={"object_refs": [first.reference_id, second.reference_id]},
    )

    question = await main._try_handle_working_context_followup(
        "Open it", actor=_actor(), conversation_id=conversation
    )
    assert question and question["action_outcome"] == "waiting_user"
    assert "Quote A or Quote B" in str(question["response"])
    pending = await interactions.current(principal_id="aaron", conversation_id=conversation)
    assert pending and pending["kind"] == "selection"
    state = await service.get(principal_id="aaron", conversation_id=conversation)
    assert state["active_interaction_id"] == pending["interaction_id"]

    selected = await interactions.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        answer="the first one",
        request_id="selection-1",
    )
    assert selected and selected.handled is True
    assert "don’t currently have a registered capability" in str(selected.response)
    completed = await service.get(principal_id="aaron", conversation_id=conversation)
    assert completed["active_interaction_id"] is None
    assert completed["waiting_state"]["status"] == "waiting_for_capability"
    assert selected.result and selected.result["action_outcome"] == "waiting_capability"


@pytest.mark.asyncio
async def test_live_state_followup_does_not_reuse_stale_home_evidence(context_runtime) -> None:
    _dialogue, service, _ = context_runtime
    conversation = "usr:aaron:stale-home"
    stale = make_context_object(
        object_type="device",
        display_name="Hall light",
        source="home_assistant",
        canonical_id="light.hall",
        provider="home_assistant",
        metadata={"state": "on", "area_name": "Hall"},
        observed_at="2020-01-01T00:00:00+00:00",
        freshness_seconds=30,
        immutable=False,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[stale],
        result_set={"object_refs": [stale.reference_id]},
        focus_refs=[stale.reference_id],
    )
    result = await main._try_handle_working_context_followup(
        "Where is it now?", actor=_actor(), conversation_id=conversation
    )
    assert result is None


@pytest.mark.asyncio
async def test_reference_resolution_does_not_grant_email_delete_authority(
    context_runtime,
) -> None:
    _dialogue, service, interactions = context_runtime
    conversation = "usr:aaron:authority"
    email = make_context_object(
        object_type="email_message",
        display_name="Statement",
        source="provider_mailbox_read",
        canonical_id="message-id",
        provider="microsoft_outlook",
        metadata={"subject": "Statement"},
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[email],
        result_set={"object_refs": [email.reference_id]},
        focus_refs=[email.reference_id],
    )
    # Mutations fall through to the existing provider/authority route.
    result = await main._try_handle_working_context_followup(
        "Delete it", actor=_actor(), conversation_id=conversation
    )
    assert result is None
    assert await interactions.current(principal_id="aaron", conversation_id=conversation) is None


@pytest.mark.asyncio
async def test_next_result_monitor_uses_existing_durable_monitor_runtime(
    context_runtime, monkeypatch
) -> None:
    dialogue, _service, _interactions = context_runtime
    conversation = "usr:aaron:next-wageslip"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "query_kind": "topic_search",
            "topic_query": "wage slip",
            "messages": [
                {
                    "message_id": "september",
                    "sender_name": "Joseph Scott",
                    "subject": "WAGE SLIP",
                    "received_at": "2026-09-24T14:11:47+00:00",
                }
            ],
        },
    )
    create_monitor = AsyncMock(
        return_value={"success": True, "job_id": "monitor-1", "status": "pending"}
    )
    monkeypatch.setattr(
        main,
        "external_agent",
        SimpleNamespace(create_external_monitor=create_monitor),
    )

    result = await main._try_handle_working_context_followup(
        "Let me know when the next one arrives",
        actor=_actor(),
        conversation_id=conversation,
        request_id="monitor-request-1",
    )

    assert result and result["action_outcome"] == "completed"
    assert result["task_id"] == "monitor-1"
    create_monitor.assert_awaited_once_with(
        conversation_id=conversation,
        principal_id="aaron",
        provider="microsoft",
        capability_id="outlook.search",
        query="wage slip",
        value_path="message_ids",
        comparison={"operator": "new_items"},
        polling_interval_seconds=300,
        label="Next WAGE SLIP",
        continuous=False,
        notify=True,
        request_id="monitor-request-1",
    )
    state = await dialogue.get(conversation)
    assert state.working_context["active_task_id"] == "followup:monitor-1"


@pytest.mark.asyncio
async def test_pause_first_task_resolves_context_then_uses_task_centre_authority(
    context_runtime, monkeypatch
) -> None:
    _dialogue, service, _interactions = context_runtime
    conversation = "usr:aaron:task-control"
    first = make_context_object(
        object_type="task",
        display_name="Inbox cleanup",
        source="task_centre",
        canonical_id="followup:cleanup-1",
        task_id="followup:cleanup-1",
        metadata={"status": "RUNNING"},
        immutable=False,
        freshness_seconds=10,
    )
    second = make_context_object(
        object_type="task",
        display_name="Amplifier research",
        source="task_centre",
        canonical_id="executive:research-2",
        task_id="executive:research-2",
        metadata={"status": "RUNNING"},
        immutable=False,
        freshness_seconds=10,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[first, second],
        result_set={"object_refs": [first.reference_id, second.reference_id]},
        focus_refs=[first.reference_id, second.reference_id],
    )
    pause = AsyncMock(
        return_value={
            "task_id": "followup:cleanup-1",
            "title": "Inbox cleanup",
            "status": "PAUSED",
        }
    )
    monkeypatch.setattr(main, "task_centre", SimpleNamespace(pause=pause))

    result = await main._try_handle_working_context_followup(
        "Pause the first one",
        actor=_actor(),
        conversation_id=conversation,
        request_id="pause-1",
    )

    assert result and result["response"] == "Inbox cleanup is paused."
    pause.assert_awaited_once_with(
        principal_id="aaron",
        task_id="followup:cleanup-1",
        request_id="pause-1",
    )


@pytest.mark.asyncio
async def test_why_explains_latest_grounded_comparison(context_runtime) -> None:
    _dialogue, service, _interactions = context_runtime
    conversation = "usr:aaron:comparison-why"
    current = make_context_object(
        object_type="measurement",
        display_name="September net pay",
        source="verified_document_result",
        canonical_id="pay-september",
        metadata={"net_pay": 2400, "unit": "pounds"},
        immutable=True,
    )
    previous = make_context_object(
        object_type="measurement",
        display_name="August net pay",
        source="verified_document_result",
        canonical_id="pay-august",
        metadata={"net_pay": 2250, "unit": "pounds"},
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[current, previous],
        result_set={"object_refs": [current.reference_id, previous.reference_id]},
        focus_refs=[current.reference_id, previous.reference_id],
    )
    await service.compare(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[current, previous],
        metric="net_pay",
    )

    result = await main._try_handle_working_context_followup(
        "Why?", actor=_actor(), conversation_id=conversation
    )

    assert result
    assert "September net pay was 2400 pounds" in str(result["response"])
    assert "August net pay was 2250 pounds" in str(result["response"])


@pytest.mark.asyncio
async def test_current_value_and_temporal_comparison_use_grounded_measurements(
    context_runtime,
) -> None:
    _dialogue, service, _interactions = context_runtime
    conversation = "usr:aaron:measurement-continuity"
    current = make_context_object(
        object_type="measurement",
        display_name="September net pay",
        source="verified_document_result",
        canonical_id="pay-september",
        metadata={"net_pay": 2400, "unit": "pounds"},
        immutable=True,
    )
    previous = make_context_object(
        object_type="measurement",
        display_name="August net pay",
        source="verified_document_result",
        canonical_id="pay-august",
        metadata={"net_pay": 2250, "unit": "pounds"},
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[current, previous],
        result_set={"object_refs": [current.reference_id, previous.reference_id]},
        focus_refs=[current.reference_id],
    )

    amount = await main._try_handle_working_context_followup(
        "How much did I get?", actor=_actor(), conversation_id=conversation
    )
    comparison = await main._try_handle_working_context_followup(
        "Is that more than last time?", actor=_actor(), conversation_id=conversation
    )

    assert amount and amount["response"] == "September net pay is 2400 pounds."
    assert comparison and "150 pounds more" in str(comparison["response"])


@pytest.mark.asyncio
async def test_temporal_comparison_uses_verified_document_metrics(
    context_runtime,
) -> None:
    _dialogue, service, _interactions = context_runtime
    conversation = "usr:aaron:document-comparison"
    current = make_context_object(
        object_type="document",
        display_name="Current statement",
        source="verified_document_extraction",
        canonical_id="current-document",
        metadata={
            "net_pay": 2400,
            "primary_metric": "net_pay",
            "currency": "GBP",
        },
        immutable=True,
    )
    previous = make_context_object(
        object_type="document",
        display_name="Previous statement",
        source="verified_document_extraction",
        canonical_id="previous-document",
        metadata={
            "net_pay": 2250,
            "primary_metric": "net_pay",
            "currency": "GBP",
        },
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[current, previous],
        result_set={"object_refs": [current.reference_id, previous.reference_id]},
        focus_refs=[current.reference_id],
    )

    comparison = await main._try_handle_working_context_followup(
        "Is that more than last time?", actor=_actor(), conversation_id=conversation
    )

    assert comparison and "150 GBP more" in str(comparison["response"])


@pytest.mark.asyncio
async def test_unverified_document_amount_waits_for_real_capability(
    context_runtime,
) -> None:
    _dialogue, service, _interactions = context_runtime
    conversation = "usr:aaron:document-capability-gap"
    message = make_context_object(
        object_type="email_message",
        display_name="WAGE SLIP",
        source="provider_mailbox_read",
        canonical_id="message-1",
        provider="microsoft_outlook",
        metadata={"attachments": [{"filename": "wage-slip.pdf"}]},
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[message],
        result_set={"object_refs": [message.reference_id]},
        focus_refs=[message.reference_id],
    )

    result = await main._try_handle_working_context_followup(
        "How much did I get?", actor=_actor(), conversation_id=conversation
    )

    assert result and result["action_outcome"] == "failed"
    assert "couldn’t read that attachment safely" in str(result["response"])


def _read_result(*, selection_required: bool = False) -> dict:
    if selection_required:
        return {
            "success": True,
            "data": {
                "selection_required": True,
                "attachments": [
                    {
                        "attachment_id": "attachment-a",
                        "filename": "document-a.pdf",
                        "mime_type": "application/pdf",
                    },
                    {
                        "attachment_id": "attachment-b",
                        "filename": "document-b.pdf",
                        "mime_type": "application/pdf",
                    },
                ],
            },
        }
    text = "Gross Pay: £3000\nTax: £400\nNet Pay: £2400"
    return {
        "success": True,
        "data": {
            "provider": "microsoft_outlook",
            "attachment": {
                "attachment_id": "attachment-a",
                "filename": "pay.pdf",
                "mime_type": "application/pdf",
            },
            "document": {
                "filename": "pay.pdf",
                "mime_type": "application/pdf",
                "fingerprint_sha256": "f" * 64,
                "size_bytes": 123,
                "page_count": 1,
                "text_content": text,
                "text_chunks": [{"page": 1, "text": text}],
                "truncated": False,
                "evidence_status": "verified",
                "warnings": [],
            },
            "cache_hit": False,
        },
    }


@pytest.mark.asyncio
async def test_grounded_email_attachment_answers_generic_document_questions(
    context_runtime, monkeypatch
) -> None:
    dialogue, service, _ = context_runtime
    conversation = "usr:aaron:generic-document-reading"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "messages": [
                {
                    "message_id": "message-a",
                    "subject": "Payroll document",
                    "sender_name": "Payroll",
                    "attachments": [{"present": True}],
                }
            ],
        },
    )
    execute = AsyncMock(return_value=_read_result())
    monkeypatch.setattr(main.external_agent, "execute", execute)
    monkeypatch.setattr(
        main.ai,
        "select_document_evidence",
        AsyncMock(
            side_effect=[
                {
                    "found": True,
                    "label": "Net Pay",
                    "value": "£2400",
                    "evidence_quote": "Net Pay: £2400",
                },
                {
                    "found": True,
                    "label": "Tax",
                    "value": "£400",
                    "evidence_quote": "Tax: £400",
                },
                {
                    "found": True,
                    "label": "Gross Pay",
                    "value": "£3000",
                    "evidence_quote": "Gross Pay: £3000",
                },
            ]
        ),
    )

    net = await main._try_handle_working_context_followup(
        "How much did I get?", actor=_actor(), conversation_id=conversation
    )
    tax = await main._try_handle_working_context_followup(
        "How much tax did I pay?", actor=_actor(), conversation_id=conversation
    )
    gross = await main._try_handle_working_context_followup(
        "What was my gross pay?", actor=_actor(), conversation_id=conversation
    )

    assert net and net["response"] == "Net Pay was £2400."
    assert tax and tax["response"] == "Tax was £400."
    assert gross and gross["response"] == "Gross Pay was £3000."
    assert all(call.args[0] == "document.read" for call in execute.await_args_list)
    context = await service.get(principal_id="aaron", conversation_id=conversation)
    documents = [item for item in context["objects"] if item["object_type"] == "document"]
    assert documents and documents[0]["metadata"]["gross_pay"] == 3000
    assert "text_content" not in documents[0]["metadata"]


@pytest.mark.asyncio
async def test_document_question_resumes_durable_topic_search_then_reads_attachment(
    context_runtime, monkeypatch
) -> None:
    dialogue, _service, _ = context_runtime
    conversation = "usr:aaron:document-search-resumption"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "query_kind": "topic_search",
            "topic_query": "pay statement",
            "messages": [],
        },
    )
    search_mailbox = AsyncMock(
        return_value={
            "success": True,
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "messages": [
                {
                    "message_id": "message-a",
                    "subject": "Payroll document",
                    "sender_name": "Payroll",
                    "attachments": [{"present": True}],
                }
            ],
            "count": 1,
            "exact": True,
            "query_kind": "topic_search",
            "topic_query": "pay statement",
        }
    )
    monkeypatch.setattr(
        main,
        "email_policies",
        SimpleNamespace(
            assistant_status=AsyncMock(
                return_value={
                    "accounts": [
                        {
                            "provider": "microsoft_outlook",
                            "account_id": "outlook-account",
                            "healthy": True,
                        }
                    ]
                }
            ),
            search_mailbox=search_mailbox,
        ),
    )
    document_read = AsyncMock(return_value=_read_result())
    monkeypatch.setattr(main.external_agent, "execute", document_read)
    monkeypatch.setattr(
        main.ai,
        "select_document_evidence",
        AsyncMock(
            return_value={
                "found": True,
                "label": "Net Pay",
                "value": "£2400",
                "evidence_quote": "Net Pay: £2400",
            }
        ),
    )

    result = await main._try_handle_email_assistant(
        "How much did I get?",
        actor=_actor(),
        conversation_id=conversation,
        request_id="document-search-resumption-1",
    )

    assert result and result["response"] == "Net Pay was £2400."
    search_mailbox.assert_awaited_once()
    assert search_mailbox.await_args.kwargs["provider"] == "microsoft_outlook"
    assert search_mailbox.await_args.kwargs["topic_query"] == "pay statement"
    document_read.assert_awaited_once()
    assert document_read.await_args.args[0] == "document.read"


@pytest.mark.asyncio
async def test_multiple_attachments_use_pending_interaction_without_guessing(
    context_runtime, monkeypatch
) -> None:
    dialogue, _service, interactions = context_runtime
    conversation = "usr:aaron:multiple-document-attachments"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "messages": [{"message_id": "message-a", "subject": "Documents"}],
        },
    )
    execute = AsyncMock(return_value=_read_result(selection_required=True))
    monkeypatch.setattr(main.external_agent, "execute", execute)

    result = await main._try_handle_working_context_followup(
        "Read it", actor=_actor(), conversation_id=conversation
    )

    assert result and result["action_outcome"] == "waiting_user"
    assert "document-a.pdf or document-b.pdf" in str(result["response"])
    pending = await interactions.current(principal_id="aaron", conversation_id=conversation)
    assert pending and pending["unresolved_slot"] == "attachment_identity"
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_document_context_survives_restart_and_never_grants_delete_authority(
    context_runtime, monkeypatch, tmp_path
) -> None:
    dialogue, _service, _ = context_runtime
    conversation = "usr:aaron:document-restart"
    await dialogue.record_email_read_focus(
        conversation,
        {
            "principal_id": "aaron",
            "provider": "microsoft_outlook",
            "account_id": "outlook-account",
            "messages": [{"message_id": "message-a", "subject": "Statement"}],
        },
    )
    monkeypatch.setattr(main.external_agent, "execute", AsyncMock(return_value=_read_result()))
    monkeypatch.setattr(
        main.ai,
        "select_document_evidence",
        AsyncMock(
            return_value={
                "found": True,
                "label": "Net Pay",
                "value": "£2400",
                "evidence_quote": "Net Pay: £2400",
            }
        ),
    )
    await main._try_handle_working_context_followup(
        "How much did I get?", actor=_actor(), conversation_id=conversation
    )

    restarted_dialogue = DialogueManager(str(dialogue.database_path))
    restarted_context = WorkingContextService(restarted_dialogue)
    monkeypatch.setattr(main, "dialogue", restarted_dialogue)
    monkeypatch.setattr(main, "working_context", restarted_context)
    after_restart = await main._try_handle_working_context_followup(
        "How much did I get?", actor=_actor(), conversation_id=conversation
    )
    delete = await main._try_handle_working_context_followup(
        "Delete it", actor=_actor(), conversation_id=conversation
    )

    assert after_restart and after_restart["response"] == "Net Pay was £2400."
    assert delete is None
