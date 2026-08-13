from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from typing import Any


# Failures carrying one of these markers are structural,
# permission/policy-related, or execution-budget failures.
# They must never be hidden merely because another read-only
# inspection succeeded.
_BLOCKING_READ_ONLY_FAILURE_MARKERS = (
    "tool_call_limit_reached",
    "duplicate_tool_call",
    "tool call limit",
    "same tool call was already attempted",
    "not_authorized",
    "not_authorised",
    "unauthorized",
    "unauthorised",
    "forbidden",
    "permission denied",
    "authentication",
    "invalid_arguments",
    "invalid_tool_arguments",
    "invalid tool arguments",
    "validation_error",
    "policy violation",
    "restricted",
    "absolute paths are not permitted",
    "path escapes the permitted code root",
    "invalid git path",
    "unknown code root",
    "code awareness is disabled",
    "git is not installed",
)


def _tool_name(call: dict[str, Any]) -> str:
    return str(
        call.get("tool")
        or call.get("name")
        or ""
    ).strip()


def _result(call: dict[str, Any]) -> dict[str, Any]:
    result = call.get("result")

    if isinstance(result, dict):
        return result

    return {}


def call_failed(call: dict[str, Any]) -> bool:
    return _result(call).get("success") is False


def call_succeeded(call: dict[str, Any]) -> bool:
    return _result(call).get("success") is True


def is_tolerable_read_only_failure(
    call: dict[str, Any],
    *,
    read_only_tools: Collection[str],
) -> bool:
    """
    Return True only for a non-authoritative inspection miss.

    This deliberately does not infer success from "any successful
    call". The failed call itself must belong to the explicitly
    read-only tool set and must not represent a policy, permission,
    validation, duplicate-call, or tool-budget failure.
    """

    if not call_failed(call):
        return False

    if _tool_name(call) not in read_only_tools:
        return False

    rendered = json.dumps(
        _result(call),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).casefold()

    return not any(
        marker in rendered
        for marker in _BLOCKING_READ_ONLY_FAILURE_MARKERS
    )


def request_tool_success(
    calls: Sequence[dict[str, Any]],
    *,
    partial_read_only_allowed: bool,
    read_only_tools: Collection[str],
    final_reply: str,
) -> bool:
    """
    Resolve overall request success conservatively.

    Existing behaviour remains fail-closed for normal requests.
    A mixed inspection request may succeed only when:
      * partial read-only handling is explicitly enabled;
      * every failed call is a tolerable read-only inspection miss;
      * at least one read-only inspection actually succeeded; and
      * Jarvis produced a non-empty answer from the evidence.

    All-read-only failure and every authoritative failure remain
    failures.
    """

    calls = list(calls)

    if not calls:
        return True

    failed = [
        call
        for call in calls
        if call_failed(call)
    ]

    if not failed:
        return True

    if not partial_read_only_allowed:
        return False

    if any(
        not is_tolerable_read_only_failure(
            call,
            read_only_tools=read_only_tools,
        )
        for call in failed
    ):
        return False

    useful_evidence = any(
        call_succeeded(call)
        and _tool_name(call) in read_only_tools
        for call in calls
    )

    if not useful_evidence:
        return False

    return bool(
        str(final_reply or "").strip()
    )


def has_blocking_tool_failure(
    calls: Sequence[dict[str, Any]],
    *,
    overall_success: bool,
    read_only_tools: Collection[str],
) -> bool:
    """
    Decide whether SelfImprovement should treat a failed tool as
    failure evidence.

    A failed call is suppressed only after the request has already
    been classified successful and every failed call is an explicitly
    tolerable read-only inspection miss.
    """

    failed = [
        call
        for call in calls
        if call_failed(call)
    ]

    if not failed:
        return False

    if not overall_success:
        return True

    return any(
        not is_tolerable_read_only_failure(
            call,
            read_only_tools=read_only_tools,
        )
        for call in failed
    )
