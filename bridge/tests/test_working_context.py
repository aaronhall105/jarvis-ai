from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai_engine import AIEngine
from app.dialogue_manager import DialogueManager
from app.user_context import UserContext
from app.working_context import (
    EvidenceStatus,
    ReferenceQuery,
    ReferenceStatus,
    ResultIntelligence,
    WorkingContextService,
    email_read_projection,
    home_action_receipt_projection,
    make_context_object,
    model_safe_context,
    reference_query,
    tool_call_projection,
)


def _home_action_context(name: str = "Studio Control"):
    return make_context_object(
        object_type="capability",
        display_name=name,
        source="home_assistant_canonical_inventory",
        canonical_id="capability:studio-control",
        provider="home_assistant",
        capability="homeassistant.semantic",
        metadata={"state": "on"},
    )


def _receipt_call(
    *,
    display_name: str = "Studio Control",
    parameter: str | None = "level",
    mode: str = "ABSOLUTE",
    direction: str = "NONE",
    requested_value=30,
    before_value=40,
    after_value=30,
    unit: str = "%",
    verification_status: str = "VERIFIED",
    receipt_status: str = "verified",
    already: bool = False,
    changed: bool = True,
    message: str | None = None,
    qualitative_delta=None,
    action: str = "set_parameter",
    error: str | None = None,
):
    outcome = {
        "canonical_id": "capability:studio-control",
        "display_name": display_name,
        "status": verification_status,
        "verified": verification_status == "VERIFIED",
        "already_in_target_state": already,
        "changed": changed,
        "command_sent": changed,
        "parameter": parameter,
        "requested_value": after_value,
        "before_value": before_value,
        "after_value": after_value,
        "unit": unit,
        "qualitative_delta": qualitative_delta,
        "message": message,
    }
    result = {
        "action": action,
        "parameter": parameter,
        "parameter_mode": mode,
        "parameter_direction": direction,
        "requested_value": requested_value,
        "requested_unit": unit,
        "verification_status": verification_status,
        "verified": verification_status == "VERIFIED",
        "already_in_target_state": already,
        "changed": changed,
        "command_sent": changed,
        "outcome_unknown": verification_status == "UNKNOWN",
        "outcomes": [outcome],
        "response_message": "provider result",
    }
    receipt = {
        "action_id": "receipt-1",
        "requested_operation": ("execute_home_parameter" if parameter else "execute_home_action"),
        "status": receipt_status,
        "result": result,
        "verification": {
            "action": action,
            "parameter": parameter,
            "parameter_mode": mode,
            "requested_value": requested_value,
            "requested_unit": unit,
            "verification_status": verification_status,
            "outcomes": [outcome],
        },
        "error": error,
        "started_at": "2026-10-15T10:00:00+00:00",
        "completed_at": "2026-10-15T10:00:01+00:00",
    }
    return {
        "tool": "execute_home_parameter" if parameter else "execute_home_action",
        "arguments": {"handle": "opaque"},
        "result": {**result, "action_receipt": receipt},
    }


