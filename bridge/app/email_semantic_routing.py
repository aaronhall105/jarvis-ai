"""Provider-neutral semantic routing for safe mailbox reads and cleanup previews.

This module recognises bounded email-read intents.  It never selects an
unregistered capability, invents an account/contact, or authorises a write;
those decisions remain at the capability and policy boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class EmailReadIntent:
    kind: str
    provider: str | None = None
    filter_kind: str | None = None
    person: str | None = None
    literal_query: str | None = None


@dataclass(frozen=True)
class EmailCleanupClause:
    """One user-selected mailbox predicate, without execution authority."""

    type: str
    values: tuple[str, ...] = ()
    unread: bool | None = None
    older_than_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EmailCleanupIntent:
    """A provider-neutral, read-only description of a requested cleanup scope."""

    operation: str | None
    provider_scope: str | None
    clauses: tuple[EmailCleanupClause, ...]
    combination: str = "OR"
    remove_clause_types: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "provider_scope": self.provider_scope,
            "clauses": [clause.as_dict() for clause in self.clauses],
            "combination": self.combination,
            "remove_clause_types": list(self.remove_clause_types),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EmailCleanupIntent":
        clauses = tuple(
            EmailCleanupClause(
                type=str(item.get("type") or ""),
                values=tuple(str(entry) for entry in item.get("values") or ()),
                unread=item.get("unread") if isinstance(item.get("unread"), bool) else None,
                older_than_days=(
                    int(item["older_than_days"])
                    if isinstance(item.get("older_than_days"), int)
                    else None
                ),
            )
            for item in value.get("clauses") or ()
            if isinstance(item, Mapping) and str(item.get("type") or "")
        )
        return cls(
            operation=str(value.get("operation") or "") or None,
            provider_scope=str(value.get("provider_scope") or "") or None,
            clauses=clauses,
            combination=str(value.get("combination") or "OR").upper(),
            remove_clause_types=tuple(
                str(item) for item in value.get("remove_clause_types") or () if str(item)
            ),
        )


_CLEANUP_DOMAIN_TERMS = frozenset(
    {
        "clean",
        "cleanup",
        "tidy",
        "clear",
        "remove",
        "delete",
        "trash",
        "bin",
        "archive",
        "inbox",
        "inboxes",
        "email",
        "emails",
        "mail",
        "mailbox",
        "mailboxes",
        "messages",
        "promotional",
        "promotions",
        "social",
        "newsletter",
        "newsletters",
        "unread",
        "gmail",
        "outlook",
        "microsoft",
        "account",
        "accounts",
        "both",
    }
)


def classify_email_cleanup(text: str) -> EmailCleanupIntent | None:
    """Describe a cleanup request semantically without authorising a mutation.

    The classifier intentionally recognises concepts (provider scope, reversible
    operation, categories and age/read predicates) rather than complete canned
    utterances.  Provider/account grounding and confirmation remain outside this
    module.
    """

    command = " ".join(str(text or "")[:4_096].casefold().replace("’", "'").split())
    tokens = set(re.findall(r"[a-z0-9]+", command))
    continuation_language = any(
        phrase in command
        for phrase in ("same thing", "same for", "do that", "leave outlook", "leave gmail")
    )
    if not command or (
        not tokens.intersection(_CLEANUP_DOMAIN_TERMS) and not continuation_language
    ):
        return None

    has_mailbox_context = bool(
        tokens.intersection(
            {
                "inbox",
                "inboxes",
                "email",
                "emails",
                "mail",
                "mailbox",
                "mailboxes",
                "messages",
                "gmail",
                "outlook",
                "promotional",
                "promotions",
                "social",
                "newsletter",
                "newsletters",
                "unread",
            }
        )
    )
    has_cleanup_action = bool(
        tokens.intersection(
            {
                "clean",
                "cleanup",
                "tidy",
                "clear",
                "remove",
                "delete",
                "trash",
                "bin",
                "archive",
            }
        )
        or "get rid" in command
        or "same thing" in command
        or "same for" in command
        or "do that" in command
    )
    if not has_mailbox_context and not has_cleanup_action:
        return None

    operation: str | None = None
    if "archive" in tokens:
        operation = "archive"
    elif tokens.intersection({"remove", "delete", "trash", "bin", "clear"}) or "get rid" in command:
        operation = "trash"

    provider_scope: str | None = None
    mentions_gmail = "gmail" in tokens or "google mail" in command
    mentions_outlook = bool(tokens.intersection({"outlook", "microsoft"}))
    if (
        (mentions_gmail and mentions_outlook)
        or "both" in tokens
        or "all my inbox" in command
        or "all inbox" in command
        or "all my mailbox" in command
        or "all mailbox" in command
        or "same for both" in command
    ):
        provider_scope = "all"
    elif mentions_gmail:
        provider_scope = "google_gmail"
    elif mentions_outlook:
        provider_scope = "microsoft_outlook"
    if re.search(r"\b(?:leave|exclude|ignore)\b.{0,24}\boutlook\b", command):
        provider_scope = "google_gmail"
    elif re.search(r"\b(?:leave|exclude|ignore)\b.{0,24}\bgmail\b", command):
        provider_scope = "microsoft_outlook"

    clauses: list[EmailCleanupClause] = []
    categories = tuple(
        category
        for category, aliases in (
            ("promotional", {"promo", "promos", "promotion", "promotions", "promotional"}),
            ("social", {"social"}),
            ("newsletter", {"newsletter", "newsletters"}),
        )
        if tokens.intersection(aliases)
    )
    if categories:
        clauses.append(EmailCleanupClause(type="category", values=categories))

    age_match = re.search(
        r"\b(\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
        command,
    )
    has_age_semantics = any(
        marker in command for marker in ("older than", "over ", "more than", "sitting")
    )
    word_numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if "unread" in tokens and age_match and has_age_semantics:
        raw_days = age_match.group(1)
        days = int(raw_days) if raw_days.isdigit() else word_numbers[raw_days]
        clauses.append(
            EmailCleanupClause(
                type="unread_age",
                unread=True,
                older_than_days=max(1, min(days, 3_650)),
            )
        )
    elif "unread" in tokens and "old" in tokens:
        clauses.append(EmailCleanupClause(type="unread_age", unread=True))
    elif "unread" in tokens and bool(tokens.intersection({"all", "every", "anything"})):
        clauses.append(EmailCleanupClause(type="unread", unread=True))

    broad_all = bool(
        "everything" in tokens
        or "all emails" in command
        or "all messages" in command
        or "clear out" in command
        and not clauses
    )
    if broad_all:
        clauses.append(EmailCleanupClause(type="all_inbox"))

    if operation is None and tokens.intersection({"clean", "tidy"}) and clauses:
        operation = "trash"

    remove_clause_types: list[str] = []
    negated_unread = bool(
        re.search(r"\b(?:don't|dont|do not|leave|exclude|without)\b.{0,24}\bunread\b", command)
    )
    if negated_unread:
        clauses = [item for item in clauses if item.type not in {"unread", "unread_age"}]
        remove_clause_types.extend(("unread", "unread_age"))

    return EmailCleanupIntent(
        operation=operation,
        provider_scope=provider_scope,
        clauses=tuple(clauses),
        remove_clause_types=tuple(remove_clause_types),
    )


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
