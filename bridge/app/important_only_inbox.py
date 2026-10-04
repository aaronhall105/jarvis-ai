"""Evidence-grounded classification for the Important-Only Inbox policy.

This module decides whether a message is safe to remove from the Inbox.  It
does not execute provider actions and it does not grant authority.  Message
content is untrusted data; content signals may make a decision more
conservative, but automatic disposal requires corroborating provider or
mail-transport evidence.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from enum import Enum
from typing import Any


class InboxDisposition(str, Enum):
    KEEP_IMPORTANT = "keep_important"
    KEEP_ACTIVE = "keep_active"
    TEMPORARY = "temporary"
    DISPOSABLE = "disposable"
    UNCERTAIN = "uncertain"


class LifecycleState(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class DecisionConfidence(str, Enum):
    HIGH_CONFIDENCE_DISPOSABLE = "high_confidence_disposable"
    PROBABLY_DISPOSABLE = "probably_disposable"
    UNCERTAIN = "uncertain"
    PROBABLY_IMPORTANT = "probably_important"
    VERIFIED_IMPORTANT = "verified_important"


@dataclass(frozen=True, slots=True)
class ImportantOnlyDecision:
    disposition: InboxDisposition
    lifecycle: LifecycleState
    confidence: DecisionConfidence
    reason_codes: tuple[str, ...]
    evidence: tuple[Mapping[str, Any], ...]
    campaign_signature: str | None = None

    @property
    def automatically_disposable(self) -> bool:
        return (
            self.disposition is InboxDisposition.DISPOSABLE
            and self.confidence is DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE
            and self.lifecycle
            in {
                LifecycleState.COMPLETED,
                LifecycleState.EXPIRED,
                LifecycleState.SUPERSEDED,
                LifecycleState.UNKNOWN,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "lifecycle": self.lifecycle.value,
            "confidence": self.confidence.value,
            "reason_codes": list(self.reason_codes),
            "evidence": [dict(item) for item in self.evidence],
            "campaign_signature": self.campaign_signature,
            "automatic_disposal_eligible": self.automatically_disposable,
            "content_is_authority": False,
            "classification_version": "important-only-v1",
        }


_AUTH_PATTERN = re.compile(
    r"(?i)\b(?:verify|verification|confirm (?:your )?email|one[- ]time (?:passcode|code)|"
    r"\botp\b|login code|sign[- ]in code|magic link|password reset|reset (?:your )?password|"
    r"device verification)\b"
)
_AUTH_SHORT_LIVED_PATTERN = re.compile(
    r"(?i)\b(?:one[- ]time (?:passcode|code)|\botp\b|login code|sign[- ]in code|magic link)\b"
)
_SECURITY_ALERT_PATTERN = re.compile(
    r"(?i)\b(?:suspicious|unusual|unrecognized|unauthori[sz]ed|account (?:locked|compromised)|"
    r"security alert|new sign[- ]in|new login)\b"
)
_FINANCIAL_PATTERN = re.compile(
    r"(?i)\b(?:payroll|pay ?slip|wage ?slip|bank statement|tax statement|invoice|receipt|"
    r"payment (?:due|failed|received)|bill due|credit note)\b"
)
_ACTIVE_DELIVERY_PATTERN = re.compile(
    r"(?i)\b(?:out for delivery|in transit|dispatched|shipped|delivery (?:due|delayed)|"
    r"arriving (?:today|tomorrow))\b"
)
_COMPLETED_DELIVERY_PATTERN = re.compile(
    r"(?i)\b(?:delivered|delivery complete|order collected|ready for collection)\b"
)
_ACTIVE_BOOKING_PATTERN = re.compile(
    r"(?i)\b(?:booking confirmed|reservation confirmed|appointment confirmed|ticket|"
    r"check[- ]in|boarding pass)\b"
)
_BULK_CONTENT_PATTERN = re.compile(
    r"(?i)\b(?:newsletter|weekly digest|daily digest|recommendations?|friend suggestions?|"
    r"abandoned (?:basket|cart)|we miss you|sale ends|special offer|discount|"
    r"your favourites are selling|suggested for you)\b"
)
_EXPIRY_PATTERN = re.compile(r"(?i)\bexpires?\s+(?:in|after)\s+(\d{1,4})\s*(minute|hour|day)s?\b")


def _text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _address(value: Any) -> str:
    parsed = getaddresses([str(value or "")[:1000]])
    if len(parsed) != 1:
        return ""
    address = parsed[0][1].strip().casefold()
    local, separator, domain = address.rpartition("@")
    if not separator or not local or not domain or any(char.isspace() for char in address):
        return ""
    return address


def _received_at(message: Mapping[str, Any]) -> datetime | None:
    milliseconds = message.get("internal_date_ms")
    if isinstance(milliseconds, int) and not isinstance(milliseconds, bool) and milliseconds >= 0:
        try:
            return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    raw = str(message.get("received_at") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _headers(message: Mapping[str, Any]) -> dict[str, str]:
    values = {
        "list-id": _text(message.get("list_id"), limit=1000),
        "list-unsubscribe": _text(message.get("list_unsubscribe"), limit=2000),
        "precedence": _text(message.get("precedence"), limit=100),
    }
    raw = message.get("internet_message_headers")
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip().casefold()
        if name in values and not values[name]:
            values[name] = _text(item.get("value"), limit=2000)
    return values


def _direct_recipient(message: Mapping[str, Any], owner_email: str) -> bool:
    owner = _address(owner_email)
    if not owner:
        return False
    raw_recipients = [
        str(message.get(key) or "")[:5000]
        for key in ("to", "cc", "delivered_to")
        if str(message.get(key) or "").strip()
    ]
    recipients = {_address(address) for _, address in getaddresses(raw_recipients) if address}
    return owner in recipients


def _protected_sender(sender: str, protected_senders: Sequence[str]) -> bool:
    for raw in protected_senders:
        protected = str(raw or "").strip().casefold()
        if sender == protected or (protected.startswith("@") and sender.endswith(protected)):
            return True
    return False


def _expiry_from_text(text: str, received: datetime | None) -> datetime | None:
    match = _EXPIRY_PATTERN.search(text)
    if match is None or received is None:
        return None
    quantity = int(match.group(1))
    unit = match.group(2).casefold()
    delta = (
        timedelta(minutes=quantity)
        if unit == "minute"
        else timedelta(hours=quantity)
        if unit == "hour"
        else timedelta(days=quantity)
    )
    return received + delta


def campaign_signature(message: Mapping[str, Any]) -> str | None:
    """Return a conservative stream identity for repeated bulk mail."""

    headers = _headers(message)
    list_id = headers["list-id"].casefold()
    sender = _address(message.get("from"))
    labels = sorted(
        item
        for item in {str(value).upper() for value in message.get("label_ids") or ()}
        if item in {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
    )
    if list_id:
        material = f"list-id\x00{list_id}"
    elif sender and (headers["list-unsubscribe"] or labels):
        subject_shape = re.sub(r"\d+", "#", _text(message.get("subject"), limit=200).casefold())
        material = "\x00".join(("sender-shape", sender, subject_shape, *labels))
    else:
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _grounded_lifecycle(message: Mapping[str, Any]) -> tuple[str, LifecycleState, str] | None:
    """Return lifecycle evidence supplied by a trusted adapter, never message prose."""

    raw = message.get("grounded_lifecycle")
    if not isinstance(raw, Mapping) or raw.get("verified") is not True:
        return None
    source = str(raw.get("source") or "").casefold()
    if source not in {"provider", "registered_capability", "explicit_user"}:
        return None
    kind = str(raw.get("kind") or "").casefold()
    aliases = {
        "verification": "ephemeral_auth",
        "otp": "ephemeral_auth",
        "password_reset": "ephemeral_auth",
        "magic_link": "ephemeral_auth",
        "delivery": "order",
        "booking": "booking",
        "event": "booking",
        "security": "security_alert",
    }
    kind = aliases.get(kind, kind)
    if kind not in {"ephemeral_auth", "order", "booking", "security_alert"}:
        return None
    state_text = str(raw.get("state") or "").casefold()
    state_aliases = {"resolved": "completed", "verified": "completed"}
    state_text = state_aliases.get(state_text, state_text)
    try:
        state = LifecycleState(state_text)
    except ValueError:
        return None
    return kind, state, source


def classify_important_only(
    message: Mapping[str, Any],
    *,
    now: datetime | None = None,
    owner_email: str = "",
    known_contact: bool | None = None,
    protected_senders: Sequence[str] = (),
    watched_reply: bool = False,
    base_classification: Mapping[str, Any] | None = None,
    explicitly_protected: bool = False,
) -> ImportantOnlyDecision:
    """Classify one message; uncertainty always fails closed to KEEP."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    received = _received_at(message)
    age = current - received if received is not None else None
    labels = {str(item).upper() for item in message.get("label_ids") or ()}
    sender = _address(message.get("from"))
    headers = _headers(message)
    subject = _text(message.get("subject"), limit=1000)
    preview = _text(message.get("snippet") or message.get("body"), limit=4000)
    content = f"{subject}\n{preview}"
    direct = _direct_recipient(message, owner_email)
    has_attachments = bool(message.get("attachments") or message.get("has_attachments"))
    signature = campaign_signature(message)
    evidence: list[Mapping[str, Any]] = []

    def decision(
        disposition: InboxDisposition,
        lifecycle: LifecycleState,
        confidence: DecisionConfidence,
        *reasons: str,
    ) -> ImportantOnlyDecision:
        return ImportantOnlyDecision(
            disposition=disposition,
            lifecycle=lifecycle,
            confidence=confidence,
            reason_codes=tuple(dict.fromkeys(reasons)),
            evidence=tuple(evidence),
            campaign_signature=signature,
        )

    if explicitly_protected:
        evidence.append({"source": "explicit_policy", "signal": "protected_message"})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.VERIFIED_IMPORTANT,
            "USER_PROTECTED",
        )
    protected_labels = labels & {"STARRED", "IMPORTANT", "CATEGORY_PERSONAL"}
    if protected_labels:
        evidence.append({"source": "provider_label", "values": sorted(protected_labels)})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.VERIFIED_IMPORTANT,
            "USER_PROTECTED",
        )
    if _protected_sender(sender, protected_senders):
        evidence.append({"source": "explicit_policy", "signal": "protected_sender"})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.VERIFIED_IMPORTANT,
            "USER_PROTECTED",
        )
    if watched_reply:
        evidence.append({"source": "durable_reply_watch", "signal": "active_thread"})
        return decision(
            InboxDisposition.KEEP_ACTIVE,
            LifecycleState.ACTIVE,
            DecisionConfidence.VERIFIED_IMPORTANT,
            "KEEP_WORK",
        )
    grounded_lifecycle = _grounded_lifecycle(message)
    if grounded_lifecycle is not None:
        lifecycle_kind, lifecycle_state, lifecycle_source = grounded_lifecycle
        evidence.append(
            {
                "source": lifecycle_source,
                "signal": "grounded_lifecycle",
                "kind": lifecycle_kind,
                "state": lifecycle_state.value,
            }
        )
        if lifecycle_state is LifecycleState.ACTIVE:
            reason = {
                "ephemeral_auth": "ACTIVE_AUTH",
                "order": "KEEP_ACTIVE_ORDER",
                "booking": "KEEP_ACTIVE_BOOKING",
                "security_alert": "KEEP_SECURITY",
            }[lifecycle_kind]
            return decision(
                (
                    InboxDisposition.KEEP_IMPORTANT
                    if lifecycle_kind == "security_alert"
                    else InboxDisposition.TEMPORARY
                    if lifecycle_kind == "ephemeral_auth"
                    else InboxDisposition.KEEP_ACTIVE
                ),
                LifecycleState.ACTIVE,
                DecisionConfidence.VERIFIED_IMPORTANT,
                reason,
            )
        if lifecycle_kind == "ephemeral_auth" and lifecycle_state in {
            LifecycleState.COMPLETED,
            LifecycleState.EXPIRED,
            LifecycleState.SUPERSEDED,
        }:
            return decision(
                InboxDisposition.DISPOSABLE,
                lifecycle_state,
                DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
                "EXPIRED_AUTH" if lifecycle_state is LifecycleState.EXPIRED else "COMPLETED_AUTH",
            )
        if lifecycle_state in {
            LifecycleState.COMPLETED,
            LifecycleState.EXPIRED,
            LifecycleState.SUPERSEDED,
        }:
            retention_elapsed = age is not None and age >= timedelta(days=30)
            if retention_elapsed and not has_attachments:
                reason = {
                    "order": "COMPLETED_DELIVERY",
                    "booking": "COMPLETED_BOOKING",
                    "security_alert": "RESOLVED_SECURITY",
                }[lifecycle_kind]
                return decision(
                    InboxDisposition.DISPOSABLE,
                    lifecycle_state,
                    DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
                    reason,
                )
            evidence.append(
                {
                    "source": "policy_lifecycle",
                    "signal": "retention_window",
                    "review_at": (
                        (received + timedelta(days=30)).isoformat()
                        if received is not None
                        else (current + timedelta(days=1)).isoformat()
                    ),
                }
            )
            return decision(
                InboxDisposition.TEMPORARY,
                lifecycle_state,
                DecisionConfidence.PROBABLY_IMPORTANT,
                "COMPLETED_RETAINED_TEMPORARILY",
            )
    if _SECURITY_ALERT_PATTERN.search(content):
        evidence.append({"source": "bounded_content", "signal": "security_alert"})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_SECURITY",
        )
    if _FINANCIAL_PATTERN.search(content):
        evidence.append({"source": "bounded_content", "signal": "financial_record"})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_FINANCIAL",
        )
    if _ACTIVE_DELIVERY_PATTERN.search(content):
        evidence.append({"source": "bounded_content", "signal": "active_delivery"})
        return decision(
            InboxDisposition.KEEP_ACTIVE,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_ACTIVE_ORDER",
        )
    if _ACTIVE_BOOKING_PATTERN.search(content):
        evidence.append({"source": "bounded_content", "signal": "active_booking"})
        return decision(
            InboxDisposition.KEEP_ACTIVE,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_ACTIVE_BOOKING",
        )

    auth = _AUTH_PATTERN.search(content)
    if auth:
        expiry = _expiry_from_text(content, received)
        if expiry is not None:
            evidence.append(
                {
                    "source": "message_expiry",
                    "signal": "explicit_expiry",
                    "expired": current >= expiry,
                    "expires_at": expiry.isoformat(),
                }
            )
            if current >= expiry:
                return decision(
                    InboxDisposition.DISPOSABLE,
                    LifecycleState.EXPIRED,
                    DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
                    "EXPIRED_AUTH",
                )
            return decision(
                InboxDisposition.TEMPORARY,
                LifecycleState.ACTIVE,
                DecisionConfidence.PROBABLY_IMPORTANT,
                "ACTIVE_AUTH",
            )
        stale_after = timedelta(days=7 if _AUTH_SHORT_LIVED_PATTERN.search(content) else 30)
        if age is not None and age >= stale_after:
            evidence.append(
                {
                    "source": "policy_lifecycle",
                    "signal": "ephemeral_auth_max_age",
                    "age_days": age.days,
                    "maximum_age_days": stale_after.days,
                }
            )
            return decision(
                InboxDisposition.DISPOSABLE,
                LifecycleState.EXPIRED,
                DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
                "EXPIRED_AUTH",
            )
        lifecycle_evidence: dict[str, Any] = {
            "source": "bounded_content",
            "signal": "ephemeral_auth",
        }
        if received is not None:
            lifecycle_evidence["review_at"] = (received + stale_after).isoformat()
        evidence.append(lifecycle_evidence)
        return decision(
            InboxDisposition.TEMPORARY,
            LifecycleState.UNKNOWN,
            DecisionConfidence.UNCERTAIN,
            "ACTIVE_AUTH",
        )

    if _COMPLETED_DELIVERY_PATTERN.search(content):
        lifecycle_evidence = {"source": "bounded_content", "signal": "completed_delivery"}
        if received is not None:
            lifecycle_evidence["review_at"] = (received + timedelta(days=30)).isoformat()
        evidence.append(lifecycle_evidence)
        if age is not None and age >= timedelta(days=30) and not has_attachments:
            return decision(
                InboxDisposition.DISPOSABLE,
                LifecycleState.COMPLETED,
                DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
                "COMPLETED_DELIVERY",
            )
        return decision(
            InboxDisposition.TEMPORARY,
            LifecycleState.COMPLETED,
            DecisionConfidence.UNCERTAIN,
            "COMPLETED_DELIVERY",
        )

    base_category = str((base_classification or {}).get("category") or "").casefold()
    protected_base_categories = {
        "account/security": "KEEP_SECURITY",
        "finance/bill/receipt": "KEEP_FINANCIAL",
        "appointment/calendar": "KEEP_ACTIVE_BOOKING",
        "delivery/order": "KEEP_ACTIVE_ORDER",
        "work/action": "KEEP_WORK",
        "legal/government": "KEEP_LEGAL",
        "medical": "KEEP_MEDICAL",
        "travel/booking": "KEEP_ACTIVE_BOOKING",
        "personal": "KEEP_PERSONAL",
        "reply received": "KEEP_WORK",
    }
    if base_category in protected_base_categories:
        evidence.append({"source": "email_assistant_classification", "category": base_category})
        return decision(
            (
                InboxDisposition.KEEP_IMPORTANT
                if base_category
                in {"account/security", "finance/bill/receipt", "legal/government", "medical"}
                else InboxDisposition.KEEP_ACTIVE
            ),
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            protected_base_categories[base_category],
        )

    if known_contact is True:
        evidence.append({"source": "contacts", "signal": "known_contact"})
        return decision(
            InboxDisposition.KEEP_IMPORTANT,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_PERSONAL",
        )

    provider_categories = labels & {
        "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL",
        "CATEGORY_FORUMS",
    }
    transport_bulk = bool(
        headers["list-id"]
        or headers["list-unsubscribe"]
        or headers["precedence"].casefold() in {"bulk", "list", "junk"}
    )
    strong_bulk = bool(
        headers["list-id"]
        and headers["list-unsubscribe"]
        or headers["list-unsubscribe"]
        and headers["precedence"].casefold() in {"bulk", "list", "junk"}
    )
    provider_other = str(message.get("inference_classification") or "").casefold() == "other"
    if provider_categories:
        evidence.append({"source": "provider_category", "values": sorted(provider_categories)})
        reason = (
            "SOCIAL_NOTIFICATION" if "CATEGORY_SOCIAL" in provider_categories else "PROMOTIONAL"
        )
        return decision(
            InboxDisposition.DISPOSABLE,
            LifecycleState.UNKNOWN,
            DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
            reason,
        )
    if (transport_bulk or provider_other) and _BULK_CONTENT_PATTERN.search(content):
        evidence.append(
            {
                "source": "mail_transport" if transport_bulk else "provider_classification",
                "signal": "bulk_headers" if transport_bulk else "other_inbox",
            }
        )
        evidence.append({"source": "bounded_content", "signal": "bulk_campaign"})
        return decision(
            InboxDisposition.DISPOSABLE,
            LifecycleState.UNKNOWN,
            DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
            "GENERIC_DIGEST",
        )
    if strong_bulk or (transport_bulk and not direct):
        evidence.append({"source": "mail_transport", "signal": "bulk_headers"})
        return decision(
            InboxDisposition.DISPOSABLE,
            LifecycleState.UNKNOWN,
            DecisionConfidence.HIGH_CONFIDENCE_DISPOSABLE,
            "NEWSLETTER",
        )
    if transport_bulk:
        evidence.append({"source": "mail_transport", "signal": "bulk_headers"})
        return decision(
            InboxDisposition.UNCERTAIN,
            LifecycleState.UNKNOWN,
            DecisionConfidence.PROBABLY_DISPOSABLE,
            "UNCERTAIN",
        )
    if direct:
        evidence.append({"source": "recipient_header", "signal": "addressed_directly"})
        return decision(
            InboxDisposition.KEEP_ACTIVE,
            LifecycleState.ACTIVE,
            DecisionConfidence.PROBABLY_IMPORTANT,
            "KEEP_PERSONAL",
        )
    if has_attachments:
        evidence.append({"source": "provider_metadata", "signal": "has_attachments"})
    return decision(
        InboxDisposition.UNCERTAIN,
        LifecycleState.UNKNOWN,
        DecisionConfidence.UNCERTAIN,
        "UNCERTAIN",
    )


__all__ = [
    "DecisionConfidence",
    "ImportantOnlyDecision",
    "InboxDisposition",
    "LifecycleState",
    "campaign_signature",
    "classify_important_only",
]
