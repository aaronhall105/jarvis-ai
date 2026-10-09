"""Presentation projection of Jarvis's authoritative grounded home state.

``HomeExperience`` is deliberately not another home-state store. It projects a
fresh :class:`HomeSnapshot`, Home Assistant registry topology, and persisted
proactive evidence into a stable client contract. Android and Home Assistant
must render this contract without independently deriving household semantics.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import re
import time
from typing import Any

from app.home_canonicalization import (
    CanonicalHomeItem,
    aggregate_area_ids,
    canonicalize_cameras,
    canonicalize_domain,
    clean_text,
    occupancy_evidence_class,
    physical_identity,
)
from app.home_intelligence import (
    DeviceAvailability,
    GroundedDeviceStatus,
    GroundedHomeEntity,
    HomeSnapshot,
    roll_up_physical_devices,
)
from app.runtime_observability import runtime_metrics


logger = logging.getLogger(__name__)


class SourceFreshness(str, Enum):
    LIVE = "LIVE"
    RECENT_CACHED = "RECENT_CACHED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class OccupancyState(str, Enum):
    OCCUPIED = "OCCUPIED"
    LIKELY_OCCUPIED = "LIKELY_OCCUPIED"
    CLEAR = "CLEAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RoomStatus:
    area_id: str
    name: str
    occupancy_state: OccupancyState
    occupancy_summary: str
    occupancy_detail: str
    occupancy_evidence: tuple[dict[str, Any], ...]
    lights_on_count: int
    lights_total: int
    lights: tuple[dict[str, Any], ...]
    cameras: tuple[dict[str, Any], ...]
    devices: tuple[dict[str, Any], ...]
    appliances: tuple[dict[str, Any], ...]
    climate: tuple[dict[str, Any], ...]
    media: tuple[dict[str, Any], ...]
    important_incidents: tuple[dict[str, Any], ...]
    recent_events: tuple[dict[str, Any], ...]
    quick_actions: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["occupancy_state"] = self.occupancy_state.value
        return value


@dataclass(frozen=True, slots=True)
class HomeExperience:
    schema_version: int
    generated_at: str
    revision: str
    source_freshness: dict[str, Any]
    overall_status: dict[str, Any]
    people: tuple[dict[str, Any], ...]
    rooms: tuple[RoomStatus, ...]
    lights: dict[str, Any]
    devices: dict[str, Any]
    appliances: tuple[dict[str, Any], ...]
    cameras: tuple[dict[str, Any], ...]
    energy: tuple[dict[str, Any], ...]
    active_media: tuple[dict[str, Any], ...]
    current_incidents: tuple[dict[str, Any], ...]
    recent_events: tuple[dict[str, Any], ...]
    quick_actions: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "revision": self.revision,
            "source_freshness": self.source_freshness,
            "overall_status": self.overall_status,
            "people": list(self.people),
            "rooms": [item.as_dict() for item in self.rooms],
            "lights": self.lights,
            "devices": self.devices,
            "appliances": list(self.appliances),
            "cameras": list(self.cameras),
            "energy": list(self.energy),
            "active_media": list(self.active_media),
            "current_incidents": list(self.current_incidents),
            "recent_events": list(self.recent_events),
            "quick_actions": list(self.quick_actions),
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True, slots=True)
class FrozenHomeAction:
    action_id: str
    principal_id: str
    revision: str
    kind: str
    target_entity_ids: tuple[str, ...]
    created_monotonic: float


class HomeExperienceService:
    """Build, briefly cache, and freeze action sets for client projections."""

    def __init__(
        self,
        *,
        snapshot_loader: Callable[[], Awaitable[HomeSnapshot]],
        event_loader: Callable[[str], Sequence[Mapping[str, Any]]],
        incident_loader: Callable[[], Sequence[Mapping[str, Any]]],
        action_ttl_seconds: float = 600.0,
    ) -> None:
        self._snapshot_loader = snapshot_loader
        self._event_loader = event_loader
        self._incident_loader = incident_loader
        self._action_ttl_seconds = max(30.0, action_ttl_seconds)
        self._cached: dict[str, tuple[HomeExperience, float]] = {}
        self._actions: dict[tuple[str, str], FrozenHomeAction] = {}
        self._live_principals: set[str] = set()

    def project(self, snapshot: HomeSnapshot, principal_id: str) -> HomeExperience:
        started = time.monotonic()
        principal = principal_id.strip().casefold() or "user"
        evidence_available = True
        try:
            events = self._event_loader(principal)
        except Exception:
            logger.exception("HomeExperience proactive event projection unavailable")
            events = ()
            evidence_available = False
        try:
            incidents = self._incident_loader()
        except Exception:
            logger.exception("HomeExperience proactive incident projection unavailable")
            incidents = ()
            evidence_available = False
        experience = project_home_experience(
            snapshot,
            principal_id=principal,
            proactive_events=events,
            proactive_incidents=incidents,
        )
        if not evidence_available:
            experience = replace(
                experience,
                diagnostics={
                    **experience.diagnostics,
                    "proactive_evidence_status": "UNAVAILABLE",
                },
            )
        self._cache_live(principal, experience)
        runtime_metrics.observe(
            "home_experience_construction_ms",
            (time.monotonic() - started) * 1000,
        )
        return experience

    async def get(self, principal_id: str) -> HomeExperience:
        principal = principal_id.strip().casefold() or "user"
        try:
            snapshot = await self._snapshot_loader()
        except Exception:
            self._live_principals.discard(principal)
            self._drop_principal_actions(principal)
            cached = self._cached.get(principal)
            if cached is None:
                now = datetime.now(timezone.utc).isoformat()
                return project_home_experience(
                    HomeSnapshot(observed_at=now, entities=()),
                    principal_id=principal,
                    freshness=SourceFreshness.UNAVAILABLE,
                    generated_at=now,
                )
            previous, cached_at = cached
            age = max(0.0, time.monotonic() - cached_at)
            state = SourceFreshness.RECENT_CACHED if age <= 60.0 else SourceFreshness.STALE
            disabled_actions = tuple(
                {**item, "enabled": False, "disabled_reason": "Home state is not live"}
                for item in previous.quick_actions
            )
            disabled_rooms = tuple(
                replace(
                    room,
                    quick_actions=tuple(
                        {
                            **item,
                            "enabled": False,
                            "disabled_reason": "Home state is not live",
                        }
                        for item in room.quick_actions
                    ),
                )
                for room in previous.rooms
            )
            return replace(
                previous,
                generated_at=datetime.now(timezone.utc).isoformat(),
                source_freshness={
                    **previous.source_freshness,
                    "status": state.value,
                    "age_seconds": round(age, 3),
                    "actions_allowed": False,
                    "message": "Home state unavailable — showing last known state",
                },
                rooms=disabled_rooms,
                quick_actions=disabled_actions,
            )
        return self.project(snapshot, principal)

    def resolve_action(self, principal_id: str, action_id: str) -> FrozenHomeAction:
        principal = principal_id.strip().casefold() or "user"
        action = self._actions.get((principal, action_id))
        if principal not in self._live_principals or action is None:
            raise ValueError("This home action is no longer available; refresh Home first.")
        if time.monotonic() - action.created_monotonic > self._action_ttl_seconds:
            self._actions.pop((principal, action_id), None)
            raise ValueError("This home action has expired; refresh Home first.")
        return action

    def _cache_live(self, principal: str, experience: HomeExperience) -> None:
        now = time.monotonic()
        self._cached[principal] = (experience, now)
        self._live_principals.add(principal)
        self._drop_principal_actions(principal)
        actions = [*experience.quick_actions]
        for room in experience.rooms:
            actions.extend(room.quick_actions)
        for item in actions:
            action_id = str(item.get("action_id") or "")
            target_ids = tuple(str(value) for value in item.get("target_entity_ids") or ())
            if not action_id or not target_ids or item.get("enabled") is not True:
                continue
            self._actions[(principal, action_id)] = FrozenHomeAction(
                action_id=action_id,
                principal_id=principal,
                revision=experience.revision,
                kind=str(item.get("kind") or ""),
                target_entity_ids=target_ids,
                created_monotonic=now,
            )

    def _drop_principal_actions(self, principal: str) -> None:
        for key in tuple(self._actions):
            if key[0] == principal:
                self._actions.pop(key, None)


_ACTIVE_MEDIA = frozenset({"on", "playing", "paused", "buffering"})
_ACTIVE_APPLIANCE_STATES = frozenset(
    {"on", "running", "washing", "drying", "cleaning", "heating", "returning"}
)
_APPLIANCE_DOMAINS = frozenset({"fan", "humidifier", "vacuum", "water_heater"})
_APPLIANCE_TERMS = re.compile(r"\b(washing machine|washer|dryer|dishwasher|oven)\b", re.I)
_ENERGY_DEVICE_CLASSES = frozenset({"energy", "power", "monetary"})
_ENERGY_UNITS = frozenset({"w", "kw", "wh", "kwh", "£/kwh", "p/kwh", "gbp/kwh"})


def _entity_ref(entity: GroundedHomeEntity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "state": entity.state,
        "available": entity.available,
        "area_id": entity.area_id,
        "area_name": entity.area_name,
        "device_id": entity.device_id,
        "device_name": entity.device_name,
        "device_class": entity.device_class,
        "display_value": entity.display_value,
        "unit": entity.unit,
        "observed_at": entity.source_last_updated or entity.observed_at,
        "source_last_updated": entity.source_last_updated,
    }


def _canonical_ref(item: CanonicalHomeItem) -> dict[str, Any]:
    entity = item.primary_entity
    return {
        "canonical_id": item.canonical_id,
        "entity_id": entity.entity_id,
        "name": item.display_name,
        "category": item.category,
        "state": entity.state,
        "available": entity.available,
        "availability": item.availability,
        "area_id": item.area_id,
        "area_name": item.area_name,
        "device_id": entity.device_id,
        "physical_device_ids": list(item.physical_device_ids),
        "display_value": clean_text(entity.display_value),
        "unit": clean_text(entity.unit),
        "observed_at": entity.source_last_updated or entity.observed_at,
        "diagnostics": item.diagnostics(),
    }


def _canonical_device_ref(
    item: CanonicalHomeItem,
    physical_devices: Sequence[GroundedDeviceStatus],
) -> dict[str, Any]:
    statuses = tuple(
        status
        for status in physical_devices
        if status.device_id in item.physical_device_ids
        or any(
            member.entity_id == item.primary_entity.entity_id for member in status.member_entities
        )
    )
    value = _canonical_ref(item)
    if item.availability == "UNAVAILABLE":
        availability = "UNAVAILABLE"
    elif any(status.availability is DeviceAvailability.PARTIAL for status in statuses):
        availability = "PARTIAL"
    else:
        availability = "AVAILABLE"
    value.update(
        availability=availability,
        unavailable_entity_count=sum(status.unavailable_entity_count for status in statuses),
        member_entity_count=sum(len(status.member_entities) for status in statuses),
        diagnostic_entity_ids=sorted(
            {member.entity_id for status in statuses for member in status.member_entities}
        ),
    )
    return value


def _camera_ref(
    item: CanonicalHomeItem, area_entities: Sequence[GroundedHomeEntity]
) -> dict[str, Any]:
    related = [
        entity
        for entity in area_entities
        if entity.domain == "binary_sensor"
        and (
            entity.device_id in item.physical_device_ids
            or (
                not item.physical_device_ids
                and not entity.device_id
                and entity.area_id == item.area_id
            )
        )
        and occupancy_evidence_class(entity) in {"person_presence", "motion"}
    ]
    person = next(
        (entity for entity in related if occupancy_evidence_class(entity) == "person_presence"),
        None,
    )
    motion = next(
        (entity for entity in related if occupancy_evidence_class(entity) == "motion"),
        None,
    )
    value = _canonical_ref(item)
    value.update(
        recent_activity=(
            "Person detected"
            if person is not None
            and person.available
            and person.state in {"on", "detected", "true", "person"}
            else "Activity detected"
            if motion is not None
            and motion.available
            and motion.state in {"on", "detected", "true"}
            else ""
        ),
        person_status=(
            "DETECTED"
            if person is not None
            and person.available
            and person.state in {"on", "detected", "true", "person"}
            else "NOT_DETECTED"
            if person is not None and person.available
            else "UNKNOWN"
        ),
        motion_status=(
            "DETECTED"
            if motion is not None
            and motion.available
            and motion.state in {"on", "detected", "true"}
            else "NOT_DETECTED"
            if motion is not None and motion.available
            else "UNKNOWN"
        ),
        detail_route=f"camera:{item.primary_entity.entity_id}",
    )
    return value


def _device_ref(device: GroundedDeviceStatus) -> dict[str, Any]:
    source_observations = tuple(
        item.source_last_updated for item in device.member_entities if item.source_last_updated
    )
    return {
        "device_key": device.device_key,
        "device_id": device.device_id,
        "name": device.name,
        "area_id": device.area_id,
        "area_name": device.area_name,
        "availability": device.availability.value,
        "unavailable_entity_count": device.unavailable_entity_count,
        "member_entity_count": len(device.member_entities),
        "observed_at": max(source_observations, default=device.observed_at),
        "evidence_kind": device.evidence_kind,
        "diagnostic_entity_ids": [item.entity_id for item in device.member_entities],
    }


def _event_time(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _natural_join(values: Sequence[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _event_projection(event: Mapping[str, Any]) -> dict[str, Any]:
    decision = event.get("decision")
    decision = decision if isinstance(decision, Mapping) else {}
    evidence = event.get("evidence")
    evidence = (
        list(evidence) if isinstance(evidence, Sequence) and not isinstance(evidence, str) else []
    )
    return {
        "event_id": str(event.get("id") or ""),
        "title": str(event.get("title") or "Home activity"),
        "message": str(event.get("message") or ""),
        "kind": str(event.get("kind") or ""),
        "category": str(event.get("category") or ""),
        "status": str(event.get("status") or ""),
        "occurred_at": _event_time(event.get("created_at")),
        "updated_at": _event_time(event.get("updated_at")),
        "area_name": str(event.get("room") or "") or None,
        "why": str(event.get("reason") or ""),
        "evidence_summary": [str(item)[:300] for item in evidence[:12]],
        "recovery": bool(event.get("kind") in {"device_recovered", "recovery"}),
        "incident_id": str(decision.get("incident_id") or "") or None,
        "actions": [str(item) for item in (event.get("actions") or ())],
        "diagnostics": {"entity_id": str(event.get("entity_id") or "") or None},
    }


def _incident_projection(incident: Mapping[str, Any]) -> dict[str, Any]:
    decision = incident.get("last_decision")
    decision = decision if isinstance(decision, Mapping) else {}
    kind = str(incident.get("kind") or "")
    return {
        "incident_id": str(incident.get("incident_id") or ""),
        "title": str(decision.get("title") or kind.replace("_", " ").title() or "Home attention"),
        "message": str(decision.get("message") or ""),
        "kind": kind,
        "status": str(incident.get("status") or "unknown").upper(),
        "area_id": str(incident.get("area_id") or "") or None,
        "area_name": str(incident.get("room") or "") or None,
        "device_id": str(incident.get("device_id") or "") or None,
        "device_name": str(incident.get("device_name") or "") or None,
        "last_seen": _event_time(incident.get("last_seen")),
        "resolved_at": _event_time(incident.get("resolved_at")),
        "occurrence_count": int(incident.get("occurrence_count") or 1),
        "why": str(decision.get("reason") or ""),
        "evidence_summary": [str(item)[:300] for item in (decision.get("evidence") or ())[:12]],
        "diagnostics": {"entity_id": str(incident.get("entity_id") or "") or None},
    }


def _occupancy(
    entities: Sequence[GroundedHomeEntity],
) -> tuple[
    OccupancyState,
    str,
    str,
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    evidence: list[dict[str, Any]] = []
    strongest = OccupancyState.UNKNOWN
    for entity in entities:
        if entity.domain != "binary_sensor" or not entity.available:
            continue
        evidence_class = occupancy_evidence_class(entity)
        if evidence_class == "diagnostic_observation":
            continue
        active = entity.state in {"on", "detected", "true", "person"}
        evidence.append(
            {
                "kind": evidence_class,
                "name": entity.name,
                "state": "detected" if active else "not_detected",
                "observed_at": entity.source_last_updated or entity.observed_at,
                "entity_id": entity.entity_id,
            }
        )
        if active and evidence_class == "person_presence":
            strongest = OccupancyState.OCCUPIED
        elif (
            active
            and evidence_class in {"presence", "motion"}
            and strongest is not OccupancyState.OCCUPIED
        ):
            strongest = OccupancyState.LIKELY_OCCUPIED
    normal = [
        item for item in evidence if item["kind"] in {"person_presence", "presence", "motion"}
    ]
    normal.sort(
        key=lambda item: (
            0 if item["state"] == "detected" else 1,
            0 if item["kind"] == "person_presence" else 1,
            str(item["name"]).casefold(),
        )
    )
    if strongest is OccupancyState.OCCUPIED:
        summary = "Person detected"
        detail = "Grounded person-detection evidence"
    elif strongest is OccupancyState.LIKELY_OCCUPIED:
        summary = "Activity detected"
        detail = "Motion or presence evidence only"
    elif any(item["kind"] == "person_presence" for item in normal):
        # An inactive camera/person/motion binary is only absence of a current
        # detection. It is never promoted into proof that the room is empty.
        summary = "No current person detection"
        detail = "Occupancy remains unknown"
    else:
        summary = "Occupancy unknown"
        detail = "No reliable occupancy evidence"
    return strongest, summary, detail, tuple(normal[:3]), tuple(evidence)


def _appliance(entity: GroundedHomeEntity) -> dict[str, Any] | None:
    is_domain = entity.domain in _APPLIANCE_DOMAINS
    is_validated_sensor = entity.domain == "sensor" and bool(_APPLIANCE_TERMS.search(entity.name))
    if not (is_domain or is_validated_sensor) or not entity.available:
        return None
    if entity.state not in _ACTIVE_APPLIANCE_STATES:
        return None
    return {
        "entity_id": entity.entity_id,
        "name": entity.device_name or entity.name,
        "state": entity.state.upper(),
        "state_label": entity.state.replace("_", " ").title(),
        "area_id": entity.area_id,
        "area_name": entity.area_name,
        "observed_at": entity.source_last_updated or entity.observed_at,
    }


def _energy(entity: GroundedHomeEntity) -> dict[str, Any] | None:
    device_class = str(entity.device_class or "").casefold()
    unit = str(entity.unit or "").casefold().replace(" ", "")
    if entity.domain != "sensor" or not entity.available:
        return None
    if device_class not in _ENERGY_DEVICE_CLASSES and unit not in _ENERGY_UNITS:
        return None
    kind = (
        "POWER"
        if device_class == "power" or unit in {"w", "kw"}
        else "COST"
        if device_class == "monetary"
        else "ENERGY"
    )
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "kind": kind,
        "value": entity.state,
        "display_value": entity.display_value or entity.state,
        "unit": entity.unit,
        "area_id": entity.area_id,
        "area_name": entity.area_name,
        "observed_at": entity.source_last_updated or entity.observed_at,
    }


def _numeric(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _format_energy_value(value: Any, unit: Any) -> str:
    number = _numeric(value)
    clean_unit = clean_text(unit)
    if number is None:
        return ""
    normalised = clean_unit.casefold().replace(" ", "")
    if normalised == "gbp":
        return f"£{number:.2f}"
    if normalised in {"gbp/kwh", "£/kwh"}:
        return f"{number * 100:.2f} p/kWh"
    if normalised == "p/kwh":
        return f"{number:.2f} p/kWh"
    if normalised == "w":
        return f"{number:.0f} W" if number >= 10 or number.is_integer() else f"{number:.1f} W"
    if normalised == "kw":
        return f"{number:.2f} kW"
    if normalised == "kwh" and 0 < abs(number) < 0.1:
        return f"{number * 1000:.1f} Wh"
    if normalised == "kwh":
        return f"{number:.3f}".rstrip("0").rstrip(".") + " kWh"
    if normalised == "wh":
        return f"{number:.1f}".rstrip("0").rstrip(".") + " Wh"
    return f"{number:.3f}".rstrip("0").rstrip(".") + (f" {clean_unit}" if clean_unit else "")


def _energy_role(entity: GroundedHomeEntity) -> tuple[str, str, int] | None:
    identity = f"{entity.name} {entity.entity_id}".casefold().replace("_", " ")
    if "next rate" in identity:
        return "NEXT_RATE", "Next rate", 50
    if "current rate" in identity:
        return "CURRENT_RATE", "Electricity rate", 40
    if "current demand" in identity or "house power" in identity:
        return "CURRENT_POWER", "Current draw", 10
    if "current accumulative cost" in identity:
        return "CURRENT_COST", "Current period cost", 30
    if "current accumulative consumption" in identity:
        return "CURRENT_CONSUMPTION", "Current period consumption", 20
    if "previous" in identity or "current total consumption" in identity:
        return None
    area = clean_text(entity.area_name)
    device = clean_text(entity.device_name)
    prefix = device if device and not re.search(r"[0-9a-f]{10,}", device, re.I) else area
    if entity.device_class == "power" or clean_text(entity.unit).casefold() in {"w", "kw"}:
        return "APPLIANCE_POWER", f"{prefix or 'Appliance'} power", 70
    if clean_text(entity.unit).casefold() == "gbp":
        period = "month" if "month" in identity else "week" if "week" in identity else "current"
        return "APPLIANCE_COST", f"{prefix or 'Appliance'} cost · {period}", 80
    if entity.device_class == "energy" and area:
        return "APPLIANCE_ENERGY", f"{prefix or area} energy", 90
    return None


def _curated_energy(
    entities: Sequence[GroundedHomeEntity],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    raw = tuple(projected for entity in entities if (projected := _energy(entity)) is not None)
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    appliance_counts: dict[tuple[str, str], int] = {}
    for entity in entities:
        projected = _energy(entity)
        if projected is None:
            continue
        role = _energy_role(entity)
        if role is None:
            continue
        key, label, priority = role
        if key.startswith("APPLIANCE_"):
            group = (clean_text(entity.area_id), key)
            appliance_counts[group] = appliance_counts.get(group, 0) + 1
            if appliance_counts[group] > 1:
                continue
            selection_key = f"{key}:{clean_text(entity.area_id) or entity.entity_id}"
        else:
            selection_key = key
        display = _format_energy_value(entity.state, entity.unit)
        if not display:
            continue
        item = {
            "entity_id": entity.entity_id,
            "name": label,
            "role": key,
            "kind": projected["kind"],
            "value": entity.state,
            "display_value": display,
            "unit": clean_text(entity.unit),
            "area_id": entity.area_id,
            "area_name": entity.area_name,
            "observed_at": entity.source_last_updated or entity.observed_at,
        }
        previous = selected.get(selection_key)
        if previous is None or priority < previous[0]:
            selected[selection_key] = (priority, item)
    ordered = tuple(item for _, item in sorted(selected.values(), key=lambda value: value[0]))
    return ordered[:8], raw


def _semantic_revision(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def project_home_experience(
    snapshot: HomeSnapshot,
    *,
    principal_id: str,
    proactive_events: Sequence[Mapping[str, Any]] = (),
    proactive_incidents: Sequence[Mapping[str, Any]] = (),
    freshness: SourceFreshness = SourceFreshness.LIVE,
    age_seconds: float = 0.0,
    generated_at: str | None = None,
) -> HomeExperience:
    """Build a deterministic presentation model from existing grounded truth."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    entities = snapshot.presentation_entities or snapshot.entities
    presentable_entities = tuple(
        item
        for item in entities
        if str(item.entity_category or "").casefold() not in {"config", "diagnostic"}
    )
    physical_devices = roll_up_physical_devices(snapshot.entities, observed_at=snapshot.observed_at)
    raw_unavailable_devices = tuple(
        item for item in physical_devices if item.availability is DeviceAvailability.UNAVAILABLE
    )
    raw_partial_devices = tuple(
        item for item in physical_devices if item.availability is DeviceAvailability.PARTIAL
    )

    canonicalization_started = time.monotonic()
    canonical_cameras = canonicalize_cameras(presentable_entities)
    aggregate_ids = aggregate_area_ids(
        [dict(item) for item in snapshot.areas], entities, canonical_cameras
    )
    canonical_cameras = tuple(
        item for item in canonical_cameras if item.area_id not in aggregate_ids
    )
    canonical_lights = tuple(
        item
        for item in canonicalize_domain(presentable_entities, domain="light", category="light")
        if item.area_id not in aggregate_ids
    )
    canonical_media = tuple(
        item
        for item in canonicalize_domain(
            presentable_entities, domain="media_player", category="media"
        )
        if item.area_id not in aggregate_ids and item.availability != "UNAVAILABLE"
    )
    runtime_metrics.observe(
        "home_experience_canonicalization_ms",
        (time.monotonic() - canonicalization_started) * 1000,
    )

    canonical_items_by_identity: dict[str, CanonicalHomeItem] = {}
    category_rank = {"camera": 0, "light": 1, "media": 2}
    for item in (*canonical_cameras, *canonical_lights, *canonical_media):
        identity = physical_identity(item.primary_entity)
        existing = canonical_items_by_identity.get(identity)
        if existing is None or category_rank[item.category] < category_rank[existing.category]:
            canonical_items_by_identity[identity] = item
    canonical_devices = tuple(canonical_items_by_identity.values())
    canonical_device_refs = tuple(
        _canonical_device_ref(item, physical_devices) for item in canonical_devices
    )
    canonical_unavailable = tuple(
        item
        for item in canonical_device_refs
        if item["availability"] == "UNAVAILABLE" and item["category"] in {"camera", "light"}
    )
    canonical_partial = tuple(
        item
        for item in canonical_device_refs
        if item["availability"] == "PARTIAL" and item["category"] in {"camera", "light", "media"}
    )

    entity_to_item: dict[str, CanonicalHomeItem] = {}
    device_to_item: dict[str, CanonicalHomeItem] = {}
    for item in (*canonical_cameras, *canonical_lights, *canonical_media):
        for source in item.all_entities:
            entity_to_item[source.entity_id] = item
    # Device-level evidence must resolve through the same ranked physical item
    # used by availability counts. A camera and its floodlight can legitimately
    # share one HA device; iteration order must not turn one outage into two
    # different user-facing incidents. Entity-specific evidence remains mapped
    # to its exact canonical capability above.
    for item in canonical_devices:
        for device_id in item.physical_device_ids:
            device_to_item[device_id] = item

    lights_on = tuple(
        item
        for item in canonical_lights
        if item.primary_entity.available and item.primary_entity.state == "on"
    )
    people = tuple(
        {
            "entity_id": item.entity_id,
            "name": clean_text(item.name) or "Person",
            "presence": (
                "HOME"
                if item.available and item.state == "home"
                else "AWAY"
                if item.available and item.state in {"away", "not_home"}
                else "UNKNOWN"
            ),
            "observed_at": item.source_last_updated or item.observed_at,
        }
        for item in presentable_entities
        if item.domain == "person"
    )
    active_media = tuple(
        _canonical_ref(item)
        for item in canonical_media
        if any(source.available and source.state in _ACTIVE_MEDIA for source in item.all_entities)
    )
    appliances = tuple(
        projected for item in presentable_entities if (projected := _appliance(item)) is not None
    )
    energy, raw_energy = _curated_energy(presentable_entities)
    cameras = tuple(
        _camera_ref(
            item,
            tuple(entity for entity in entities if entity.area_id == item.area_id),
        )
        for item in canonical_cameras
    )

    principal = principal_id.strip().casefold()
    raw_surfaced_rows = [
        item
        for item in proactive_events
        if str(item.get("target_user") or "all").casefold() in {principal, "all"}
        and (item.get("notified_at") is not None or item.get("spoken_at") is not None)
    ]
    surfaced_values: list[dict[str, Any]] = []
    surfaced_keys: set[str] = set()
    for row in raw_surfaced_rows:
        projected = _event_projection(row)
        canonical = entity_to_item.get(clean_text(row.get("entity_id"))) or device_to_item.get(
            clean_text(row.get("device_id"))
        )
        if canonical is not None:
            projected["canonical_id"] = canonical.canonical_id
            projected["device_name"] = canonical.display_name
            projected["area_id"] = canonical.area_id
            projected["area_name"] = projected.get("area_name") or canonical.area_name
            title = clean_text(projected.get("title")).casefold()
            if title in {"device unavailable", "device restored", "home activity"}:
                suffix = "restored" if projected.get("recovery") else "unavailable"
                projected["title"] = f"{canonical.display_name} {suffix}"
        key = clean_text(projected.get("incident_id")) or ":".join(
            (
                clean_text(projected.get("canonical_id")),
                clean_text(projected.get("kind")),
                clean_text(projected.get("status")),
            )
        )
        if key in surfaced_keys:
            continue
        surfaced_keys.add(key)
        surfaced_values.append(projected)
    surfaced_events = tuple(surfaced_values[:20])

    incident_rows = [
        item
        for item in proactive_incidents
        if str(item.get("status") or "").casefold() == "active"
        and str(item.get("target_user") or "all").casefold() in {principal, "all"}
    ]
    surfaced_incident_ids = {
        clean_text(item.get("incident_id")) for item in surfaced_events if item.get("incident_id")
    }
    always_important = {
        "safety_alert",
        "occupancy_while_away",
        "opening_open_long",
        "lock_unlocked",
        "smoke_detected",
        "water_leak",
        "door_open",
        "opening_open",
    }
    current_incident_values: list[dict[str, Any]] = []
    incident_keys: set[str] = set()
    for row in incident_rows:
        projected = _incident_projection(row)
        incident_id = clean_text(projected.get("incident_id"))
        canonical = device_to_item.get(clean_text(row.get("device_id"))) or entity_to_item.get(
            clean_text(row.get("entity_id"))
        )
        kind = clean_text(projected.get("kind")).casefold()
        relevant = kind in always_important or incident_id in surfaced_incident_ids
        if kind in {"device_unavailable", "camera_offline", "critical_unavailable"}:
            relevant = relevant or bool(
                canonical is not None
                and canonical.category in {"camera", "light"}
                and canonical.availability == "UNAVAILABLE"
            )
        if not relevant:
            continue
        event = next(
            (item for item in surfaced_events if item.get("incident_id") == incident_id), None
        )
        if event is not None:
            projected.update(
                title=event.get("title"),
                message=event.get("message"),
                area_name=event.get("area_name"),
                why=event.get("why"),
                evidence_summary=event.get("evidence_summary"),
            )
        if canonical is not None:
            projected.update(
                canonical_id=canonical.canonical_id,
                device_id=canonical.primary_entity.device_id,
                device_name=canonical.display_name,
                area_id=canonical.area_id,
                area_name=projected.get("area_name") or canonical.area_name,
            )
            if clean_text(projected.get("title")).casefold() in {
                "device unavailable",
                "home attention",
            }:
                projected["title"] = f"{canonical.display_name} unavailable"
        key = clean_text(projected.get("canonical_id")) or incident_id
        key = f"{key}:{kind}"
        if key in incident_keys:
            continue
        incident_keys.add(key)
        current_incident_values.append(projected)
    current_incidents = tuple(current_incident_values)

    area_rows: list[tuple[str, str]] = []
    for raw in snapshot.areas:
        area_id = clean_text(raw.get("area_id") or raw.get("id"))
        name = clean_text(raw.get("name"))
        if area_id and name and area_id not in aggregate_ids:
            area_rows.append((area_id, name))
    if not area_rows:
        area_rows = sorted(
            {
                (str(item.area_id), str(item.area_name))
                for item in entities
                if item.area_id and item.area_name and item.area_id not in aggregate_ids
            },
            key=lambda item: item[1].casefold(),
        )

    rooms: list[RoomStatus] = []
    quick_actions: list[dict[str, Any]] = []
    for area_id, name in sorted(area_rows, key=lambda item: item[1].casefold()):
        area_entities = tuple(item for item in entities if item.area_id == area_id)
        meaningful = tuple(
            item
            for item in area_entities
            if str(item.entity_category or "").casefold() not in {"config", "diagnostic"}
        )
        room_light_items = tuple(item for item in canonical_lights if item.area_id == area_id)
        room_lights_on = tuple(
            item
            for item in room_light_items
            if item.primary_entity.available and item.primary_entity.state == "on"
        )
        room_camera_items = tuple(item for item in canonical_cameras if item.area_id == area_id)
        room_cameras = tuple(_camera_ref(item, area_entities) for item in room_camera_items)
        room_media_items = tuple(item for item in canonical_media if item.area_id == area_id)
        room_devices = tuple(
            item for item in canonical_device_refs if item.get("area_id") == area_id
        )
        room_appliances = tuple(item for item in appliances if item.get("area_id") == area_id)
        room_climate = tuple(
            _entity_ref(item)
            for item in meaningful
            if item.domain in {"climate", "fan", "humidifier", "water_heater"}
        )
        (
            occupancy_state,
            occupancy_summary,
            occupancy_detail,
            occupancy_evidence,
            raw_occupancy_evidence,
        ) = _occupancy(area_entities)
        room_incidents = tuple(
            item
            for item in current_incidents
            if item.get("area_id") == area_id
            or clean_text(item.get("area_name")).casefold() == name.casefold()
        )
        room_events = tuple(
            item
            for item in surfaced_events
            if clean_text(item.get("area_name")).replace("_", " ").casefold()
            == name.replace("_", " ").casefold()
        )[:5]
        room_actions: list[dict[str, Any]] = []
        if room_lights_on and freshness is SourceFreshness.LIVE:
            action = {
                "action_id": f"lights-off:{area_id}",
                "kind": "TURN_OFF_EXACT_LIGHT_SET",
                "label": f"Turn {name} lights off",
                "enabled": True,
                "target_count": len(room_lights_on),
                "target_entity_ids": [item.primary_entity.entity_id for item in room_lights_on],
            }
            room_actions.append(action)
            quick_actions.append(action)
        if (
            not meaningful
            and not room_camera_items
            and not room_light_items
            and not room_media_items
        ):
            continue
        rooms.append(
            RoomStatus(
                area_id=area_id,
                name=name,
                occupancy_state=occupancy_state,
                occupancy_summary=occupancy_summary,
                occupancy_detail=occupancy_detail,
                occupancy_evidence=occupancy_evidence,
                lights_on_count=len(room_lights_on),
                lights_total=len(room_light_items),
                lights=tuple(_canonical_ref(item) for item in room_light_items),
                cameras=room_cameras,
                devices=room_devices,
                appliances=room_appliances,
                climate=room_climate,
                media=tuple(_canonical_ref(item) for item in room_media_items),
                important_incidents=room_incidents,
                recent_events=room_events,
                quick_actions=tuple(room_actions),
                diagnostics={
                    "occupancy_evidence": list(raw_occupancy_evidence),
                    "raw_entities": [_entity_ref(item) for item in area_entities],
                    "camera_sources": [item.diagnostics() for item in room_camera_items],
                    "light_sources": [item.diagnostics() for item in room_light_items],
                    "media_control_paths": [item.diagnostics() for item in room_media_items],
                },
            )
        )

    if lights_on and freshness is SourceFreshness.LIVE:
        quick_actions.insert(
            0,
            {
                "action_id": "lights-off:displayed",
                "kind": "TURN_OFF_EXACT_LIGHT_SET",
                "label": "Turn all displayed lights off",
                "enabled": True,
                "target_count": len(lights_on),
                "target_entity_ids": [item.primary_entity.entity_id for item in lights_on],
            },
        )

    attention: dict[str, str] = {}
    for device_ref in canonical_unavailable:
        key = clean_text(device_ref.get("canonical_id"))
        attention[key] = f"{clean_text(device_ref.get('name')) or 'Device'} unavailable"
    for incident in current_incidents:
        key = clean_text(incident.get("canonical_id")) or f"incident:{incident.get('incident_id')}"
        attention.setdefault(
            key,
            clean_text(incident.get("message"))
            or clean_text(incident.get("title"))
            or "Home attention",
        )
    attention_count = len(attention)
    status_facts: list[str] = []
    home_names = [str(item["name"]) for item in people if item["presence"] == "HOME"]
    if home_names:
        status_facts.append(
            f"{_natural_join(home_names)} {'is' if len(home_names) == 1 else 'are'} home"
        )
    status_facts.append(
        f"{len(lights_on)} {'light' if len(lights_on) == 1 else 'lights'} on"
        if lights_on
        else "All lights off"
    )
    if appliances:
        status_facts.append(
            f"{len(appliances)} {'appliance' if len(appliances) == 1 else 'appliances'} active"
        )
    if freshness is SourceFreshness.UNAVAILABLE:
        overall = {
            "status": "UNAVAILABLE",
            "headline": "Home state unavailable",
            "detail": "No current grounded state is available",
            "spoken_summary": "Home state is unavailable.",
            "attention_count": 0,
        }
    elif attention_count:
        headline = (
            "1 thing needs attention"
            if attention_count == 1
            else f"{attention_count} things need attention"
        )
        attention_detail = " · ".join(list(attention.values())[:2])
        facts = " · ".join(status_facts)
        detail = " · ".join(item for item in (attention_detail, facts) if item)
        overall = {
            "status": "ATTENTION",
            "headline": headline,
            "detail": detail,
            "spoken_summary": ". ".join(
                item for item in (headline, attention_detail, ". ".join(status_facts)) if item
            )
            + ".",
            "attention_count": attention_count,
        }
    else:
        detail = " · ".join(status_facts)
        overall = {
            "status": "NORMAL",
            "headline": "Home looks good",
            "detail": detail,
            "spoken_summary": "Home looks good. " + ". ".join(status_facts) + ".",
            "attention_count": 0,
        }

    semantic = {
        "people": people,
        "rooms": [item.as_dict() for item in rooms],
        "lights_on": [item.primary_entity.entity_id for item in lights_on],
        "unavailable_devices": canonical_unavailable,
        "partial_devices": canonical_partial,
        "appliances": appliances,
        "energy": energy,
        "incidents": current_incidents,
        "events": surfaced_events,
        "aggregate_area_ids": sorted(aggregate_ids),
    }
    revision = _semantic_revision(semantic)
    # Action identifiers include the immutable presentation revision. The Core
    # action registry uses these to execute exactly what the user saw.
    revisioned_actions = tuple(
        {**item, "action_id": f"{item['action_id']}:{revision}"} for item in quick_actions
    )
    revisioned_rooms = tuple(
        RoomStatus(
            **{
                **{field: getattr(room, field) for field in room.__dataclass_fields__},
                "quick_actions": tuple(
                    {**item, "action_id": f"{item['action_id']}:{revision}"}
                    for item in room.quick_actions
                ),
            }
        )
        for room in rooms
    )
    return HomeExperience(
        schema_version=1,
        generated_at=generated_at,
        revision=revision,
        source_freshness={
            "status": freshness.value,
            "observed_at": snapshot.observed_at,
            "age_seconds": round(max(0.0, age_seconds), 3),
            "actions_allowed": freshness is SourceFreshness.LIVE,
        },
        overall_status=overall,
        people=people,
        rooms=revisioned_rooms,
        lights={
            "on_count": len(lights_on),
            "total_count": len(canonical_lights),
            "items": [_canonical_ref(item) for item in canonical_lights],
        },
        devices={
            "unavailable_count": len(canonical_unavailable),
            "partial_count": len(canonical_partial),
            "unavailable": list(canonical_unavailable),
            "partial": list(canonical_partial),
        },
        appliances=appliances,
        cameras=cameras,
        energy=energy,
        active_media=active_media,
        current_incidents=current_incidents,
        recent_events=surfaced_events,
        quick_actions=revisioned_actions,
        diagnostics={
            "principal_id": principal_id,
            "grounded_entity_count": len(entities),
            "physical_device_count": len(physical_devices),
            "aggregate_area_ids": sorted(aggregate_ids),
            "raw_camera_entity_count": sum(item.domain == "camera" for item in entities),
            "canonical_camera_count": len(cameras),
            "raw_unavailable_device_count": len(raw_unavailable_devices),
            "raw_partial_device_count": len(raw_partial_devices),
            "raw_energy_sensor_count": len(raw_energy),
            "raw_energy": list(raw_energy),
            "raw_unavailable_devices": [_device_ref(item) for item in raw_unavailable_devices],
            "raw_partial_devices": [_device_ref(item) for item in raw_partial_devices],
            "raw_incidents": [_incident_projection(item) for item in incident_rows],
            "camera_sources": [item.diagnostics() for item in canonical_cameras],
            "light_sources": [item.diagnostics() for item in canonical_lights],
            "media_control_paths": [item.diagnostics() for item in canonical_media],
        },
    )


__all__ = [
    "FrozenHomeAction",
    "HomeExperience",
    "HomeExperienceService",
    "OccupancyState",
    "RoomStatus",
    "SourceFreshness",
    "project_home_experience",
]
