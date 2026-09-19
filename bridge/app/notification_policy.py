from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping


NotificationMode = Literal["important_only", "all_useful", "critical_only"]

NOTIFICATION_MODES: tuple[NotificationMode, ...] = (
    "important_only",
    "all_useful",
    "critical_only",
)

CRITICAL_SAFETY_KINDS = {
    "smoke_detected",
    "carbon_monoxide_detected",
    "gas_detected",
    "water_leak",
}

APPLIANCE_TERMS = (
    "washing machine",
    "washing_machine",
    "washer",
    "dryer",
    "dishwasher",
)


@dataclass(frozen=True, slots=True)
class NotificationPolicyDecision:
    notify: bool
    activity_only: bool
    level: str
    reason_code: str
    reason: str
    critical: bool
    quiet_hours: bool
    quiet_hours_bypassed: bool
    mode: NotificationMode
    recipient: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_mode(value: object) -> NotificationMode:
    candidate = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if candidate in NOTIFICATION_MODES:
        return candidate  # type: ignore[return-value]
    return "important_only"


def _event_text(event: Mapping[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "") for key in ("entity_id", "title", "message", "reason")
    ).casefold()


def _everyone_away(presence: Mapping[str, str]) -> bool:
    values = [str(value or "unknown").casefold() for value in presence.values()]
    return bool(values) and all(
        value not in {"", "home", "unknown", "unavailable"} for value in values
    )


def contextual_level(
    event: Mapping[str, Any],
    *,
    presence: Mapping[str, str],
) -> tuple[str, str, str]:
    """Classify interruption value from grounded context, not score alone."""

    kind = str(event.get("kind") or "").casefold()
    text = _event_text(event)
    away = _everyone_away(presence)

    if kind in CRITICAL_SAFETY_KINDS:
        return (
            "critical",
            "deterministic_safety_hazard",
            "A verified smoke, carbon-monoxide, gas or water-leak event is critical.",
        )

    if kind == "person_detected":
        if away:
            return (
                "important",
                "security_detection_while_away",
                "A person was detected while everyone appears to be away.",
            )
        return (
            "routine",
            "occupied_house_person_detection",
            "A person detection while somebody is home is routine activity.",
        )

    if kind in {"door_open", "opening_open_long", "appliance_door_open"}:
        if kind == "appliance_door_open" or any(term in text for term in APPLIANCE_TERMS):
            return (
                "routine",
                "appliance_opening",
                "An appliance door is not a perimeter security opening.",
            )
        if away:
            return (
                "important",
                "perimeter_open_while_away",
                "A perimeter opening remained open while everyone appears to be away.",
            )
        return (
            "routine",
            "occupied_house_opening",
            "An opening left open while somebody is home stays in activity history.",
        )

    if kind == "oven_left_on":
        if away:
            return (
                "important",
                "oven_on_while_away",
                "The oven appears to remain on while everyone is away.",
            )
        return (
            "useful",
            "oven_on_while_occupied",
            "The oven remains on while somebody is home.",
        )

    if kind == "battery_critical":
        return (
            "important",
            "personal_battery_critical",
            "A personally routed battery crossed the critical threshold.",
        )

    if kind in {"cycle_finished", "washing_finished", "battery_low"}:
        return (
            "useful",
            "routine_useful_event",
            "This is useful household activity but not normally an interruption.",
        )

    if kind in {"package", "package_detected", "delivery_detected"}:
        return (
            "useful",
            "verified_delivery_event",
            "A verified package or delivery event is useful household context.",
        )

    if kind in {"arrival", "departure", "person_arrived", "person_left"}:
        return (
            "routine",
            "routine_presence_change",
            "Routine arrival and departure changes remain in activity history.",
        )

    if kind in {"critical_unavailable", "camera_offline"}:
        return (
            "useful",
            "integration_or_device_unavailable",
            "A device or integration became unavailable.",
        )

    if kind in {"high_power", "energy_high"}:
        return (
            "useful",
            "high_energy_use",
            "High energy use is useful context but not automatically urgent.",
        )

    return (
        "routine",
        "unclassified_routine_event",
        "No grounded context makes this event important enough to interrupt.",
    )


def notification_recipients(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Resolve recipients without treating an event subject as authority."""

    target = str(event.get("target_user") or "all").casefold()
    kind = str(event.get("kind") or "").casefold()
    if target in {"aaron", "amber"}:
        return (target,)
    if kind in CRITICAL_SAFETY_KINDS:
        return ("aaron", "amber")
    # Household operational/security alerts are owner-routed.  A presence
    # event about Amber must never imply that Aaron should be interrupted.
    if kind in {"arrival", "departure", "person_arrived", "person_left"}:
        return ()
    return ("aaron",)


def decide_notification(
    event: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
    presence: Mapping[str, str],
    recipient: str,
    quiet_hours: bool,
    incident_cooldown: bool = False,
) -> NotificationPolicyDecision:
    mode = normalise_mode(settings.get("notification_mode"))
    level, context_code, context_reason = contextual_level(event, presence=presence)
    critical = level == "critical"

    if not bool(settings.get("enabled", True)):
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "proactive_disabled",
            "Proactive intelligence is disabled for this recipient.",
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )
    if not bool(settings.get("notify_enabled", True)):
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "phone_notifications_disabled",
            "Phone notifications are disabled for this recipient.",
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )
    categories = settings.get("categories")
    category = str(event.get("category") or "system")
    if isinstance(categories, Mapping) and not bool(categories.get(category, True)):
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "category_disabled",
            f"The {category} notification category is disabled.",
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )
    if incident_cooldown and not critical:
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "incident_cooldown",
            "The same incident was already notified and is still within its cooldown.",
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )

    allowed = critical
    if mode == "important_only":
        allowed = critical or level == "important"
    elif mode == "all_useful":
        allowed = level in {"critical", "important", "useful"}
    elif mode == "critical_only":
        allowed = critical

    if not allowed:
        return NotificationPolicyDecision(
            False,
            True,
            level,
            f"{mode}_{context_code}",
            context_reason,
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )

    if not critical and int(event.get("importance") or 0) < int(
        settings.get("min_importance") or 0
    ):
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "below_configured_importance",
            "The event is below this recipient's configured importance threshold.",
            critical,
            quiet_hours,
            False,
            mode,
            recipient,
        )

    if quiet_hours and not critical:
        return NotificationPolicyDecision(
            False,
            True,
            level,
            "quiet_hours",
            "Non-critical phone interruptions are suppressed during quiet hours.",
            critical,
            True,
            False,
            mode,
            recipient,
        )

    return NotificationPolicyDecision(
        True,
        False,
        level,
        context_code,
        context_reason,
        critical,
        quiet_hours,
        critical and quiet_hours,
        mode,
        recipient,
    )
