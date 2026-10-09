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
_PERSON_TERMS = re.compile(r"\bperson\b", re.I)
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
        "observed_at": entity.observed_at,
        "source_last_updated": entity.source_last_updated,
    }


def _device_ref(device: GroundedDeviceStatus) -> dict[str, Any]:
    return {
        "device_key": device.device_key,
        "device_id": device.device_id,
        "name": device.name,
        "area_id": device.area_id,
        "area_name": device.area_name,
        "availability": device.availability.value,
        "unavailable_entity_count": device.unavailable_entity_count,
        "member_entity_count": len(device.member_entities),
        "observed_at": device.observed_at,
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
) -> tuple[OccupancyState, str, tuple[dict[str, Any], ...]]:
    evidence: list[dict[str, Any]] = []
    strongest = OccupancyState.UNKNOWN
    for entity in entities:
        if entity.domain != "binary_sensor" or not entity.available:
            continue
        identity = f"{entity.name} {entity.entity_id.replace('_', ' ')}"
        device_class = str(entity.device_class or "").casefold()
        is_person = bool(_PERSON_TERMS.search(identity))
        is_presence = device_class in {"occupancy", "presence"}
        is_motion = device_class == "motion" or " motion" in identity.casefold()
        if not (is_person or is_presence or is_motion):
            continue
        active = entity.state in {"on", "detected", "true", "person"}
        kind = "person_detection" if is_person else "occupancy_sensor" if is_presence else "motion"
        evidence.append(
            {
                "kind": kind,
                "name": entity.name,
                "state": "detected" if active else "not_detected",
                "observed_at": entity.source_last_updated or entity.observed_at,
                "entity_id": entity.entity_id,
            }
        )
        if active and is_person:
            strongest = OccupancyState.OCCUPIED
        elif active and strongest is not OccupancyState.OCCUPIED:
            strongest = OccupancyState.LIKELY_OCCUPIED
    if strongest is OccupancyState.OCCUPIED:
        summary = "Person detected"
    elif strongest is OccupancyState.LIKELY_OCCUPIED:
        summary = "Activity detected"
    elif evidence:
        # An inactive camera/person/motion binary is only absence of a current
        # detection. It is never promoted into proof that the room is empty.
        summary = "No current occupancy evidence"
    else:
        summary = "Occupancy unknown"
    return strongest, summary, tuple(evidence)


