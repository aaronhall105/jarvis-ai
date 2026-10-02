"""Durable, grounded working context and deterministic result intelligence.

The dialogue store remains the persistence authority.  This module defines the
versioned, provider-neutral projection kept inside ``DialogueState`` and the
local reference resolver used before conversational history or a model.  It is
deliberately not an authority system: a resolved object identifies *what* the
user means, never whether a write may execute.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from app.dialogue_manager import DialogueManager


_SCHEMA_VERSION = 1
_DEFAULT_CONTEXT_TTL_SECONDS = 48 * 60 * 60
_MAX_OBJECTS = 60
_MAX_RESULT_SETS = 12
_MAX_DERIVED_RESULTS = 12
_MAX_METADATA_ITEMS = 30
_MAX_STRING = 600
_FORBIDDEN_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "credential",
    "token",
    "cookie",
    "chain_of_thought",
    "reasoning_content",
)
_FORBIDDEN_CONTENT_KEYS = {
    "body",
    "body_html",
    "body_text",
    "content",
    "full_body",
    "headers",
    "mime_content",
    "raw",
    "raw_payload",
}
_MODEL_INTERNAL_KEYS = {
    "account_id",
    "attachment_id",
    "conversation_id",
    "message_id",
    "provider_reference",
    "thread_id",
}
_ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
}
_NUMBER_WORDS = {
    "zero": 0,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_text(value: Any, *, limit: int = _MAX_STRING) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Return bounded JSON-safe evidence without credentials or raw payloads."""

    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _normalise_text(value)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:_MAX_METADATA_ITEMS]:
            key = _normalise_text(raw_key, limit=80)
            lowered = key.casefold()
            if (
                not key
                or lowered in _FORBIDDEN_CONTENT_KEYS
                or any(part in lowered for part in _FORBIDDEN_KEY_PARTS)
            ):
                continue
            safe = _safe_value(item, depth=depth + 1)
            if safe is not None:
                output[key] = safe
        return output
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [
            safe
            for item in list(value)[:_MAX_METADATA_ITEMS]
            if (safe := _safe_value(item, depth=depth + 1)) is not None
        ]
    return _normalise_text(value)


def _model_safe_metadata(value: Any, *, depth: int = 0) -> Any:
    safe = _safe_value(value, depth=depth)
    if isinstance(safe, Mapping):
        return {
            key: _model_safe_metadata(item, depth=depth + 1)
            for key, item in safe.items()
            if key.casefold() not in _MODEL_INTERNAL_KEYS
        }
    if isinstance(safe, list):
        return [_model_safe_metadata(item, depth=depth + 1) for item in safe]
    return safe


def principal_from_conversation(conversation_id: str) -> str | None:
    parts = str(conversation_id or "").split(":", 2)
    if len(parts) == 3 and parts[0] == "usr" and parts[1]:
        return parts[1]
    return None


class EvidenceStatus(str, Enum):
    VERIFIED = "verified"
    STRONG_MATCH = "strong_match"
    AMBIGUOUS = "ambiguous"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"
    PARTIAL = "partial"


class ReferenceStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    STALE = "stale"


def _evidence_status(value: Any) -> EvidenceStatus:
    try:
        return EvidenceStatus(str(value or "unverified"))
    except ValueError:
        return EvidenceStatus.UNVERIFIED


