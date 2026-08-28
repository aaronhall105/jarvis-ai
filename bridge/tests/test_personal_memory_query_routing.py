from app.ai_engine import (
    RequestIntent,
    RequestRouter,
    unbacked_future_promise_reply,
    unsupported_external_capability_reply,
    verified_monitor_creation_reply,
    verified_plan_creation_reply,
    unbacked_external_write_claim_reply,
)


def classify(text: str):
    return RequestRouter.classify(text, [])


def test_named_person_health_question_uses_memory() -> None:
    decision = classify("Has Amber got any health conditions?")
    assert decision.intent == RequestIntent.GENERAL
    assert decision.use_long_term_memory is True
    assert decision.allow_home_read is False
    assert "saved personal context" in str(decision.model_instruction)


def test_first_person_health_question_uses_memory() -> None:
    decision = classify("Do I have any health conditions?")
    assert decision.intent == RequestIntent.GENERAL
    assert decision.use_long_term_memory is True
    assert decision.allow_home_read is False


def test_allergy_question_uses_memory() -> None:
    decision = classify("Does Amber have any allergies?")
    assert decision.intent == RequestIntent.GENERAL
    assert decision.use_long_term_memory is True


def test_intolerance_question_uses_memory() -> None:
    decision = classify("Is Amber lactose intolerant?")
    assert decision.intent == RequestIntent.GENERAL
    assert decision.use_long_term_memory is True


def test_medication_question_uses_memory() -> None:
    decision = classify("What medication does Aaron take?")
    assert decision.intent == RequestIntent.GENERAL
    assert decision.use_long_term_memory is True


def test_presence_question_remains_live_state() -> None:
    decision = classify("Is Amber home?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True
    assert decision.use_long_term_memory is False


def test_phone_battery_question_remains_live_state() -> None:
    decision = classify("What is Amber's phone battery?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True


def test_battery_health_question_remains_device_state() -> None:
    decision = classify("What is the battery health of Amber's phone?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True


def test_unavailable_external_capability_cannot_be_claimed() -> None:
    reply = unsupported_external_capability_reply(
        "Can you submit a support ticket?", []
    )
    assert reply is not None
    assert "don’t have a connected" in reply
    assert unsupported_external_capability_reply(
        "Can you submit a support ticket?", [{"tool": "support_ticket"}]
    ) is not None
    assert unsupported_external_capability_reply(
        "Can you submit a support ticket?",
        [
            {
                "tool": "support_ticket",
                "result": {
                    "success": True,
                    "status": "verified",
                    "provider_reference": "ticket-1",
                },
            }
        ],
    ) is None


def test_book_research_is_not_misclassified_as_a_booking_action() -> None:
    assert unsupported_external_capability_reply(
        "Find the latest book recommendations.",
        [],
    ) is None


def test_unbacked_future_commitment_is_blocked() -> None:
    assert unbacked_future_promise_reply("I'll get back to you later.", []) is not None
    assert unbacked_future_promise_reply(
        "I'll get back to you later.", [{"tool": "task"}]
    ) is not None
    assert unbacked_future_promise_reply("I'll keep an eye on it.", []) is not None
    assert unbacked_future_promise_reply("I'll watch it.", []) is not None
    assert unbacked_future_promise_reply("I'll notify you when it changes.", []) is not None
    assert unbacked_future_promise_reply("I'll keep you posted.", []) is not None
    assert unbacked_future_promise_reply(
        "I'll get back to you later.",
        [{"tool": "task", "result": {"success": True, "job_id": "job-1"}}],
    ) is None


def test_monitor_creation_reply_never_exposes_or_embellishes_baseline() -> None:
    reply = verified_monitor_creation_reply(
        [
            {
                "tool": "create_external_monitor",
                "result": {
                    "success": True,
                    "job_id": "monitor-1",
                    "baseline": {"captured": True, "size_bytes": 120_000},
                },
            }
        ]
    )
    assert reply is not None
    assert "monitor-1" in reply
    assert "120" not in reply
    assert "baseline" in reply
    assert verified_monitor_creation_reply(
        [{"tool": "create_external_monitor", "result": {"success": False}}]
    ) is None


def test_unverified_external_write_completion_claim_is_blocked() -> None:
    assert unbacked_external_write_claim_reply(
        "I've sent the email.", []
    ) is not None
    assert unbacked_external_write_claim_reply(
        "I drafted the email for your review.", []
    ) is None
    assert unbacked_external_write_claim_reply(
        "The email draft was created.", []
    ) is not None
    assert unbacked_external_write_claim_reply(
        "The calendar event was created.", []
    ) is not None


def test_google_write_claim_requires_verified_provider_receipt() -> None:
    accepted = {
        "tool": "google_integration",
        "result": {
            "success": True,
            "status": "accepted_unverified",
            "receipt": {"status": "accepted_unverified"},
        },
    }
    verified = {
        "tool": "google_integration",
        "result": {
            "success": True,
            "status": "verified",
            "receipt": {"status": "verified"},
        },
    }
    assert unbacked_external_write_claim_reply(
        "The calendar event was created.", [accepted]
    ) is not None
    assert unbacked_external_write_claim_reply(
        "The calendar event was created.", [verified]
    ) is None


def test_created_but_blocked_plan_is_never_described_as_completed() -> None:
    call = {
        "tool": "create_personal_plan",
        "result": {
            "success": True,
            "plan_created": True,
            "goal_completed": False,
            "data": {
                "plan": {
                    "plan_id": "plan-1",
                    "status": "blocked",
                    "steps": [
                        {
                            "status": "blocked",
                            "capability": {
                                "capability_id": "calendar.create",
                                "access": "write",
                            },
                            "failure": {
                                "message": "Calendar is not configured."
                            },
                            "action_receipt": None,
                        }
                    ],
                }
            },
        },
    }

    reply = verified_plan_creation_reply([call])

    assert reply is not None
    assert "has not completed" in reply
    assert "blocked" in reply
    assert "Calendar is not configured" in reply
    assert unsupported_external_capability_reply(
        "Book it and put it in my calendar.", [call]
    ) is not None
    assert unbacked_external_write_claim_reply(
        "The booking was confirmed.", [call]
    ) is not None


def test_completed_plan_write_requires_nested_verified_receipt() -> None:
    call = {
        "tool": "create_personal_plan",
        "result": {
            "success": True,
            "plan_created": True,
            "goal_completed": True,
            "data": {
                "plan": {
                    "plan_id": "plan-2",
                    "status": "completed",
                    "steps": [
                        {
                            "status": "succeeded",
                            "capability": {
                                "capability_id": "calendar.create",
                                "access": "write",
                            },
                            "action_receipt": {"status": "verified"},
                        }
                    ],
                }
            },
        },
    }

    assert "completed" in (verified_plan_creation_reply([call]) or "")
    assert unsupported_external_capability_reply(
        "Book it and put it in my calendar.", [call]
    ) is None
    assert unbacked_external_write_claim_reply(
        "The booking was confirmed.", [call]
    ) is None
    assert unbacked_external_write_claim_reply(
        "I've sent the email.",
        [
            {
                "tool": "email_send",
                "result": {
                    "success": True,
                    "receipt": {"status": "accepted_unverified"},
                },
            }
        ],
    ) is not None
    assert unbacked_external_write_claim_reply(
        "I've sent the email.",
        [
            {
                "tool": "email_send",
                "result": {
                    "success": True,
                    "receipt": {"status": "verified"},
                },
            }
        ],
    ) is None


def test_motion_sensor_question_remains_live_state() -> None:
    decision = classify("Is the bedroom motion sensor on?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True
