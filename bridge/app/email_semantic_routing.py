"""Provider-neutral semantic routing for safe mailbox reads and cleanup previews.

This module recognises bounded email-read intents.  It never selects an
unregistered capability, invents an account/contact, or authorises a write;
those decisions remain at the capability and policy boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from email.utils import parseaddr
import re
from typing import Any, Mapping, Sequence
import unicodedata


@dataclass(frozen=True)
class EmailReadIntent:
    kind: str
    provider: str | None = None
    filter_kind: str | None = None
    person: str | None = None
    literal_query: str | None = None
    topic_query: str | None = None


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


def _search_words(value: object) -> tuple[str, ...]:
    normalised = unicodedata.normalize("NFKD", str(value or "")).casefold()
    ascii_value = "".join(
        character for character in normalised if not unicodedata.combining(character)
    )
    return tuple(re.findall(r"[a-z0-9]+", ascii_value))


def email_topic_match_score(query: str, message: Mapping[str, Any]) -> float:
    """Rank bounded provider metadata without treating message content as authority."""

    query_words = _search_words(query)
    if not query_words:
        return 0.0
    query_phrase = " ".join(query_words)
    query_compact = "".join(query_words)
    best = 0.0
    for field, weight in (("subject", 1.0), ("snippet", 0.82)):
        value_words = _search_words(message.get(field))
        if not value_words:
            continue
        value_phrase = " ".join(value_words)
        value_compact = "".join(value_words)
        score = 0.0
        if query_phrase in value_phrase or value_phrase in query_phrase:
            score = 1.0
        elif query_compact in value_compact or value_compact in query_compact:
            score = 0.96
        else:
            query_set = set(query_words)
            value_set = set(value_words)
            overlap = len(query_set & value_set) / max(1, len(query_set))
            if overlap:
                score = 0.72 + (0.18 * overlap)
            elif min(len(query_compact), len(value_compact)) >= 6:
                matcher = SequenceMatcher(None, query_compact, value_compact)
                ratio = matcher.ratio()
                common = matcher.find_longest_match().size
                coverage = common / max(1, min(len(query_compact), len(value_compact)))
                if ratio >= 0.64 and common >= 4 and coverage >= 0.5:
                    score = 0.66 + min(0.18, (ratio - 0.64) * 0.5)
        best = max(best, score * weight)
    return round(best, 6)


def rank_email_topic_messages(
    query: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    sender_address: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return only grounded topic matches from a bounded metadata window."""

    sender = str(sender_address or "").strip().casefold()
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for raw in messages:
        message = dict(raw)
        message_sender = parseaddr(str(message.get("from") or ""))[1].strip().casefold()
        if (
            sender
            and (message_sender or str(message.get("from") or "").strip().casefold()) != sender
        ):
            continue
        score = email_topic_match_score(query, message)
        if score < 0.64:
            continue
        received = str(message.get("received_at") or message.get("internal_date_ms") or "")
        message["topic_match_score"] = score
        ranked.append((score, received, message))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[: max(1, min(int(limit), 25))]]


def grounded_sender_candidates(
    messages: Sequence[Mapping[str, Any]], query: str
) -> list[dict[str, Any]]:
    """Resolve a partial sender only from provider-returned message metadata."""

    query_words = _search_words(query)
    if not query_words:
        return []
    needle = " ".join(query_words)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for message in messages:
        display_name = " ".join(str(message.get("sender_name") or "").split()).strip()
        raw_sender = str(message.get("from") or "").strip()
        parsed_name, parsed_address = parseaddr(raw_sender)
        address = (parsed_address or raw_sender).strip().casefold()
        display_name = display_name or " ".join(parsed_name.split()).strip()
        name_words = _search_words(display_name)
        local_words = _search_words(address.partition("@")[0])
        full_name = " ".join(name_words)
        matches = full_name.startswith(needle) or any(
            word.startswith(needle) for word in (*name_words, *local_words)
        )
        if not matches or not address:
            continue
        key = (full_name, address)
        candidate = candidates.setdefault(
            key,
            {
                "option_id": f"sender-{len(candidates) + 1}",
                "label": display_name or address,
                "value": address,
                "display_name": display_name or address,
                "address": address,
                "message_count": 0,
                "evidence": "provider_message_metadata",
            },
        )
        candidate["message_count"] = int(candidate["message_count"]) + 1
    return sorted(
        candidates.values(),
        key=lambda item: (-int(item["message_count"]), str(item["label"]).casefold()),
    )


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

    if focused_kind == "topic_search" and command.startswith("from "):
        person = raw[len("from ") :].strip(" .?!'\"")
        if person:
            return EmailReadIntent(
                kind="topic_sender_narrowing",
                provider=provider or focused_provider,
                person=person,
            )

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

    # A user asking Jarvis to check whether their mail contains a subject/topic
    # is a provider read, not a generic web question.  Extract only the user's
    # stated search term; provider query syntax is added by the connector.
    topic_query = ""
    if email_domain:
        topic_patterns = (
            r"\b(?:see|check|find out)\s+(?:if|whether)\s+(?:i|we)\s+"
            r"(?:have got|have|got|received)\s+(?P<query>.+)$",
            r"\b(?:search|look through|check|find)\s+(?:my\s+)?"
            r"(?:emails?|mail|mailbox|inbox)(?:\s+for)?\s+(?P<query>.+)$",
            r"\b(?:any|find|show me)\s+(?:emails?|messages?)\s+"
            r"(?:about|containing|with)\s+(?P<query>.+)$",
        )
        for pattern in topic_patterns:
            match = re.search(pattern, command, re.I)
            if match is not None:
                topic_query = match.group("query").strip(" .?!'\"")
                break
    topic_query = re.sub(
        r"^(?:an?|any|the|my)\s+",
        "",
        topic_query,
        flags=re.I,
    ).strip()
    if topic_query and len(topic_query) <= 500:
        return EmailReadIntent(
            kind="topic_search",
            provider=provider,
            topic_query=topic_query,
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
    person_reference = _person_reference(raw) if email_domain else None
    if person_reference and any(marker in command for marker in latest_markers):
        return EmailReadIntent(kind="sender_search", provider=provider, person=person_reference)
    if email_domain and any(marker in command for marker in latest_markers):
        return EmailReadIntent(kind="latest", provider=provider)

    if (
        person_reference
        and email_domain
        and (
            any(
                marker in command
                for marker in ("search", "find", "show", "any email", "any message")
            )
            or command.startswith("any ")
        )
    ):
        return EmailReadIntent(kind="sender_search", provider=provider, person=person_reference)

    if command in {"show me the latest one", "show the latest one"} and focused_kind:
        return EmailReadIntent(kind=focused_kind, provider=focused_provider)
    return None