@dataclass(frozen=True, slots=True)
class ContextObject:
    reference_id: str
    object_type: str
    display_name: str
    source: str
    evidence_status: EvidenceStatus
    observed_at: str
    canonical_id: str | None = None
    provider: str | None = None
    capability: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    relations: Mapping[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    freshness_seconds: int | None = None
    immutable: bool = False
    task_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["evidence_status"] = self.evidence_status.value
        record["metadata"] = _safe_value(self.metadata)
        record["relations"] = _safe_value(self.relations)
        record["aliases"] = [_normalise_text(item, limit=120) for item in self.aliases[:20]]
        return record


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    status: ReferenceStatus
    objects: tuple[ContextObject, ...] = ()
    reason: str | None = None
    result_set_id: str | None = None
    requested_relation: str = "current"
    requires_fresh_read: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceQuery:
    relation: str = "current"
    ordinal: int | None = None
    object_types: tuple[str, ...] = ()
    provider: str | None = None
    plural: bool = False
    explicit_reference: str | None = None
    require_current_state: bool = False
    relation_key: str | None = None
    metric: str | None = None
    metric_operator: str | None = None
    metric_value: float | str | None = None


@dataclass(frozen=True, slots=True)
class ContextFollowUp:
    kind: str
    query: ReferenceQuery
    attribute: str | None = None


def reference_query(text: str, *, object_types: Sequence[str] = ()) -> ReferenceQuery:
    """Parse grammatical reference features, not domain-specific commands."""

    words = re.findall(r"[a-z0-9]+", str(text or "").casefold())
    word_set = set(words)
    ordinal = next((_ORDINALS[word] for word in words if word in _ORDINALS), None)
    relation = "current"
    if word_set & {"previous", "prior", "before"} or ("last" in word_set and "time" in word_set):
        relation = "previous"
    elif word_set & {"next", "after"}:
        relation = "next"
    elif ordinal is not None:
        relation = "ordinal"
    provider = None
    if "outlook" in word_set or "microsoft" in word_set:
        provider = "microsoft_outlook"
    elif "gmail" in word_set or "google" in word_set:
        provider = "google_gmail"
    plural = bool(word_set & {"them", "those", "these", "both", "all"})
    require_current = bool(word_set & {"still", "currently", "now"})
    relation_key = None
    for marker in ("her", "his", "their", "hers", "theirs", "its"):
        if marker in words:
            index = words.index(marker)
            if index + 1 < len(words):
                relation_key = words[index + 1]
            break
    metric = None
    metric_operator = None
    metric_value: float | str | None = None
    value_match = re.search(
        r"\b(?:has|have|with)\s+(-?\d+(?:\.\d+)?)\s+([a-z][a-z0-9_-]*)\b",
        " ".join(words),
    )
    if value_match:
        metric_value = float(value_match.group(1))
        if metric_value.is_integer():
            metric_value = int(metric_value)
        metric = value_match.group(2)
        metric_operator = "equals"
    else:
        word_value_match = re.search(
            r"\b(?:has|have|with)\s+(" + "|".join(_NUMBER_WORDS) + r")\s+([a-z][a-z0-9_-]*)\b",
            " ".join(words),
        )
        if word_value_match:
            metric_value = _NUMBER_WORDS[word_value_match.group(1)]
            metric = word_value_match.group(2)
            metric_operator = "equals"
    if metric_operator is None and (
        word_set & {"cheaper", "cheapest"} or ("least" in word_set and "expensive" in word_set)
    ):
        metric = "price"
        metric_operator = "minimum"
    elif metric_operator is None and (
        word_set & {"dearer", "pricier", "priciest"}
        or ("most" in word_set and "expensive" in word_set)
    ):
        metric = "price"
        metric_operator = "maximum"
    elif metric_operator is None:
        extremum = re.search(r"\b(lowest|highest)\s+([a-z][a-z0-9_-]*)\b", " ".join(words))
        if extremum:
            metric_operator = "minimum" if extremum.group(1) == "lowest" else "maximum"
            metric = extremum.group(2)
    return ReferenceQuery(
        relation=relation,
        ordinal=ordinal,
        object_types=tuple(str(item) for item in object_types if str(item)),
        provider=provider,
        plural=plural,
        require_current_state=require_current,
        relation_key=relation_key,
        metric=metric,
        metric_operator=metric_operator,
        metric_value=metric_value,
    )


def classify_context_followup(text: str) -> ContextFollowUp | None:
    """Recognise generic referential grammar without domain command phrases."""

    words = re.findall(r"[a-z0-9]+", str(text or "").casefold())
    word_set = set(words)
    if "why" in word_set and len(words) <= 6:
        return ContextFollowUp(
            kind="explain",
            query=ReferenceQuery(plural=True),
        )
    has_reference = bool(
        word_set
        & {
            "it",
            "its",
            "that",
            "this",
            "them",
            "those",
            "these",
            "one",
            "ones",
            "previous",
            "prior",
            "next",
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "her",
            "his",
            "their",
            "hers",
            "theirs",
            "she",
            "he",
            "they",
        }
    )
    query = reference_query(text)
    if (
        query.relation == "previous"
        and "than" in word_set
        and word_set & {"more", "less", "higher", "lower", "greater", "smaller"}
    ):
        return ContextFollowUp(kind="temporal_compare", query=query)
    if query.metric and query.metric_operator:
        return ContextFollowUp(kind="metric_select", query=query, attribute=query.metric)
    if not has_reference:
        if "how" in word_set and "much" in word_set and len(words) <= 10:
            return ContextFollowUp(kind="attribute", query=query, attribute="value")
        return None
    if (
        word_set & {"notify", "alert", "tell", "know", "let"}
        and word_set & {"next", "another"}
        and word_set & {"arrive", "arrives", "come", "comes", "received"}
    ):
        return ContextFollowUp(kind="monitor_next", query=query)
    if "compare" in word_set:
        return ContextFollowUp(kind="compare", query=query)
    attribute = None
    if "who" in word_set or "sender" in word_set or "sent" in word_set:
        attribute = "sender"
    elif "when" in word_set or "date" in word_set or "dated" in word_set:
        attribute = "date"
    elif "room" in word_set or "where" in word_set:
        attribute = "room"
    elif "status" in word_set or "state" in word_set:
        attribute = "status"
    elif "amount" in word_set or ("how" in word_set and "much" in word_set):
        attribute = "value"
    if attribute:
        return ContextFollowUp(kind="attribute", query=query, attribute=attribute)
    if query.relation_key:
        return ContextFollowUp(kind="select", query=query)
    if query.relation in {"previous", "next", "ordinal"}:
        return ContextFollowUp(kind="select", query=query)
    if word_set & {"open", "read"}:
        return ContextFollowUp(kind="open", query=query)
    return None


def empty_working_context(*, principal_id: str, conversation_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": _SCHEMA_VERSION,
        "principal_id": principal_id,
        "conversation_id": conversation_id,
        "active_task_id": None,
        "active_interaction_id": None,
        "current_goal": None,
        "current_intent": None,
        "focused_object_refs": [],
        "objects": [],
        "result_sets": [],
        "derived_results": [],
        "temporal_context": {},
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=_DEFAULT_CONTEXT_TTL_SECONDS)),
    }


def _context_object(record: Mapping[str, Any]) -> ContextObject | None:
    status = _evidence_status(record.get("evidence_status"))
    reference_id = _normalise_text(record.get("reference_id"), limit=160)
    object_type = _normalise_text(record.get("object_type"), limit=80)
    if not reference_id or not object_type:
        return None
    freshness = record.get("freshness_seconds")
    return ContextObject(
        reference_id=reference_id,
        object_type=object_type,
        display_name=_normalise_text(record.get("display_name"), limit=240) or object_type,
        source=_normalise_text(record.get("source"), limit=120) or "unknown",
        evidence_status=status,
        observed_at=_normalise_text(record.get("observed_at"), limit=80) or _iso(),
        canonical_id=_normalise_text(record.get("canonical_id"), limit=500) or None,
        provider=_normalise_text(record.get("provider"), limit=100) or None,
        capability=_normalise_text(record.get("capability"), limit=120) or None,
        metadata=_safe_value(record.get("metadata") or {}),
        relations=_safe_value(record.get("relations") or {}),
        aliases=tuple(
            _normalise_text(item, limit=120) for item in record.get("aliases") or () if item
        ),
        freshness_seconds=(max(0, int(freshness)) if freshness is not None else None),
        immutable=bool(record.get("immutable")),
        task_id=_normalise_text(record.get("task_id"), limit=160) or None,
    )


def _object_is_stale(item: ContextObject, *, at: datetime | None = None) -> bool:
    if item.immutable or item.freshness_seconds is None:
        return False
    observed = _parse_time(item.observed_at)
    if observed is None:
        return True
    return ((at or _now()) - observed).total_seconds() > item.freshness_seconds