@pytest.mark.parametrize(
    ("parameter", "requested", "after", "unit", "expected"),
    [
        ("brightness", 30, 30, "%", "brightness to 30%"),
        ("volume", 40, 40, "%", "volume to 40%"),
        ("target_temperature", 21, 21, "°C", "target temperature to 21°C"),
        ("position", 60, 60, "%", "position to 60%"),
        ("value", 80, 80, "", "value to 80"),
        ("mode", "Eco", "Eco", "", "mode to Eco"),
    ],
)
def test_verified_parameter_receipt_generically_explains_absolute_actions(
    parameter, requested, after, unit, expected
) -> None:
    item = _home_action_context()
    calls = [
        _receipt_call(
            parameter=parameter,
            requested_value=requested,
            after_value=after,
            unit=unit,
        )
    ]

    derived = home_action_receipt_projection(
        calls=calls,
        objects=[item],
        source_turn_index=4,
    )

    assert derived is not None
    assert derived["evidence_source"] == "action_receipt"
    assert derived["receipt_id"] == "receipt-1"
    assert derived["source_turn_index"] == 4
    answer = ResultIntelligence.explain(derived, (item,))
    assert expected.replace(" to ", " for Studio Control to ") in answer
    assert "checked it again" in answer
    assert all(
        jargon not in answer
        for jargon in ("canonical", "entity_id", "service schema", "parameterized")
    )
    assert len(calls) == 1  # Explanation only reads projected receipt evidence.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parameter", "requested", "unit"),
    [
        ("brightness", 30, "%"),
        ("volume", 40, "%"),
        ("target_temperature", 21, "°C"),
        ("position", 60, "%"),
        ("value", 80, ""),
        ("mode", "Eco", ""),
    ],
)
async def test_historical_action_lookup_matches_receipt_without_live_home(
    tmp_path, parameter, requested, unit
) -> None:
    dialogue = DialogueManager(str(tmp_path / f"{parameter}.db"))
    conversation = f"usr:aaron:historical-{parameter}"
    await dialogue.record_result(
        conversation,
        intent="control_now",
        success=True,
        response="Verified.",
        calls=[
            _receipt_call(
                parameter=parameter,
                requested_value=requested,
                after_value=requested,
                unit=unit,
            )
        ],
    )
    await dialogue.record_result(
        conversation,
        intent="general_conversation",
        success=True,
        response="A separate task.",
        calls=[],
    )

    explanation = await WorkingContextService(dialogue).explain_matching_home_action(
        principal_id="aaron",
        conversation_id=conversation,
        requested_parameter=parameter,
        parameter_mode="ABSOLUTE",
        requested_value=requested,
        semantic_concepts=("Studio Control",),
    )

    assert explanation is not None
    assert explanation["success"] is True
    assert explanation["evidence_source"] == "action_receipt"
    assert explanation["action_attempted"] is False
    assert explanation["write_performed"] is False
    assert explanation["referenced_action_status"] == "VERIFIED"
    assert "Studio Control" in explanation["response"]


@pytest.mark.asyncio
async def test_collection_explanation_cannot_be_hijacked_by_old_action_receipt(tmp_path) -> None:
    dialogue = DialogueManager(str(tmp_path / "collection.db"))
    conversation = "usr:aaron:collection-after-action"
    await dialogue.record_result(
        conversation,
        intent="control_now",
        success=True,
        response="Verified.",
        calls=[_receipt_call()],
    )

    explanation = await WorkingContextService(dialogue).explain_matching_home_action(
        principal_id="aaron",
        conversation_id=conversation,
        semantic_concepts=("primary capabilities",),
    )

    assert explanation is None


def test_relative_and_qualitative_receipts_explain_recorded_before_after_values() -> None:
    item = _home_action_context()
    relative = home_action_receipt_projection(
        calls=[
            _receipt_call(
                parameter="level",
                mode="RELATIVE",
                direction="INCREASE",
                requested_value=10,
                before_value=30,
                after_value=40,
            )
        ],
        objects=[item],
        source_turn_index=1,
    )
    qualitative = home_action_receipt_projection(
        calls=[
            _receipt_call(
                parameter="level",
                mode="RELATIVE",
                direction="INCREASE",
                requested_value=None,
                before_value=30,
                after_value=40,
                qualitative_delta=10,
            )
        ],
        objects=[item],
        source_turn_index=2,
    )

    assert relative is not None and qualitative is not None
    relative_answer = ResultIntelligence.explain(relative, (item,))
    qualitative_answer = ResultIntelligence.explain(qualitative, (item,))
    assert "increase" in relative_answer
    assert "30%" in relative_answer and "40%" in relative_answer
    assert "a little" in qualitative_answer
    assert "configured 10% adjustment" in qualitative_answer
    assert "30%" in qualitative_answer and "40%" in qualitative_answer


def test_already_satisfied_discrete_receipt_explains_verified_no_op() -> None:
    item = _home_action_context("Bedroom Display")
    derived = home_action_receipt_projection(
        calls=[
            _receipt_call(
                display_name="Bedroom Display",
                parameter=None,
                action="turn_off",
                requested_value=None,
                before_value=None,
                after_value=None,
                unit="",
                already=True,
                changed=False,
            )
        ],
        objects=[item],
        source_turn_index=1,
    )

    assert derived is not None
    answer = ResultIntelligence.explain(derived, (item,))
    assert answer == ("Bedroom Display was already off when I checked it, so no change was needed.")


