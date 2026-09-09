"""Deterministic presentation policy for user-facing Jarvis responses.

Provider, tool and database results are evidence.  This module is the boundary
that turns already-verified facts into concise conversational wording without
asking a language model to reinterpret them.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any


_TECHNICAL_REQUEST = re.compile(
    r"\b(?:raw(?: output| data| json)?|json|diagnostic(?:s)?|debug|logs?|"
    r"stack trace|traceback|mime|headers?|provider reference|action receipt|"
    r"thread id|message id|principal id|capability id|implementation|source code)\b",
    re.I,
)

_INTERNAL_TERM = re.compile(
    r"\b(?:principal_id|conversation_id|capability_id|action_id|provider_reference|"
    r"execution_status|thread_id|message_id|receipt_action_id|reply_count|"
    r"battery_level|entity_id|raw JSON|MIME)\b",
    re.I,
)


class _SafeTextExtractor(HTMLParser):
    """Extract readable text while ignoring active and quoted HTML content."""

    _BLOCKS = {"address", "article", "br", "div", "h1", "h2", "h3", "li", "p", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        classes = " ".join(value or "" for key, value in attrs if key.casefold() == "class")
        if self._ignored_depth or lowered in {"script", "style", "head", "svg", "form"}:
            self._ignored_depth += 1
            return
        if lowered == "blockquote" or "gmail_quote" in classes.casefold():
            self._ignored_depth = 1
            return
        if lowered in self._BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._ignored_depth:
            self._ignored_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag.casefold() in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def technical_output_requested(request_text: str | None) -> bool:
    """Return whether the current user explicitly requested implementation detail."""

    return bool(_TECHNICAL_REQUEST.search(str(request_text or "")))


def _html_to_text(value: str) -> str:
    extractor = _SafeTextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
    except Exception:
        return re.sub(r"<[^>]{0,1000}>", " ", html.unescape(value))
    return "".join(extractor.parts)


def clean_email_reply_body(value: str) -> str:
    """Return only the newest human-readable portion of an inbound reply body."""

    text = html.unescape(str(value or ""))
    if re.search(r"<\s*(?:html|body|div|p|br|blockquote|span|table)\b", text, re.I):
        text = _html_to_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    lines = [line.strip() for line in text.splitlines()]

    # Some clients flatten their mobile signature and link onto the final body
    # line. Treat the well-known signature as a presentation cutoff even when
    # MIME decoding did not preserve the preceding newline.
    inline_signature = re.search(
        r"(?<!\w)(?:sent from outlook for android|sent from my (?:iphone|ipad|android))"
        r"(?:\s*<?https?://[^>\s]+>?)*",
        "\n".join(lines),
        re.I,
    )
    if inline_signature is not None:
        lines = "\n".join(lines)[: inline_signature.start()].splitlines()

    cutoff = len(lines)
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if not line:
            continue
        if re.match(r"^on .{3,220} wrote:$", line, re.I):
            cutoff = index
            break
        if re.match(r"^-{2,}\s*(?:original|forwarded) message\s*-{2,}$", line, re.I):
            cutoff = index
            break
        if re.match(r"^_{8,}$", line):
            cutoff = index
            break
        if lowered in {
            "sent from outlook for android",
            "sent from my iphone",
            "sent from my ipad",
            "sent from my android",
        }:
            cutoff = index
            break
        if lowered.startswith("from:"):
            following = [item.casefold() for item in lines[index + 1 : index + 7] if item]
            header_names = {item.split(":", 1)[0] for item in following if ":" in item}
            if len(header_names & {"sent", "date", "to", "subject", "cc"}) >= 2:
                cutoff = index
                break

    visible = lines[:cutoff]
    while visible and not visible[-1]:
        visible.pop()
    paragraphs: list[str] = []
    current: list[str] = []
    for line in visible:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(re.sub(r"\s+", " ", part).strip() for part in paragraphs if part.strip())


def _person_name(sender: str, recipient: str, preferred_name: str | None) -> str:
    preferred = str(preferred_name or "").strip()
    if preferred:
        return preferred.split()[0]
    display, address = parseaddr(str(sender or ""))
    if display.strip():
        return display.strip().split()[0]
    local = (address or recipient).split("@", 1)[0]
    first = re.split(r"[._+\-]+", local)[0].strip()
    return first.title() if first else "They"


def render_gmail_reply_status(result: Mapping[str, Any]) -> str:
    """Render one dedicated reply-status result without exposing provider internals."""

    if result.get("clarification_required") is True:
        return str(result.get("clarification") or "Which email do you mean?").strip()
    if result.get("success") is not True:
        reason = str(result.get("error") or "").casefold()
        if any(word in reason for word in ("oauth", "token", "reconnect", "authentication")):
            return "I can’t check Gmail properly right now because Google needs reconnecting."
        if "unavailable" in reason or "unhealthy" in reason:
            return "I can’t check Gmail properly right now because Google is unavailable."
        return "I couldn’t check that email properly just now."

    recipient = str(result.get("recipient") or "").strip()
    preferred_name = str(result.get("recipient_name") or "").strip() or None
    if result.get("reply_received") is not True:
        name = _person_name("", recipient, preferred_name)
        if name and name != "They":
            return f"No, I haven’t found a reply from {name} yet."
        return "No, I haven’t found a reply yet."

    replies = [item for item in result.get("replies") or () if isinstance(item, Mapping)]
    latest = replies[-1] if replies else {}
    sender = str(latest.get("from") or "").strip()
    name = _person_name(sender, recipient, preferred_name)
    body = clean_email_reply_body(str(latest.get("body") or latest.get("snippet") or ""))
    if body:
        compact = re.sub(r"\s+", " ", body).strip()
        if len(compact) > 280:
            compact = compact[:277].rstrip() + "…"
        quote = compact.replace("'", "’")
        return f"Yeah, {name} replied. {name}’s reply just said ‘{quote}’."
    attachments = latest.get("attachments")
    if (
        isinstance(attachments, Sequence)
        and not isinstance(attachments, (str, bytes))
        and attachments
    ):
        return f"Yeah, {name} replied with an attachment, but there wasn’t any message text."
    return f"Yeah, {name} replied, but there wasn’t any message text."


def _readable_state(value: Any) -> str:
    state = str(value or "unknown").strip().casefold().replace("_", " ")
    return "away" if state == "not home" else state


def _entity_name(entity: Mapping[str, Any]) -> str:
    attributes = entity.get("attributes")
    attribute_name = attributes.get("friendly_name") if isinstance(attributes, Mapping) else None
    name = str(entity.get("name") or entity.get("friendly_name") or attribute_name or "").strip()
    return name or "That device"


def render_home_state_evidence(
    calls: Sequence[Mapping[str, Any]],
    *,
    request_text: str,
) -> str | None:
    """Render Home Assistant read evidence without allowing model overclaims."""

    if technical_output_requested(request_text):
        return None
    normalised_request = " ".join(
        "".join(
            character.casefold() if character.isalnum() else " "
            for character in str(request_text or "")
        ).split()
    )
    request_words = set(normalised_request.split())
    entities: list[Mapping[str, Any]] = []
    seen_entities: set[str] = set()
    unresolved_queries: list[str] = []
    ambiguous_entity_ids: set[str] = set()

    def normalise_reference(value: Any) -> str:
        return " ".join(
            "".join(
                character.casefold() if character.isalnum() else " "
                for character in str(value or "")
            ).split()
        )

    def explicitly_referenced(entity: Any) -> bool:
        if not isinstance(entity, Mapping):
            return False
        references = [
            entity.get("name"),
            entity.get("friendly_name"),
            entity.get("entity_id"),
        ]
        attributes = entity.get("attributes")
        if isinstance(attributes, Mapping):
            references.append(attributes.get("friendly_name"))
        return any(
            reference
            and len(normalised := normalise_reference(reference)) >= 3
            and normalised in normalised_request
            for reference in references
        )

    def generic_area_request(domain: Any) -> bool:
        domain_terms = {
            "light": {"light", "lights"},
            "switch": {"switch", "switches"},
            "sensor": {"sensor", "sensors"},
            "binary_sensor": {"sensor", "sensors"},
            "camera": {"camera", "cameras"},
            "media_player": {"speaker", "speakers", "player", "players"},
        }
        value = str(domain or "").casefold()
        if not value:
            return bool(request_words & {"devices", "entities", "states"})
        return bool(request_words & domain_terms.get(value, {value, f"{value}s"}))

    def add_entity(entity: Any) -> None:
        if not isinstance(entity, Mapping):
            return
        identity = str(entity.get("entity_id") or entity.get("name") or id(entity)).casefold()
        if identity in seen_entities:
            return
        seen_entities.add(identity)
        entities.append(entity)

    def add_unresolved(query: Any) -> None:
        value = " ".join(str(query or "").split()).strip()
        if not value:
            return
        lowered = value.casefold()
        if any(lowered == current.casefold() for current in unresolved_queries):
            return
        # Model retries often shorten the same unresolved phrase. Keep the most
        # descriptive form so a clarification remains useful to the user.
        if any(lowered in current.casefold() for current in unresolved_queries):
            return
        unresolved_queries[:] = [
            current for current in unresolved_queries if current.casefold() not in lowered
        ]
        unresolved_queries.append(value)

    for call in calls:
        if call.get("tool") not in {
            "search_entity_states",
            "list_area_states",
            "get_entity_state",
        }:
            continue
        result = call.get("result")
        if not isinstance(result, Mapping) or result.get("success") is not True:
            continue
        if call.get("tool") == "get_entity_state":
            entity = result.get("entity")
            entity_id = (
                str(entity.get("entity_id") or "").casefold() if isinstance(entity, Mapping) else ""
            )
            if entity_id in ambiguous_entity_ids and not explicitly_referenced(entity):
                continue
            add_entity(entity)
            continue
        if call.get("tool") == "search_entity_states":
            resolution = str(result.get("resolution") or "").casefold()
            arguments = call.get("arguments")
            domain = arguments.get("domain") if isinstance(arguments, Mapping) else None
            raw_entities = [
                entity for entity in result.get("entities") or () if isinstance(entity, Mapping)
            ]
            referenced_entities = [
                entity for entity in raw_entities if explicitly_referenced(entity)
            ]
            if referenced_entities:
                for entity in referenced_entities:
                    add_entity(entity)
                continue
            if resolution == "ambiguous" and domain and generic_area_request(domain):
                for entity in raw_entities:
                    add_entity(entity)
                continue
            if resolution in {"ambiguous", "zero"}:
                ambiguous_entity_ids.update(
                    str(entity.get("entity_id") or "").casefold()
                    for entity in raw_entities
                    if entity.get("entity_id")
                )
                add_unresolved(result.get("query") or call.get("arguments", {}).get("query"))
                continue
            selected = result.get("selected_entity")
            if isinstance(selected, Mapping):
                add_entity(selected)
                continue
            if resolution == "ranked" and len(raw_entities) != 1:
                add_unresolved(result.get("query") or call.get("arguments", {}).get("query"))
                continue
        else:
            raw_entities = [
                entity for entity in result.get("entities") or () if isinstance(entity, Mapping)
            ]
            referenced_entities = [
                entity for entity in raw_entities if explicitly_referenced(entity)
            ]
            if referenced_entities:
                raw_entities = referenced_entities
            else:
                arguments = call.get("arguments")
                domain = arguments.get("domain") if isinstance(arguments, Mapping) else None
                if not generic_area_request(domain):
                    add_unresolved("one of the requested devices")
                    continue
        for entity in raw_entities:
            add_entity(entity)

    if not entities:
        if unresolved_queries:
            return f"I couldn’t confidently match {unresolved_queries[0]} to one device."
        return None

    known: list[tuple[str, str]] = []
    unavailable: list[str] = []
    for entity in entities[:5]:
        name = _entity_name(entity)
        state = _readable_state(entity.get("display_value") or entity.get("state"))
        if state in {"", "unknown", "unavailable"} or entity.get("available") is False:
            unavailable.append(name)
        else:
            known.append((name, state))

    asks_on = normalised_request.startswith(("is ", "are ")) and normalised_request.endswith(" on")
    unresolved_note = (
        f"I couldn’t confidently match {unresolved_queries[0]} to one device"
        if unresolved_queries
        else ""
    )
    if asks_on and known:
        switched_on = [name for name, state in known if state == "on"]
        switched_off = [name for name, state in known if state == "off"]
        if switched_on and switched_off:
            on_names = ", ".join(switched_on)
            off_names = ", ".join(switched_off)
            answer = (
                f"{on_names} {'is' if len(switched_on) == 1 else 'are'} on, while "
                f"{off_names} {'is' if len(switched_off) == 1 else 'are'} off"
            )
            if unavailable:
                unknown_names = ", ".join(unavailable)
                answer += (
                    f", but I can’t confirm {unknown_names} because "
                    f"{'it’s' if len(unavailable) == 1 else 'they’re'} unavailable"
                )
            if unresolved_note:
                answer += f", and {unresolved_note}"
            return answer + "."
        if switched_on:
            names = ", ".join(switched_on)
            answer = f"Yes — {names} {'is' if len(switched_on) == 1 else 'are'} on"
            if unavailable:
                unknown_names = ", ".join(unavailable)
                answer += (
                    f", but I can’t confirm {unknown_names} because "
                    f"{'it’s' if len(unavailable) == 1 else 'they’re'} unavailable"
                )
            if unresolved_note:
                answer += f", but {unresolved_note}"
            return answer + "."
        if switched_off and unavailable:
            off_names = ", ".join(switched_off)
            unknown_names = ", ".join(unavailable)
            answer = (
                f"{off_names} {'is' if len(switched_off) == 1 else 'are'} off, "
                f"but I can’t confirm {unknown_names} because "
                f"{'it’s' if len(unavailable) == 1 else 'they’re'} unavailable"
            )
            if unresolved_note:
                answer += f", and {unresolved_note}"
            return answer + "."
        if switched_off and len(switched_off) == len(known):
            names = ", ".join(switched_off)
            if unresolved_note:
                return (
                    f"{names} {'is' if len(switched_off) == 1 else 'are'} off, "
                    f"but {unresolved_note}."
                )
            return f"No, {names} {'is' if len(switched_off) == 1 else 'are'} off."

    parts = [f"{name} is {state}" for name, state in known]
    parts.extend(f"I can’t confirm {name} because it’s unavailable" for name in unavailable)
    if unresolved_queries:
        parts.append(f"I couldn’t confidently match {unresolved_queries[0]} to one device")
    return ". ".join(parts) + "."


def render_presence_evidence(
    result: Mapping[str, Any],
    *,
    person_name: str,
    first_person: bool = False,
    explain: bool = False,
) -> str:
    """Render verified presence naturally while retaining meaningful uncertainty."""

    person = result.get("person")
    state = _readable_state(person.get("state") if isinstance(person, Mapping) else None)
    if state in {"", "unknown", "unavailable"}:
        return (
            "I can’t confirm your location right now."
            if first_person
            else (f"I can’t confirm {person_name}’s location right now.")
        )
    location = "at home" if state == "home" else "away" if state == "away" else f"at {state}"
    natural = f"You’re {location}." if first_person else f"{person_name}’s {location}."
    conflicts = [item for item in result.get("conflicts") or () if isinstance(item, Mapping)]
    if conflicts:
        details = ", ".join(
            f"{_entity_name(item)} says {_readable_state(item.get('state'))}" for item in conflicts
        )
        subject = "you are" if first_person else f"{person_name} is"
        return (
            f"Home Assistant says {subject} {location}, but {details}, "
            "so I can’t confirm that properly."
        )
    if not explain:
        return natural
    source = result.get("source")
    if isinstance(source, Mapping) and source:
        return natural[:-1] + f", based on {_entity_name(source)}."
    return natural[:-1] + ", but Home Assistant doesn’t show which tracker reported it."


def present_user_response(
    value: str,
    *,
    request_text: str | None = None,
    allow_technical: bool | None = None,
) -> str:
    """Apply the final deterministic user-facing response policy."""

    raw = str(value or "")
    technical = (
        technical_output_requested(request_text) if allow_technical is None else allow_technical
    )
    if technical:
        return raw.strip()
    lines = [" ".join(line.split()) for line in html.unescape(raw).strip().splitlines()]
    normalised_lines: list[str] = []
    for line in lines:
        if not line and normalised_lines and not normalised_lines[-1]:
            continue
        normalised_lines.append(line)
    text = "\n".join(normalised_lines)

    lowered = text.casefold()
    if "no principal-owned verified gmail send receipt matched" in lowered:
        return "Which email do you mean?"
    if any(word in lowered for word in ("oauth", "token refresh", "needs reconnecting")):
        return "I can’t check Gmail properly right now because Google needs reconnecting."
    if lowered.startswith("gmail is unavailable"):
        if "no provider" in lowered or "not connected" in lowered:
            return "I can’t check Gmail because Google isn’t connected."
        return "I can’t check Gmail properly right now because Google is unavailable."
    unavailable_separator = " is unavailable — "
    if unavailable_separator in lowered:
        label = text[: lowered.index(unavailable_separator)].strip()
        reason = lowered.split(unavailable_separator, 1)[1]
        if label and len(label) <= 80:
            if any(word in reason for word in ("oauth", "token", "reconnect")):
                return f"I can’t check {label} right now because it needs reconnecting."
            if any(word in reason for word in ("configured", "adapter", "setup", "set up")):
                return f"I can’t check {label} yet because it isn’t set up."
            return f"I can’t check {label} properly right now."
    if lowered.startswith("jarvis core error:"):
        return "Something went wrong on my side, so I couldn’t finish that."
    if _INTERNAL_TERM.search(text):
        return "I couldn’t confirm that properly."
    return text


def present_error(exc: BaseException, *, service: str | None = None) -> str:
    """Render a safe conversational error without leaking exception internals."""

    label = str(service or "that").strip()
    if label.casefold() == "gmail":
        return "I can’t check Gmail properly right now."
    return f"Something went wrong on my side, so I couldn’t finish {label}."


__all__ = [
    "clean_email_reply_body",
    "present_error",
    "present_user_response",
    "render_gmail_reply_status",
    "render_home_state_evidence",
    "render_presence_evidence",
    "technical_output_requested",
]
