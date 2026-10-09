"""Deterministic presentation canonicalization for HomeExperience.

This module does not own home state. It groups already-grounded Home Assistant
entities for presentation using registry device identity and shared device
connections. Raw entities remain attached as diagnostic sources.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence

from app.home_intelligence import GroundedHomeEntity


_TECHNICAL_NAME = re.compile(
    r"(?:^|[-_ ])(?:[0-9a-f]{10,}|[0-9a-f]{2}(?::[0-9a-f]{2}){5})(?:$|[-_ ])",
    re.I,
)
_CAMERA_VARIANT = re.compile(r"\b(?:snapshots?|clear|fluent|main|sub)\b", re.I)
_OBJECT_EVIDENCE = re.compile(
    r"\b(?:animal|backpack|cell phone|laptop|remote|suitcase|television|tv|vehicle|all)\b",
    re.I,
)


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"null", "none", "undefined"} else text


def normalise(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", clean_text(value).casefold()).split())


def technical_name(value: Any) -> bool:
    text = clean_text(value)
    return not text or bool(_TECHNICAL_NAME.search(text))


def physical_identity(entity: GroundedHomeEntity) -> str:
    """Return the strongest registry-backed identity available."""

    connections = tuple(
        sorted(
            f"{kind.casefold()}:{identifier.casefold()}"
            for kind, identifier in entity.device_connections
            if kind and identifier
        )
    )
    preferred = next((item for item in connections if item.startswith("mac:")), None)
    if preferred:
        return "connection:" + preferred
    if connections:
        return "connection:" + connections[0]
    if entity.device_id:
        return f"device:{entity.device_id}"
    return f"entity:{entity.entity_id}"


def friendly_name(entity: GroundedHomeEntity, category: str) -> str:
    candidates = (
        entity.registry_name,
        entity.device_name_by_user,
        entity.device_name,
        entity.name,
    )
    selected = next((clean_text(item) for item in candidates if not technical_name(item)), "")
    area = clean_text(entity.area_name)
    noun = {
        "camera": "Camera",
        "light": "Light",
        "media": "Media device",
        "device": "Device",
    }.get(category, category.replace("_", " ").title())
    selected = selected or f"{area} {noun}".strip() or noun
    if category == "camera":
        selected = _CAMERA_VARIANT.sub("", selected)
        selected = " ".join(selected.split())
        if "camera" not in selected.casefold():
            selected += " Camera"
    elif category == "light" and area and normalise(selected) == normalise(area):
        entity_name = clean_text(entity.name)
        selected = (
            entity_name
            if not technical_name(entity_name) and normalise(entity_name) != normalise(area)
            else f"{area} Light"
        )
    return selected


@dataclass(frozen=True, slots=True)
class CanonicalHomeItem:
    canonical_id: str
    physical_device_ids: tuple[str, ...]
    display_name: str
    area_id: str | None
    area_name: str | None
    category: str
    primary_entity: GroundedHomeEntity
    alternate_entities: tuple[GroundedHomeEntity, ...]
    diagnostic_entities: tuple[GroundedHomeEntity, ...]
    availability: str
    capabilities: tuple[str, ...]
    evidence: tuple[str, ...]
    presentation_priority: int

    @property
    def all_entities(self) -> tuple[GroundedHomeEntity, ...]:
        return (self.primary_entity, *self.alternate_entities, *self.diagnostic_entities)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "physical_device_ids": list(self.physical_device_ids),
            "primary_entity_id": self.primary_entity.entity_id,
            "alternate_entity_ids": [item.entity_id for item in self.alternate_entities],
            "diagnostic_entity_ids": [item.entity_id for item in self.diagnostic_entities],
            "platforms": sorted(
                {
                    clean_text(item.platform)
                    for item in self.all_entities
                    if clean_text(item.platform)
                }
            ),
            "identity_evidence": list(self.evidence),
        }


def _primary_rank(entity: GroundedHomeEntity, category: str) -> tuple[int, int, int, str]:
    available = 0 if entity.available and entity.state != "unavailable" else 1
    name = f"{entity.name} {entity.entity_id}".casefold()
    if category == "camera":
        variant = (
            0
            if "clear" in name or "main" in name
            else 1
            if "fluent" in name or "sub" in name
            else 3
            if "snapshot" in name
            else 2
        )
    else:
        variant = (
            0
            if category == "media" and entity.state in {"on", "playing", "paused", "buffering"}
            else 1
            if category == "media"
            else 0
        )
    explicit = 0 if clean_text(entity.registry_name) else 1
    return available, variant, explicit, entity.entity_id


def _availability(entities: Sequence[GroundedHomeEntity], category: str) -> str:
    available = [item for item in entities if item.available and item.state != "unavailable"]
    if not available:
        return "UNAVAILABLE"
    if len(available) != len(entities):
        return "PARTIAL"
    return "ONLINE" if category == "camera" else "AVAILABLE"


def canonicalize_domain(
    entities: Sequence[GroundedHomeEntity],
    *,
    domain: str,
    category: str,
    include_technical: bool = False,
) -> tuple[CanonicalHomeItem, ...]:
    groups: dict[str, list[GroundedHomeEntity]] = {}
    for entity in entities:
        if entity.domain != domain:
            continue
        if str(entity.entity_category or "").casefold() in {"config", "diagnostic"}:
            continue
        groups.setdefault(physical_identity(entity), []).append(entity)

    result: list[CanonicalHomeItem] = []
    for identity, members in groups.items():
        ordered = sorted(members, key=lambda item: _primary_rank(item, category))
        primary = ordered[0]
        technical = technical_name(primary.name) and technical_name(primary.device_name)
        if technical and not include_technical:
            continue
        alternates = tuple(ordered[1:])
        result.append(
            CanonicalHomeItem(
                canonical_id=f"{category}:{identity}",
                physical_device_ids=tuple(
                    sorted({item.device_id for item in ordered if item.device_id})
                ),
                display_name=friendly_name(primary, category),
                area_id=primary.area_id,
                area_name=primary.area_name,
                category=category,
                primary_entity=primary,
                alternate_entities=alternates,
                diagnostic_entities=(),
                availability=_availability(ordered, category),
                capabilities=tuple(sorted({item.domain for item in ordered})),
                evidence=(identity,),
                presentation_priority=100 if primary.available else 50,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: ((item.area_name or "").casefold(), item.display_name.casefold()),
        )
    )


def canonicalize_cameras(
    entities: Sequence[GroundedHomeEntity],
) -> tuple[CanonicalHomeItem, ...]:
    """Collapse registry-linked streams and attach analytical sources safely.

    A source with no physical connection and a gateway parent (for example an
    analytical/NVR camera surface) can become a diagnostic source of exactly
    one connected camera with the same grounded device label. This affects
    display only and never creates an action target.
    """

    items = list(canonicalize_domain(entities, domain="camera", category="camera"))
    strong = [item for item in items if item.primary_entity.device_connections]
    consumed: set[str] = set()
    replacements: dict[str, CanonicalHomeItem] = {}
    for weak in items:
        entity = weak.primary_entity
        if entity.device_connections or not entity.via_device_id:
            continue
        label = normalise(entity.device_name or entity.name)
        matches = [
            item
            for item in strong
            if normalise(
                item.primary_entity.device_name or item.display_name.removesuffix(" Camera")
            )
            == label
        ]
        if len(matches) != 1:
            continue
        target = replacements.get(matches[0].canonical_id, matches[0])
        replacements[target.canonical_id] = CanonicalHomeItem(
            canonical_id=target.canonical_id,
            physical_device_ids=tuple(
                sorted(set(target.physical_device_ids) | set(weak.physical_device_ids))
            ),
            display_name=target.display_name,
            area_id=target.area_id,
            area_name=target.area_name,
            category=target.category,
            primary_entity=target.primary_entity,
            alternate_entities=target.alternate_entities,
            diagnostic_entities=(*target.diagnostic_entities, *weak.all_entities),
            availability=target.availability,
            capabilities=target.capabilities,
            evidence=(*target.evidence, "unique_gateway_source_label_match"),
            presentation_priority=target.presentation_priority,
        )
        consumed.add(weak.canonical_id)
    return tuple(
        replacements.get(item.canonical_id, item)
        for item in items
        if item.canonical_id not in consumed
    )


def aggregate_area_ids(
    areas: Sequence[dict[str, Any] | Any],
    entities: Sequence[GroundedHomeEntity],
    cameras: Sequence[CanonicalHomeItem],
) -> frozenset[str]:
    """Classify explicit or structurally grounded aggregate/container areas."""

    explicit = {
        clean_text(area.get("area_id") or area.get("id"))
        for area in areas
        if isinstance(area, dict)
        and clean_text(area.get("presentation_kind")).casefold() in {"aggregate", "container"}
    }
    physical_labels = {
        normalise(item.primary_entity.device_name or item.display_name.removesuffix(" Camera"))
        for item in cameras
        if item.primary_entity.device_connections
    }
    by_area: dict[str, list[GroundedHomeEntity]] = {}
    for entity in entities:
        if entity.area_id:
            by_area.setdefault(entity.area_id, []).append(entity)
    inferred: set[str] = set()
    for area_id, members in by_area.items():
        weak_cameras = [
            item
            for item in members
            if item.domain == "camera" and item.via_device_id and not item.device_connections
        ]
        matches = sum(
            normalise(item.device_name or item.name) in physical_labels for item in weak_cameras
        )
        user_controls = [
            item
            for item in members
            if item.domain in {"light", "climate", "cover", "lock"}
            and item.available
            and not technical_name(item.name)
        ]
        if len(weak_cameras) >= 2 and matches >= 2 and not user_controls:
            inferred.add(area_id)
    return frozenset(explicit | inferred)


def occupancy_evidence_class(entity: GroundedHomeEntity) -> str:
    identity = f"{entity.name} {entity.entity_id.replace('_', ' ')}"
    device_class = clean_text(entity.device_class).casefold()
    if _OBJECT_EVIDENCE.search(identity):
        return "object_detection"
    if re.search(r"\bperson\b", identity, re.I):
        return "person_presence"
    if device_class in {"motion"} or re.search(r"\bmotion\b", identity, re.I):
        return "motion"
    if device_class in {"occupancy", "presence"}:
        return "presence"
    return "diagnostic_observation"


def iter_source_ids(items: Iterable[CanonicalHomeItem]) -> set[str]:
    return {entity.entity_id for item in items for entity in item.all_entities}


__all__ = [
    "CanonicalHomeItem",
    "aggregate_area_ids",
    "canonicalize_cameras",
    "canonicalize_domain",
    "clean_text",
    "friendly_name",
    "iter_source_ids",
    "normalise",
    "occupancy_evidence_class",
    "physical_identity",
    "technical_name",
]