@pytest.mark.parametrize(
    ("verification_status", "receipt_status", "message", "expected"),
    [
        ("PARTIAL", "accepted_unverified", "One target does not support this setting", "partial"),
        ("FAILED", "failed", "The provider rejected the request", "couldn’t change"),
        ("UNKNOWN", "outcome_unknown", None, "couldn’t confirm the final state"),
    ],
)
def test_non_verified_receipts_are_explained_truthfully(
    verification_status, receipt_status, message, expected
) -> None:
    item = _home_action_context()
    derived = home_action_receipt_projection(
        calls=[
            _receipt_call(
                verification_status=verification_status,
                receipt_status=receipt_status,
                message=message,
                error=message if verification_status == "FAILED" else None,
            )
        ],
        objects=[item],
        source_turn_index=1,
    )

    assert derived is not None
    answer = ResultIntelligence.explain(derived, (item,))
    assert expected in answer
    if verification_status != "VERIFIED":
        assert "verified it" not in answer


def test_partial_receipt_names_successes_and_unsupported_targets() -> None:
    first = _home_action_context("First Control")
    second = make_context_object(
        object_type="capability",
        display_name="Second Control",
        source="home_assistant_canonical_inventory",
        canonical_id="capability:second-control",
        provider="home_assistant",
        capability="homeassistant.semantic",
    )
    call = _receipt_call(verification_status="PARTIAL", receipt_status="accepted_unverified")
    result = call["result"]
    receipt = result["action_receipt"]
    successful = dict(result["outcomes"][0])
    successful.update(
        canonical_id="capability:studio-control",
        display_name="First Control",
        status="VERIFIED",
        verified=True,
    )
    unsupported = {
        "canonical_id": "capability:second-control",
        "display_name": "Second Control",
        "status": "UNSUPPORTED",
        "verified": False,
        "message": "Second Control does not support this setting",
    }
    result["outcomes"] = [successful, unsupported]
    result["verified"] = False
    receipt["result"] = dict(result)
    receipt["result"].pop("action_receipt", None)
    receipt["verification"]["outcomes"] = [successful, unsupported]

    derived = home_action_receipt_projection(
        calls=[call], objects=[first, second], source_turn_index=1
    )

    assert derived is not None
    answer = ResultIntelligence.explain(derived, (first, second))
    assert "verified the change for First Control" in answer
    assert "but not for Second Control" in answer
    assert "does not support this setting" in answer
    assert "overall result was only partial" in answer


def test_model_context_keeps_receipt_evidence_but_hides_internal_receipt_id() -> None:
    item = _home_action_context()
    derived = home_action_receipt_projection(
        calls=[_receipt_call()], objects=[item], source_turn_index=3
    )
    assert derived is not None
    safe = model_safe_context(
        {
            "objects": [item.to_record()],
            "derived_results": [derived],
        }
    )

    projected = safe["derived_results"][0]
    assert "receipt_id" not in projected
    assert projected["verification_status"] == "VERIFIED"
    assert projected["values"][0]["before_value"] == 40
    assert projected["values"][0]["after_value"] == 30


def _service(tmp_path) -> WorkingContextService:
    return WorkingContextService(DialogueManager(str(tmp_path / "dialogue.db")))


@pytest.mark.asyncio
async def test_email_temporal_sender_and_date_survive_restart(tmp_path) -> None:
    service = _service(tmp_path)
    conversation = "usr:aaron:email-context"
    evidence = {
        "principal_id": "aaron",
        "provider": "microsoft_outlook",
        "account_id": "outlook-account",
        "query_kind": "topic_search",
        "topic_query": "wage slip",
        "messages": [
            {
                "message_id": "current-message",
                "sender_name": "Joseph Scott",
                "from": "Joseph Scott <payroll@example.invalid>",
                "subject": "WAGE SLIP",
                "received_at": "2026-09-24T14:11:47+00:00",
            },
            {
                "message_id": "previous-message",
                "sender_name": "Joseph Scott",
                "from": "Joseph Scott <payroll@example.invalid>",
                "subject": "Wage slip August",
                "received_at": "2026-08-24T14:11:47+00:00",
            },
        ],
    }
    objects, result_set = email_read_projection(evidence)
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=objects,
        intent="email_topic_search",
        goal="find latest wage slip",
        result_set=result_set,
        focus_refs=[objects[0].reference_id],
    )

    current = await service.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        query=ReferenceQuery(object_types=("email_message",)),
    )
    assert current.status is ReferenceStatus.RESOLVED
    assert ResultIntelligence.answer_attribute(attribute="sender", resolution=current) == (
        "It’s from Joseph Scott."
    )
    assert ResultIntelligence.answer_attribute(attribute="date", resolution=current) == (
        "It’s dated 24 September."
    )

    restarted = _service(tmp_path)
    previous = await restarted.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        query=reference_query("What about the previous one?", object_types=("email_message",)),
    )
    assert previous.status is ReferenceStatus.RESOLVED
    assert previous.objects[0].canonical_id == "previous-message"


