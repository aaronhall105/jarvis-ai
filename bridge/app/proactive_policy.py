from __future__ import annotations

import re
from typing import Any


HELPER_DOMAINS = {
    "automation",
    "button",
    "counter",
    "input_boolean",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "number",
    "scene",
    "script",
    "select",
    "text",
    "timer",
}

BATTERY_EXCLUDED_TERMS = (
    "cycle",
    "cycle count",
    "power",
    "voltage",
    "current",
    "temperature",
    "health",
    "capacity",
    "energy",
    "charge type",
    "charger",
    "charging state",
    "battery state",
    "last charge",
)

OVEN_EXCLUDED_TERMS = (
    "alert reset",
    "preheat alert",
    "oven alert",
    "notification",
    "acknowledge",
    "helper",
    "test",
    "reset",
)

URGENT_SPEAKABLE_KINDS = {
    "smoke_detected",
    "carbon_monoxide_detected",
    "gas_detected",
    "water_leak",
}

USEFUL_SPEAKABLE_KINDS = {
    "cycle_finished",
    "oven_left_on",
}


def entity_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def attributes(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    value = state.get("attributes")
    return value if isinstance(value, dict) else {}


def friendly_name(state: dict[str, Any] | None) -> str:
    attrs = attributes(state)
    name = str(attrs.get("friendly_name") or "").strip()
    if name:
        return name
    entity_id = str((state or {}).get("entity_id") or "")
    return entity_id.split(".", 1)[-1].replace("_", " ").strip()


def combined_text(state: dict[str, Any] | None) -> str:
    entity_id = str((state or {}).get("entity_id") or "")
    return f"{entity_id} {friendly_name(state)}".casefold()


def numeric(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_real_battery_level(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict):
        return False

    entity_id = str(state.get("entity_id") or "")
    if entity_domain(entity_id) != "sensor":
        return False

    attrs = attributes(state)
    device_class = str(attrs.get("device_class") or "").casefold()
    unit = str(attrs.get("unit_of_measurement") or "").strip().casefold()
    text = combined_text(state)

    if device_class != "battery":
        return False
    if unit not in {"%", "percent", "percentage"}:
        return False
    if any(term in text for term in BATTERY_EXCLUDED_TERMS):
        return False

    value = numeric(state.get("state"))
    return value is not None and 0.0 <= value <= 100.0


def battery_transition(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    low_percent: float = 15.0,
    critical_percent: float = 5.0,
) -> tuple[str, int, float] | None:
    if not is_real_battery_level(current):
        return None

    level = numeric(current.get("state"))
    if level is None:
        return None

    old_level = (
        numeric(previous.get("state"))
        if is_real_battery_level(previous)
        else None
    )

    # Do not emit a startup burst when the proactive engine first
    # discovers existing low batteries. Alert only on a real crossing.
    if old_level is None:
        return None

    if (
        level <= critical_percent
        and old_level > critical_percent
    ):
        return "battery_critical", 96, level

    if (
        level <= low_percent
        and old_level > low_percent
    ):
        return "battery_low", 80, level

    return None


def is_real_oven_entity(
    state: dict[str, Any],
    *,
    explicit_entities: set[str] | None = None,
) -> bool:
    entity_id = str(state.get("entity_id") or "")
    domain = entity_domain(entity_id)
    text = combined_text(state)

    if not entity_id or domain in HELPER_DOMAINS:
        return False

    explicit = explicit_entities or set()
    if entity_id in explicit:
        return True

    if not any(term in text for term in ("oven", "hob", "cooker")):
        return False
    if any(term in text for term in OVEN_EXCLUDED_TERMS):
        return False

    return domain in {"switch", "climate", "binary_sensor"}


def safety_kind(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> str:
    entity_id = str(current.get("entity_id") or "")
    if entity_domain(entity_id) != "binary_sensor":
        return ""

    state = str(current.get("state") or "").strip().casefold()
    old = str((previous or {}).get("state") or "").strip().casefold()
    if state not in {"on", "true", "detected", "wet"}:
        return ""
    if old in {"on", "true", "detected", "wet"}:
        return ""

    device_class = str(
        attributes(current).get("device_class") or ""
    ).strip().casefold()

    return {
        "smoke": "smoke_detected",
        "carbon_monoxide": "carbon_monoxide_detected",
        "gas": "gas_detected",
        "moisture": "water_leak",
    }.get(device_class, "")


def proactive_speech_allowed(
    event: dict[str, Any],
    *,
    quiet: bool,
) -> bool:
    kind = str(event.get("kind") or "")
    importance = int(event.get("importance") or 0)

    if kind in URGENT_SPEAKABLE_KINDS:
        return True

    if quiet:
        return False

    if kind in USEFUL_SPEAKABLE_KINDS:
        return True
    if kind == "door_open" and importance >= 90:
        return True
    if kind == "person_detected" and importance >= 95:
        return True

    return False


def proactive_notification_tag(event: dict[str, Any]) -> str:
    category = str(event.get("category") or "system").casefold()
    kind = str(event.get("kind") or "event").casefold()
    target = str(event.get("target_user") or "all").casefold()
    entity_id = str(event.get("entity_id") or "")

    if category == "batteries":
        return f"jarvis_battery_{target}"
    if kind in URGENT_SPEAKABLE_KINDS:
        return f"jarvis_safety_{kind}"
    if kind == "oven_left_on":
        safe_entity = re.sub(r"[^a-z0-9]+", "_", entity_id.casefold())
        return f"jarvis_oven_{safe_entity[:80]}"

    fingerprint = str(event.get("fingerprint") or "")
    return "jarvis_proactive_" + fingerprint
