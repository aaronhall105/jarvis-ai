from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.conversation_engine import ConversationEngine
from app.google_integration import GoogleConnector
from app.response_presentation import (
    clean_email_reply_body,
    present_user_response,
    render_gmail_message_action,
    render_gmail_reply_status,
    render_home_state_evidence,
    render_presence_evidence,
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
    ("result", "expected"),
    [
        (
            {
                "success": True,
                "status": "verified",
                "operation": "send",
                "recipient": "amber.gill1992@outlook.com",
                "recipient_name": "Amber Gill",
                "provider_reference": "hidden",
            },
            "Done — I sent that email to Amber.",
        ),
        (
            {
                "success": True,
                "status": "verified",
                "operation": "draft",
                "recipient": "amber.gill1992@outlook.com",
                "recipient_name": "Amber",
            },
            "Done — I’ve drafted that email to Amber.",
        ),
        (
            {
                "operation": "send",
                "clarification_required": True,
                "clarification": "Which Amber do you mean?",
            },
            "Which Amber do you mean?",
        ),
        (
            {"operation": "send", "success": False, "error": "OAuth token expired"},
            "I couldn’t send that because Google needs reconnecting.",
        ),
    ],
)
def test_gmail_message_actions_are_natural_and_hide_evidence(
    result: dict[str, object], expected: str
) -> None:
    rendered = render_gmail_message_action(result)
    assert rendered == expected
    assert not any(term in rendered.casefold() for term in INTERNAL_TERMS)


def test_gmail_send_unavailable_response_keeps_action_specific_wording() -> None:
    response = "I can’t send that right now because Gmail needs reconnecting."
    assert present_user_response(response, request_text="Send Amber an email") == response


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


def test_setup_only_provider_failure_is_naturalised() -> None:
    raw = (
        "Instagram is unavailable — No supported Instagram adapter and authorised "
        "account are configured."
    )

    assert present_user_response(raw, request_text="Check my Instagram messages") == (
        "I can’t check Instagram yet because it isn’t set up."
    )


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
            "Yes Sent from Outlook for Android<https://aka.ms/AAb9ysg>",
            "Yes",
        ),
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


def test_home_state_renderer_does_not_call_unavailable_light_off() -> None:
    response = render_home_state_evidence(
        [
            {
                "tool": "search_entity_states",
                "result": {
                    "success": True,
                    "entities": [
                        {
                            "name": "Living Room Floodlight",
                            "state": "off",
                            "available": True,
                        },
                        {
                            "name": "Living Room LED Ring",
                            "state": "unavailable",
                            "available": False,
                        },
                    ],
                },
            }
        ],
        request_text="Are the living-room lights on?",
    )

    assert response == (
        "Living Room Floodlight is off, but I can’t confirm Living Room LED Ring "
        "because it’s unavailable."
    )
    assert "both" not in response.casefold()

    mixed = render_home_state_evidence(
        [
            {
                "tool": "search_entity_states",
                "result": {
                    "success": True,
                    "resolution": "exact",
                    "selected_entity": {
                        "entity_id": "switch.living_room_detect",
                        "name": "Living Room Detect",
                        "state": "on",
                    },
                },
            },
            {
                "tool": "search_entity_states",
                "result": {
                    "success": True,
                    "resolution": "ranked",
                    "entities": [
                        {
                            "entity_id": "light.living_room_floodlight",
                            "name": "Living Room Floodlight",
                            "state": "off",
                        }
                    ],
                },
            },
        ],
        request_text="Is Living Room Detect on, and is the Living Room Floodlight on?",
    )
    assert mixed == ("Living Room Detect is on, while Living Room Floodlight is off.")


def test_home_state_renderer_never_presents_ambiguous_search_matches_as_facts() -> None:
    response = render_home_state_evidence(
        [
            {
                "tool": "search_entity_states",
                "arguments": {"query": "living room Detect"},
                "result": {
                    "success": True,
                    "resolution": "ambiguous",
                    "query": "living room Detect",
                    "entities": [
                        {"name": "Sofa TV occupancy", "state": "off"},
                        {"name": "Sofa Remote occupancy", "state": "off"},
                    ],
                },
            },
            {
                "tool": "search_entity_states",
                "arguments": {"query": "floodlight"},
                "result": {
                    "success": True,
                    "resolution": "ranked",
                    "entities": [
                        {
                            "entity_id": "light.living_room_floodlight",
                            "name": "Living Room Floodlight",
                            "state": "off",
                        }
                    ],
                },
            },
        ],
        request_text="Is Living Room Detect on, and is the Living Room Floodlight on?",
    )

    assert response == (
        "Living Room Floodlight is off, but I couldn’t confidently match "
        "living room Detect to one device."
    )
    assert "Sofa" not in response