@pytest.mark.asyncio
async def test_home_ordered_result_resolves_second_without_inventing_entity(tmp_path) -> None:
    service = _service(tmp_path)
    objects, result_set = tool_call_projection(
        intent="list_lights",
        calls=[
            {
                "tool": "list_area_states",
                "result": {
                    "success": True,
                    "area_name": "Downstairs",
                    "entities": [
                        {
                            "entity_id": "light.hall",
                            "name": "Hall light",
                            "state": "on",
                            "area_name": "Hall",
                        },
                        {
                            "entity_id": "light.lounge",
                            "name": "Lounge light",
                            "state": "on",
                            "area_name": "Lounge",
                        },
                    ],
                },
            }
        ],
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:home",
        objects=objects,
        intent="list_lights",
        result_set=result_set,
        focus_refs=[item.reference_id for item in objects],
    )
    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:home",
        query=reference_query("Turn the second one off", object_types=("device",)),
    )
    assert resolution.status is ReferenceStatus.RESOLVED
    assert resolution.objects[0].canonical_id == "light.lounge"
    # Context resolves identity only; it deliberately contains no write authority.
    assert "authority" not in resolution.objects[0].metadata


@pytest.mark.asyncio
async def test_home_result_set_is_recoverable_after_independent_task_focus(tmp_path) -> None:
    service = _service(tmp_path)
    conversation = "usr:aaron:task-switch"
    televisions = [
        make_context_object(
            object_type="device",
            display_name=name,
            source="home_assistant_canonical_inventory",
            canonical_id=canonical_id,
            provider="home_assistant",
            metadata={"kind": "tv", "state": state},
            aliases=(name, "television"),
        )
        for canonical_id, name, state in (
            ("home:bedroom-display", "Bedroom Display", "off"),
            ("home:lounge-display", "Lounge Display", "on"),
        )
    ]
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=televisions,
        intent="semantic_home_query",
        result_set={
            "result_set_id": "home:televisions",
            "object_refs": [item.reference_id for item in televisions],
            "filters": {"query": "television", "operation": "QUERY"},
        },
        focus_refs=[item.reference_id for item in televisions],
    )
    task = make_context_object(
        object_type="task",
        display_name="Check inbox",
        source="task_centre",
        canonical_id="task:inbox",
        provider="jarvis",
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[task],
        intent="mail_task",
        result_set={
            "result_set_id": "tasks:mail",
            "object_refs": [task.reference_id],
        },
        focus_refs=[task.reference_id],
    )

    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        query=ReferenceQuery(
            object_types=("device",),
            plural=True,
            semantic_concepts=("television",),
        ),
    )

    assert resolution.status is ReferenceStatus.RESOLVED
    assert resolution.result_set_id == "home:televisions"
    assert {item.canonical_id for item in resolution.objects} == {
        "home:bedroom-display",
        "home:lounge-display",
    }


@pytest.mark.asyncio
async def test_new_task_focus_does_not_destroy_grounded_home_history(tmp_path) -> None:
    service = _service(tmp_path)
    conversation = "usr:aaron:remembered-object-independent-task"
    display = make_context_object(
        object_type="device",
        display_name="Studio Display",
        source="home_assistant_canonical_inventory",
        canonical_id="home:studio-display",
        provider="home_assistant",
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[display],
        intent="semantic_home_query",
        result_set={"object_refs": [display.reference_id]},
        focus_refs=[display.reference_id],
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=(),
        intent="general",
        goal="",
    )

    context = await service.get(principal_id="aaron", conversation_id=conversation)

    assert context["current_intent"] == "general"
    assert context["current_goal"] is None
    assert context["result_sets"]
    assert any(item["canonical_id"] == "home:studio-display" for item in context["objects"])