def _default_freshness(object_type: str) -> tuple[int | None, bool]:
    if object_type in {"email_message", "document", "attachment"}:
        return None, True
    if object_type == "calendar_event":
        return 300, False
    if object_type in {"device", "room", "person", "measurement"}:
        return 30, False
    if object_type == "task":
        return 10, False
    return 300, False


def _metric_candidates(metric: str) -> tuple[str, ...]:
    """Return conservative metadata-key variants for one explicit metric."""

    value = re.sub(r"[^a-z0-9_]+", "_", str(metric or "").casefold()).strip("_")
    if not value:
        return ()
    candidates = [value]
    if value.endswith("s"):
        candidates.append(value[:-1])
    else:
        candidates.append(value + "s")
    if value in {"price", "prices", "cost", "costs"}:
        candidates.extend(("price", "cost"))
    return tuple(dict.fromkeys(item for item in candidates if item))


def _metric_value(item: ContextObject, metric: str) -> Any:
    normalised = {
        re.sub(r"[^a-z0-9_]+", "_", str(key).casefold()).strip("_"): value
        for key, value in item.metadata.items()
    }
    for candidate in _metric_candidates(metric):
        if candidate in normalised:
            return normalised[candidate]
    return None


def common_numeric_metric(objects: Sequence[ContextObject]) -> str | None:
    """Find one unambiguous shared numeric metric without semantic invention."""

    if not objects:
        return None
    ignored = {"internal_date_ms", "freshness_seconds"}
    shared: set[str] | None = None
    for item in objects:
        numeric = {
            str(key)
            for key, value in item.metadata.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and str(key) not in ignored
        }
        shared = numeric if shared is None else shared & numeric
    if not shared:
        return None
    primary = {str(item.metadata.get("primary_metric") or "") for item in objects}
    primary.discard("")
    if len(primary) == 1 and next(iter(primary)) in shared:
        return next(iter(primary))
    return next(iter(shared)) if len(shared) == 1 else None


def make_context_object(
    *,
    object_type: str,
    display_name: str,
    source: str,
    canonical_id: str | None = None,
    provider: str | None = None,
    capability: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    relations: Mapping[str, Any] | None = None,
    aliases: Sequence[str] = (),
    evidence_status: EvidenceStatus = EvidenceStatus.VERIFIED,
    observed_at: str | None = None,
    freshness_seconds: int | None = None,
    immutable: bool | None = None,
    task_id: str | None = None,
) -> ContextObject:
    default_freshness, default_immutable = _default_freshness(object_type)
    stable_identity = ":".join(
        value for value in (object_type, provider or "", canonical_id or "") if value
    )
    reference_id = (
        "ctx:" + str(uuid.uuid5(uuid.NAMESPACE_URL, stable_identity))
        if canonical_id
        else "ctx:" + str(uuid.uuid4())
    )
    return ContextObject(
        reference_id=reference_id,
        object_type=_normalise_text(object_type, limit=80),
        display_name=_normalise_text(display_name, limit=240),
        source=_normalise_text(source, limit=120),
        evidence_status=evidence_status,
        observed_at=observed_at or _iso(),
        canonical_id=_normalise_text(canonical_id, limit=500) or None,
        provider=_normalise_text(provider, limit=100) or None,
        capability=_normalise_text(capability, limit=120) or None,
        metadata=_safe_value(metadata or {}),
        relations=_safe_value(relations or {}),
        aliases=tuple(_normalise_text(item, limit=120) for item in aliases[:20]),
        freshness_seconds=(default_freshness if freshness_seconds is None else freshness_seconds),
        immutable=default_immutable if immutable is None else bool(immutable),
        task_id=_normalise_text(task_id, limit=160) or None,
    )


def merge_projection(
    existing: Mapping[str, Any] | None,
    *,
    principal_id: str,
    conversation_id: str,
    objects: Sequence[ContextObject],
    intent: str | None = None,
    goal: str | None = None,
    result_set: Mapping[str, Any] | None = None,
    focus_refs: Sequence[str] = (),
    derived_results: Sequence[Mapping[str, Any]] = (),
    active_task_id: str | None = None,
    active_interaction_id: str | None = None,
) -> dict[str, Any]:
    context = dict(
        existing
        or empty_working_context(principal_id=principal_id, conversation_id=conversation_id)
    )
    if (
        context.get("principal_id") != principal_id
        or context.get("conversation_id") != conversation_id
    ):
        context = empty_working_context(principal_id=principal_id, conversation_id=conversation_id)
    current = [item for item in context.get("objects") or () if isinstance(item, Mapping)]
    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for current_item in current:
        key = (
            str(current_item.get("object_type") or ""),
            str(current_item.get("provider") or ""),
            str(current_item.get("canonical_id") or current_item.get("reference_id") or ""),
        )
        by_identity[key] = dict(current_item)
    for projected_item in objects:
        record = projected_item.to_record()
        key = (
            projected_item.object_type,
            projected_item.provider or "",
            projected_item.canonical_id or projected_item.reference_id,
        )
        by_identity[key] = record
    context["objects"] = list(by_identity.values())[-_MAX_OBJECTS:]
    object_ref_ids = {str(item.get("reference_id")) for item in context["objects"]}
    selected = [str(ref) for ref in focus_refs if str(ref) in object_ref_ids]
    if selected:
        previous = [
            str(ref)
            for ref in context.get("focused_object_refs") or ()
            if str(ref) in object_ref_ids and str(ref) not in selected
        ]
        context["focused_object_refs"] = (selected + previous)[:8]
    if result_set:
        safe_set = dict(_safe_value(result_set) or {})
        safe_set.setdefault("result_set_id", "set:" + str(uuid.uuid4()))
        safe_set.setdefault("observed_at", _iso())
        safe_set["object_refs"] = [
            str(ref) for ref in safe_set.get("object_refs") or () if str(ref) in object_ref_ids
        ]
        sets = [
            dict(item)
            for item in context.get("result_sets") or ()
            if isinstance(item, Mapping)
            and item.get("result_set_id") != safe_set.get("result_set_id")
        ]
        context["result_sets"] = [safe_set, *sets][:_MAX_RESULT_SETS]
        context["temporal_context"] = {
            "active_result_set_id": safe_set["result_set_id"],
            "latest_ref": safe_set["object_refs"][0] if safe_set["object_refs"] else None,
            "current_index": 0 if safe_set["object_refs"] else None,
        }
    if derived_results:
        existing_derived = [
            dict(item) for item in context.get("derived_results") or () if isinstance(item, Mapping)
        ]
        safe_derived = [dict(_safe_value(item) or {}) for item in derived_results]
        context["derived_results"] = (safe_derived + existing_derived)[:_MAX_DERIVED_RESULTS]
    if intent is not None:
        context["current_intent"] = _normalise_text(intent, limit=120) or None
    if goal is not None:
        context["current_goal"] = _normalise_text(goal, limit=300) or None
    if active_task_id is not None:
        context["active_task_id"] = _normalise_text(active_task_id, limit=160) or None
    if active_interaction_id is not None:
        context["active_interaction_id"] = _normalise_text(active_interaction_id, limit=160) or None
    context["schema_version"] = _SCHEMA_VERSION
    context["updated_at"] = _iso()
    context["expires_at"] = _iso(_now() + timedelta(seconds=_DEFAULT_CONTEXT_TTL_SECONDS))
    return context


