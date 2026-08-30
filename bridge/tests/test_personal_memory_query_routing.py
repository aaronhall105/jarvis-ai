from app.ai_engine import (
    RequestIntent,
    RequestRouter,
    unbacked_future_promise_reply,
    unsupported_external_capability_reply,
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


def test_supported_notification_is_not_overridden_by_capability_guard() -> None:
    assert unsupported_external_capability_reply(
        "Send Amber a message saying dinner is ready",
        [
            {
                "tool": "send_mobile_notification",
                "result": {"success": True, "verified": False, "command_accepted": True},
            }
        ],
    ) is None


def test_external_action_advice_is_not_mistaken_for_execution() -> None:
    assert unsupported_external_capability_reply("What book should I buy?", []) is None


def test_unrelated_ha_read_does_not_satisfy_live_web_request() -> None:
    assert unsupported_external_capability_reply(
        "Check current prices online",
        [{"tool": "get_entity_state", "result": {"success": True}}],
    ) is not None


def test_current_ha_state_is_not_mistaken_for_web_research() -> None:
    assert unsupported_external_capability_reply(
        "Check current bedroom temperature",
        [{"tool": "get_entity_state", "result": {"success": True}}],
    ) is None


def test_unbacked_future_commitment_is_blocked() -> None:
    assert unbacked_future_promise_reply("I'll get back to you later.", []) is not None
    assert unbacked_future_promise_reply(
        "I'll get back to you later.",
        [{"tool": "task", "result": {"success": True}}],
    ) is not None
    assert unbacked_future_promise_reply(
        "I'll remind you tomorrow.",
        [
            {
                "tool": "followup_schedule",
                "result": {"success": True, "job_id": "job-1", "status": "pending"},
            }
        ],
    ) is None
    assert unbacked_future_promise_reply(
        "I'll notify you when it is done.",
        [
            {
                "tool": "followup_schedule",
                "result": {"success": False, "job_id": "job-2", "status": "failed"},
            }
        ],
    ) is not None


def test_motion_sensor_question_remains_live_state() -> None:
    decision = classify("Is the bedroom motion sensor on?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True