@pytest.mark.asyncio
async def test_stale_live_state_requires_refresh_but_immutable_email_does_not(tmp_path) -> None:
    service = _service(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    light = make_context_object(
        object_type="device",
        display_name="Hall light",
        source="home_assistant",
        canonical_id="light.hall",
        provider="home_assistant",
        metadata={"state": "on"},
        observed_at=old,
        freshness_seconds=30,
        immutable=False,
    )
    email = make_context_object(
        object_type="email_message",
        display_name="Statement",
        source="provider_mailbox_read",
        canonical_id="message-1",
        provider="microsoft_outlook",
        metadata={"sender_name": "Joseph Scott"},
        observed_at=old,
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:freshness",
        objects=[light, email],
        result_set={"object_refs": [light.reference_id, email.reference_id]},
        focus_refs=[light.reference_id],
    )
    stale = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:freshness",
        query=ReferenceQuery(object_types=("device",), require_current_state=True),
    )
    assert stale.status is ReferenceStatus.STALE
    assert stale.requires_fresh_read is True
    immutable = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:freshness",
        query=ReferenceQuery(
            object_types=("email_message",),
            explicit_reference=email.reference_id,
            require_current_state=True,
        ),
    )
    assert immutable.status is ReferenceStatus.RESOLVED
    assert immutable.requires_fresh_read is False


@pytest.mark.asyncio
async def test_person_relation_is_grounded_not_guessed(tmp_path) -> None:
    service = _service(tmp_path)
    person = make_context_object(
        object_type="person",
        display_name="Amber",
        source="home_assistant",
        canonical_id="person.amber",
        provider="home_assistant",
        metadata={"state": "home"},
        relations={"phone": "ctx:amber-phone"},
    )
    phone = make_context_object(
        object_type="device",
        display_name="Amber’s phone",
        source="home_assistant",
        canonical_id="device_tracker.amber_phone",
        provider="home_assistant",
        metadata={"state": "home"},
    )
    # The relation must reference a real projected object, never a fabricated ID.
    person_record = person.to_record()
    person_record["relations"]["phone"] = phone.reference_id
    person = make_context_object(
        object_type="person",
        display_name="Amber",
        source="home_assistant",
        canonical_id="person.amber",
        provider="home_assistant",
        metadata={"state": "home"},
        relations=person_record["relations"],
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:person",
        objects=[person, phone],
        result_set={"object_refs": [person.reference_id, phone.reference_id]},
        focus_refs=[person.reference_id],
    )
    context = await service.get(principal_id="aaron", conversation_id="usr:aaron:person")
    projected_person = next(item for item in context["objects"] if item["object_type"] == "person")
    assert projected_person["relations"]["phone"] == phone.reference_id
    resolved_phone = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:person",
        query=reference_query("What about her phone?", object_types=("device",)),
    )
    assert resolved_phone.status is ReferenceStatus.RESOLVED
    assert resolved_phone.objects[0].canonical_id == "device_tracker.amber_phone"


@pytest.mark.asyncio
async def test_live_presence_projection_uses_authoritative_person_tracker_relation(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    objects, result_set = tool_call_projection(
        intent="person_location",
        calls=[
            {
                "tool": "inspect_presence",
                "result": {
                    "success": True,
                    "person": {
                        "entity_id": "person.amber",
                        "name": "Amber",
                        "state": "home",
                    },
                    "trackers": [
                        {
                            "entity_id": "device_tracker.amber_phone",
                            "name": "Amber Phone",
                            "domain": "device_tracker",
                            "state": "home",
                            "relationship": "configured_for_person",
                        }
                    ],
                    "person_configuration_matched": True,
                },
            }
        ],
    )
    person = next(item for item in objects if item.object_type == "person")
    phone = next(item for item in objects if item.object_type == "device")
    assert person.relations["phone"] == [phone.reference_id]
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:live-person-relation",
        objects=objects,
        result_set=result_set,
        focus_refs=[person.reference_id],
    )
    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:live-person-relation",
        query=reference_query("What about her phone?", object_types=("device",)),
    )
    assert resolution.status is ReferenceStatus.RESOLVED
    assert resolution.objects[0].canonical_id == "device_tracker.amber_phone"


