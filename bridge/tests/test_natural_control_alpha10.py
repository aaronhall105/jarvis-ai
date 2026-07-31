from app.ai_engine import JARVIS_INSTRUCTIONS
from app.realtime_voice import VERSION, control_voice_policy, sanitise_tool_events
from app.tool_engine import ToolEngine


def test_verified_failure_is_not_silenced():
    events = sanitise_tool_events([{"tool": "control_device", "result": {"success": True, "verified": False, "response_message": "Floodlight has not reported off."}}])
    assert events[0]["success"] is False
    assert control_voice_policy("turn it off", events, enabled=True) == (False, "")


def test_routine_success_is_compact():
    events = sanitise_tool_events([{"tool": "control_device", "result": {"success": True, "verified": True, "response_message": "Floodlight is now off."}}])
    quiet, response = control_voice_policy("turn it off", events, enabled=True)
    assert quiet is True
    assert response


def test_alpha10_contract():
    assert VERSION == "19.0.0-alpha10"
    assert ToolEngine.STATE_VERIFY_DELAYS[0] <= 0.12
    assert len(ToolEngine.STATE_RETRY_VERIFY_DELAYS) >= 4
    lowered = JARVIS_INSTRUCTIONS.casefold()
    for phrase in ("trusted companion", "personal butler", "dry wit", "respectfully disagree", "do not ask for confirmation"):
        assert phrase in lowered