def test_home_state_renderer_rejects_ambiguous_followup_reads_and_irrelevant_area_lists() -> None:
    response = render_home_state_evidence(
        [
            {
                "tool": "search_entity_states",
                "arguments": {"query": "living room Detect"},
                "result": {
                    "success": True,
                    "resolution": "ambiguous",
                    "query": "living room Detect",
                    "entities": [
                        {
                            "entity_id": "binary_sensor.sofa_tv_occupancy",
                            "name": "Sofa TV occupancy",
                            "state": "off",
                        }
                    ],
                },
            },
            {
                "tool": "get_entity_state",
                "result": {
                    "success": True,
                    "entity": {
                        "entity_id": "binary_sensor.sofa_tv_occupancy",
                        "name": "Sofa TV occupancy",
                        "state": "off",
                    },
                },
            },
            {
                "tool": "list_area_states",
                "arguments": {"area_id": "living_room", "domain": "binary_sensor"},
                "result": {
                    "success": True,
                    "entities": [
                        {
                            "entity_id": "binary_sensor.dining_table_person_occupancy",
                            "name": "Dining Table Person occupancy",
                            "state": "off",
                        }
                    ],
                },
            },
            {
                "tool": "list_area_states",
                "arguments": {"area_id": "living_room", "domain": "light"},
                "result": {
                    "success": True,
                    "entities": [
                        {
                            "entity_id": "light.living_room_floodlight",
                            "name": "Living Room Floodlight",
                            "state": "off",
                        },
                        {
                            "entity_id": "light.home_assistant_voice_led_ring",
                            "name": "Jarvis Voice Living Room LED Ring",
                            "state": "unavailable",
                            "available": False,
                        },
                    ],
                },
            },
        ],
        request_text="Is Living Room Detect on, and is the Living Room Floodlight on?",
    )

    assert response == (
        "Living Room Floodlight is off, but I couldn’t confidently match "
        "living room Detect to one device."
    )
    assert "Sofa" not in response
    assert "Dining Table" not in response
    assert "LED Ring" not in response


def test_home_state_renderer_keeps_generic_area_lists_and_exact_named_entities() -> None:
    area_result = {
        "success": True,
        "entities": [
            {"entity_id": "light.lamp", "name": "Lamp", "state": "on"},
            {"entity_id": "light.wall", "name": "Wall Light", "state": "off"},
        ],
    }

    assert (
        render_home_state_evidence(
            [
                {
                    "tool": "list_area_states",
                    "arguments": {"area_id": "living_room", "domain": "light"},
                    "result": area_result,
                }
            ],
            request_text="Are the living-room lights on?",
        )
        == "Lamp is on, while Wall Light is off."
    )

    assert (
        render_home_state_evidence(
            [
                {
                    "tool": "list_area_states",
                    "arguments": {"area_id": "living_room", "domain": "light"},
                    "result": area_result,
                }
            ],
            request_text="Is the Wall Light on?",
        )
        == "No, Wall Light is off."
    )

    adversarial = render_home_state_evidence(
        [
            {
                "tool": "get_entity_state",
                "result": {"success": True, "entity": {"name": "Lamp", "state": "off"}},
            }
        ],
        request_text="Are" + (" " * 20_000) + "the lights on?",
    )
    assert adversarial == "No, Lamp is off."

    long_punctuation = render_home_state_evidence(
        [
            {
                "tool": "get_entity_state",
                "result": {"success": True, "entity": {"name": "Lamp", "state": "off"}},
            }
        ],
        request_text="Are the lights on" + ("?" * 20_000),
    )
    assert long_punctuation == "No, Lamp is off."

    long_non_match = render_home_state_evidence(
        [
            {
                "tool": "get_entity_state",
                "result": {"success": True, "entity": {"name": "Lamp", "state": "off"}},
            }
        ],
        request_text="Tell me " + ("something ordinary " * 5_000),
    )
    assert long_non_match == "Lamp is off."

    large_valid_question = render_home_state_evidence(
        [
            {
                "tool": "get_entity_state",
                "result": {"success": True, "entity": {"name": "Lamp", "state": "off"}},
            }
        ],
        request_text="Are " + ("the living room " * 5_000) + "lights on?",
    )
    assert large_valid_question == "No, Lamp is off."

    assert (
        render_home_state_evidence(
            [
                {
                    "tool": "get_entity_state",
                    "result": {"success": True, "entity": {"name": "Lamp", "state": "off"}},
                }
            ],
            request_text="Show me the raw JSON state for the lamp",
        )
        is None
    )


def test_home_state_renderer_keeps_generic_multi_result_domain_search() -> None:
    response = render_home_state_evidence(
        [
            {
                "tool": "search_entity_states",
                "arguments": {
                    "query": "living room light",
                    "domain": "light",
                    "area_id": "living_room",
                },
                "result": {
                    "success": True,
                    "resolution": "ambiguous",
                    "query": "living room light",
                    "entities": [
                        {
                            "entity_id": "light.living_room_floodlight",
                            "name": "Living Room Floodlight",
                            "state": "off",
                        },
                        {
                            "entity_id": "light.home_assistant_voice_led_ring",
                            "name": "Jarvis Voice Living Room LED Ring",
                            "state": "unavailable",
                            "available": False,
                        },
                    ],
                },
            }
        ],
        request_text="What are the current states of the living-room lights?",
    )

    assert response == (
        "Living Room Floodlight is off. "
        "I can’t confirm Jarvis Voice Living Room LED Ring because it’s unavailable."
    )


def test_presence_renderer_is_natural_but_keeps_conflicting_evidence() -> None:
    at_home = render_presence_evidence(
        {
            "person": {"name": "Amber", "state": "home"},
            "source": {"name": "Amber Phone", "state": "home"},
            "conflicts": [],
        },
        person_name="Amber",
    )
    conflicted = render_presence_evidence(
        {
            "person": {"name": "Aaron", "state": "home"},
            "conflicts": [{"name": "Aaron Phone", "state": "not_home"}],
        },
        person_name="Aaron",
        first_person=True,
    )

    assert at_home == "Amber’s at home."
    assert conflicted == (
        "Home Assistant says you are at home, but Aaron Phone says away, "
        "so I can’t confirm that properly."
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