@pytest.mark.asyncio
async def test_calendar_next_and_one_after_use_ordered_result(tmp_path) -> None:
    service = _service(tmp_path)
    objects, result_set = tool_call_projection(
        intent="calendar_list",
        calls=[
            {
                "tool": "calendar_list_events",
                "result": {
                    "events": [
                        {"event_id": "event-1", "summary": "Dentist", "start": "2026-10-03"},
                        {"event_id": "event-2", "summary": "Review", "start": "2026-10-04"},
                    ]
                },
            }
        ],
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:calendar",
        objects=objects,
        result_set=result_set,
        focus_refs=[objects[0].reference_id],
    )
    after = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:calendar",
        query=reference_query("What about the one after that?", object_types=("calendar_event",)),
    )
    assert after.status is ReferenceStatus.RESOLVED
    assert after.objects[0].canonical_id == "event-2"


@pytest.mark.asyncio
async def test_research_and_comparison_state_are_provider_neutral(tmp_path) -> None:
    service = _service(tmp_path)
    amplifiers = [
        make_context_object(
            object_type="search_result",
            display_name=name,
            source="research",
            canonical_id=f"amp-{index}",
            metadata={"zones": zones, "price": price, "unit": "pounds"},
            immutable=True,
        )
        for index, (name, zones, price) in enumerate(
            (("Amp One", 2, 800), ("Amp Four", 4, 950), ("Amp Budget", 4, 600)), start=1
        )
    ]
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:research",
        objects=amplifiers,
        result_set={"object_refs": [item.reference_id for item in amplifiers]},
        focus_refs=[item.reference_id for item in amplifiers],
    )
    plural = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:research",
        query=reference_query("Compare them", object_types=("search_result",)),
    )
    assert [item.display_name for item in plural.objects] == [
        "Amp One",
        "Amp Four",
        "Amp Budget",
    ]
    comparison = await service.compare(
        principal_id="aaron",
        conversation_id="usr:aaron:research",
        objects=plural.objects[:2],
        metric="price",
    )
    answer = ResultIntelligence.comparison(comparison, plural.objects[:2])
    assert "150 pounds" in answer

    four_zones = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:research",
        query=reference_query("Which one has four zones?", object_types=("search_result",)),
    )
    assert four_zones.status is ReferenceStatus.AMBIGUOUS
    assert {item.display_name for item in four_zones.objects} == {
        "Amp Four",
        "Amp Budget",
    }
    cheaper = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:research",
        query=reference_query("What about the cheaper one?", object_types=("search_result",)),
    )
    assert cheaper.status is ReferenceStatus.RESOLVED
    assert cheaper.objects[0].display_name == "Amp Budget"


@pytest.mark.asyncio
async def test_grounded_device_reference_rewrites_for_existing_control_authority(
    tmp_path,
) -> None:
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    service = WorkingContextService(dialogue)
    lights = [
        make_context_object(
            object_type="device",
            display_name=name,
            source="home_assistant",
            canonical_id=entity_id,
            provider="home_assistant",
            metadata={"state": "on", "area_name": room},
            immutable=False,
            freshness_seconds=30,
        )
        for name, entity_id, room in (
            ("Hall light", "light.hall", "Hall"),
            ("Bedroom light", "light.bedroom", "Bedroom"),
        )
    ]
    conversation = "usr:aaron:grounded-control"
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=lights,
        result_set={"object_refs": [item.reference_id for item in lights]},
        focus_refs=[item.reference_id for item in lights],
    )
    engine = AIEngine.__new__(AIEngine)
    engine.dialogue = dialogue
    actor = UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        device_id=None,
        voice_mode=False,
    )
    rewritten = await engine._resolve_working_context_control_reference(
        "Turn the second one off",
        conversation_id=conversation,
        actor=actor,
    )
    assert rewritten == "Turn off light.bedroom"
    assert "authority" not in lights[1].metadata


@pytest.mark.asyncio
async def test_task_projection_supports_first_task_without_task_engine_duplication(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    tasks, result_set = tool_call_projection(
        intent="task_centre_list",
        calls=[
            {
                "tool": "task_centre_list",
                "result": {
                    "tasks": [
                        {"task_id": "followup:1", "title": "Inbox cleanup", "status": "running"},
                        {
                            "task_id": "executive:2",
                            "title": "Amplifier research",
                            "status": "running",
                        },
                    ]
                },
            }
        ],
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:tasks",
        objects=tasks,
        result_set=result_set,
        focus_refs=[item.reference_id for item in tasks],
    )
    selected = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:tasks",
        query=reference_query("Pause the first one", object_types=("task",)),
    )
    assert selected.objects[0].canonical_id == "followup:1"
    assert selected.objects[0].source == "task_centre_list"


