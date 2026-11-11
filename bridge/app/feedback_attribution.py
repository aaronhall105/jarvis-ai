"""Correlate feedback only with recorded write receipts, never conversational claims."""

from __future__ import annotations

from typing import Any

WRITE_TOOLS = frozenset(
    {
        "execute_home_action",
        "execute_home_parameter",
        "send_mobile_notification",
        "control_device",
        "set_media_volume",
    }
)


def action_receipts(source: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    calls = source.get("tool_calls")
    if not isinstance(calls, list):
        return receipts
    for call in calls:
        if not isinstance(call, dict) or call.get("tool") not in WRITE_TOOLS:
            continue
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        receipt = result.get("action_receipt") or result.get("receipt")
        if not isinstance(receipt, dict):
            continue
        identifier = receipt.get("action_id")
        if (
            not isinstance(identifier, str)
            or not identifier
            or receipt.get("conversation_id") != source.get("conversation_id")
            or not receipt.get("requested_operation")
            or receipt.get("status")
            not in {"verified", "accepted_unverified", "failed", "outcome_unknown", "rejected"}
        ):
            continue
        receipts.append(receipt)
    return receipts


def attribute_feedback(
    context: dict[str, Any], history: list[dict[str, Any]], correction: str
) -> dict[str, Any]:
    """Resolve one receipt in the last turn, or an exact explanation reference.

    No semantic search over old writes: a generic complaint cannot choose an
    unrelated historical action. Multiple writes and absent receipts need review.
    History is already scoped to the authenticated principal and conversation.
    """
    receipts = action_receipts(context)
    matches = [(context, receipt) for receipt in receipts]
    referenced: set[str] = set()
    if not receipts:
        calls = context.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, dict) or call.get("tool") != "explain_home_action_receipt":
                continue
            result = call.get("result")
            if isinstance(result, dict) and result.get("success") is True:
                identifier = result.get("receipt_id")
                if isinstance(identifier, str) and identifier:
                    referenced.add(identifier)
        for previous in history:
            for receipt in action_receipts(previous):
                if receipt["action_id"] in referenced:
                    matches.append((previous, receipt))
    # Replayed receipt appearances are not distinct actions, but disagreeing
    # snapshots cannot establish one authoritative result.
    unique: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    conflict = False
    for source, receipt in matches:
        prior = unique.get(receipt["action_id"])
        if prior is not None and prior[1] != receipt:
            conflict = True
        unique[receipt["action_id"]] = (source, receipt)
    if len(unique) == 1 and not conflict and (not referenced or referenced == set(unique)):
        source, receipt = next(iter(unique.values()))
        return {
            **source,
            "feedback_attribution": {
                "status": "receipt_correlated",
                "feedback_context_interaction_id": context["interaction_id"],
                "receipt_id": receipt["action_id"],
                "receipt": receipt,
            },
        }
    return {
        "conversation_id": context["conversation_id"],
        "user_key": context["user_key"],
        "intent": "feedback_review",
        "raw_text": correction,
        "tool_calls": [],
        "feedback_attribution": {
            "status": "review_required",
            "reason": "ambiguous_receipts" if matches else "no_recorded_action_receipt",
            "feedback_context_interaction_id": context["interaction_id"],
            "candidate_receipt_ids": sorted(unique),
            "referenced_receipt_ids": sorted(referenced),
            "context": context,
        },
    }
