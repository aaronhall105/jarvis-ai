"""Provider-neutral semantic routing for safe mailbox reads.

This module recognises bounded email-read intents.  It never selects an
unregistered capability, invents an account/contact, or authorises a write;
those decisions remain at the capability and policy boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailReadIntent:
    kind: str
    provider: str | None = None
    filter_kind: str | None = None
    person: str | None = None
    literal_query: str | None = None


def provider_from_text(text: str) -> str | None:
    lowered = str(text or "").casefold()
    if "gmail" in lowered or "google mail" in lowered:
        return "google_gmail"
    if any(term in lowered for term in ("outlook", "microsoft 365", "microsoft email")):
        return "microsoft_outlook"
    return None


def _person_reference(text: str) -> str | None:
    """Extract a person phrase from general sender-oriented email grammar."""

    cleaned = " ".join(str(text or "")[:4_096].strip().split()).strip(" .?!")
    lowered = cleaned.casefold().replace("’", "'")

    def trim_provider_suffix(value: str) -> str:
        lowered_value = value.casefold()
        suffixes = tuple(
            f" {preposition} {possessive}{provider}{account}"
            for preposition in ("on", "in", "from")
            for possessive in ("", "my ")
            for provider in ("gmail", "outlook", "microsoft 365")
            for account in ("", " account")
        )
        for suffix in suffixes:
            if lowered_value.endswith(suffix):
                return value[: -len(suffix)]
        return value

    value = ""
    for marker in (
        " emails from ",
        " email from ",
        " messages from ",
        " message from ",
        " mail from ",
    ):
        index = lowered.rfind(marker)
        if index >= 0:
            value = cleaned[index + len(marker) :]
            break
    if not value:
        index = lowered.rfind(" from ")
        if index >= 0 and any(
            lowered.endswith(suffix)
            for suffix in (
                " in gmail",
                " on gmail",
                " in outlook",
                " on outlook",
                " in microsoft 365",
                " on microsoft 365",
            )
        ):
            value = cleaned[index + len(" from ") :]
    if not value:
        for noun in ("emails ", "email ", "messages ", "message "):
            start = lowered.find(noun)
            if start < 0:
                continue
            for suffix in (" sent to me", " sent me"):
                if lowered.endswith(suffix):
                    value = cleaned[start + len(noun) : -len(suffix)]
                    break
            if value:
                break
    if not value:
        prefix_length = len("show me ") if lowered.startswith("show me ") else 0
        possessive = lowered.find("'s ", prefix_length)
        if possessive >= 0 and any(
            lowered.endswith(f" {recency} {provider}{noun}")
            for recency in ("latest", "newest", "most recent", "last")
            for provider in ("", "gmail ", "outlook ", "microsoft 365 ")
            for noun in ("email", "message")
        ):
            value = cleaned[prefix_length:possessive]
    value = trim_provider_suffix(value).strip(" ,.'\"")
    if value and value.casefold() not in {
        "me",
        "my",
        "the",
        "a",
        "an",
        "what",
        "who",
        "where",
        "when",
        "how",
        "it",
        "that",
        "there",
    }:
        return value
    return None


def classify_email_read(
    text: str,
    *,
    focused_provider: str | None = None,
    focused_kind: str | None = None,
) -> EmailReadIntent | None:
    """Classify explicit mailbox reads and grounded conversational follow-ups."""

    raw = " ".join(str(text or "")[:4_096].strip().split())
    command = raw.casefold().strip(" .?!")
    provider = provider_from_text(command)
    email_domain = provider is not None or any(
        token in f" {command} "
        for token in (
            " email ",
            " emails ",
            " message ",
            " messages ",
            " mailbox ",
            " inbox ",
            " unread ",
            " bin ",
            " deleted items ",
        )
    )

    provider_switches = {
        f"{prefix}what about {possessive}{provider}{suffix}"
        for prefix in ("", "and ")
        for possessive in ("", "my ")
        for provider in ("gmail", "outlook")
        for suffix in ("", " account", " one", " ones")
    }
    if command in provider_switches and focused_kind:
        return EmailReadIntent(kind=focused_kind, provider=provider or focused_provider)

    provider_lists = {
        f"show me {article}{provider} {noun}"
        for article in ("", "the ")
        for provider in ("gmail", "outlook")
        for noun in ("one", "ones", "email", "emails", "message", "messages")
    }
    if command in provider_lists and focused_kind == "count":
        return EmailReadIntent(kind="list_filter", provider=provider or focused_provider)

    possessive_follow_up = command.removeprefix("what about ")
    for suffix in ("'s emails", "'s email", "'s messages", "'s message"):
        if possessive_follow_up.endswith(suffix):
            followup_person = raw[len("what about ") : -len(suffix)].strip()
            if followup_person:
                return EmailReadIntent(
                    kind="sender_search",
                    provider=provider or focused_provider,
                    person=followup_person,
                )

    if command in {"what about hers", "what about her emails", "show me her emails"}:
        if focused_kind == "sender_search":
            return EmailReadIntent(kind="sender_search", provider=focused_provider)
        return None

    if command in {
        "one before that",
        "one before it",
        "the one before that",
        "the one before it",
        "what about one before that",
        "what about one before it",
        "what about the one before that",
        "what about the one before it",
    }:
        return EmailReadIntent(kind="previous", provider=focused_provider)
    if command in {
        "who sent it",
        "who sent that",
        "who was it",
        "who was that",
        "who is it",
        "who is that",
        "who was it from",
        "who was that from",
        "who is it from",
        "who is that from",
    }:
        return EmailReadIntent(kind="focused_sender", provider=focused_provider)

    literal_query = ""
    if any(term in f" {command} " for term in (" search ", " find ", " look for ")):
        for marker in (" exact text ", " exact phrase "):
            index = command.find(marker)
            if index >= 0:
                literal_query = raw[index + len(marker) :].strip(" .?!'\"")
                break
    if literal_query and email_domain:
        return EmailReadIntent(
            kind="literal_search", provider=provider, literal_query=literal_query
        )

    if email_domain and "how many" in command:
        filter_kind = (
            "bin"
            if "deleted items" in command or " bin " in f" {command} "
            else "unread_inbox"
            if "unread" in command
            else "all_mail"
        )
        return EmailReadIntent(kind="count", provider=provider, filter_kind=filter_kind)

    latest_markers = ("latest", "newest", "most recent", "last email", "last message")
    person = _person_reference(raw) if email_domain else None
    if person and any(marker in command for marker in latest_markers):
        return EmailReadIntent(kind="sender_search", provider=provider, person=person)
    if email_domain and any(marker in command for marker in latest_markers):
        return EmailReadIntent(kind="latest", provider=provider)

    if (
        person
        and email_domain
        and (
            any(
                marker in command
                for marker in ("search", "find", "show", "any email", "any message")
            )
            or command.startswith("any ")
        )
    ):
        return EmailReadIntent(kind="sender_search", provider=provider, person=person)

    if command in {"show me the latest one", "show the latest one"} and focused_kind:
        return EmailReadIntent(kind=focused_kind, provider=focused_provider)
    return None