@pytest.mark.asyncio
async def test_ambiguous_current_reference_does_not_guess(tmp_path) -> None:
    service = _service(tmp_path)
    first = make_context_object(
        object_type="document", display_name="Quote A", source="files", canonical_id="a"
    )
    second = make_context_object(
        object_type="document", display_name="Quote B", source="files", canonical_id="b"
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:ambiguous",
        objects=[first, second],
        result_set={"object_refs": [first.reference_id, second.reference_id]},
    )
    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:ambiguous",
        query=ReferenceQuery(object_types=("document",)),
    )
    assert resolution.status is ReferenceStatus.AMBIGUOUS
    assert {item.display_name for item in resolution.objects} == {"Quote A", "Quote B"}


@pytest.mark.asyncio
async def test_two_focused_compatible_objects_remain_ambiguous(tmp_path) -> None:
    service = _service(tmp_path)
    first = make_context_object(
        object_type="device", display_name="Lamp A", source="home_assistant", canonical_id="light.a"
    )
    second = make_context_object(
        object_type="device", display_name="Lamp B", source="home_assistant", canonical_id="light.b"
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:focused-ambiguity",
        objects=[first, second],
        result_set={"object_refs": [first.reference_id, second.reference_id]},
        focus_refs=[first.reference_id, second.reference_id],
    )
    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:focused-ambiguity",
        query=ReferenceQuery(object_types=("device",)),
    )
    assert resolution.status is ReferenceStatus.AMBIGUOUS
    assert len(resolution.objects) == 2


@pytest.mark.asyncio
async def test_new_single_focus_supersedes_prior_turn_focus(tmp_path) -> None:
    service = _service(tmp_path)
    first = make_context_object(
        object_type="device",
        display_name="First device",
        source="home_assistant",
        canonical_id="device.first",
        provider="home_assistant",
    )
    second = make_context_object(
        object_type="device",
        display_name="Second device",
        source="home_assistant",
        canonical_id="device.second",
        provider="home_assistant",
    )
    conversation = "usr:aaron:latest-single-focus"
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[first, second],
        result_set={"object_refs": [first.reference_id, second.reference_id]},
        focus_refs=[first.reference_id],
    )
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=[second],
        result_set={"object_refs": [second.reference_id]},
        focus_refs=[second.reference_id],
    )

    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        query=ReferenceQuery(object_types=("device",)),
    )

    assert resolution.status is ReferenceStatus.RESOLVED
    assert resolution.objects[0].canonical_id == "device.second"
    context = await service.get(principal_id="aaron", conversation_id=conversation)
    assert context["focused_object_refs"] == [second.reference_id]


@pytest.mark.asyncio
async def test_cross_principal_and_conversation_isolation(tmp_path) -> None:
    service = _service(tmp_path)
    item = make_context_object(
        object_type="email_message",
        display_name="Private message",
        source="mail",
        canonical_id="private",
        immutable=True,
    )
    await service.project(
        principal_id="aaron",
        conversation_id="usr:aaron:conversation-a",
        objects=[item],
        result_set={"object_refs": [item.reference_id]},
        focus_refs=[item.reference_id],
    )
    with pytest.raises(ValueError, match="principal"):
        await service.get(principal_id="amber", conversation_id="usr:aaron:conversation-a")
    other = await service.resolve(
        principal_id="aaron",
        conversation_id="usr:aaron:conversation-b",
        query=ReferenceQuery(object_types=("email_message",)),
    )
    assert other.status is ReferenceStatus.MISSING


def test_projection_redacts_secrets_and_model_view_omits_canonical_ids() -> None:
    item = make_context_object(
        object_type="generic_capability_result",
        display_name="Booking result",
        source="future_booking_capability",
        canonical_id="provider-internal-booking-id",
        metadata={
            "status": "held",
            "access_token": "synthetic-value-must-not-persist",
            "nested": {"password": "synthetic", "safe": "yes"},
        },
    )
    record = item.to_record()
    assert "access_token" not in record["metadata"]
    assert "password" not in record["metadata"]["nested"]
    safe = model_safe_context(
        {
            "objects": [record],
            "focused_object_refs": [item.reference_id],
            "result_sets": [],
            "derived_results": [],
            "temporal_context": {},
        }
    )
    assert "canonical_id" not in safe["objects"][0]
    assert safe["objects"][0]["metadata"]["nested"]["safe"] == "yes"


