from __future__ import annotations

from app.code_awareness import CodeAwarenessEngine
from app.tool_outcomes import (
    has_blocking_tool_failure,
    request_tool_success,
)


READ_ONLY = CodeAwarenessEngine.TOOL_NAMES


def _call(
    tool: str,
    success: bool,
    **result,
):
    return {
        "tool": tool,
        "arguments": {},
        "result": {
            "success": success,
            **result,
        },
    }


def test_mixed_read_only_inspection_can_succeed():
    calls = [
        _call(
            "code_roots",
            True,
            roots=["jarvis", "voice_pe"],
        ),
        _call(
            "git_status",
            True,
            root="jarvis",
            branch="conversation-engine",
        ),
        _call(
            "git_status",
            False,
            root="voice_pe",
            error="fatal: not a git repository",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=True,
        read_only_tools=READ_ONLY,
        final_reply=(
            "The Jarvis repository is on conversation-engine. "
            "The optional Voice PE Git status was unavailable."
        ),
    ) is True

    assert has_blocking_tool_failure(
        calls,
        overall_success=True,
        read_only_tools=READ_ONLY,
    ) is False


def test_all_read_only_inspections_failing_remains_failure():
    calls = [
        _call(
            "git_status",
            False,
            root="jarvis",
            error="fatal: not a git repository",
        ),
        _call(
            "code_read",
            False,
            error="Requested file does not exist.",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=True,
        read_only_tools=READ_ONLY,
        final_reply="I could not inspect the requested implementation.",
    ) is False

    assert has_blocking_tool_failure(
        calls,
        overall_success=False,
        read_only_tools=READ_ONLY,
    ) is True


def test_authoritative_action_failure_remains_fail_closed():
    calls = [
        _call(
            "code_roots",
            True,
            roots=["jarvis"],
        ),
        _call(
            "control_device",
            False,
            error="device unavailable",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=True,
        read_only_tools=READ_ONLY,
        final_reply="Inspection succeeded but the action failed.",
    ) is False

    assert has_blocking_tool_failure(
        calls,
        overall_success=True,
        read_only_tools=READ_ONLY,
    ) is True


def test_duplicate_read_only_call_failure_is_not_hidden():
    calls = [
        _call(
            "code_roots",
            True,
            roots=["jarvis"],
        ),
        _call(
            "git_status",
            False,
            code="duplicate_tool_call",
            message="The same tool call was already attempted.",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=True,
        read_only_tools=READ_ONLY,
        final_reply="Some evidence was available.",
    ) is False


def test_tool_call_limit_failure_is_not_hidden():
    calls = [
        _call(
            "code_roots",
            True,
            roots=["jarvis"],
        ),
        _call(
            "code_read",
            False,
            code="tool_call_limit_reached",
            message="The maximum number of tool calls was reached.",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=True,
        read_only_tools=READ_ONLY,
        final_reply="Some evidence was available.",
    ) is False


def test_normal_requests_preserve_all_calls_must_succeed():
    calls = [
        _call(
            "code_roots",
            True,
            roots=["jarvis"],
        ),
        _call(
            "git_status",
            False,
            error="optional inspection unavailable",
        ),
    ]

    assert request_tool_success(
        calls,
        partial_read_only_allowed=False,
        read_only_tools=READ_ONLY,
        final_reply="Evidence exists.",
    ) is False


def test_failure99_policy_is_wired_into_both_runtime_layers():
    from pathlib import Path

    ai_source = Path(
        "bridge/app/ai_engine.py"
    ).read_text(encoding="utf-8")

    improvement_source = Path(
        "bridge/app/self_improvement.py"
    ).read_text(encoding="utf-8")

    assert (
        "success = request_tool_success("
        in ai_source
    )

    assert (
        "partial_read_only_allowed=code_awareness_requested"
        in ai_source
    )

    assert (
        "read_only_tools=CodeAwarenessEngine.TOOL_NAMES"
        in ai_source
    )

    assert (
        "blocking_failed_tool = has_blocking_tool_failure("
        in improvement_source
    )

    assert (
        "failure_like = (\n"
        "            blocking_failed_tool"
        in improvement_source
    )

    assert (
        "if blocking_failed_tool or ("
        in improvement_source
    )

    assert (
        "failed_tool = any("
        not in improvement_source
    )
