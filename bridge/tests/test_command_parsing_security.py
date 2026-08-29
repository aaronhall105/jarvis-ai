from __future__ import annotations

from app.ai_engine import (
    _FRUSTRATION_COMMANDS,
    _NOTIFICATION_CANCEL_COMMANDS,
)
from app.command_text import MAX_COMMAND_CHARS, normalized_command
from app.person_room_context import _is_room_follow_up
from app.self_improvement import _parse_improvement_command


def test_command_normalization_is_bounded_before_parsing() -> None:
    attacker_input = "stop " + (" " * (MAX_COMMAND_CHARS * 100))
    assert normalized_command(attacker_input) is None
    assert _is_room_follow_up(attacker_input) is False
    assert _parse_improvement_command(attacker_input) is None


def test_finite_notification_commands_preserve_supported_phrases() -> None:
    assert normalized_command("  Don't send it!!! ") in _NOTIFICATION_CANCEL_COMMANDS
    assert normalized_command("What   the hell?!") in _FRUSTRATION_COMMANDS
    assert normalized_command("stop eventually") not in _NOTIFICATION_CANCEL_COMMANDS


def test_room_follow_up_grammar_is_exact_and_preserves_referents() -> None:
    for phrase in (
        "In what room?",
        "What room is Amber?",
        "Which room is he in?",
        "Where in the flat is Aaron?",
        "Where home?",
    ):
        assert _is_room_follow_up(phrase) is True
    assert _is_room_follow_up("Where home is an attacker controlled suffix") is False


def test_improvement_command_grammar_preserves_actions_and_codes() -> None:
    expected = {
        "Emergency stop self-improvement": ("stop", None, None),
        "Resume Jarvis self improvement": ("resume", None, None),
        "Show the self-improvement status": ("status", None, None),
        "List pending failures you've recorded": ("failures", None, None),
        "Show pending fixes": ("candidates", None, None),
        "Record that as a mistake": ("record", None, None),
        "Please prepare a safe fix for the last mistake": ("prepare_last", None, None),
        "Build patch for issue #42": ("prepare_id", 42, None),
        "Approve improvement #42 code 123456": ("approve", 42, "123456"),
        "Deploy improvement 42 123456": ("deploy", 42, "123456"),
        "Reject improvement #42": ("reject", 42, None),
        "Issue a rollback ticket for improvement #42": ("rollback_ticket", 42, None),
        "Roll back improvement #42 code 123456": ("rollback", 42, "123456"),
    }
    for phrase, parsed in expected.items():
        assert _parse_improvement_command(phrase) == parsed


def test_improvement_parser_rejects_suffixes_and_malformed_codes() -> None:
    assert _parse_improvement_command("Approve improvement 4 code 123456 now") is None
    assert _parse_improvement_command("Rollback improvement 4 code 12345") is None
    assert _parse_improvement_command("Show improvement status attacker suffix") is None