def email_read_projection(
    evidence: Mapping[str, Any],
) -> tuple[list[ContextObject], dict[str, Any]]:
    provider = _normalise_text(evidence.get("provider"), limit=100)
    account_id = _normalise_text(evidence.get("account_id"), limit=200)
    observed_at = _normalise_text(evidence.get("observed_at"), limit=80) or _iso()
    status = (
        EvidenceStatus.STRONG_MATCH
        if bool(evidence.get("semantic_match"))
        else EvidenceStatus.VERIFIED
    )
    objects: list[ContextObject] = []
    for raw in evidence.get("messages") or ():
        if not isinstance(raw, Mapping):
            continue
        message_id = _normalise_text(raw.get("message_id"), limit=500)
        if not message_id:
            continue
        sender = _normalise_text(raw.get("sender_name") or raw.get("from"), limit=240)
        subject = _normalise_text(raw.get("subject"), limit=300) or "No subject"
        attachments = [
            _safe_value(item) for item in raw.get("attachments") or () if isinstance(item, Mapping)
        ][:20]
        item = make_context_object(
            object_type="email_message",
            display_name=subject,
            source="provider_mailbox_read",
            canonical_id=message_id,
            provider=provider,
            capability="email.read",
            observed_at=observed_at,
            evidence_status=status,
            immutable=True,
            metadata={
                "account_id": account_id,
                "thread_id": raw.get("thread_id") or raw.get("conversation_id"),
                "sender": raw.get("from"),
                "sender_name": sender,
                "subject": subject,
                "snippet": raw.get("snippet"),
                "received_at": raw.get("received_at"),
                "internal_date_ms": raw.get("internal_date_ms"),
                "attachments": attachments,
            },
            aliases=(subject, sender),
        )
        objects.append(item)
    result_set = {
        "result_set_id": "email:" + str(uuid.uuid4()),
        "object_refs": [item.reference_id for item in objects],
        "ordering": "newest_first",
        "filters": {
            "query_kind": evidence.get("query_kind"),
            "filter_kind": evidence.get("filter_kind"),
            "contact": evidence.get("contact"),
            "literal_query": evidence.get("literal_query"),
            "topic_query": evidence.get("topic_query"),
        },
        "provider": provider,
        "observed_at": observed_at,
    }
    return objects, result_set


