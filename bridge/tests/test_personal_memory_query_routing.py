from app.ai_engine import RequestIntent, RequestRouter


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


def test_motion_sensor_question_remains_live_state() -> None:
    decision = classify("Is the bedroom motion sensor on?")
    assert decision.intent == RequestIntent.STATE_QUERY
    assert decision.allow_home_read is True
