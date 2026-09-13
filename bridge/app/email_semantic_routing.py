"""Provider-neutral semantic routing for safe mailbox reads.

This module recognises bounded email-read intents.  It never selects an
unregistered capability, invents an account/contact, or authorises a write;
those decisions remain at the capability and policy boundaries.
"""

from __future__ import annotations

import re
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

    cleaned = " ".join(str(text or "").strip().split()).strip(" .?!")
    patterns = (
        r"\b(?:emails?|messages?|mail)\s+from\s+(.+)$",
        r"\bfrom\s+(.+?)\s+(?:in|on)\s+(?:gmail|outlook|microsoft 365)$",
        r"\b(?:emails?|messages?)\s+(.+?)\s+sent\s+(?:to\s+)?me$",
        r"^(?:show me\s+)?(.+?)(?:'s|’s)\s+(?:latest|newest|most recent|last)\s+(?:email|message)$",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        value = re.sub(
            r"\s+(?:on|in|from)\s+(?:my\s+)?(?:gmail|outlook|microsoft 365)(?:\s+account)?$",
            "",
            match.group(1),
            flags=re.IGNORECASE,
        ).strip(" ,.'\"")
        if value and value.casefold() not in {"me", "my", "the", "a", "an"}:
            return value
    return None


def classify_email_read(
    text: str,
    *,
    focused_provider: str | None = None,
    focused_kind: str | None = None,
) -> EmailReadIntent | None:
    """Classify explicit mailbox reads and grounded conversational follow-ups."""

    raw = " ".join(str(text or "").strip().split())
    command = raw.casefold().strip(" .?!")
    provider = provider_from_text(command)
    email_domain = provider is not None or bool(
        re.search(r"\b(?:emails?|messages?|mailbox|inbox|unread|bin|deleted items)\b", command)
    )

    provider_switch = re.fullmatch(
        r"(?:and\s+)?what about\s+(?:my\s+)?(?:gmail|outlook)(?:\s+(?:account|ones?))?",
        command,
    )
    if provider_switch and focused_kind:
        return EmailReadIntent(kind=focused_kind, provider=provider or focused_provider)

    provider_list = re.fullmatch(
        r"show me (?:the )?(?:gmail|outlook) (?:ones|emails|messages)", command
    )
    if provider_list and focused_kind == "count":
        return EmailReadIntent(kind="list_filter", provider=provider or focused_provider)

    possessive_follow_up = re.fullmatch(
        r"what about (.+?)(?:'s|’s) (?:emails|messages)", raw, flags=re.IGNORECASE
    )
    if possessive_follow_up:
        return EmailReadIntent(
            kind="sender_search",
            provider=provider or focused_provider,
            person=possessive_follow_up.group(1).strip(),
        )

    if command in {"what about hers", "what about her emails", "show me her emails"}:
        if focused_kind == "sender_search":
            return EmailReadIntent(kind="sender_search", provider=focused_provider)
        return None

    if re.fullmatch(r"(?:what about\s+)?(?:the\s+)?one before (?:that|it)", command):
        return EmailReadIntent(kind="previous", provider=focused_provider)
    if re.fullmatch(r"who (?:sent|was|is) (?:it|that)(?: from)?", command):
        return EmailReadIntent(kind="focused_sender", provider=focused_provider)

    literal = re.search(
        r"\b(?:search|find|look for)\b.*?\bexact (?:text|phrase)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if literal and email_domain:
        query = literal.group(1).strip(" .?!'\"")
        return EmailReadIntent(kind="literal_search", provider=provider, literal_query=query)

    if email_domain and "how many" in command:
        filter_kind = (
            "bin"
            if "deleted items" in command or re.search(r"\b(?:the\s+)?bin\b", command)
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
        and any(
            marker in command for marker in ("search", "find", "show", "any email", "any message")
        )
    ) or (person and email_domain and command.startswith("any ")):
        return EmailReadIntent(kind="sender_search", provider=provider, person=person)

    if command in {"show me the latest one", "show the latest one"} and focused_kind:
        return EmailReadIntent(kind=focused_kind, provider=focused_provider)
    return None