def _camera(
    entity: GroundedHomeEntity, area_entities: Sequence[GroundedHomeEntity]
) -> dict[str, Any]:
    related = [
        item
        for item in area_entities
        if item.domain == "binary_sensor"
        and (item.device_id == entity.device_id or item.area_id == entity.area_id)
        and (
            _PERSON_TERMS.search(f"{item.name} {item.entity_id.replace('_', ' ')}")
            or item.device_class == "motion"
        )
    ]
    activity = next(
        (
            "Person detected" if _PERSON_TERMS.search(item.name) else "Activity detected"
            for item in related
            if item.available and item.state in {"on", "detected", "true", "person"}
        ),
        None,
    )
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "area_id": entity.area_id,
        "area_name": entity.area_name,
        "availability": "UNAVAILABLE"
        if entity.state == "unavailable"
        else "ONLINE"
        if entity.available
        else "UNKNOWN",
        "recent_activity": activity,
        "observed_at": entity.source_last_updated or entity.observed_at,
        "detail_route": f"camera:{entity.entity_id}",
    }


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
    unavailable_devices = tuple(
        item for item in physical_devices if item.availability is DeviceAvailability.UNAVAILABLE
    )
    partial_devices = tuple(
        item for item in physical_devices if item.availability is DeviceAvailability.PARTIAL
    )
    lights = tuple(item for item in presentable_entities if item.domain == "light")
    lights_on = tuple(item for item in lights if item.available and item.state == "on")
    people = tuple(
        {
            "entity_id": item.entity_id,
            "name": item.name,
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
        _entity_ref(item)
        for item in presentable_entities
        if item.domain == "media_player" and item.available and item.state in _ACTIVE_MEDIA
    )
    appliances = tuple(
        projected for item in presentable_entities if (projected := _appliance(item)) is not None
    )
    energy = tuple(
        projected for item in presentable_entities if (projected := _energy(item)) is not None
    )
    cameras = tuple(
        _camera(
            item, tuple(candidate for candidate in entities if candidate.area_id == item.area_id)
        )
        for item in presentable_entities
        if item.domain == "camera"
    )

    principal = principal_id.strip().casefold()
    surfaced_events = tuple(
        _event_projection(item)
        for item in proactive_events
        if str(item.get("target_user") or "all").casefold() in {principal, "all"}
        and (item.get("notified_at") is not None or item.get("spoken_at") is not None)
    )[:30]
    incident_rows = [
        item
        for item in proactive_incidents
        if str(item.get("status") or "").casefold() == "active"
        and str(item.get("target_user") or "all").casefold() in {principal, "all"}
    ]
    current_incident_values: list[dict[str, Any]] = []
    for row in incident_rows:
        projected = _incident_projection(row)
        incident_id = str(projected.get("incident_id") or "")
        event = next(
            (item for item in surfaced_events if item.get("incident_id") == incident_id),
            None,
        )
        entity_id = str(row.get("entity_id") or "")
        device = next(
            (
                item
                for item in physical_devices
                if any(member.entity_id == entity_id for member in item.member_entities)
            ),
            None,
        )
        if event is not None:
            projected.update(
                title=event.get("title"),
                message=event.get("message"),
                area_name=event.get("area_name"),
                why=event.get("why"),
                evidence_summary=event.get("evidence_summary"),
            )
        if device is not None:
            projected.update(
                device_id=device.device_id,
                device_name=device.name,
                area_id=device.area_id,
                area_name=projected.get("area_name") or device.area_name,
            )
        current_incident_values.append(projected)
    current_incidents = tuple(current_incident_values)

    area_rows: list[tuple[str, str]] = []
    for raw in snapshot.areas:
        area_id = str(raw.get("area_id") or raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if area_id and name:
            area_rows.append((area_id, name))
    if not area_rows:
        area_rows = sorted(
            {
                (str(item.area_id), str(item.area_name))
                for item in entities
                if item.area_id and item.area_name
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
        if not meaningful:
            continue
        room_lights = tuple(item for item in meaningful if item.domain == "light")
        room_lights_on = tuple(
            item for item in room_lights if item.available and item.state == "on"
        )
        room_cameras = tuple(
            _camera(item, area_entities) for item in meaningful if item.domain == "camera"
        )
        room_devices = tuple(
            _device_ref(item) for item in physical_devices if item.area_id == area_id
        )
        room_appliances = tuple(item for item in appliances if item.get("area_id") == area_id)
        room_climate = tuple(
            _entity_ref(item)
            for item in meaningful
            if item.domain in {"climate", "fan", "humidifier", "water_heater"}
        )
        room_media = tuple(
            _entity_ref(item) for item in meaningful if item.domain == "media_player"
        )
        occupancy_state, occupancy_summary, occupancy_evidence = _occupancy(area_entities)
        room_incidents = tuple(
            item
            for item in current_incidents
            if item.get("area_id") == area_id
            or str(item.get("area_name") or "").casefold() == name.casefold()
        )
        room_events = tuple(
            item
            for item in surfaced_events
            if str(item.get("area_name") or "").replace("_", " ").casefold()
            == name.replace("_", " ").casefold()
        )[:10]
        room_actions: list[dict[str, Any]] = []
        if room_lights_on and freshness is SourceFreshness.LIVE:
            action = {
                "action_id": f"lights-off:{area_id}",
                "kind": "TURN_OFF_EXACT_LIGHT_SET",
                "label": f"Turn {name} lights off",
                "enabled": True,
                "target_count": len(room_lights_on),
                "target_entity_ids": [item.entity_id for item in room_lights_on],
            }
            room_actions.append(action)
            quick_actions.append(action)
        rooms.append(
            RoomStatus(
                area_id=area_id,
                name=name,
                occupancy_state=occupancy_state,
                occupancy_summary=occupancy_summary,
                occupancy_evidence=occupancy_evidence,
                lights_on_count=len(room_lights_on),
                lights_total=len(room_lights),
                lights=tuple(_entity_ref(item) for item in room_lights),
                cameras=room_cameras,
                devices=room_devices,
                appliances=room_appliances,
                climate=room_climate,
                media=room_media,
                important_incidents=room_incidents,
                recent_events=room_events,
                quick_actions=tuple(room_actions),
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
                "target_entity_ids": [item.entity_id for item in lights_on],
            },
        )

    device_attention_keys = {
        f"device:{item.device_id or item.device_key}" for item in unavailable_devices
    }
    incident_attention_keys: set[str] = set()
    for incident in current_incidents:
        device_identity = incident.get("device_id") or incident.get("device_name")
        incident_attention_keys.add(
            f"device:{device_identity}"
            if device_identity
            else f"incident:{incident.get('incident_id')}"
        )
    attention_keys = device_attention_keys | incident_attention_keys
    unmatched_incident_count = len(incident_attention_keys - device_attention_keys)
    attention_count = len(attention_keys)
    status_facts: list[str] = []
    home_names = [str(item["name"]) for item in people if item["presence"] == "HOME"]
    status_facts.append(
        f"{len(lights_on)} {'light is' if len(lights_on) == 1 else 'lights are'} on"
        if lights_on
        else "All lights are off"
    )
    if home_names:
        status_facts.append(
            f"{_natural_join(home_names)} {'is' if len(home_names) == 1 else 'are'} home"
        )
    if appliances:
        status_facts.append(
            f"{len(appliances)} {'appliance is' if len(appliances) == 1 else 'appliances are'} running"
        )
    if freshness is SourceFreshness.UNAVAILABLE:
        overall = {
            "status": "UNAVAILABLE",
            "headline": "Home state unavailable",
            "attention_count": 0,
        }
    elif attention_count:
        attention_detail = ""
        if len(unavailable_devices) == 1:
            attention_detail = f": {unavailable_devices[0].name} is unavailable"
        elif len(unavailable_devices) > 1:
            attention_detail = f": {len(unavailable_devices)} devices are unavailable"
        if unavailable_devices and unmatched_incident_count:
            attention_detail += (
                f" and {unmatched_incident_count} other incident is active"
                if unmatched_incident_count == 1
                else f" and {unmatched_incident_count} other incidents are active"
            )
        elif len(current_incidents) == 1:
            incident_message = str(
                current_incidents[0].get("message")
                or current_incidents[0].get("title")
                or "Home attention"
            ).rstrip(".")
            attention_detail = f": {incident_message}"
        elif len(current_incidents) > 1:
            attention_detail = f": {len(current_incidents)} incidents are active"
        overall = {
            "status": "ATTENTION",
            "headline": (
                (
                    "1 thing needs attention"
                    if attention_count == 1
                    else f"{attention_count} things need attention"
                )
                + attention_detail
                + ". "
                + ". ".join(status_facts)
                + "."
            ),
            "attention_count": attention_count,
        }
    else:
        overall = {
            "status": "NORMAL",
            "headline": "Everything looks normal. " + ". ".join(status_facts) + ".",
            "attention_count": 0,
        }

    semantic = {
        "people": people,
        "rooms": [item.as_dict() for item in rooms],
        "lights_on": [item.entity_id for item in lights_on],
        "unavailable_devices": [item.device_key for item in unavailable_devices],
        "partial_devices": [item.device_key for item in partial_devices],
        "appliances": appliances,
        "energy": energy,
        "incidents": current_incidents,
        "events": surfaced_events,
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
            "total_count": len(lights),
            "items": [_entity_ref(item) for item in lights],
        },
        devices={
            "unavailable_count": len(unavailable_devices),
            "partial_count": len(partial_devices),
            "unavailable": [_device_ref(item) for item in unavailable_devices],
            "partial": [_device_ref(item) for item in partial_devices],
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
