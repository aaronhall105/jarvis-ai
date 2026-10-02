from __future__ import annotations

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
    make_context_object,
    model_safe_context,
    reference_query,
    tool_call_projection,
)


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


def test_attachment_metadata_is_context_not_fake_document_read_capability() -> None:
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
    assert all(item.object_type != "document" for item in objects)


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
