"""Grounded whole-home queries over Home Assistant registry and live state.

The language model may propose a :class:`HomeQueryPlan`, but every scope,
category, predicate and result member is validated here.  Entity identities and
states always come from the authoritative Home Assistant read surface.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any

from app.runtime_observability import runtime_metrics


class HomeQueryOperation(str, Enum):
    QUERY = "QUERY"
    SNAPSHOT = "SNAPSHOT"


class HomeQueryScope(str, Enum):
    HOME = "HOME"
    AREA = "AREA"
    EXPLICIT_ENTITY_SET = "EXPLICIT_ENTITY_SET"
    REFERENCED_ENTITY_SET = "REFERENCED_ENTITY_SET"


class HomePredicate(str, Enum):
    ANY = "ANY"
    ON = "ON"
    OFF = "OFF"
    ACTIVE = "ACTIVE"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    HOME = "HOME"
    AWAY = "AWAY"
    OCCUPIED = "OCCUPIED"
    CLEAR = "CLEAR"
    UNKNOWN = "UNKNOWN"


class HomeAggregation(str, Enum):
    LIST = "LIST"
    COUNT = "COUNT"
    SUMMARY = "SUMMARY"


class DeviceAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    AMBIGUOUS = "AMBIGUOUS"


_CATEGORY_DOMAINS: dict[str, frozenset[str]] = {
    "devices": frozenset(),
    "lights": frozenset({"light"}),
    "switches": frozenset({"switch"}),
    "cameras": frozenset({"camera"}),
    "people": frozenset({"person"}),
    "media": frozenset({"media_player"}),
    "climate": frozenset({"climate", "fan", "humidifier", "water_heater"}),
    "locks": frozenset({"lock"}),
    "appliances": frozenset({"fan", "humidifier", "vacuum", "water_heater"}),
    "battery": frozenset({"sensor"}),
    "security": frozenset({"alarm_control_panel", "binary_sensor", "lock", "siren"}),
    "rooms": frozenset(),
}

_ACTIVE_STATES: dict[str, frozenset[str]] = {
    "light": frozenset({"on"}),
    "switch": frozenset({"on"}),
    "fan": frozenset({"on"}),
    "humidifier": frozenset({"on"}),
    "vacuum": frozenset({"cleaning", "returning"}),
    "water_heater": frozenset({"on", "heat", "heating"}),
    "media_player": frozenset({"on", "playing", "paused", "buffering"}),
    "climate": frozenset({"auto", "cool", "dry", "fan_only", "heat", "heat_cool"}),
}

_USER_FACING_DEVICE_DOMAINS = frozenset(
    {
        "camera",
        "alarm_control_panel",
        "binary_sensor",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "person",
        "sensor",
        "siren",
        "switch",
        "vacuum",
        "water_heater",
    }
)


@dataclass(frozen=True, slots=True)
class HomeQueryPlan:
    operation: HomeQueryOperation = HomeQueryOperation.QUERY
    scope: HomeQueryScope = HomeQueryScope.HOME
    category: str = "devices"
    predicate: HomePredicate = HomePredicate.ANY
    aggregation: HomeAggregation = HomeAggregation.LIST
    area_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    reference_result_set_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> HomeQueryPlan:
        try:
            operation = HomeQueryOperation(str(value.get("operation") or "QUERY").upper())
            scope = HomeQueryScope(str(value.get("scope") or "HOME").upper())
            predicate = HomePredicate(str(value.get("predicate") or "ANY").upper())
            aggregation = HomeAggregation(str(value.get("aggregation") or "LIST").upper())
        except ValueError as exc:
            raise ValueError("Unsupported Home Assistant query semantics") from exc
        category = str(value.get("category") or "devices").strip().casefold()
        if category not in _CATEGORY_DOMAINS:
            raise ValueError(f"Unsupported Home Assistant category: {category}")
        area_id = str(value.get("area_id") or "").strip() or None
        raw_entity_ids = value.get("entity_ids") or ()
        if not isinstance(raw_entity_ids, Sequence) or isinstance(
            raw_entity_ids, (str, bytes, bytearray)
        ):
            raise ValueError("An explicit Home Assistant entity set must be a sequence")
        entity_ids = tuple(
            dict.fromkeys(str(item).strip() for item in raw_entity_ids if str(item).strip())
        )
        reference_result_set_id = str(value.get("reference_result_set_id") or "").strip() or None
        if scope is HomeQueryScope.AREA and not area_id:
            raise ValueError("An exact configured area is required for an area query")
        if scope is not HomeQueryScope.AREA and area_id:
            raise ValueError("Only an area query can name an area")
        set_scopes = {
            HomeQueryScope.EXPLICIT_ENTITY_SET,
            HomeQueryScope.REFERENCED_ENTITY_SET,
        }
        if scope in set_scopes and not entity_ids:
            raise ValueError("An exact grounded entity set is required for this scope")
        if scope not in set_scopes and entity_ids:
            raise ValueError("Entity identities are allowed only for an exact set scope")
        if scope is HomeQueryScope.REFERENCED_ENTITY_SET and not reference_result_set_id:
            raise ValueError("A durable grounded result-set reference is required")
        if scope is not HomeQueryScope.REFERENCED_ENTITY_SET and reference_result_set_id:
            raise ValueError("A result-set reference is allowed only for referenced scope")
        if predicate in {HomePredicate.HOME, HomePredicate.AWAY} and category != "people":
            raise ValueError("Home and away predicates apply only to people")
        occupancy_predicates = {
            HomePredicate.OCCUPIED,
            HomePredicate.CLEAR,
            HomePredicate.UNKNOWN,
        }
        if predicate in occupancy_predicates and category != "rooms":
            raise ValueError("Occupancy predicates apply only to rooms")
        if category == "rooms" and predicate not in occupancy_predicates | {HomePredicate.ANY}:
            raise ValueError("Rooms support only occupancy predicates")
        if operation is HomeQueryOperation.SNAPSHOT and scope is not HomeQueryScope.HOME:
            raise ValueError("A home snapshot must use whole-home scope")
        return cls(
            operation,
            scope,
            category,
            predicate,
            aggregation,
            area_id,
            entity_ids,
            reference_result_set_id,
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            operation=self.operation.value,
            scope=self.scope.value,
            predicate=self.predicate.value,
            aggregation=self.aggregation.value,
        )
        value["entity_ids"] = list(self.entity_ids)
        return value


@dataclass(frozen=True, slots=True)
class GroundedHomeEntity:
    entity_id: str
    name: str
    domain: str
    state: str
    available: bool
    observed_at: str
    area_id: str | None = None
    area_name: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    device_class: str | None = None
    entity_category: str | None = None
    unit: str | None = None
    display_value: str | None = None
    supported_features: int | None = None
    source_last_changed: str | None = None
    source_last_updated: str | None = None
    platform: str | None = None
    registry_name: str | None = None
    registry_original_name: str | None = None
    device_name_by_user: str | None = None
    device_manufacturer: str | None = None
    device_model: str | None = None
    device_identifiers: tuple[tuple[str, str], ...] = ()
    device_connections: tuple[tuple[str, str], ...] = ()
    via_device_id: str | None = None

    @classmethod
    def from_state(cls, state: Mapping[str, Any], observed_at: str) -> GroundedHomeEntity:
        entity_id = str(state.get("entity_id") or "")
        domain = str(state.get("domain") or entity_id.partition(".")[0])
        if not entity_id or "." not in entity_id or not domain:
            raise ValueError("Home Assistant returned an invalid entity identity")
        return cls(
            entity_id=entity_id,
            name=str(state.get("name") or entity_id),
            domain=domain,
            state=str(state.get("state") or "unknown").casefold(),
            available=bool(state.get("available", True)),
            observed_at=observed_at,
            area_id=str(state.get("area_id") or "") or None,
            area_name=str(state.get("area_name") or "") or None,
            device_id=str(state.get("device_id") or "") or None,
            device_name=str(state.get("device_name") or "") or None,
            device_class=str(state.get("device_class") or "") or None,
            entity_category=str(state.get("entity_category") or "") or None,
            unit=str(state.get("unit") or "") or None,
            display_value=str(state.get("display_value") or "") or None,
            supported_features=(
                int(state["supported_features"])
                if isinstance(state.get("supported_features"), int)
                else None
            ),
            source_last_changed=str(state.get("last_changed") or "") or None,
            source_last_updated=str(state.get("last_updated") or "") or None,
            platform=str(state.get("platform") or "") or None,
            registry_name=str(state.get("registry_name") or "") or None,
            registry_original_name=(str(state.get("registry_original_name") or "") or None),
            device_name_by_user=str(state.get("device_name_by_user") or "") or None,
            device_manufacturer=str(state.get("device_manufacturer") or "") or None,
            device_model=str(state.get("device_model") or "") or None,
            device_identifiers=_registry_pairs(state.get("device_identifiers")),
            device_connections=_registry_pairs(state.get("device_connections")),
            via_device_id=str(state.get("via_device_id") or "") or None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _registry_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            continue
        parts = tuple(str(part).strip() for part in item)
        if len(parts) == 2 and all(parts):
            pairs.append((parts[0], parts[1]))
    return tuple(sorted(set(pairs)))


_PRIMARY_DEVICE_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "camera",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "siren",
        "switch",
        "vacuum",
        "water_heater",
    }
)
_PRIMARY_DOMAIN_PRIORITY = (
    "camera",
    "alarm_control_panel",
    "lock",
    "climate",
    "media_player",
    "vacuum",
    "water_heater",
    "light",
    "cover",
    "fan",
    "humidifier",
    "switch",
    "siren",
)


@dataclass(frozen=True, slots=True)
class GroundedDeviceStatus:
    """A physical-device view derived only from authoritative HA identity links."""

    device_key: str
    device_id: str | None
    name: str
    area_id: str | None
    area_name: str | None
    availability: DeviceAvailability
    member_entities: tuple[GroundedHomeEntity, ...]
    unavailable_entity_count: int
    observed_at: str
    evidence_kind: str

    @property
    def physical_device(self) -> bool:
        return self.device_id is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_key": self.device_key,
            "device_id": self.device_id,
            "name": self.name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "availability": self.availability.value,
            "available": self.availability is DeviceAvailability.AVAILABLE,
            "physical_device": self.physical_device,
            "unavailable_entity_count": self.unavailable_entity_count,
            "member_entity_count": len(self.member_entities),
            "member_entities": [item.as_dict() for item in self.member_entities],
            "observed_at": self.observed_at,
            "evidence_kind": self.evidence_kind,
        }


def roll_up_physical_devices(
    entities: Sequence[GroundedHomeEntity],
    *,
    observed_at: str,
) -> tuple[GroundedDeviceStatus, ...]:
    """Roll entities up by registry ``device_id`` without name-based guessing.

    A physical device is unavailable only when its primary entity surface is
    unavailable. A diagnostic child failing while a primary entity remains
    available is PARTIAL. Entities without a device link remain isolated so
    the presentation never invents physical identity.
    """

    grouped: dict[str, list[GroundedHomeEntity]] = {}
    for entity in entities:
        key = f"device:{entity.device_id}" if entity.device_id else f"entity:{entity.entity_id}"
        grouped.setdefault(key, []).append(entity)

    result: list[GroundedDeviceStatus] = []
    for key, members in grouped.items():
        ordered = tuple(sorted(members, key=lambda item: (item.domain, item.name.casefold())))
        unavailable = tuple(item for item in ordered if item.state == "unavailable")
        primary = tuple(
            item
            for item in ordered
            if item.domain in _PRIMARY_DEVICE_DOMAINS
            and str(item.entity_category or "").casefold() not in {"config", "diagnostic"}
        )
        decisive: tuple[GroundedHomeEntity, ...] = ()
        for primary_domain in _PRIMARY_DOMAIN_PRIORITY:
            same_domain = tuple(item for item in primary if item.domain == primary_domain)
            if same_domain:
                decisive = same_domain
                break
        decisive = decisive or tuple(
            item
            for item in ordered
            if str(item.entity_category or "").casefold() not in {"config", "diagnostic"}
        )
        if not decisive:
            availability = DeviceAvailability.AMBIGUOUS
        elif all(item.state == "unavailable" for item in decisive):
            availability = DeviceAvailability.UNAVAILABLE
        elif unavailable:
            availability = DeviceAvailability.PARTIAL
        else:
            availability = DeviceAvailability.AVAILABLE
        representative = next(
            (item for item in primary if item.state == "unavailable"),
            primary[0] if primary else ordered[0],
        )
        device_name = next((item.device_name for item in ordered if item.device_name), None)
        result.append(
            GroundedDeviceStatus(
                device_key=key,
                device_id=representative.device_id,
                name=device_name or representative.name,
                area_id=representative.area_id,
                area_name=representative.area_name,
                availability=availability,
                member_entities=ordered,
                unavailable_entity_count=len(unavailable),
                observed_at=observed_at,
                evidence_kind=(
                    "home_assistant_device_registry"
                    if representative.device_id
                    else "ungrouped_entity_without_device_id"
                ),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: ((item.area_name or "").casefold(), item.name.casefold()),
        )
    )


@dataclass(frozen=True, slots=True)
class GroundedEntitySet:
    plan: HomeQueryPlan
    entities: tuple[GroundedHomeEntity, ...]
    observed_at: str
    area_name: str | None = None
    devices: tuple[GroundedDeviceStatus, ...] = ()

    def as_result(self) -> dict[str, Any]:
        rows = [item.as_dict() for item in self.entities]
        device_rows = [item.as_dict() for item in self.devices]
        local_ids = [item.entity_id for item in self.entities]
        return {
            "success": True,
            "query_plan": self.plan.as_dict(),
            "scope": self.plan.scope.value,
            "category": self.plan.category,
            "predicate": self.plan.predicate.value,
            "aggregation": self.plan.aggregation.value,
            "area_id": self.plan.area_id,
            "area_name": self.area_name,
            "count": len(device_rows) if self.devices else len(rows),
            "entities": rows,
            "devices": device_rows,
            "observed_at": self.observed_at,
            "complete": True,
            "context_projection": {
                "objects": [
                    {
                        "reference_id": item.entity_id,
                        "object_type": "person" if item.domain == "person" else "device",
                        "display_name": item.name,
                        "source": "home_assistant_whole_home_query",
                        "canonical_id": item.entity_id,
                        "provider": "home_assistant",
                        "capability": "homeassistant.read",
                        "evidence_status": "verified",
                        "freshness_seconds": 30,
                        "immutable": False,
                        "metadata": item.as_dict(),
                        "aliases": [item.name, item.entity_id],
                    }
                    for item in self.entities
                ],
                "result_set": {
                    "object_refs": local_ids,
                    "ordering": "friendly_name",
                    "observed_at": self.observed_at,
                    "filters": self.plan.as_dict(),
                },
            },
        }


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    observed_at: str
    entities: tuple[GroundedHomeEntity, ...]
    # Presentation-only sensors (energy, appliance enum sensors, and similar)
    # are retained separately so richer clients can use their grounded values
    # without changing the alpha36 physical-device availability population.
    presentation_entities: tuple[GroundedHomeEntity, ...] = ()
    areas: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        unavailable = [item for item in self.entities if item.state == "unavailable"]
        people_home = [
            item for item in self.entities if item.domain == "person" and item.state == "home"
        ]
        lights_on = [
            item for item in self.entities if item.domain == "light" and item.state == "on"
        ]
        active_media = [
            item
            for item in self.entities
            if item.domain == "media_player" and item.state in _ACTIVE_STATES["media_player"]
        ]
        switches_on = [
            item for item in self.entities if item.domain == "switch" and item.state == "on"
        ]
        running_appliances = [
            item
            for item in self.entities
            if item.domain in _CATEGORY_DOMAINS["appliances"]
            and item.available
            and item.state in _ACTIVE_STATES.get(item.domain, ())
        ]
        unavailable_cameras = [item for item in unavailable if item.domain == "camera"]
        low_batteries = [
            item
            for item in self.entities
            if item.domain == "sensor"
            and item.device_class == "battery"
            and item.available
            and _numeric_state(item.state) is not None
            and float(_numeric_state(item.state) or 0) <= 20
        ]
        active_climate = [
            item
            for item in self.entities
            if item.domain in _CATEGORY_DOMAINS["climate"]
            and item.available
            and item.state in _ACTIVE_STATES.get(item.domain, ())
        ]
        unlocked_locks = [
            item
            for item in self.entities
            if item.domain == "lock" and item.available and item.state == "unlocked"
        ]
        security_entities = [
            item for item in self.entities if item.domain in _CATEGORY_DOMAINS["security"]
        ]
        domains = Counter(item.domain for item in self.entities)
        devices = roll_up_physical_devices(self.entities, observed_at=self.observed_at)
        unavailable_devices = [
            item for item in devices if item.availability is DeviceAvailability.UNAVAILABLE
        ]
        partially_unavailable_devices = [
            item for item in devices if item.availability is DeviceAvailability.PARTIAL
        ]
        return {
            "observed_at": self.observed_at,
            "entity_count": len(self.entities),
            "domain_counts": dict(sorted(domains.items())),
            "lights_on": [item.as_dict() for item in lights_on],
            "unavailable_entities": [item.as_dict() for item in unavailable],
            "unavailable_devices": [item.as_dict() for item in unavailable_devices],
            "partially_unavailable_devices": [
                item.as_dict() for item in partially_unavailable_devices
            ],
            "people_home": [item.as_dict() for item in people_home],
            "active_media": [item.as_dict() for item in active_media],
            "switches_on": [item.as_dict() for item in switches_on],
            "running_appliances": [item.as_dict() for item in running_appliances],
            "offline_cameras": [item.as_dict() for item in unavailable_cameras],
            "low_batteries": [item.as_dict() for item in low_batteries],
            "active_climate": [item.as_dict() for item in active_climate],
            "unlocked_locks": [item.as_dict() for item in unlocked_locks],
            "security_entities": [item.as_dict() for item in security_entities],
            "areas": [dict(item) for item in self.areas],
        }


class HomeIntelligenceEngine:
    """Resolve semantic plans against complete, fresh Home Assistant state."""

    def __init__(
        self,
        *,
        area_loader: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]],
        state_loader: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]],
    ) -> None:
        self._area_loader = area_loader
        self._state_loader = state_loader

    @staticmethod
    def _matches_predicate(entity: GroundedHomeEntity, predicate: HomePredicate) -> bool:
        if predicate is HomePredicate.ANY:
            return True
        if predicate is HomePredicate.AVAILABLE:
            return entity.available
        if predicate is HomePredicate.UNAVAILABLE:
            # Home Assistant's explicit ``unavailable`` state is evidence that
            # an entity is offline/unreachable. ``off`` and ``unknown`` are
            # distinct and must not be relabelled as unavailable.
            return entity.state == "unavailable"
        if predicate is HomePredicate.ON:
            return entity.available and entity.state == "on"
        if predicate is HomePredicate.OFF:
            return entity.available and entity.state == "off"
        if predicate is HomePredicate.ACTIVE:
            return entity.available and entity.state in _ACTIVE_STATES.get(entity.domain, ())
        if predicate is HomePredicate.HOME:
            return entity.domain == "person" and entity.state == "home"
        if predicate is HomePredicate.AWAY:
            return entity.domain == "person" and entity.state in {"away", "not_home"}
        return False

    async def query(self, plan: HomeQueryPlan) -> GroundedEntitySet:
        started = time.monotonic()
        area_rows = [dict(item) for item in await self._area_loader()]
        areas = {
            str(item.get("area_id") or item.get("id")): str(item.get("name") or "")
            for item in area_rows
            if item.get("area_id") or item.get("id")
        }
        if plan.area_id and plan.area_id not in areas:
            raise ValueError(f"Unknown Home Assistant area: {plan.area_id}")
        observed_at = datetime.now(timezone.utc).isoformat()
        domains = _CATEGORY_DOMAINS[plan.category]
        grounded: list[GroundedHomeEntity] = []
        seen: set[str] = set()
        requested_ids = set(plan.entity_ids)
        observed_ids: set[str] = set()
        all_device_entities: list[GroundedHomeEntity] = []
        for raw in await self._state_loader():
            item = GroundedHomeEntity.from_state(raw, observed_at)
            if item.entity_id in seen:
                continue
            seen.add(item.entity_id)
            observed_ids.add(item.entity_id)
            if plan.scope is HomeQueryScope.AREA and item.area_id != plan.area_id:
                continue
            if requested_ids and item.entity_id not in requested_ids:
                continue
            if domains and item.domain not in domains:
                continue
            if not domains and item.domain not in _USER_FACING_DEVICE_DOMAINS:
                continue
            if plan.category == "devices" and plan.predicate is HomePredicate.UNAVAILABLE:
                all_device_entities.append(item)
                continue
            if not self._matches_predicate(item, plan.predicate):
                continue
            grounded.append(item)
        missing_ids = requested_ids - observed_ids
        if missing_ids:
            raise ValueError("One or more grounded Home Assistant entities no longer exists")
        grounded.sort(key=lambda item: ((item.area_name or "").casefold(), item.name.casefold()))
        devices: tuple[GroundedDeviceStatus, ...] = ()
        if plan.category == "devices" and plan.predicate is HomePredicate.UNAVAILABLE:
            devices = tuple(
                item
                for item in roll_up_physical_devices(
                    all_device_entities,
                    observed_at=observed_at,
                )
                if item.availability is DeviceAvailability.UNAVAILABLE
            )
            grounded = [
                entity
                for device in devices
                for entity in device.member_entities
                if entity.state == "unavailable"
            ]
        result = GroundedEntitySet(
            plan=plan,
            entities=tuple(grounded),
            observed_at=observed_at,
            area_name=areas.get(plan.area_id or "") or None,
            devices=devices,
        )
        runtime_metrics.observe(
            "home_entity_set_resolution_ms", (time.monotonic() - started) * 1000
        )
        return result

    async def snapshot(self) -> HomeSnapshot:
        started = time.monotonic()
        observed_at = datetime.now(timezone.utc).isoformat()
        entities: list[GroundedHomeEntity] = []
        presentation_entities: list[GroundedHomeEntity] = []
        seen: set[str] = set()
        for raw in await self._state_loader():
            item = GroundedHomeEntity.from_state(raw, observed_at)
            if item.entity_id in seen or item.domain not in _USER_FACING_DEVICE_DOMAINS:
                continue
            seen.add(item.entity_id)
            presentation_entities.append(item)
            if item.domain != "sensor" or item.device_class == "battery":
                entities.append(item)
        entities.sort(key=lambda item: ((item.area_name or "").casefold(), item.name.casefold()))
        presentation_entities.sort(
            key=lambda item: ((item.area_name or "").casefold(), item.name.casefold())
        )
        areas = tuple(dict(item) for item in await self._area_loader())
        result = HomeSnapshot(
            observed_at=observed_at,
            entities=tuple(entities),
            presentation_entities=tuple(presentation_entities),
            areas=areas,
        )
        runtime_metrics.observe(
            "home_snapshot_construction_ms", (time.monotonic() - started) * 1000
        )
        return result


def _numeric_state(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DeviceAvailability",
    "GroundedDeviceStatus",
    "GroundedEntitySet",
    "GroundedHomeEntity",
    "HomeAggregation",
    "HomeIntelligenceEngine",
    "HomePredicate",
    "HomeQueryOperation",
    "HomeQueryPlan",
    "HomeQueryScope",
    "HomeSnapshot",
    "roll_up_physical_devices",
]
