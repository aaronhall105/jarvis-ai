from app.version import CORE_APPLICATION_VERSION, JARVIS_RELEASE, REALTIME_PROTOCOL_VERSION
from app.realtime_voice import CORE_APPLICATION_VERSION as VOICE_CORE_VERSION
from app.realtime_voice import VERSION, speak_response_event


def test_authoritative_release_identity() -> None:
    assert JARVIS_RELEASE == "19.0.0-alpha21"
    assert VERSION == JARVIS_RELEASE
    assert VOICE_CORE_VERSION == CORE_APPLICATION_VERSION
    assert REALTIME_PROTOCOL_VERSION >= 2


def test_openai_renderer_response_has_immutable_turn_metadata() -> None:
    payload = speak_response_event(
        "hello",
        "marin",
        generation=7,
        client_turn_id=42,
    )
    metadata = payload["response"]["metadata"]
    assert metadata["jarvis_generation"] == "7"
    assert metadata["jarvis_client_turn_id"] == "42"
    assert metadata["release"] == JARVIS_RELEASE