def test_attachment_metadata_projects_grounded_attachment_without_exposing_ids() -> None:
    objects, _ = email_read_projection(
        {
            "provider": "google_gmail",
            "account_id": "account",
            "messages": [
                {
                    "message_id": "message",
                    "subject": "Statement",
                    "attachments": [
                        {
                            "filename": "statement.pdf",
                            "mime_type": "application/pdf",
                            "attachment_id": "provider-attachment",
                        }
                    ],
                }
            ],
        }
    )
    assert objects[0].metadata["attachments"][0]["filename"] == "statement.pdf"
    assert objects[0].capability == "email.read"
    assert objects[0].object_type == "email_message"
    assert objects[1].object_type == "attachment"
    assert objects[1].display_name == "statement.pdf"
    assert objects[1].relations["email_message"] == objects[0].reference_id
    assert all(item.object_type != "document" for item in objects)
    safe = model_safe_context(
        {
            "objects": [item.to_record() for item in objects],
            "focused_object_refs": [objects[0].reference_id],
            "result_sets": [],
            "derived_results": [],
            "temporal_context": {},
        }
    )
    rendered = json.dumps(safe)
    assert "provider-attachment" not in rendered
    assert '"message_id"' not in rendered


def test_future_capability_uses_generic_projection_without_core_domain_adapter() -> None:
    objects, result_set = tool_call_projection(
        intent="appointment_search",
        calls=[
            {
                "tool": "future.appointments.search",
                "result": {
                    "context_projection": {
                        "objects": [
                            {
                                "object_type": "appointment_slot",
                                "display_name": "Tuesday at 10am",
                                "canonical_id": "slot-1",
                                "provider": "future_booking_provider",
                                "capability": "appointments.search",
                                "source": "provider_result",
                                "evidence_status": "provider-specific-unknown-state",
                                "metadata": {
                                    "time": "2026-10-06T10:00:00+00:00",
                                    "raw_payload": "must not persist",
                                    "credential": "must not persist",
                                },
                            }
                        ],
                        "result_set": {"ordering": "soonest_first"},
                    }
                },
            }
        ],
    )
    assert len(objects) == 1
    assert objects[0].object_type == "appointment_slot"
    assert objects[0].evidence_status is EvidenceStatus.UNVERIFIED
    assert objects[0].metadata == {"time": "2026-10-06T10:00:00+00:00"}
    assert result_set and result_set["ordering"] == "soonest_first"


@pytest.mark.asyncio
async def test_future_capability_relations_are_remapped_without_domain_code(tmp_path) -> None:
    objects, result_set = tool_call_projection(
        intent="appointment_search",
        calls=[
            {
                "tool": "future.appointments.search",
                "result": {
                    "context_projection": {
                        "objects": [
                            {
                                "reference_id": "provider-slot",
                                "object_type": "appointment_slot",
                                "display_name": "Tuesday at 10am",
                                "canonical_id": "slot-1",
                                "source": "provider_result",
                                "relations": {"venue": "provider-venue"},
                            },
                            {
                                "reference_id": "provider-venue",
                                "object_type": "venue",
                                "display_name": "Town Hall",
                                "canonical_id": "venue-1",
                                "source": "provider_result",
                            },
                        ],
                        "result_set": {
                            "ordering": "provider_order",
                            "object_refs": ["provider-slot", "provider-venue"],
                        },
                    }
                },
            }
        ],
    )
    slot = next(item for item in objects if item.object_type == "appointment_slot")
    venue = next(item for item in objects if item.object_type == "venue")
    assert slot.relations == {"venue": venue.reference_id}
    assert result_set and result_set["object_refs"] == [
        slot.reference_id,
        venue.reference_id,
    ]

    service = _service(tmp_path)
    conversation = "usr:aaron:future-capability"
    await service.project(
        principal_id="aaron",
        conversation_id=conversation,
        objects=objects,
        result_set=result_set,
        focus_refs=[slot.reference_id],
    )
    resolution = await service.resolve(
        principal_id="aaron",
        conversation_id=conversation,
        query=reference_query("What about its venue?", object_types=("venue",)),
    )
    assert resolution.status is ReferenceStatus.RESOLVED
    assert resolution.objects[0].display_name == "Town Hall"
