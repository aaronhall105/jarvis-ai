from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.email_assistant import EmailAssistantPolicyEngine
from app.email_semantic_routing import (
    email_topic_match_score,
    grounded_sender_candidates,
    rank_email_topic_messages,
)


@pytest.mark.parametrize("query", ("wageslip", "wage slip", "payslip", "pay slip"))
def test_human_topic_variants_rank_grounded_wage_slip_metadata(query):
    message = {
        "message_id": "wage-1",
        "subject": "WAGE SLIP",
        "snippet": "Your September payroll document",
        "received_at": "2026-09-24T14:11:47Z",
    }

    assert email_topic_match_score(query, message) >= 0.64
    assert [item["message_id"] for item in rank_email_topic_messages(query, [message])] == [
        "wage-1"
    ]


def test_topic_ranking_rejects_unrelated_metadata_and_is_bounded():
    unrelated = {
        "message_id": "social-1",
        "subject": "Weekend sale and social updates",
        "snippet": "Offers inside",
    }
    matches = [
        {"message_id": f"wage-{index}", "subject": "WAGE SLIP", "received_at": str(index)}
        for index in range(50)
    ]

    assert email_topic_match_score("wageslip", unrelated) == 0
    ranked = rank_email_topic_messages("wageslip", [unrelated, *matches], limit=10)
    assert len(ranked) == 10
    assert all(str(item["message_id"]).startswith("wage-") for item in ranked)


def test_partial_sender_grounding_is_unique_or_ambiguous_from_evidence_only():
    messages = [
        {
            "message_id": "1",
            "sender_name": "Joseph Scott",
            "from": "joseph@example.test",
        },
        {
            "message_id": "2",
            "sender_name": "Joseph Scott",
            "from": "joseph@example.test",
        },
    ]
    unique = grounded_sender_candidates(messages, "Jo")
    ambiguous = grounded_sender_candidates(
        [
            *messages,
            {"message_id": "3", "sender_name": "Jo Smith", "from": "jo@example.test"},
        ],
        "Jo",
    )

    assert [(item["display_name"], item["address"]) for item in unique] == [
        ("Joseph Scott", "joseph@example.test")
    ]
    assert {item["address"] for item in ambiguous} == {
        "joseph@example.test",
        "jo@example.test",
    }


class SearchRegistry:
    def __init__(self, *, native_messages, fallback_messages):
        self.native_messages = native_messages
        self.fallback_messages = fallback_messages
        self.requests = []

    async def execute(self, request, *, refresh_health=False):
        self.requests.append(request)
        assert refresh_health is True
        messages = self.native_messages if len(self.requests) == 1 else self.fallback_messages
        return SimpleNamespace(
            success=True,
            error=None,
            data={"messages": messages, "count": len(messages), "truncated": False},
        )


@pytest.mark.asyncio
async def test_provider_native_miss_uses_bounded_metadata_fallback(tmp_path):
    registry = SearchRegistry(
        native_messages=[],
        fallback_messages=[
            {
                "message_id": "outlook-wage-slip",
                "sender_name": "Joseph Scott",
                "from": "joseph@example.test",
                "subject": "WAGE SLIP",
                "received_at": "2026-09-24T14:11:47Z",
            },
            {
                "message_id": "unrelated",
                "sender_name": "Someone Else",
                "from": "other@example.test",
                "subject": "Weekend update",
            },
        ],
    )

    async def accounts(_principal):
        return [{"provider": "microsoft_outlook", "account_id": "outlook-1"}]

    engine = EmailAssistantPolicyEngine(
        tmp_path / "search.db",
        registry,
        account_resolver=accounts,  # type: ignore[arg-type]
    )
    result = await engine.search_mailbox(
        principal_id="aaron",
        conversation_id="usr:aaron:wageslip",
        provider="microsoft_outlook",
        account_id="outlook-1",
        request_id="search-1",
        topic_query="wageslip",
    )

    assert result["success"] is True
    assert result["search_strategy"] == "bounded_metadata_fallback"
    assert [item["message_id"] for item in result["messages"]] == ["outlook-wage-slip"]
    assert registry.requests[0].payload == {"query": "wageslip", "limit": 10}
    assert registry.requests[1].payload == {
        "folder": "inbox",
        "limit": 100,
        "metadata_only": True,
        "all_pages": True,
        "max_messages": 200,
    }


@pytest.mark.asyncio
async def test_gmail_topic_fallback_is_bounded_and_uses_inbox_metadata(tmp_path):
    registry = SearchRegistry(
        native_messages=[],
        fallback_messages=[
            {
                "message_id": "gmail-pay-slip",
                "sender_name": "Payroll",
                "from": "payroll@example.test",
                "subject": "Pay Slip",
                "internal_date_ms": 1_796_000_000_000,
            }
        ],
    )

    async def accounts(_principal):
        return [{"provider": "google_gmail", "account_id": "gmail-1"}]

    engine = EmailAssistantPolicyEngine(
        tmp_path / "gmail-search.db",
        registry,
        account_resolver=accounts,  # type: ignore[arg-type]
    )
    result = await engine.search_mailbox(
        principal_id="aaron",
        conversation_id="usr:aaron:gmail-payslip",
        provider="google_gmail",
        account_id="gmail-1",
        request_id="gmail-search-1",
        topic_query="wageslip",
    )

    assert [item["message_id"] for item in result["messages"]] == ["gmail-pay-slip"]
    assert registry.requests[1].payload == {
        "query": "in:inbox",
        "limit": 25,
        "metadata_only": True,
        "all_pages": True,
        "max_messages": 25,
    }


@pytest.mark.asyncio
async def test_sender_topic_filter_uses_exact_grounded_address(tmp_path):
    registry = SearchRegistry(
        native_messages=[],
        fallback_messages=[
            {
                "message_id": "right",
                "sender_name": "Joseph Scott",
                "from": "Joseph Scott <joseph@example.test>",
                "subject": "WAGE SLIP",
            },
            {
                "message_id": "wrong-sender",
                "sender_name": "Jo Smith",
                "from": "jo@example.test",
                "subject": "WAGE SLIP",
            },
        ],
    )

    async def accounts(_principal):
        return [{"provider": "microsoft_outlook", "account_id": "outlook-1"}]

    engine = EmailAssistantPolicyEngine(
        tmp_path / "sender.db",
        registry,
        account_resolver=accounts,  # type: ignore[arg-type]
    )
    result = await engine.search_mailbox(
        principal_id="aaron",
        conversation_id="usr:aaron:sender",
        provider="microsoft_outlook",
        account_id="outlook-1",
        request_id="sender-1",
        topic_query="pay slip",
        sender_address="joseph@example.test",
    )

    assert [item["message_id"] for item in result["messages"]] == ["right"]
    assert registry.requests[0].payload == {
        "folder": "inbox",
        "limit": 10,
        "sender": "joseph@example.test",
    }
