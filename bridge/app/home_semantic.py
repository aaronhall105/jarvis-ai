"""Canonical semantic discovery and verified actions for the live home.

The language model supplies semantic concepts and an operation.  This module
never trusts it to supply Home Assistant entity IDs or service calls: it builds
a bounded physical-object inventory from registry-backed state, returns opaque
canonical identities, and converts a short-lived grounded action plan into an
exact service call with read-back verification.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import time
from typing import Any
import uuid

from app.home_canonicalization import canonicalize_cameras, physical_identity
from app.home_intelligence import GroundedHomeEntity
from app.home_parameters import (
    ParameterizedCapability,
    compile_service_schemas,
    match_parameter,
    project_parameterized_capabilities,
    read_parameter,
    resolve_parameter_value,
    verification_tolerance,
)
from app.runtime_observability import runtime_metrics


logger = logging.getLogger("jarvis-core.home-semantic")


_OBSERVATION_DOMAINS = frozenset({"binary_sensor", "sensor"})
_CANONICAL_OBJECT_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "binary_sensor",
        "button",
        "camera",
        "climate",
        "cover",
        "device_tracker",
        "event",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "number",
        "person",
        "remote",
        "select",
        "sensor",
        "siren",
        "switch",
        "update",
        "vacuum",
        "valve",
        "water_heater",
    }
)
_NON_USER_ENTITY_CATEGORIES = frozenset({"config", "diagnostic"})
_CONTROL_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "button",
        "camera",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "number",
        "remote",
        "select",
        "siren",
        "switch",
        "vacuum",
        "valve",
        "water_heater",
    }
)
_UNKNOWN_STATES = frozenset({"", "unknown", "unavailable"})
_ACTIVE_STATES = frozenset(
    {
        "auto",
        "buffering",
        "cleaning",
        "cool",
        "dry",
        "fan_only",
        "heat",
        "heat_cool",
        "heating",
        "idle",
        "on",
        "paused",
        "playing",
        "returning",
    }
)

# These are generic Home Assistant service capabilities, not language intents.
# The model never chooses an entity or service from this table; it asks for a
# capability and the grounded inventory selects an actual supporting surface.
_POWER_DOMAINS = frozenset(
    {"fan", "humidifier", "input_boolean", "light", "media_player", "siren", "switch"}
)
_MEDIA_FEATURES = {
    "pause": 1,
    "set_volume": 4,
    "mute": 8,
    "previous": 16,
    "next": 32,
    "turn_on": 128,
    "turn_off": 256,
    "volume_up": 1024,
    "volume_down": 1024,
    "stop": 4096,
    "play": 16384,
}
_ACTION_SERVICES: dict[str, str] = {
    "turn_on": "turn_on",
    "turn_off": "turn_off",
    "play": "media_play",
    "pause": "media_pause",
    "stop": "media_stop",
    "mute": "volume_mute",
    "unmute": "volume_mute",
    "next": "media_next_track",
    "previous": "media_previous_track",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
}
_ACTION_CAPABILITIES = {action: action for action in _ACTION_SERVICES}


def _normalise(value: Any) -> str:
    text = str(value or "").casefold().replace("_", " ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _tokens(value: Any) -> frozenset[str]:
    result: set[str] = set()
    for token in _normalise(value).split():
        result.add(token)
        if len(token) > 3 and token.endswith("s"):
            result.add(token[:-1])
    return frozenset(result)


def _pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    pairs: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            continue
        parts = tuple(str(part).strip().casefold() for part in item)
        if len(parts) == 2 and all(parts):
            pairs.add((parts[0], parts[1]))
    return tuple(sorted(pairs))


def _capabilities(entity: Mapping[str, Any]) -> frozenset[str]:
    domain = str(entity.get("domain") or "").casefold()
    features = entity.get("supported_features")
    supported_features = int(features) if isinstance(features, int) else 0
    values = {"state"}
    if domain == "camera":
        values.add("view")
    if domain in _POWER_DOMAINS:
        if domain != "media_player" or supported_features & _MEDIA_FEATURES["turn_on"]:
            values.add("turn_on")
        if domain != "media_player" or supported_features & _MEDIA_FEATURES["turn_off"]:
            values.add("turn_off")
    if domain == "media_player":
        for capability, bit in _MEDIA_FEATURES.items():
            if capability not in {"turn_on", "turn_off"} and supported_features & bit:
                values.add(capability)
        if "mute" in values:
            values.add("unmute")
    return frozenset(values)


def _effective_state(entity: Mapping[str, Any]) -> str:
    state = str(entity.get("state") or "unknown").casefold()
    if state in _UNKNOWN_STATES:
        return state or "unknown"
    if str(entity.get("domain") or "") == "media_player" and state in _ACTIVE_STATES:
        return "on"
    return state


def _matches_state(entity: Mapping[str, Any], wanted: str | None) -> bool:
    if not wanted:
        return True
    expected = _normalise(wanted).replace(" ", "_")
    state = str(entity.get("state") or "unknown").casefold()
    effective = _effective_state(entity)
    if expected == "available":
        return state not in _UNKNOWN_STATES
    if expected == "unavailable":
        return state == "unavailable"
    if expected in {"active", "running"}:
        return state in _ACTIVE_STATES
    if expected in {"inactive", "not_running"}:
        return state not in _ACTIVE_STATES and state not in _UNKNOWN_STATES
    return expected in {state, effective}


def _entity_role(entity: Mapping[str, Any]) -> str:
    """Classify a registry entity by structural HA metadata, never its target noun."""

    category = str(entity.get("entity_category") or "").casefold()
    domain = str(entity.get("domain") or "").casefold()
    if category == "diagnostic":
        return "DIAGNOSTIC"
    if category == "config":
        return "CONFIGURATION"
    if domain == "update":
        return "UPDATE"
    if domain in _OBSERVATION_DOMAINS:
        return "OBSERVATION" if domain == "binary_sensor" else "MEASUREMENT"
    if domain in {"event", "device_tracker", "person"}:
        return "EVENT" if domain == "event" else "OBSERVATION"
    if domain in _CONTROL_DOMAINS:
        return "PRIMARY_CONTROL"
    return "SECONDARY_CONTROL"


def _structural_object_role(members: Sequence[Mapping[str, Any]]) -> str:
    """Return PHYSICAL, GROUP, AGGREGATE, or VIRTUAL from registry structure."""

    structural_values: list[str] = []
    group_relationship = False
    for item in members:
        structural_values.extend(
            str(item.get(key) or "") for key in ("device_class", "device_name", "device_model")
        )
        attributes = item.get("attributes")
        if isinstance(attributes, Mapping):
            group_relationship = group_relationship or any(
                isinstance(attributes.get(key), Sequence)
                and not isinstance(attributes.get(key), (str, bytes, bytearray))
                and bool(attributes.get(key))
                for key in ("entity_id", "group_members")
            )
    structural_tokens = set(_normalise(" ".join(structural_values)).split())
    if group_relationship or "group" in structural_tokens:
        return "GROUP"
    camera_members = [item for item in members if item.get("domain") == "camera"]
    if camera_members and all(
        item.get("via_device_id") and not _pairs(item.get("device_connections"))
        for item in camera_members
    ):
        return "AGGREGATE"
    if any(item.get("device_id") for item in members):
        return "PHYSICAL"
    return "VIRTUAL"


def _capability_kind(entity: Mapping[str, Any]) -> str:
    """Derive a capability vocabulary from HA domain and device class metadata."""

    domain = str(entity.get("domain") or "entity").casefold()
    device_class = _normalise(entity.get("device_class")).replace(" ", "_")
    if domain in _OBSERVATION_DOMAINS and device_class:
        return device_class
    return domain


_CAPABILITY_ROLES = {
    "PRIMARY",
    "AUXILIARY",
    "OBSERVATIONAL",
    "MEASUREMENT",
    "DIAGNOSTIC",
    "CONFIGURATION",
    "MAINTENANCE",
    "VIRTUAL",
    "UNKNOWN",
}

_CAPABILITY_ROLE_FILTERS = {
    "PRIMARY_ONLY",
    "PRIMARY_OR_AUXILIARY",
    "AUXILIARY_ONLY",
    "ANY",
}

_NON_IDENTITY_ENTITY_ROLES = {
    "OBSERVATION",
    "MEASUREMENT",
    "DIAGNOSTIC",
    "CONFIGURATION",
    "UPDATE",
    "EVENT",
}

_SUPPORTING_MEASUREMENT_CLASSES = {
    "battery",
    "current",
    "energy",
    "power",
    "signal_strength",
    "voltage",
}


@dataclass(frozen=True, slots=True)
class CanonicalHomeObject:
    canonical_id: str
    display_name: str
    area_id: str | None
    area_name: str | None
    kind: str
    domains: tuple[str, ...]
    capabilities: tuple[str, ...]
    state: str
    available: bool
    primary_entity_id: str
    member_entity_ids: tuple[str, ...]
    physical_device_ids: tuple[str, ...]
    aliases: tuple[str, ...]
    semantic_text: str
    entities: tuple[Mapping[str, Any], ...]
    evidence: tuple[str, ...]
    object_role: str = "PHYSICAL"
    capability_kind: str | None = None
    parent_canonical_id: str | None = None
    entity_roles: tuple[tuple[str, str], ...] = ()
    primary_purposes: tuple[str, ...] = ()
    capability_role: str | None = None
    parameterized_capabilities: tuple[ParameterizedCapability, ...] = ()

    def compact(self, *, confidence: float | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "canonical_id": self.canonical_id,
            "display_name": self.display_name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "kind": self.kind,
            "domains": list(self.domains),
            "capabilities": list(self.capabilities),
            "state": self.state,
            "available": self.available,
            "object_role": self.object_role,
            "capability_kind": self.capability_kind,
            "parent_canonical_id": self.parent_canonical_id,
            "entity_roles": {entity_id: role for entity_id, role in self.entity_roles},
            "primary_purposes": list(self.primary_purposes),
            "capability_role": self.capability_role,
            "parameterized_capabilities": [
                capability.public() for capability in self.parameterized_capabilities
            ],
            "evidence": list(self.evidence),
        }
        if confidence is not None:
            result["confidence"] = round(confidence, 3)
        return result

    def inspected(self) -> dict[str, Any]:
        return {
            **self.compact(),
            "aliases": list(self.aliases),
            "physical_device_ids": list(self.physical_device_ids),
            "primary_entity_id": self.primary_entity_id,
            "member_entities": [
                {
                    key: entity.get(key)
                    for key in (
                        "entity_id",
                        "name",
                        "domain",
                        "state",
                        "available",
                        "device_class",
                        "entity_category",
                        "platform",
                        "supported_features",
                    )
                }
                | {"role": _entity_role(entity)}
                for entity in self.entities
            ],
        }


@dataclass(frozen=True, slots=True)
class SemanticHomeGraph:
    """Compact indexes over live registry-backed objects and capability surfaces."""

    physical_objects: tuple[CanonicalHomeObject, ...]
    virtual_targets: tuple[CanonicalHomeObject, ...]
    capabilities: tuple[CanonicalHomeObject, ...]

    @property
    def all_items(self) -> tuple[CanonicalHomeObject, ...]:
        return (*self.physical_objects, *self.virtual_targets, *self.capabilities)

    def inventory(
        self, kind: str, *, include_virtual: bool = False
    ) -> tuple[CanonicalHomeObject, ...]:
        normalized = str(kind or "PHYSICAL_DEVICE").upper()
        if normalized == "CAPABILITY":
            return tuple(
                item
                for item in self.capabilities
                if include_virtual or item.object_role not in {"GROUP", "VIRTUAL", "AGGREGATE"}
            )
        if normalized == "VIRTUAL_TARGET":
            return self.virtual_targets
        if normalized != "PHYSICAL_DEVICE":
            raise ValueError("Unsupported semantic inventory kind")
        if include_virtual:
            return (*self.physical_objects, *self.virtual_targets)
        return self.physical_objects


@dataclass(frozen=True, slots=True)
class _ActionPlan:
    handle: str
    principal_id: str
    conversation_id: str
    request_id: str
    action: str | None
    requested_parameter: str | None
    parameter_mode: str
    parameter_direction: str
    parameter_value: float | str | None
    parameter_unit: str | None
    canonical_ids: frozenset[str]
    expires_at: datetime


class HomeSemanticEngine:
    """Build and query a compact live inventory of canonical home objects."""

    VERIFY_DELAYS = (0.12, 0.20, 0.35, 0.55)
    PLAN_TTL_SECONDS = 120

    def __init__(
        self,
        *,
        state_loader: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]],
        service_caller: Callable[..., Awaitable[Any]],
        service_schema_loader: Callable[[], Awaitable[Any]] | None = None,
        service_schema_ttl_seconds: float = 300.0,
    ) -> None:
        self._state_loader = state_loader
        self._service_caller = service_caller
        self._service_schema_loader = service_schema_loader
        self._service_schema_ttl_seconds = max(1.0, float(service_schema_ttl_seconds))
        self._service_schema_cache: Any = ()
        self._service_schema_cached_at: float | None = None
        self._service_schema_lock = asyncio.Lock()
        self._plans: dict[str, _ActionPlan] = {}

    async def _service_schemas(self) -> Any:
        if self._service_schema_loader is None:
            return ()
        now = time.monotonic()
        if (
            self._service_schema_cached_at is not None
            and now - self._service_schema_cached_at < self._service_schema_ttl_seconds
        ):
            return self._service_schema_cache
        async with self._service_schema_lock:
            now = time.monotonic()
            if (
                self._service_schema_cached_at is not None
                and now - self._service_schema_cached_at < self._service_schema_ttl_seconds
            ):
                return self._service_schema_cache
            started = time.monotonic()
            try:
                self._service_schema_cache = compile_service_schemas(
                    await self._service_schema_loader()
                )
            except Exception:
                # Parameter discovery may temporarily degrade, but ordinary Home
                # identity/state/power reasoning must remain available.
                logger.warning(
                    "Could not refresh Home Assistant service schemas",
                    exc_info=True,
                )
                self._service_schema_cache = ()
            self._service_schema_cached_at = time.monotonic()
            runtime_metrics.observe(
                "home_parameter_schema_load_ms", (time.monotonic() - started) * 1000
            )
        return self._service_schema_cache

    @staticmethod
    def _enrich_item(item: CanonicalHomeObject, schemas: Any) -> CanonicalHomeObject:
        parameters = project_parameterized_capabilities(item.entities, schemas)
        return replace(
            item,
            parameterized_capabilities=parameters,
            capabilities=tuple(
                sorted({*item.capabilities, *(value.parameter for value in parameters)})
            ),
        )

    @staticmethod
    def _identity_tokens(entity: Mapping[str, Any]) -> frozenset[str]:
        grounded = GroundedHomeEntity.from_state(
            entity,
            str(entity.get("observed_at") or datetime.now(timezone.utc).isoformat()),
        )
        # Start from the same alpha38 identity used by HomeExperience, then use
        # all registry relationships to merge additional integration surfaces.
        values: set[str] = {physical_identity(grounded)}
        device_id = str(entity.get("device_id") or "").strip()
        if device_id:
            values.add(f"device:{device_id}")
        values.update(
            f"connection:{kind}:{identifier}"
            for kind, identifier in _pairs(entity.get("device_connections"))
        )
        values.update(
            f"identifier:{kind}:{identifier}"
            for kind, identifier in _pairs(entity.get("device_identifiers"))
        )
        if not values:
            entity_id = str(entity.get("entity_id") or "").strip()
            if entity_id:
                values.add(f"entity:{entity_id}")
        return frozenset(values)

    @classmethod
    def _groups(
        cls, entities: Sequence[Mapping[str, Any]]
    ) -> tuple[tuple[Mapping[str, Any], ...], ...]:
        parents = list(range(len(entities)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        owner: dict[str, int] = {}
        for index, entity in enumerate(entities):
            for identity in cls._identity_tokens(entity):
                previous = owner.setdefault(identity, index)
                union(index, previous)
        grouped: dict[int, list[Mapping[str, Any]]] = {}
        for index, entity in enumerate(entities):
            grouped.setdefault(find(index), []).append(entity)
        return tuple(tuple(items) for items in grouped.values())

    @staticmethod
    def _entity_rank(entity: Mapping[str, Any]) -> tuple[int, int, int, str]:
        capabilities = _capabilities(entity)
        category = str(entity.get("entity_category") or "").casefold()
        domain = str(entity.get("domain") or "")
        return (
            1 if category in _NON_USER_ENTITY_CATEGORIES else 0,
            0 if len(capabilities) > 1 else 1,
            1 if domain in _OBSERVATION_DOMAINS else 0,
            str(entity.get("entity_id") or ""),
        )

    @staticmethod
    def _display_name(members: Sequence[Mapping[str, Any]], primary: Mapping[str, Any]) -> str:
        candidates: list[str] = []
        for key in ("device_name_by_user", "device_name"):
            candidates.extend(str(item.get(key) or "").strip() for item in members)
        for key in ("registry_name", "name", "registry_original_name"):
            candidates.append(str(primary.get(key) or "").strip())
        name = next(
            (value for value in candidates if value), str(primary.get("entity_id") or "Device")
        )
        area = str(primary.get("area_name") or "").strip()
        if area and _normalise(area) not in _normalise(name):
            return f"{area} {name}"
        return name

    @classmethod
    def _object_from_members(
        cls,
        members: Sequence[Mapping[str, Any]],
        *,
        identity_seed: str | None = None,
        primary_entity_id: str | None = None,
        display_name: str | None = None,
        kind: str | None = None,
        object_role: str | None = None,
        evidence: Sequence[str] = (),
    ) -> CanonicalHomeObject:
        ordered = tuple(sorted((dict(item) for item in members), key=cls._entity_rank))
        if not ordered:
            raise ValueError("A canonical home object requires at least one entity")
        primary = next(
            (
                item
                for item in ordered
                if primary_entity_id and str(item.get("entity_id") or "") == primary_entity_id
            ),
            ordered[0],
        )
        identities = sorted({value for item in ordered for value in cls._identity_tokens(item)})
        seed = identity_seed or "\n".join(identities)
        digest = hashlib.sha256(seed.encode()).hexdigest()[:24]
        canonical_id = f"home:{digest}"
        resolved_name = display_name or cls._display_name(ordered, primary)
        capabilities = tuple(sorted({value for item in ordered for value in _capabilities(item)}))
        domains = tuple(
            sorted({str(item.get("domain") or "") for item in ordered if item.get("domain")})
        )
        control_members = tuple(item for item in ordered if _entity_role(item) == "PRIMARY_CONTROL")
        classed_controls = tuple(
            item for item in control_members if str(item.get("device_class") or "").strip()
        )
        device_names = {
            _normalise(item.get(key))
            for item in ordered
            for key in ("device_name_by_user", "device_name")
            if _normalise(item.get(key))
        }
        primary_surface_matches_device = any(
            _normalise(item.get(key)) in device_names
            for item in control_members
            for key in ("name", "registry_name", "registry_original_name")
            if _normalise(item.get(key))
        )
        has_distinct_functional_evidence = any(
            _entity_role(item) == "OBSERVATION"
            or (
                _entity_role(item) == "MEASUREMENT"
                and _normalise(item.get("device_class")).replace(" ", "_")
                not in _SUPPORTING_MEASUREMENT_CLASSES
            )
            for item in ordered
            if item not in control_members
        )
        subordinate_control_surface = bool(
            control_members
            and not kind
            and not classed_controls
            and not primary_surface_matches_device
            and has_distinct_functional_evidence
        )
        dynamic_device_purpose = min(device_names, key=len) if device_names else ""
        resolved_kind = str(
            kind
            or (classed_controls[0].get("device_class") if classed_controls else None)
            or (dynamic_device_purpose if subordinate_control_surface else None)
            or primary.get("device_class")
            or primary.get("domain")
            or "device"
        ).strip()
        primary_purposes = {
            *(
                (_normalise(resolved_kind).replace(" ", "_"),)
                if kind
                or classed_controls
                or primary_surface_matches_device
                or not subordinate_control_surface
                else ()
            ),
            *(_normalise(item.get("device_class")).replace(" ", "_") for item in classed_controls),
        }
        if subordinate_control_surface and dynamic_device_purpose:
            primary_purposes.add(dynamic_device_purpose.replace(" ", "_"))
        primary_purposes.discard("")
        physical_ids = tuple(
            sorted({str(item.get("device_id")) for item in ordered if item.get("device_id")})
        )
        semantic_domains = {
            str(item.get("domain") or "")
            for item in ordered
            if item.get("domain") and _entity_role(item) not in _NON_IDENTITY_ENTITY_ROLES
        }
        aliases: set[str] = {resolved_name, resolved_kind, *semantic_domains}
        for item in ordered:
            aliases.update(
                str(item.get(key) or "").strip()
                for key in (
                    "device_name_by_user",
                    "device_name",
                    "device_manufacturer",
                    "device_model",
                    "device_class",
                )
            )
            # Supporting entity names describe capabilities/evidence, not physical identity.
            if _entity_role(item) not in _NON_IDENTITY_ENTITY_ROLES or not physical_ids:
                aliases.update(
                    str(item.get(key) or "").strip()
                    for key in ("registry_name", "registry_original_name", "name")
                )
        aliases.discard("")
        available = str(primary.get("state") or "").casefold() not in _UNKNOWN_STATES
        return CanonicalHomeObject(
            canonical_id=canonical_id,
            display_name=resolved_name,
            area_id=str(primary.get("area_id") or "") or None,
            area_name=str(primary.get("area_name") or "") or None,
            kind=resolved_kind,
            domains=domains,
            capabilities=capabilities,
            state=str(primary.get("state") or "unknown").casefold(),
            available=available,
            primary_entity_id=str(primary.get("entity_id") or ""),
            member_entity_ids=tuple(sorted(str(item.get("entity_id")) for item in ordered)),
            physical_device_ids=physical_ids,
            aliases=tuple(sorted(aliases, key=str.casefold)),
            semantic_text=_normalise(" ".join(sorted(aliases))),
            entities=ordered,
            evidence=tuple(dict.fromkeys([*evidence, *identities]))[:12],
            object_role=object_role or _structural_object_role(ordered),
            entity_roles=tuple(
                sorted((str(item.get("entity_id") or ""), _entity_role(item)) for item in ordered)
            ),
            primary_purposes=tuple(sorted(primary_purposes)),
        )

    @staticmethod
    def _capability_role(
        parent: CanonicalHomeObject,
        capability_kind: str,
        members: Sequence[Mapping[str, Any]],
    ) -> str:
        """Classify a capability's relationship to its physical parent.

        The role follows registry structure rather than product names. Device classes
        and the parent's grounded dominant purposes identify primary surfaces; control
        surfaces outside those purposes remain usable as auxiliary capabilities.
        """

        roles = {_entity_role(item) for item in members}
        if parent.object_role in {"GROUP", "VIRTUAL", "AGGREGATE"}:
            return "VIRTUAL"
        if roles and roles <= {"OBSERVATION"}:
            return "OBSERVATIONAL"
        if roles and roles <= {"MEASUREMENT"}:
            return "MEASUREMENT"
        if roles and roles <= {"OBSERVATION", "MEASUREMENT"}:
            return "OBSERVATIONAL"
        if roles and roles <= {"DIAGNOSTIC"}:
            return "DIAGNOSTIC"
        if roles and roles <= {"CONFIGURATION"}:
            return "CONFIGURATION"
        if roles and roles <= {"UPDATE"}:
            return "MAINTENANCE"

        purposes = set(parent.primary_purposes)
        member_classes = {
            _normalise(item.get("device_class")).replace(" ", "_")
            for item in members
            if str(item.get("device_class") or "").strip()
        }
        normalised_kind = _normalise(capability_kind).replace(" ", "_")
        if normalised_kind in purposes or member_classes & purposes:
            return "PRIMARY"

        if "PRIMARY_CONTROL" in roles:
            return "AUXILIARY" if purposes else "UNKNOWN"
        return "UNKNOWN"

    @classmethod
    def _capability_nodes(
        cls, parents: Sequence[CanonicalHomeObject]
    ) -> tuple[CanonicalHomeObject, ...]:
        nodes: list[CanonicalHomeObject] = []
        for parent in parents:
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for entity in parent.entities:
                if _entity_role(entity) in {"CONFIGURATION", "DIAGNOSTIC", "UPDATE", "EVENT"}:
                    continue
                grouped.setdefault(_capability_kind(entity), []).append(entity)
            for capability_kind, members in grouped.items():
                ordered = tuple(sorted(members, key=cls._entity_rank))
                primary = ordered[0]
                entity_ids = tuple(sorted(str(item.get("entity_id") or "") for item in ordered))
                digest = hashlib.sha256(
                    "\n".join((parent.canonical_id, capability_kind, *entity_ids)).encode()
                ).hexdigest()[:24]
                aliases: set[str] = {
                    capability_kind,
                    parent.display_name,
                    str(primary.get("domain") or ""),
                    str(primary.get("device_class") or ""),
                }
                for item in ordered:
                    aliases.update(
                        str(item.get(key) or "").strip()
                        for key in (
                            "name",
                            "registry_name",
                            "registry_original_name",
                            "device_class",
                        )
                    )
                aliases.discard("")
                display_name = cls._display_name(ordered, primary)
                capability_role = cls._capability_role(parent, capability_kind, ordered)
                if capability_role not in _CAPABILITY_ROLES:  # pragma: no cover - invariant
                    capability_role = "UNKNOWN"
                nodes.append(
                    CanonicalHomeObject(
                        canonical_id=f"capability:{digest}",
                        display_name=display_name,
                        area_id=parent.area_id,
                        area_name=parent.area_name,
                        kind=capability_kind,
                        domains=tuple(
                            sorted(
                                {
                                    str(item.get("domain") or "")
                                    for item in ordered
                                    if item.get("domain")
                                }
                            )
                        ),
                        capabilities=tuple(
                            sorted({value for item in ordered for value in _capabilities(item)})
                        ),
                        state=str(primary.get("state") or "unknown").casefold(),
                        available=str(primary.get("state") or "").casefold() not in _UNKNOWN_STATES,
                        primary_entity_id=str(primary.get("entity_id") or ""),
                        member_entity_ids=entity_ids,
                        physical_device_ids=parent.physical_device_ids,
                        aliases=tuple(sorted(aliases, key=str.casefold)),
                        semantic_text=_normalise(" ".join(sorted(aliases))),
                        entities=ordered,
                        evidence=(parent.canonical_id, *entity_ids),
                        object_role=parent.object_role,
                        capability_kind=capability_kind,
                        parent_canonical_id=parent.canonical_id,
                        entity_roles=tuple(
                            sorted(
                                (str(item.get("entity_id") or ""), _entity_role(item))
                                for item in ordered
                            )
                        ),
                        primary_purposes=parent.primary_purposes,
                        capability_role=capability_role,
                    )
                )
        return tuple(
            sorted(
                nodes,
                key=lambda item: (
                    (item.area_name or "").casefold(),
                    item.display_name.casefold(),
                    item.kind,
                ),
            )
        )

    @staticmethod
    def _disambiguate_display_names(
        items: Sequence[CanonicalHomeObject],
    ) -> tuple[CanonicalHomeObject, ...]:
        """Keep distinct grounded identities visibly distinct without merging by name."""

        collisions: dict[tuple[str, str], list[CanonicalHomeObject]] = {}
        for item in items:
            collisions.setdefault(
                (_normalise(item.area_id), _normalise(item.display_name)), []
            ).append(item)

        result: list[CanonicalHomeObject] = []
        for item in items:
            peers = collisions[(_normalise(item.area_id), _normalise(item.display_name))]
            if len(peers) == 1:
                result.append(item)
                continue
            primary = next(
                (
                    entity
                    for entity in item.entities
                    if str(entity.get("entity_id") or "") == item.primary_entity_id
                ),
                item.entities[0],
            )
            qualifier = next(
                (
                    value
                    for raw in (
                        primary.get("device_model"),
                        primary.get("device_manufacturer"),
                        primary.get("platform"),
                    )
                    if (value := str(raw or "").strip())
                    and _normalise(value) not in _normalise(item.display_name)
                ),
                item.canonical_id.rsplit(":", 1)[-1][:8],
            )
            display_name = f"{item.display_name} ({qualifier})"
            aliases = tuple(
                sorted({*item.aliases, item.display_name, display_name}, key=str.casefold)
            )
            result.append(
                replace(
                    item,
                    display_name=display_name,
                    aliases=aliases,
                    semantic_text=_normalise(" ".join(aliases)),
                )
            )
        return tuple(result)

    @classmethod
    def _graph_from(cls, rows: Sequence[Mapping[str, Any]]) -> SemanticHomeGraph:
        enabled = [
            dict(item)
            for item in rows
            if item.get("entity_id") and str(item.get("domain") or "") in _CANONICAL_OBJECT_DOMAINS
        ]
        observed_at = next(
            (str(item.get("observed_at")) for item in enabled if item.get("observed_at")),
            datetime.now(timezone.utc).isoformat(),
        )
        grounded = tuple(GroundedHomeEntity.from_state(item, observed_at) for item in enabled)
        camera_items = canonicalize_cameras(grounded)
        camera_entity_ids = {
            entity.entity_id for item in camera_items for entity in item.all_entities
        }
        camera_primary_device_ids = {
            str(item.primary_entity.device_id)
            for item in camera_items
            if item.primary_entity.device_id
        }
        aggregate_device_ids = {
            str(entity.device_id)
            for item in camera_items
            for entity in item.diagnostic_entities
            if entity.device_id
        }

        regular_rows = [
            item
            for item in enabled
            if str(item.get("device_id") or "") not in camera_primary_device_ids
            and str(item.get("entity_id") or "") not in camera_entity_ids
        ]
        objects: list[CanonicalHomeObject] = []
        for members in cls._groups(regular_rows):
            physical_ids = {str(item.get("device_id") or "") for item in members}
            role = "AGGREGATE" if physical_ids & aggregate_device_ids else None
            objects.append(cls._object_from_members(members, object_role=role))

        by_entity_id = {str(item.get("entity_id") or ""): item for item in enabled}
        for item in camera_items:
            primary_device_id = str(item.primary_entity.device_id or "")
            members_by_id: dict[str, Mapping[str, Any]] = {
                str(row.get("entity_id") or ""): row
                for row in enabled
                if primary_device_id and str(row.get("device_id") or "") == primary_device_id
            }
            for entity in item.all_entities:
                row = by_entity_id.get(entity.entity_id)
                if row is not None:
                    members_by_id[entity.entity_id] = row
            if not members_by_id:
                continue
            objects.append(
                cls._object_from_members(
                    tuple(members_by_id.values()),
                    identity_seed=item.canonical_id,
                    primary_entity_id=item.primary_entity.entity_id,
                    display_name=item.display_name,
                    kind="camera",
                    object_role="PHYSICAL",
                    evidence=item.evidence,
                )
            )

        physical = cls._disambiguate_display_names(
            sorted(
                (item for item in objects if item.object_role == "PHYSICAL"),
                key=lambda item: ((item.area_name or "").casefold(), item.display_name.casefold()),
            )
        )
        virtual = cls._disambiguate_display_names(
            sorted(
                (item for item in objects if item.object_role != "PHYSICAL"),
                key=lambda item: ((item.area_name or "").casefold(), item.display_name.casefold()),
            )
        )
        return SemanticHomeGraph(
            physical_objects=physical,
            virtual_targets=virtual,
            capabilities=cls._capability_nodes((*physical, *virtual)),
        )

    @classmethod
    def _inventory_from(cls, rows: Sequence[Mapping[str, Any]]) -> tuple[CanonicalHomeObject, ...]:
        return cls._graph_from(rows).physical_objects

    async def graph(self) -> SemanticHomeGraph:
        started = time.monotonic()
        result = self._graph_from(await self._state_loader())
        schemas = await self._service_schemas()
        if schemas:
            result = SemanticHomeGraph(
                physical_objects=tuple(
                    self._enrich_item(item, schemas) for item in result.physical_objects
                ),
                virtual_targets=tuple(
                    self._enrich_item(item, schemas) for item in result.virtual_targets
                ),
                capabilities=tuple(
                    self._enrich_item(item, schemas) for item in result.capabilities
                ),
            )
        runtime_metrics.observe("home_semantic_inventory_ms", (time.monotonic() - started) * 1000)
        return result

    async def inventory(self) -> tuple[CanonicalHomeObject, ...]:
        return (await self.graph()).physical_objects

    @staticmethod
    def _score_values(values: Sequence[str], concepts: Sequence[str]) -> float:
        phrases = [_normalise(value) for value in concepts if _normalise(value)]
        if not phrases:
            return 1.0
        normalised_values = {_normalise(value) for value in values if _normalise(value)}
        value_tokens = (
            set().union(*(_tokens(value) for value in normalised_values))
            if normalised_values
            else set()
        )
        best = 0.0
        for phrase in phrases:
            phrase_tokens = _tokens(phrase)
            if phrase in normalised_values:
                best = max(best, 1.0)
            elif any(f" {phrase} " in f" {value} " for value in normalised_values):
                best = max(best, 0.9)
            elif phrase_tokens:
                coverage = len(phrase_tokens & value_tokens) / len(phrase_tokens)
                best = max(best, 0.78 * coverage)
        return best

    @classmethod
    def _score(cls, item: CanonicalHomeObject, concepts: Sequence[str]) -> float:
        return cls._score_values(item.aliases, concepts)

    @classmethod
    def _structural_score(cls, item: CanonicalHomeObject, concepts: Sequence[str]) -> float:
        values = {
            item.kind,
            item.capability_kind or "",
            *item.domains,
            *(str(entity.get("device_class") or "") for entity in item.entities),
        }
        return cls._score_values(tuple(values), concepts)

    @staticmethod
    def _observation_only(item: CanonicalHomeObject) -> bool:
        roles = {role for _entity_id, role in item.entity_roles}
        return bool(roles) and roles <= {"OBSERVATION", "MEASUREMENT"}

    @staticmethod
    def _context_projection(
        items: Sequence[CanonicalHomeObject], filters: Mapping[str, Any]
    ) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "objects": [
                {
                    "reference_id": item.canonical_id,
                    "object_type": (
                        "capability"
                        if item.capability_kind
                        else "device"
                        if item.object_role == "PHYSICAL"
                        else "virtual_target"
                    ),
                    "display_name": item.display_name,
                    "source": "home_assistant_canonical_inventory",
                    "canonical_id": item.canonical_id,
                    "provider": "home_assistant",
                    "capability": "homeassistant.semantic",
                    "evidence_status": "verified",
                    "freshness_seconds": 30,
                    "immutable": False,
                    "metadata": item.compact(),
                    "aliases": list(item.aliases),
                }
                for item in items
            ],
            "result_set": {
                "object_refs": [item.canonical_id for item in items],
                "ordering": "semantic_confidence",
                "observed_at": observed_at,
                "filters": dict(filters),
            },
        }

    async def search(
        self,
        *,
        query: str,
        semantic_terms: Sequence[str] = (),
        area_id: str | None = None,
        capability: str | None = None,
        domain: str | None = None,
        state: str | None = None,
        inventory_kind: str = "PHYSICAL_DEVICE",
        include_virtual: bool = False,
        capability_role_filter: str = "ANY",
        parent_purpose: str | None = None,
        reference_dependency: str = "NONE",
        include_diagnostics: bool = False,
        aggregation: str = "LIST",
        operation: str = "QUERY",
        requested_action: str | None = None,
        requested_parameter: str | None = None,
        parameter_mode: str = "NONE",
        parameter_direction: str = "NONE",
        parameter_value: float | str | None = None,
        parameter_unit: str | None = None,
        target_selection: str = "SINGLE",
        limit: int = 12,
        restrict_ids: Sequence[str] = (),
        principal_id: str = "",
        conversation_id: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        del include_diagnostics  # Raw members are intentionally exposed only by inspect().
        started = time.monotonic()
        wanted_capability = _normalise(capability).replace(" ", "_") or None
        wanted_domain = _normalise(domain).replace(" ", "_") or None
        action = _normalise(requested_action).replace(" ", "_") or None
        wanted_parameter = _normalise(requested_parameter) or None
        parameter_mode = str(parameter_mode or "NONE").upper()
        parameter_direction = str(parameter_direction or "NONE").upper()
        target_selection = str(target_selection or "SINGLE").upper()
        role_filter = str(capability_role_filter or "ANY").upper()
        wanted_parent_purpose = _normalise(parent_purpose).replace(" ", "_") or None
        dependency = str(reference_dependency or "NONE").upper()
        if role_filter not in _CAPABILITY_ROLE_FILTERS:
            raise ValueError("Unsupported capability role filter")
        if inventory_kind.upper() != "CAPABILITY" and role_filter != "ANY":
            raise ValueError("Capability role filters require capability inventory")
        if inventory_kind.upper() != "CAPABILITY" and wanted_parent_purpose:
            raise ValueError("Parent-purpose filters require capability inventory")
        if parameter_mode not in {"NONE", "ABSOLUTE", "RELATIVE"}:
            raise ValueError("Unsupported parameter mode")
        if parameter_direction not in {"NONE", "INCREASE", "DECREASE"}:
            raise ValueError("Unsupported parameter direction")
        if target_selection not in {"SINGLE", "ALL_MATCHES"}:
            raise ValueError("Unsupported control target selection")
        if operation.upper() == "CONTROL":
            if bool(action) == bool(wanted_parameter):
                raise ValueError(
                    "Control discovery requires exactly one discrete action or parameter"
                )
            if action:
                if action not in _ACTION_CAPABILITIES:
                    raise ValueError(
                        "A supported requested action is required for control discovery"
                    )
                action_capability = _ACTION_CAPABILITIES[action]
                if wanted_capability and wanted_capability != action_capability:
                    raise ValueError("Requested action and required capability disagree")
                wanted_capability = action_capability
                if parameter_mode != "NONE" or parameter_value is not None:
                    raise ValueError("A discrete action cannot include a parameter value")
            else:
                if parameter_mode not in {"ABSOLUTE", "RELATIVE"}:
                    raise ValueError("A parameter control requires absolute or relative mode")
                if parameter_mode == "ABSOLUTE" and parameter_value is None:
                    raise ValueError("An absolute parameter control requires a value")
        elif parameter_mode not in {"NONE", "ABSOLUTE"} or parameter_direction != "NONE":
            raise ValueError("Read-only parameter discovery cannot request a relative change")

        allowed_ids = {str(value) for value in restrict_ids if str(value)}
        inventory = (await self.graph()).inventory(
            inventory_kind,
            include_virtual=include_virtual,
        )
        scope_tokens: set[str] = set()
        if area_id:
            scope_tokens.update(_tokens(area_id))
            for item in inventory:
                if item.area_id == area_id:
                    scope_tokens.update(_tokens(item.area_name))

        # Area is a structural filter, not target evidence. Removing its words
        # prevents every object in a requested room from appearing to match the
        # semantic concept merely because its display name includes that room.
        structural_concepts = {
            normalised
            for value in (wanted_domain, wanted_capability, action, state)
            if (normalised := _normalise(value))
        }
        structural_concepts.update(_normalise(value) for value in _CANONICAL_OBJECT_DOMAINS)
        concept_list: list[str] = []
        for index, value in enumerate((query, *semantic_terms)):
            cleaned = " ".join(
                token for token in _normalise(value).split() if token not in scope_tokens
            )
            if not cleaned:
                continue
            # The first value is the free semantic target. Later values are model-provided
            # equivalents, so structural filters such as a domain or requested capability
            # must not be allowed to broaden the semantic match to every eligible object.
            if index > 0 and cleaned in structural_concepts:
                continue
            concept_list.append(cleaned)
        inventory_tokens = _tokens(inventory_kind)

        def inventory_label(value: str) -> bool:
            stems = {
                token[:-1] if len(token) > 3 and token.endswith("s") else token
                for token in _normalise(value).split()
            }
            return bool(stems) and stems <= inventory_tokens

        broad_target = bool(
            concept_list
            and (concept_list[0] in {"*", "any", "all"} or inventory_label(concept_list[0]))
        )
        concepts = (
            ()
            if broad_target
            else tuple(
                value
                for value in concept_list
                if value not in {"*", "any", "all"} and not inventory_label(value)
            )
        )
        eligible: list[CanonicalHomeObject] = []
        for item in inventory:
            if allowed_ids and item.canonical_id not in allowed_ids:
                continue
            if area_id and item.area_id != area_id:
                continue
            if wanted_domain:
                domain_members = tuple(
                    entity
                    for entity in item.entities
                    if _normalise(entity.get("domain")).replace(" ", "_") == wanted_domain
                )
                if not domain_members:
                    continue
                if inventory_kind.upper() == "PHYSICAL_DEVICE" and all(
                    _entity_role(entity) in _NON_IDENTITY_ENTITY_ROLES for entity in domain_members
                ):
                    continue
            if wanted_capability and wanted_capability not in item.capabilities:
                continue
            if (
                wanted_parameter
                and operation.upper() != "CONTROL"
                and match_parameter(item.parameterized_capabilities, wanted_parameter) is None
            ):
                continue
            if state and not any(_matches_state(entity, state) for entity in item.entities):
                continue
            if role_filter == "PRIMARY_ONLY" and item.capability_role != "PRIMARY":
                continue
            if role_filter == "AUXILIARY_ONLY" and item.capability_role != "AUXILIARY":
                continue
            if role_filter == "PRIMARY_OR_AUXILIARY" and item.capability_role not in {
                "PRIMARY",
                "AUXILIARY",
            }:
                continue
            if (
                wanted_parent_purpose
                and self._score_values(
                    item.primary_purposes,
                    (wanted_parent_purpose,),
                )
                < 0.65
            ):
                continue
            eligible.append(item)

        ranked: list[tuple[float, CanonicalHomeObject]] = []
        for item in eligible:
            if not concepts:
                score = 1.0
            else:
                scores: list[float] = []
                for concept in concepts:
                    structural_score = self._structural_score(item, (concept,))
                    alias_score = (
                        0.0 if self._observation_only(item) else self._score(item, (concept,))
                    )
                    candidate_score = max(structural_score, alias_score)
                    scores.append(candidate_score)
                score = max(scores, default=0.0)
            if score <= 0:
                continue
            ranked.append((score, item))
        ranked.sort(
            key=lambda pair: (
                pair[0],
                pair[1].available,
                pair[1].display_name.casefold(),
            ),
            reverse=True,
        )
        safe_limit = max(1, min(int(limit), 50))
        top_score = ranked[0][0] if ranked else 0.0
        plausible_pairs = [pair for pair in ranked if pair[0] >= max(0.3, top_score - 0.12)]
        selected_pairs = plausible_pairs[:safe_limit]
        selected = [item for _, item in selected_pairs]
        resolution = "zero" if not selected else "unique" if len(selected) == 1 else "ambiguous"
        filters = {
            "query": query,
            "semantic_terms": list(semantic_terms),
            "area_id": area_id,
            "capability": wanted_capability,
            "domain": wanted_domain,
            "state": state,
            "inventory_kind": inventory_kind.upper(),
            "capability_role_filter": role_filter,
            "parent_purpose": wanted_parent_purpose,
            "reference_dependency": dependency,
            "include_virtual": include_virtual,
            "aggregation": aggregation.upper(),
            "operation": operation.upper(),
            "requested_parameter": wanted_parameter,
            "parameter_mode": parameter_mode,
            "parameter_direction": parameter_direction,
            "parameter_value": parameter_value,
            "parameter_unit": parameter_unit,
            "target_selection": target_selection,
        }
        result: dict[str, Any] = {
            "success": True,
            "inventory_kind": inventory_kind.upper(),
            "include_virtual": include_virtual,
            "capability_role_filter": role_filter,
            "parent_purpose": wanted_parent_purpose,
            "reference_dependency": dependency,
            "operation": operation.upper(),
            "aggregation": aggregation.upper(),
            "query": query,
            "requested_parameter": wanted_parameter,
            "parameter_mode": parameter_mode,
            "parameter_direction": parameter_direction,
            "target_selection": target_selection,
            "count": len(selected),
            "resolution": resolution,
            "clarification_required": (
                operation.upper() == "CONTROL"
                and resolution == "ambiguous"
                and target_selection == "SINGLE"
            ),
            "items": [item.compact(confidence=score) for score, item in selected_pairs],
            "complete": len(plausible_pairs) <= safe_limit,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "context_projection": self._context_projection(selected, filters),
        }
        actionable_selection = bool(selected) and (
            resolution == "unique" or target_selection == "ALL_MATCHES"
        )
        if operation.upper() == "CONTROL" and actionable_selection:
            handle = uuid.uuid4().hex
            self._plans[handle] = _ActionPlan(
                handle=handle,
                principal_id=principal_id,
                conversation_id=conversation_id,
                request_id=request_id,
                action=action,
                requested_parameter=wanted_parameter,
                parameter_mode=parameter_mode,
                parameter_direction=parameter_direction,
                parameter_value=parameter_value,
                parameter_unit=parameter_unit,
                canonical_ids=frozenset(item.canonical_id for item in selected),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.PLAN_TTL_SECONDS),
            )
            result["action_plan"] = {
                "handle": handle,
                "action": action,
                "parameter": wanted_parameter,
                "candidate_ids": [item.canonical_id for item in selected],
                "expires_in_seconds": self.PLAN_TTL_SECONDS,
            }
        runtime_metrics.observe("home_semantic_search_ms", (time.monotonic() - started) * 1000)
        return result

    async def inspect(self, canonical_id: str) -> dict[str, Any]:
        item = next(
            (
                value
                for value in (await self.graph()).all_items
                if value.canonical_id == canonical_id
            ),
            None,
        )
        if item is None:
            raise ValueError("The canonical home item no longer exists")
        return {
            "success": True,
            "item": item.inspected(),
            "context_projection": self._context_projection([item], {"operation": "INSPECT"}),
        }

    @staticmethod
    def _action_entity(item: CanonicalHomeObject, action: str) -> Mapping[str, Any] | None:
        candidates = [entity for entity in item.entities if action in _capabilities(entity)]
        return min(candidates, key=HomeSemanticEngine._entity_rank) if candidates else None

    @staticmethod
    def _action_satisfied(action: str, entity: Mapping[str, Any]) -> bool | None:
        state = str(entity.get("state") or "unknown").casefold()
        attributes = entity.get("attributes")
        attrs = attributes if isinstance(attributes, Mapping) else {}
        if state in _UNKNOWN_STATES:
            return None
        if action == "turn_on":
            return _effective_state(entity) == "on"
        if action == "turn_off":
            return state == "off"
        if action == "play":
            return state == "playing"
        if action == "pause":
            return state == "paused"
        if action == "stop":
            return state in {"idle", "off", "standby", "stopped"}
        if action in {"mute", "unmute"}:
            muted = attrs.get("is_volume_muted")
            return muted is (action == "mute") if isinstance(muted, bool) else None
        return False

    async def execute(
        self,
        *,
        handle: str,
        canonical_ids: Sequence[str],
        action: str,
        principal_id: str,
        conversation_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        plan = self._plans.pop(str(handle or ""), None)
        normalised_action = _normalise(action).replace(" ", "_")
        selected_ids = tuple(dict.fromkeys(str(value) for value in canonical_ids if str(value)))
        now = datetime.now(timezone.utc)
        if plan is None or plan.expires_at <= now:
            raise ValueError("The grounded home action plan is missing or expired")
        if (
            plan.principal_id != principal_id
            or plan.conversation_id != conversation_id
            or plan.request_id != request_id
            or plan.action != normalised_action
        ):
            raise ValueError("The grounded home action plan does not authorize this request")
        if not selected_ids or not set(selected_ids) <= plan.canonical_ids:
            raise ValueError("The requested canonical target was not in the grounded action plan")

        inventory = {item.canonical_id: item for item in (await self.graph()).all_items}
        selected: list[CanonicalHomeObject] = []
        paths: list[tuple[CanonicalHomeObject, Mapping[str, Any]]] = []
        for canonical_id in selected_ids:
            item = inventory.get(canonical_id)
            if item is None:
                raise ValueError("A canonical home target no longer exists")
            entity = self._action_entity(item, normalised_action)
            if entity is None:
                raise ValueError("A canonical home target no longer supports that action")
            selected.append(item)
            paths.append((item, entity))

        already: list[CanonicalHomeObject] = []
        pending: list[tuple[CanonicalHomeObject, Mapping[str, Any]]] = []
        unknown: list[CanonicalHomeObject] = []
        for item, entity in paths:
            satisfied = self._action_satisfied(normalised_action, entity)
            if satisfied is True:
                already.append(item)
            elif satisfied is None:
                unknown.append(item)
            else:
                pending.append((item, entity))
        if unknown:
            return self._action_result(
                selected,
                action=normalised_action,
                status="UNKNOWN",
                verified=False,
                already=False,
                changed=False,
                message=(
                    "I couldn’t verify the current state of "
                    + ", ".join(item.display_name for item in unknown)
                    + ", so I didn’t claim the action was complete."
                ),
            )
        if not pending:
            return self._action_result(
                selected,
                action=normalised_action,
                status="VERIFIED",
                verified=True,
                already=True,
                changed=False,
                message=(
                    ", ".join(item.display_name for item in already)
                    + " already satisfies the requested state."
                ),
            )

        service = _ACTION_SERVICES[normalised_action]
        for _item, entity in pending:
            service_data = (
                {"is_volume_muted": normalised_action == "mute"}
                if normalised_action in {"mute", "unmute"}
                else None
            )
            await self._service_caller(
                domain=str(entity.get("domain") or ""),
                service=service,
                entity_ids=[str(entity.get("entity_id") or "")],
                service_data=service_data,
            )

        verified_ids: set[str] = set()
        for delay in self.VERIFY_DELAYS:
            await asyncio.sleep(delay)
            refreshed = {item.canonical_id: item for item in (await self.graph()).all_items}
            for item, _entity in pending:
                current = refreshed.get(item.canonical_id)
                if current is None:
                    continue
                path = self._action_entity(current, normalised_action)
                if path is not None and self._action_satisfied(normalised_action, path) is True:
                    verified_ids.add(item.canonical_id)
            if len(verified_ids) == len(pending):
                break

        failed = [item for item, _entity in pending if item.canonical_id not in verified_ids]
        status = "VERIFIED" if not failed else "PARTIAL" if verified_ids else "FAILED"
        if not failed:
            message = (
                ", ".join(item.display_name for item in selected)
                + " now satisfies the requested state."
            )
        else:
            message = (
                f"Home Assistant verified {len(verified_ids)} of {len(pending)} changes; "
                f"{len(failed)} did not confirm the requested state."
            )
        return self._action_result(
            selected,
            action=normalised_action,
            status=status,
            verified=not failed,
            already=False,
            changed=bool(verified_ids),
            message=message,
        )

    @staticmethod
    def _parameter_value_text(value: float | str | None, unit: str | None) -> str:
        if value is None:
            return "an unknown value"
        if isinstance(value, float) and value.is_integer():
            rendered = str(int(value))
        else:
            rendered = str(value)
        return f"{rendered}{unit or ''}"

    @staticmethod
    def _parameter_verified(
        capability: ParameterizedCapability,
        expected: float | str,
        actual: float | str | None,
    ) -> bool:
        if actual is None:
            return False
        if isinstance(expected, str):
            return isinstance(actual, str) and expected.casefold() == actual.casefold()
        actual_number = float(actual) if isinstance(actual, (int, float)) else None
        return actual_number is not None and abs(
            actual_number - expected
        ) <= verification_tolerance(capability)

    async def execute_parameter(
        self,
        *,
        handle: str,
        canonical_ids: Sequence[str],
        principal_id: str,
        conversation_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Execute an opaque, grounded parameter plan and verify live read-back."""

        lookup_started = time.monotonic()
        plan = self._plans.pop(str(handle or ""), None)
        selected_ids = tuple(dict.fromkeys(str(value) for value in canonical_ids if str(value)))
        now = datetime.now(timezone.utc)
        if plan is None or plan.expires_at <= now:
            raise ValueError("The grounded home parameter plan is missing or expired")
        if (
            plan.principal_id != principal_id
            or plan.conversation_id != conversation_id
            or plan.request_id != request_id
            or plan.action is not None
            or not plan.requested_parameter
        ):
            raise ValueError("The grounded home parameter plan does not authorize this request")
        if not selected_ids or set(selected_ids) != plan.canonical_ids:
            raise ValueError("The exact frozen parameter target set is required")

        inventory = {item.canonical_id: item for item in (await self.graph()).all_items}
        selected: list[CanonicalHomeObject] = []
        outcomes: list[dict[str, Any]] = []
        pending: list[
            tuple[
                CanonicalHomeObject,
                ParameterizedCapability,
                Any,
            ]
        ] = []
        for canonical_id in selected_ids:
            item = inventory.get(canonical_id)
            if item is None:
                raise ValueError("A canonical home target no longer exists")
            selected.append(item)
            capability = match_parameter(item.parameterized_capabilities, plan.requested_parameter)
            if capability is None:
                outcomes.append(
                    {
                        "canonical_id": item.canonical_id,
                        "display_name": item.display_name,
                        "status": "UNSUPPORTED",
                        "verified": False,
                        "message": (
                            f"{item.display_name} does not expose a supported "
                            f"{plan.requested_parameter} control."
                        ),
                    }
                )
                continue
            if not capability.available:
                outcomes.append(
                    {
                        "canonical_id": item.canonical_id,
                        "display_name": item.display_name,
                        "status": "FAILED",
                        "verified": False,
                        "message": f"{item.display_name} is currently unavailable.",
                    }
                )
                continue
            try:
                validation_started = time.monotonic()
                resolved = resolve_parameter_value(
                    capability,
                    mode=plan.parameter_mode,
                    direction=plan.parameter_direction,
                    requested_value=plan.parameter_value,
                    requested_unit=plan.parameter_unit,
                )
                runtime_metrics.observe(
                    "home_parameter_validation_ms",
                    (time.monotonic() - validation_started) * 1000,
                )
            except ValueError as exc:
                outcomes.append(
                    {
                        "canonical_id": item.canonical_id,
                        "display_name": item.display_name,
                        "status": "FAILED",
                        "verified": False,
                        "message": str(exc),
                    }
                )
                continue
            if self._parameter_verified(capability, resolved.semantic_value, resolved.before_value):
                outcomes.append(
                    {
                        "canonical_id": item.canonical_id,
                        "display_name": item.display_name,
                        "status": "VERIFIED",
                        "verified": True,
                        "already_in_target_state": True,
                        "changed": False,
                        "parameter": capability.parameter,
                        "requested_value": resolved.semantic_value,
                        "resolved_value": resolved.service_value,
                        "before_value": resolved.before_value,
                        "after_value": resolved.before_value,
                        "unit": capability.unit,
                    }
                )
                continue
            pending.append((item, capability, resolved))

        runtime_metrics.observe(
            "home_parameter_lookup_ms", (time.monotonic() - lookup_started) * 1000
        )

        for item, capability, resolved in pending:
            service_field = capability.field
            service_value = resolved.service_value
            if (
                plan.parameter_mode == "RELATIVE"
                and capability.relative_field
                and isinstance(resolved.before_value, (int, float))
                and isinstance(resolved.semantic_value, (int, float))
            ):
                service_field = capability.relative_field
                service_value = round(
                    (resolved.semantic_value - float(resolved.before_value))
                    * capability.semantic_scale,
                    6,
                )
            try:
                execution_started = time.monotonic()
                await self._service_caller(
                    domain=capability.domain,
                    service=capability.service,
                    entity_ids=[capability.entity_id],
                    service_data={service_field: service_value},
                )
                runtime_metrics.observe(
                    "home_parameter_execution_ms",
                    (time.monotonic() - execution_started) * 1000,
                )
            except Exception as exc:  # Provider failures become truthful per-target receipts.
                outcomes.append(
                    {
                        "canonical_id": item.canonical_id,
                        "display_name": item.display_name,
                        "status": "FAILED",
                        "verified": False,
                        "message": f"Home Assistant rejected the parameter change: {exc}",
                    }
                )
                continue

            verification_started = time.monotonic()
            actual: float | str | None = None
            for delay in self.VERIFY_DELAYS:
                await asyncio.sleep(delay)
                rows = await self._state_loader()
                current = next(
                    (
                        row
                        for row in rows
                        if str(row.get("entity_id") or "") == capability.entity_id
                    ),
                    None,
                )
                if current is None:
                    continue
                actual = read_parameter(capability, current)
                if self._parameter_verified(capability, resolved.semantic_value, actual):
                    break
            runtime_metrics.observe(
                "home_parameter_verification_ms",
                (time.monotonic() - verification_started) * 1000,
            )
            verified = self._parameter_verified(capability, resolved.semantic_value, actual)
            outcomes.append(
                {
                    "canonical_id": item.canonical_id,
                    "display_name": item.display_name,
                    "status": "VERIFIED" if verified else "UNKNOWN",
                    "verified": verified,
                    "already_in_target_state": False,
                    "changed": verified,
                    "command_sent": True,
                    "parameter": capability.parameter,
                    "requested_value": resolved.semantic_value,
                    "resolved_value": service_value,
                    "resolved_field": service_field,
                    "before_value": resolved.before_value,
                    "after_value": actual,
                    "unit": capability.unit,
                    "normalized_to_step": resolved.normalized_to_step,
                    "qualitative_delta": resolved.qualitative_delta,
                }
            )

        verified_outcomes = [value for value in outcomes if value.get("verified") is True]
        unknown_outcomes = [value for value in outcomes if value.get("status") == "UNKNOWN"]
        if len(verified_outcomes) == len(outcomes):
            status = "VERIFIED"
        elif verified_outcomes:
            status = "PARTIAL"
        elif unknown_outcomes:
            status = "UNKNOWN"
        else:
            status = "FAILED"

        requested = next(
            (value.get("requested_value") for value in outcomes if "requested_value" in value),
            plan.parameter_value,
        )
        unit = next((value.get("unit") for value in outcomes if value.get("unit")), None)
        names = ", ".join(item.display_name for item in selected)
        if status == "VERIFIED":
            if all(value.get("already_in_target_state") for value in outcomes):
                message = (
                    f"{names} already has {plan.requested_parameter} at "
                    f"{self._parameter_value_text(requested, unit)}."
                )
            else:
                message = (
                    f"Set {plan.requested_parameter} for {names} to "
                    f"{self._parameter_value_text(requested, unit)} and verified it."
                )
        elif status == "PARTIAL":
            message = (
                f"Verified {len(verified_outcomes)} of {len(outcomes)} parameter changes; "
                f"{len(outcomes) - len(verified_outcomes)} could not be completed."
            )
        elif status == "UNKNOWN":
            message = "Home Assistant accepted the change, but I could not verify the final value."
        else:
            messages = [str(value.get("message")) for value in outcomes if value.get("message")]
            message = (
                messages[0] if len(messages) == 1 else "I could not apply that parameter change."
            )

        metadata = {
            "action": "set_parameter",
            "parameter": plan.requested_parameter,
            "parameter_mode": plan.parameter_mode,
            "parameter_direction": plan.parameter_direction,
            "requested_value": plan.parameter_value,
            "requested_unit": plan.parameter_unit,
            "verification_status": status,
            "verified": status == "VERIFIED",
            "already_in_target_state": bool(outcomes)
            and all(value.get("already_in_target_state") for value in outcomes),
            "changed": any(value.get("changed") for value in outcomes),
        }
        projection = self._context_projection(selected, {"operation": "CONTROL", **metadata})
        for raw in projection["objects"]:
            raw["metadata"] = {**raw["metadata"], **metadata}
        return {
            "success": status == "VERIFIED",
            **metadata,
            "command_sent": any(value.get("command_sent") for value in outcomes),
            "outcome_unknown": status == "UNKNOWN",
            "canonical_ids": [item.canonical_id for item in selected],
            "items": [item.compact() for item in selected],
            "outcomes": outcomes,
            "response_message": message,
            "context_projection": projection,
        }

    @classmethod
    def _action_result(
        cls,
        items: Sequence[CanonicalHomeObject],
        *,
        action: str,
        status: str,
        verified: bool,
        already: bool,
        changed: bool,
        message: str,
    ) -> dict[str, Any]:
        metadata = {
            "action": action,
            "verification_status": status,
            "verified": verified,
            "already_in_target_state": already,
            "changed": changed,
        }
        projection = cls._context_projection(items, {"operation": "CONTROL", **metadata})
        for raw in projection["objects"]:
            raw["metadata"] = {**raw["metadata"], **metadata}
        return {
            "success": verified,
            **metadata,
            "canonical_ids": [item.canonical_id for item in items],
            "items": [item.compact() for item in items],
            "response_message": message,
            "context_projection": projection,
        }


__all__ = ["CanonicalHomeObject", "HomeSemanticEngine", "SemanticHomeGraph"]
