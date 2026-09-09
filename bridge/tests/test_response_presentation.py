from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.conversation_engine import ConversationEngine
from app.google_integration import GoogleConnector
from app.response_presentation import (
    clean_email_reply_body,
    present_user_response,
    render_gmail_reply_status,
)


INTERNAL_TERMS = (
    "principal_id",
    "capability_id",
    "provider_reference",
    "thread_id",
    "message_id",
    "action_id",
    "execution_status",
    "&lt;",
    "mime",
)


@pytest.mark.parametrize(
    "category,response",
    [
        ("calendar", "You’ve got a dentist appointment at two."),
        ("contacts", "Amber’s mobile number ends in 42."),
        ("home_control", "Yep, I’ve turned the living-room lights off."),
        ("home_state", "The living-room lights are off."),
        ("presence", "Amber’s at home."),
        ("weather", "There’s rain due around four."),
        ("web", "I found two current sources that agree."),
        ("scheduled_action", "That’s scheduled for tomorrow at nine."),
        ("monitor", "I’ll let you know when Amber replies."),
        ("provider_unavailable", "I can’t check that service properly right now."),
        ("ambiguity", "Which one do you mean — the email to Amber or Sarah?"),
        ("battery", "Your phone’s on 23%, so I’d put it on charge soon."),
        ("voice", "Yep, that’s done."),
        ("android", "No, there’s nothing urgent at the moment."),
        ("proactive", "The front door’s been left open."),
    ],
)
def test_global_presentation_keeps_natural_evidence_based_responses(
    category: str,
    response: str,
) -> None:
    presented = present_user_response(response, request_text=f"ordinary {category}")

    assert presented == response
    assert not any(term in presented.casefold() for term in INTERNAL_TERMS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Google token refresh failed", "Google needs reconnecting"),
        ("Jarvis Core error: database exploded", "Something went wrong on my side"),
        (
            "No principal-owned verified Gmail send receipt matched the request",
            "Which email do you mean?",
        ),
        ("execution_status=failed provider_reference=x", "I couldn’t confirm that properly."),
        ("reply_count=1 thread_id=x", "I couldn’t confirm that properly."),
    ],
)
def test_internal_status_language_is_naturalised(raw: str, expected: str) -> None:
    assert expected in present_user_response(raw, request_text="Can you check?")


def test_explicit_raw_technical_request_preserves_diagnostics() -> None:
    raw = '{"principal_id":"aaron","thread_id":"thread-1"}'
    assert present_user_response(raw, request_text="Show me the raw JSON diagnostics") == raw


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Yes", "Yes"),
        ("First line\nsecond line", "First line second line"),
        ("Yes\n\nSent from Outlook for Android\nFrom: Aaron", "Yes"),
        (
            "Yes\n\nFrom: Aaron Hall\nSent: Thursday\nTo: Amber\nSubject: Dinner",
            "Yes",
        ),
        ("Yes\n\nOn Thu, Aaron <a@example.test> wrote:\nOld text", "Yes"),
        ("Yes &amp; definitely", "Yes & definitely"),
        ("<html><body><p>Yes</p><blockquote>Old text</blockquote></body></html>", "Yes"),
        ("<script>alert(1)</script><p>Safe reply</p>", "Safe reply"),
        ("Sí — definitely 👍", "Sí — definitely 👍"),
        (
            "This really is From: the best place to start.",
            "This really is From: the best place to start.",
        ),
    ],
)
def test_email_reply_cleaning_keeps_only_new_inbound_content(body: str, expected: str) -> None:
    assert clean_email_reply_body(body) == expected


def test_gmail_reply_found_uses_latest_inbound_body_naturally() -> None:
    response = render_gmail_reply_status(
        {
            "success": True,
            "reply_received": True,
            "recipient": "amber.gill1992@outlook.com",
            "recipient_name": "Amber Gill",
            "replies": [
                {"from": "Amber Gill <amber.gill1992@outlook.com>", "body": "Earlier"},
                {
                    "from": "Amber Gill <amber.gill1992@outlook.com>",
                    "body": "Yes\n\nSent from Outlook for Android\nFrom: Aaron Hall",
                },
            ],
        }
    )

    assert response == "Yeah, Amber replied. Amber’s reply just said ‘Yes’."
    assert not any(term in response.casefold() for term in INTERNAL_TERMS)


def test_gmail_body_evidence_prefers_plain_text_over_html_regardless_of_part_order() -> None:
    def encode(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": encode("<p>HTML copy</p>")}},
            {"mimeType": "text/plain", "body": {"data": encode("Plain copy")}},
        ],
    }

    assert GoogleConnector._body_text(payload) == "Plain copy"


def test_gmail_no_reply_unavailable_ambiguity_and_attachment_are_natural() -> None:
    no_reply = render_gmail_reply_status(
        {
            "success": True,
            "reply_received": False,
            "recipient": "amber.gill1992@outlook.com",
            "recipient_name": "Amber Gill",
        }
    )
    unavailable = render_gmail_reply_status(
        {"success": False, "error": "Google token refresh failed"}
    )
    ambiguous = render_gmail_reply_status(
        {
            "success": True,
            "clarification_required": True,
            "clarification": "Which email do you mean?",
        }
    )
    attachment = render_gmail_reply_status(
        {
            "success": True,
            "reply_received": True,
            "recipient_name": "Amber Gill",
            "replies": [{"from": "Amber Gill", "attachments": [{"filename": "photo.jpg"}]}],
        }
    )

    assert no_reply == "No, I haven’t found a reply from Amber yet."
    assert (
        unavailable == "I can’t check Gmail properly right now because Google needs reconnecting."
    )
    assert ambiguous == "Which email do you mean?"
    assert attachment == (
        "Yeah, Amber replied with an attachment, but there wasn’t any message text."
    )


@pytest.mark.asyncio
async def test_conversation_storage_applies_the_same_presentation_boundary(tmp_path: Path) -> None:
    conversations = ConversationEngine(tmp_path / "conversations.db")
    created = await conversations.create_conversation(conversation_id="usr:aaron:test")
    await conversations.add_user_message(created["conversation_id"], "Can you check that?")

    stored = await conversations.add_assistant_message(
        created["conversation_id"],
        "execution_status=failed provider_reference=x",
    )

    assert stored["content"] == "I couldn’t confirm that properly."


def test_all_primary_response_egress_paths_use_presentation_policy() -> None:
    root = Path(__file__).parents[1] / "app"
    for filename in (
        "ai_engine.py",
        "main.py",
        "conversation_engine.py",
        "realtime_voice.py",
        "followup_engine.py",
        "proactive_orchestrator.py",
        "house_awareness.py",
        "task_engine.py",
        "recurring_schedule_engine.py",
        "conditional_action_engine.py",
    ):
        assert "response_presentation" in (root / filename).read_text(encoding="utf-8")