def tool_call_projection(
    *, intent: str, calls: Sequence[Mapping[str, Any]]
) -> tuple[list[ContextObject], dict[str, Any] | None]:
    """Adapt existing results, while preferring the generic projection contract."""

    objects: list[ContextObject] = []
    result_set_metadata: dict[str, Any] | None = None
    for call in calls:
        tool = _normalise_text(call.get("tool"), limit=120)
        result = call.get("result")
        if not isinstance(result, Mapping):
            continue
        projection = result.get("context_projection")
        if isinstance(projection, Mapping):
            rows = [raw for raw in projection.get("objects") or () if isinstance(raw, Mapping)]
            projected: list[ContextObject] = []
            local_refs: dict[str, str] = {}
            for raw in rows:
                item = make_context_object(
                    object_type=str(raw.get("object_type") or "generic_capability_result"),
                    display_name=str(raw.get("display_name") or "Result"),
                    source=str(raw.get("source") or tool or "capability_result"),
                    canonical_id=str(raw.get("canonical_id") or "") or None,
                    provider=str(raw.get("provider") or "") or None,
                    capability=str(raw.get("capability") or tool or "") or None,
                    metadata=(
                        raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
                    ),
                    aliases=tuple(raw.get("aliases") or ()),
                    evidence_status=_evidence_status(raw.get("evidence_status") or "verified"),
                    freshness_seconds=raw.get("freshness_seconds"),
                    immutable=raw.get("immutable"),
                    task_id=str(raw.get("task_id") or "") or None,
                )
                projected.append(item)
                for local_id in (raw.get("reference_id"), raw.get("canonical_id")):
                    if str(local_id or "").strip():
                        local_refs[str(local_id)] = item.reference_id
            linked: list[ContextObject] = []
            for raw, item in zip(rows, projected):
                raw_relation_value = raw.get("relations")
                raw_relations: Mapping[str, Any] = (
                    raw_relation_value if isinstance(raw_relation_value, Mapping) else {}
                )
                projected_relations: dict[str, Any] = {}
                for relation_name, relation_value in raw_relations.items():
                    values = (
                        list(relation_value)
                        if isinstance(relation_value, Sequence)
                        and not isinstance(relation_value, (bytes, bytearray, str))
                        else [relation_value]
                    )
                    resolved = [
                        local_refs[str(value)] for value in values if str(value) in local_refs
                    ]
                    if resolved:
                        projected_relations[str(relation_name)] = (
                            resolved[0] if len(resolved) == 1 else resolved
                        )
                linked.append(replace(item, relations=projected_relations))
            objects.extend(linked)
            if isinstance(projection.get("result_set"), Mapping):
                result_set_metadata = dict(projection["result_set"])
                result_set_metadata["object_refs"] = [
                    local_refs[str(value)]
                    for value in result_set_metadata.get("object_refs") or ()
                    if str(value) in local_refs
                ] or [item.reference_id for item in linked]
            continue

        entities: list[Mapping[str, Any]] = []
        if isinstance(result.get("entity"), Mapping):
            entities.append(result["entity"])
        entities.extend(item for item in result.get("entities") or () if isinstance(item, Mapping))
        if isinstance(result.get("selected_entity"), Mapping):
            entities.append(result["selected_entity"])
        seen_entities: set[str] = set()
        for entity in entities:
            entity_id = _normalise_text(entity.get("entity_id"), limit=240)
            if not entity_id or entity_id in seen_entities:
                continue
            seen_entities.add(entity_id)
            raw_attributes = entity.get("attributes")
            attributes: Mapping[str, Any] = (
                raw_attributes if isinstance(raw_attributes, Mapping) else {}
            )
            name = (
                _normalise_text(
                    entity.get("name")
                    or entity.get("friendly_name")
                    or attributes.get("friendly_name")
                )
                or entity_id
            )
            objects.append(
                make_context_object(
                    object_type=("person" if entity_id.startswith("person.") else "device"),
                    display_name=name,
                    source="home_assistant",
                    canonical_id=entity_id,
                    provider="home_assistant",
                    capability=tool,
                    metadata={
                        "state": entity.get("state"),
                        "area_id": entity.get("area_id") or result.get("area_id"),
                        "area_name": entity.get("area_name") or result.get("area_name"),
                        "domain": entity.get("domain") or entity_id.split(".", 1)[0],
                    },
                    aliases=(name, entity_id),
                    freshness_seconds=30,
                    immutable=False,
                )
            )
        person = result.get("person")
        if isinstance(person, Mapping) and person.get("entity_id"):
            person_relations: dict[str, Any] = {}
            if tool == "inspect_presence":
                related_refs: list[str] = []
                labelled_refs: dict[str, list[str]] = {}
                for tracker in result.get("trackers") or ():
                    if not isinstance(tracker, Mapping):
                        continue
                    tracker_id = _normalise_text(tracker.get("entity_id"), limit=240)
                    if not tracker_id:
                        continue
                    tracker_name = _normalise_text(
                        tracker.get("name") or tracker.get("device_name") or tracker_id,
                        limit=240,
                    )
                    tracker_object = make_context_object(
                        object_type="device",
                        display_name=tracker_name,
                        source="home_assistant_person_graph",
                        canonical_id=tracker_id,
                        provider="home_assistant",
                        capability=tool,
                        metadata={
                            "state": tracker.get("state"),
                            "domain": tracker.get("domain") or "device_tracker",
                            "available": tracker.get("available"),
                            "relationship": tracker.get("relationship") or "configured_for_person",
                        },
                        aliases=(tracker_name, tracker_id),
                        freshness_seconds=30,
                        immutable=False,
                    )
                    objects.append(tracker_object)
                    related_refs.append(tracker_object.reference_id)
                    name_words = set(re.findall(r"[a-z0-9]+", tracker_name.casefold()))
                    for label in ("phone", "watch", "tablet"):
                        if label in name_words:
                            labelled_refs.setdefault(label, []).append(tracker_object.reference_id)
                if related_refs:
                    person_relations["device"] = related_refs
                    person_relations["tracker"] = related_refs
                person_relations.update(labelled_refs)
            objects.append(
                make_context_object(
                    object_type="person",
                    display_name=str(person.get("name") or person.get("entity_id")),
                    source="home_assistant",
                    canonical_id=str(person.get("entity_id")),
                    provider="home_assistant",
                    capability=tool,
                    metadata={
                        "state": person.get("state") or person.get("location"),
                        "location": person.get("state") or person.get("location"),
                    },
                    relations=person_relations,
                    freshness_seconds=30,
                    immutable=False,
                )
            )

        for collection, object_type, id_keys, title_keys in (
            (result.get("events"), "calendar_event", ("event_id", "id"), ("summary", "title")),
            (result.get("tasks"), "task", ("task_id", "id"), ("title", "objective")),
            (
                result.get("items") or result.get("results"),
                "search_result",
                ("id", "url"),
                ("title", "name"),
            ),
        ):
            for raw in collection or ():
                if not isinstance(raw, Mapping):
                    continue
                canonical_id = next((str(raw.get(key)) for key in id_keys if raw.get(key)), None)
                title = next((str(raw.get(key)) for key in title_keys if raw.get(key)), object_type)
                objects.append(
                    make_context_object(
                        object_type=object_type,
                        display_name=title,
                        source=tool or "capability_result",
                        canonical_id=canonical_id,
                        capability=tool,
                        metadata=raw,
                        immutable=object_type == "search_result",
                    )
                )
    if objects and result_set_metadata is None:
        result_set_metadata = {
            "result_set_id": f"{intent}:" + str(uuid.uuid4()),
            "object_refs": [item.reference_id for item in objects],
            "ordering": "provider_order",
            "observed_at": _iso(),
        }
    elif result_set_metadata is not None:
        result_set_metadata = {
            **result_set_metadata,
            "object_refs": result_set_metadata.get("object_refs")
            or [item.reference_id for item in objects],
        }
    return objects, result_set_metadata


def model_safe_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return only bounded referent facts; omit provider-internal canonical IDs."""

    objects = []
    for record in context.get("objects") or ():
        if not isinstance(record, Mapping):
            continue
        objects.append(
            {
                "reference_id": record.get("reference_id"),
                "object_type": record.get("object_type"),
                "display_name": record.get("display_name"),
                "provider": record.get("provider"),
                "source": record.get("source"),
                "evidence_status": record.get("evidence_status"),
                "observed_at": record.get("observed_at"),
                "freshness_seconds": record.get("freshness_seconds"),
                "immutable": record.get("immutable"),
                "metadata": _model_safe_metadata(record.get("metadata") or {}),
            }
        )
    return {
        "current_goal": context.get("current_goal"),
        "current_intent": context.get("current_intent"),
        "active_task_id": context.get("active_task_id"),
        "active_interaction_id": context.get("active_interaction_id"),
        "focused_object_refs": context.get("focused_object_refs"),
        "objects": objects,
        "result_sets": context.get("result_sets"),
        "derived_results": context.get("derived_results"),
        "temporal_context": context.get("temporal_context"),
        "waiting_state": context.get("waiting_state"),
    }


class WorkingContextService:
    """Read and mutate the WorkingContext envelope in the existing dialogue store."""

    def __init__(self, dialogue: DialogueManager) -> None:
        self.dialogue = dialogue

    @staticmethod
    def _scope(principal_id: str, conversation_id: str) -> None:
        if principal_from_conversation(conversation_id) != principal_id:
            raise ValueError("Working context scope does not match the principal")

    async def get(self, *, principal_id: str, conversation_id: str) -> dict[str, Any]:
        self._scope(principal_id, conversation_id)
        state = await self.dialogue.get(conversation_id)
        context = dict(state.working_context or {})
        expires = _parse_time(context.get("expires_at"))
        if not context or (expires is not None and expires <= _now()):
            return empty_working_context(principal_id=principal_id, conversation_id=conversation_id)
        if (
            context.get("principal_id") != principal_id
            or context.get("conversation_id") != conversation_id
        ):
            return empty_working_context(principal_id=principal_id, conversation_id=conversation_id)
        return context

    async def project(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        objects: Sequence[ContextObject],
        intent: str | None = None,
        goal: str | None = None,
        result_set: Mapping[str, Any] | None = None,
        focus_refs: Sequence[str] = (),
        derived_results: Sequence[Mapping[str, Any]] = (),
        active_task_id: str | None = None,
        active_interaction_id: str | None = None,
    ) -> dict[str, Any]:
        self._scope(principal_id, conversation_id)
        state = await self.dialogue.get(conversation_id)
        state.working_context = merge_projection(
            state.working_context,
            principal_id=principal_id,
            conversation_id=conversation_id,
            objects=objects,
            intent=intent,
            goal=goal,
            result_set=result_set,
            focus_refs=focus_refs,
            derived_results=derived_results,
            active_task_id=active_task_id,
            active_interaction_id=active_interaction_id,
        )
        await self.dialogue.save(
            state,
            "working_context_updated",
            {
                "intent": intent,
                "object_types": sorted({item.object_type for item in objects}),
                "object_count": len(objects),
                "result_set": bool(result_set),
            },
        )
        return dict(state.working_context)

    async def resolve(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        query: ReferenceQuery,
    ) -> ReferenceResolution:
        context = await self.get(principal_id=principal_id, conversation_id=conversation_id)
        objects = [
            item
            for record in context.get("objects") or ()
            if isinstance(record, Mapping) and (item := _context_object(record)) is not None
        ]
        by_ref = {item.reference_id: item for item in objects}
        allowed = set(query.object_types)

        def compatible(item: ContextObject) -> bool:
            return (not allowed or item.object_type in allowed) and (
                not query.provider or item.provider == query.provider
            )

        result_sets = [
            item for item in context.get("result_sets") or () if isinstance(item, Mapping)
        ]
        active_id = str((context.get("temporal_context") or {}).get("active_result_set_id") or "")
        active_set = next(
            (item for item in result_sets if str(item.get("result_set_id")) == active_id),
            result_sets[0] if result_sets else None,
        )
        ordered = [
            by_ref[ref]
            for ref in (active_set or {}).get("object_refs") or ()
            if ref in by_ref and compatible(by_ref[ref])
        ]
        all_focused = [
            by_ref[ref] for ref in context.get("focused_object_refs") or () if ref in by_ref
        ]
        focused = [item for item in all_focused if compatible(item)]
        candidates: list[ContextObject]
        relation = query.relation
        if query.metric and query.metric_operator:
            metric_candidates = ordered or focused
            measured = [(item, _metric_value(item, query.metric)) for item in metric_candidates]
            measured = [(item, value) for item, value in measured if value is not None]
            if query.metric_operator == "equals":
                candidates = [item for item, value in measured if value == query.metric_value]
            else:
                numeric = [
                    (item, value)
                    for item, value in measured
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ]
                if numeric:
                    target_value = (
                        min(value for _, value in numeric)
                        if query.metric_operator == "minimum"
                        else max(value for _, value in numeric)
                    )
                    candidates = [item for item, value in numeric if value == target_value]
                else:
                    candidates = []
        elif query.relation_key:
            owners = [item for item in all_focused if query.relation_key in item.relations] or [
                item for item in objects if query.relation_key in item.relations
            ]
            related_refs: list[str] = []
            for owner in owners:
                relation_value = owner.relations.get(query.relation_key)
                if isinstance(relation_value, str):
                    related_refs.append(relation_value)
                elif isinstance(relation_value, Sequence) and not isinstance(
                    relation_value, (bytes, bytearray, str)
                ):
                    related_refs.extend(str(item) for item in relation_value)
            candidates = [
                by_ref[ref] for ref in related_refs if ref in by_ref and compatible(by_ref[ref])
            ]
        elif query.explicit_reference:
            wanted = query.explicit_reference.casefold()
            candidates = [
                item
                for item in objects
                if compatible(item)
                and (
                    wanted == item.reference_id.casefold()
                    or wanted == (item.canonical_id or "").casefold()
                    or wanted == item.display_name.casefold()
                    or wanted in {alias.casefold() for alias in item.aliases}
                )
            ]
        elif relation == "ordinal":
            index = query.ordinal if query.ordinal is not None else 0
            candidates = [ordered[index]] if 0 <= index < len(ordered) else []
        elif relation in {"previous", "next"}:
            temporal = context.get("temporal_context") or {}
            current_index = temporal.get("current_index")
            if not isinstance(current_index, int):
                selected = focused[0].reference_id if focused else None
                current_index = next(
                    (
                        position
                        for position, item in enumerate(ordered)
                        if item.reference_id == selected
                    ),
                    0,
                )
            # Result sets are ordered in the user-facing traversal order.  Both
            # “the previous one” in a newest-first mailbox list and “the one
            # after that” in an upcoming-events list advance one position.
            target = current_index + 1
            candidates = [ordered[target]] if 0 <= target < len(ordered) else []
        elif query.plural:
            candidates = ordered or focused
        else:
            candidates = focused[:1] if focused else ordered[:1]
        if not candidates:
            return ReferenceResolution(
                status=ReferenceStatus.MISSING,
                reason="no_compatible_grounded_object",
                result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
                requested_relation=relation,
            )
        if (
            not query.plural
            and len(candidates) > 1
            and (query.relation_key or query.metric_operator)
        ):
            return ReferenceResolution(
                status=ReferenceStatus.AMBIGUOUS,
                objects=tuple(candidates[:5]),
                reason="multiple_grounded_matches",
                result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
                requested_relation=relation,
            )
        if (
            not query.explicit_reference
            and not query.relation_key
            and not query.metric_operator
            and not query.plural
            and relation == "current"
            and len(focused) > 1
        ):
            return ReferenceResolution(
                status=ReferenceStatus.AMBIGUOUS,
                objects=tuple(focused[:5]),
                reason="multiple_focused_objects",
                result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
                requested_relation=relation,
            )
        if (
            not query.explicit_reference
            and not query.metric_operator
            and not query.plural
            and relation == "current"
            and not focused
            and len(ordered) > 1
        ):
            return ReferenceResolution(
                status=ReferenceStatus.AMBIGUOUS,
                objects=tuple(ordered[:5]),
                reason="multiple_compatible_objects",
                result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
                requested_relation=relation,
            )
        stale = any(_object_is_stale(item) for item in candidates)
        if stale and query.require_current_state:
            return ReferenceResolution(
                status=ReferenceStatus.STALE,
                objects=tuple(candidates),
                reason="fresh_provider_evidence_required",
                result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
                requested_relation=relation,
                requires_fresh_read=True,
            )
        return ReferenceResolution(
            status=ReferenceStatus.RESOLVED,
            objects=tuple(candidates),
            result_set_id=str((active_set or {}).get("result_set_id") or "") or None,
            requested_relation=relation,
            requires_fresh_read=stale,
        )

    async def focus_resolution(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        resolution: ReferenceResolution,
    ) -> None:
        if resolution.status is not ReferenceStatus.RESOLVED or not resolution.objects:
            return
        state = await self.dialogue.get(conversation_id)
        context = await self.get(principal_id=principal_id, conversation_id=conversation_id)
        prior = [str(item) for item in context.get("focused_object_refs") or ()]
        selected = [item.reference_id for item in resolution.objects]
        context["focused_object_refs"] = (selected + [ref for ref in prior if ref not in selected])[
            :8
        ]
        if resolution.result_set_id:
            result_set = next(
                (
                    item
                    for item in context.get("result_sets") or ()
                    if item.get("result_set_id") == resolution.result_set_id
                ),
                None,
            )
            if isinstance(result_set, Mapping) and selected[0] in result_set.get("object_refs", ()):
                context["temporal_context"] = {
                    "active_result_set_id": resolution.result_set_id,
                    "current_index": list(result_set["object_refs"]).index(selected[0]),
                    "latest_ref": list(result_set["object_refs"])[0],
                }
        context["updated_at"] = _iso()
        state.working_context = context
        await self.dialogue.save(
            state,
            "working_context_focus_changed",
            {"object_types": [item.object_type for item in resolution.objects]},
        )

    async def compare(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        objects: Sequence[ContextObject],
        metric: str | None = None,
    ) -> dict[str, Any]:
        if len(objects) < 2:
            raise ValueError("At least two grounded objects are required for comparison")
        metric_name = _normalise_text(metric, limit=80) or None
        values: list[dict[str, Any]] = []
        if metric_name:
            for item in objects:
                value = _metric_value(item, metric_name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(
                        {
                            "reference_id": item.reference_id,
                            "value": value,
                            "unit": item.metadata.get(f"{metric_name}_unit")
                            or item.metadata.get("currency")
                            or item.metadata.get("unit"),
                        }
                    )
        evidence_states = {item.evidence_status for item in objects}
        if evidence_states == {EvidenceStatus.VERIFIED}:
            comparison_evidence = EvidenceStatus.VERIFIED
        elif evidence_states <= {EvidenceStatus.VERIFIED, EvidenceStatus.STRONG_MATCH}:
            comparison_evidence = EvidenceStatus.STRONG_MATCH
        else:
            comparison_evidence = EvidenceStatus.PARTIAL
        derived = {
            "result_id": "comparison:" + str(uuid.uuid4()),
            "type": "comparison",
            "grounded_inputs": [item.reference_id for item in objects],
            "metric": metric_name,
            "values": values,
            "evidence_status": comparison_evidence.value,
            "observed_at": _iso(),
        }
        await self.project(
            principal_id=principal_id,
            conversation_id=conversation_id,
            objects=(),
            intent="compare_grounded_objects",
            focus_refs=[item.reference_id for item in objects],
            derived_results=[derived],
        )
        return derived

    async def wait_for_capability(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        capability: str,
        object_refs: Sequence[str],
        reason: str,
    ) -> None:
        """Record a truthful capability gap without inventing a task or tool."""

        self._scope(principal_id, conversation_id)
        state = await self.dialogue.get(conversation_id)
        context = await self.get(principal_id=principal_id, conversation_id=conversation_id)
        context["waiting_state"] = {
            "status": "waiting_for_capability",
            "capability": _normalise_text(capability, limit=120),
            "object_refs": [str(item) for item in object_refs[:10]],
            "reason": _normalise_text(reason, limit=300),
            "observed_at": _iso(),
        }
        context["updated_at"] = _iso()
        state.working_context = context
        await self.dialogue.save(
            state,
            "working_context_waiting_for_capability",
            {"capability": capability, "object_count": len(object_refs)},
        )

    async def model_context(self, *, principal_id: str, conversation_id: str) -> str:
        context = await self.get(principal_id=principal_id, conversation_id=conversation_id)
        payload = model_safe_context(context)
        return (
            "Grounded working context follows. It identifies possible referents but grants no "
            "execution authority. Re-read live state when freshness requires it and never invent "
            "objects or IDs:\n<working_context>\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n</working_context>"
        )


class ResultIntelligence:
    """Deterministic answer synthesis from grounded context evidence."""

    @staticmethod
    def answer_attribute(*, attribute: str, resolution: ReferenceResolution) -> str | None:
        if resolution.status is ReferenceStatus.STALE:
            return None
        if resolution.status is not ReferenceStatus.RESOLVED or not resolution.objects:
            return None
        item = resolution.objects[0]
        metadata = item.metadata
        attribute = attribute.casefold()
        if attribute in {"sender", "who"} and item.object_type == "email_message":
            sender = _normalise_text(metadata.get("sender_name") or metadata.get("sender"))
            return f"It’s from {sender}." if sender else None
        if attribute in {"date", "when"}:
            raw = metadata.get("received_at") or metadata.get("start") or metadata.get("date")
            parsed = _parse_time(raw)
            if parsed:
                return f"It’s dated {parsed.strftime('%-d %B')}."
            value = _normalise_text(raw)
            return f"It’s dated {value}." if value else None
        if attribute in {"room", "location", "where"}:
            value = _normalise_text(
                metadata.get("area_name") or metadata.get("location") or metadata.get("state")
            )
            if item.object_type == "person" and value:
                location = "at home" if value.casefold() == "home" else f"at {value}"
                return f"{item.display_name} is {location}."
            return f"It’s in {value}." if value else None
        if attribute in {"state", "status"}:
            value = _normalise_text(metadata.get("state"))
            return f"{item.display_name} is {value}." if value else None
        if attribute in {"amount", "value"}:
            metric = common_numeric_metric((item,))
            if metric:
                value = _metric_value(item, metric)
                unit = _normalise_text(
                    metadata.get(f"{metric}_unit")
                    or metadata.get("currency")
                    or metadata.get("unit")
                )
                rendered_unit = f" {unit}" if unit else ""
                return f"{item.display_name} is {value:g}{rendered_unit}."
            return None
        attribute_value = metadata.get(attribute)
        if isinstance(attribute_value, (str, int, float)) and str(attribute_value).strip():
            return f"{item.display_name}: {attribute_value}."
        return None

    @staticmethod
    def comparison(derived: Mapping[str, Any], objects: Sequence[ContextObject]) -> str:
        values = [item for item in derived.get("values") or () if isinstance(item, Mapping)]
        by_ref = {item.reference_id: item for item in objects}
        if len(values) >= 2:
            first, second = values[0], values[1]
            first_value = first.get("value")
            second_value = second.get("value")
            if not isinstance(first_value, (int, float)) or not isinstance(
                second_value, (int, float)
            ):
                return "I have the grounded results, but not a verified numeric comparison."
            difference = first_value - second_value
            first_name = by_ref.get(str(first.get("reference_id")))
            second_name = by_ref.get(str(second.get("reference_id")))
            unit = _normalise_text(first.get("unit"))
            return (
                f"{first_name.display_name if first_name else 'The first'} is "
                f"{abs(difference):g}{(' ' + unit) if unit else ''} "
                f"{'more' if difference > 0 else 'less'} than "
                f"{second_name.display_name if second_name else 'the second'}."
            )
        names = [item.display_name for item in objects]
        return "I can compare " + " and ".join(names) + ", but I need a grounded metric."

    @staticmethod
    def explain(derived: Mapping[str, Any], objects: Sequence[ContextObject]) -> str:
        by_ref = {item.reference_id: item for item in objects}
        values = [item for item in derived.get("values") or () if isinstance(item, Mapping)]
        if len(values) >= 2:
            rendered = []
            for value in values[:4]:
                item = by_ref.get(str(value.get("reference_id") or ""))
                if item is None:
                    continue
                unit = _normalise_text(value.get("unit"))
                rendered.append(
                    f"{item.display_name} was {value.get('value')}" + (f" {unit}" if unit else "")
                )
            if len(rendered) >= 2:
                return "Because " + " while ".join(rendered) + "."
        names = [item.display_name for item in objects]
        if names:
            return (
                "That comparison used the grounded results for "
                + " and ".join(names)
                + "; there isn’t a verified numeric reason in the result."
            )
        return "I don’t have grounded evidence for that explanation."

    @staticmethod
    def describe(item: ContextObject) -> str:
        metadata = item.metadata
        if item.object_type == "email_message":
            sender = _normalise_text(metadata.get("sender_name") or metadata.get("sender"))
            raw_date = metadata.get("received_at")
            parsed = _parse_time(raw_date)
            date = parsed.strftime("%-d %B") if parsed else _normalise_text(raw_date)
            details = [
                part
                for part in (f"from {sender}" if sender else "", f"dated {date}" if date else "")
                if part
            ]
            suffix = ", " + " and ".join(details) if details else ""
            return f"{item.display_name}{suffix}."
        if item.object_type == "calendar_event":
            raw = metadata.get("start") or metadata.get("date")
            return f"{item.display_name}{(' — ' + _normalise_text(raw)) if raw else ''}."
        state = _normalise_text(metadata.get("state"))
        return f"{item.display_name}{(' is ' + state) if state else ''}."


__all__ = [
    "ContextObject",
    "ContextFollowUp",
    "EvidenceStatus",
    "ReferenceQuery",
    "ReferenceResolution",
    "ReferenceStatus",
    "ResultIntelligence",
    "WorkingContextService",
    "email_read_projection",
    "empty_working_context",
    "make_context_object",
    "merge_projection",
    "model_safe_context",
    "classify_context_followup",
    "common_numeric_metric",
    "principal_from_conversation",
    "reference_query",
    "tool_call_projection",
]
