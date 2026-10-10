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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import time
from typing import Any
import uuid

from app.home_canonicalization import physical_identity
from app.home_intelligence import GroundedHomeEntity
from app.runtime_observability import runtime_metrics


_OBSERVATION_DOMAINS = frozenset({"binary_sensor", "sensor"})
_CANONICAL_OBJECT_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "binary_sensor",
        "camera",
        "climate",
        "cover",
        "device_tracker",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "person",
        "remote",
        "sensor",
        "siren",
        "switch",
        "vacuum",
        "valve",
        "water_heater",
    }
)
_NON_USER_ENTITY_CATEGORIES = frozenset({"config", "diagnostic"})
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
                for entity in self.entities
            ],
        }


@dataclass(frozen=True, slots=True)
class _ActionPlan:
    handle: str
    principal_id: str
    conversation_id: str
    request_id: str
    action: str
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
    ) -> None:
        self._state_loader = state_loader
        self._service_caller = service_caller
        self._plans: dict[str, _ActionPlan] = {}

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
    def _inventory_from(cls, rows: Sequence[Mapping[str, Any]]) -> tuple[CanonicalHomeObject, ...]:
        enabled = [
            dict(item)
            for item in rows
            if item.get("entity_id")
            and str(item.get("domain") or "") in _CANONICAL_OBJECT_DOMAINS
            and str(item.get("entity_category") or "").casefold() != "config"
        ]
        objects: list[CanonicalHomeObject] = []
        for members in cls._groups(enabled):
            ordered = tuple(sorted(members, key=cls._entity_rank))
            primary = ordered[0]
            identities = sorted({value for item in ordered for value in cls._identity_tokens(item)})
            digest = hashlib.sha256("\n".join(identities).encode()).hexdigest()[:24]
            canonical_id = f"home:{digest}"
            display_name = cls._display_name(ordered, primary)
            capabilities = tuple(
                sorted({value for item in ordered for value in _capabilities(item)})
            )
            domains = tuple(
                sorted({str(item.get("domain") or "") for item in ordered if item.get("domain")})
            )
            kind = str(primary.get("device_class") or primary.get("domain") or "device").strip()
            physical_ids = tuple(
                sorted({str(item.get("device_id")) for item in ordered if item.get("device_id")})
            )
            aliases: set[str] = {display_name, kind, *domains}
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
                # Names of observation entities describe what was observed, not
                # the physical thing. They are available only through inspection.
                if str(item.get("domain") or "") not in _OBSERVATION_DOMAINS or not physical_ids:
                    aliases.update(
                        str(item.get(key) or "").strip()
                        for key in ("registry_name", "registry_original_name", "name")
                    )
            aliases.discard("")
            available = str(primary.get("state") or "").casefold() not in _UNKNOWN_STATES
            objects.append(
                CanonicalHomeObject(
                    canonical_id=canonical_id,
                    display_name=display_name,
                    area_id=str(primary.get("area_id") or "") or None,
                    area_name=str(primary.get("area_name") or "") or None,
                    kind=kind,
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
                    evidence=tuple(identities[:12]),
                )
            )
        return tuple(
            sorted(
                objects,
                key=lambda item: ((item.area_name or "").casefold(), item.display_name.casefold()),
            )
        )

    async def inventory(self) -> tuple[CanonicalHomeObject, ...]:
        started = time.monotonic()
        result = self._inventory_from(await self._state_loader())
        runtime_metrics.observe("home_semantic_inventory_ms", (time.monotonic() - started) * 1000)
        return result

    @staticmethod
    def _score(item: CanonicalHomeObject, concepts: Sequence[str]) -> float:
        phrases = [_normalise(value) for value in concepts if _normalise(value)]
        if not phrases:
            return 1.0
        alias_values = {_normalise(value) for value in item.aliases}
        alias_tokens = (
            set().union(*(_tokens(value) for value in alias_values)) if alias_values else set()
        )
        best = 0.0
        for phrase in phrases:
            phrase_tokens = _tokens(phrase)
            if phrase in alias_values:
                best = max(best, 1.0)
            elif any(
                phrase in alias or alias in phrase for alias in alias_values if len(alias) >= 3
            ):
                best = max(best, 0.9)
            elif phrase_tokens:
                coverage = len(phrase_tokens & alias_tokens) / len(phrase_tokens)
                best = max(best, 0.78 * coverage)
        return best

    @staticmethod
    def _context_projection(
        items: Sequence[CanonicalHomeObject], filters: Mapping[str, Any]
    ) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "objects": [
                {
                    "reference_id": item.canonical_id,
                    "object_type": "device",
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
        include_diagnostics: bool = False,
        aggregation: str = "LIST",
        operation: str = "QUERY",
        requested_action: str | None = None,
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
        if operation.upper() == "CONTROL":
            if action not in _ACTION_CAPABILITIES:
                raise ValueError("A supported requested action is required for control discovery")
            action_capability = _ACTION_CAPABILITIES[action]
            if wanted_capability and wanted_capability != action_capability:
                raise ValueError("Requested action and required capability disagree")
            wanted_capability = action_capability

        allowed_ids = {str(value) for value in restrict_ids if str(value)}
        inventory = await self.inventory()
        scope_tokens: set[str] = set()
        if area_id:
            scope_tokens.update(_tokens(area_id))
            for item in inventory:
                if item.area_id == area_id:
                    scope_tokens.update(_tokens(item.area_name))

        # Area is a structural filter, not target evidence. Removing its words
        # prevents every object in a requested room from appearing to match the
        # semantic concept merely because its display name includes that room.
        concepts = tuple(
            cleaned
            for value in (query, *semantic_terms)
            if (
                cleaned := " ".join(
                    token for token in _normalise(value).split() if token not in scope_tokens
                )
            )
        )
        eligible: list[CanonicalHomeObject] = []
        for item in inventory:
            if allowed_ids and item.canonical_id not in allowed_ids:
                continue
            if area_id and item.area_id != area_id:
                continue
            if wanted_domain and wanted_domain not in item.domains:
                continue
            if wanted_capability and wanted_capability not in item.capabilities:
                continue
            if state and not any(_matches_state(entity, state) for entity in item.entities):
                continue
            eligible.append(item)

        primary_concept = concepts[:1]
        expansion_concepts = concepts[1:]
        primary_has_matches = bool(
            primary_concept and any(self._score(item, primary_concept) > 0 for item in eligible)
        )
        ranked: list[tuple[float, CanonicalHomeObject]] = []
        for item in eligible:
            primary_score = self._score(item, primary_concept) if primary_concept else 0.0
            expansion_score = self._score(item, expansion_concepts) if expansion_concepts else 0.0
            # semantic_terms broaden genuine equivalence when the normalised
            # target has no lexical match. When query already grounds targets,
            # terms are supporting evidence and cannot let a broader structural
            # concept (for example a domain name) outrank the target itself.
            score = max(
                primary_score,
                expansion_score * (0.7 if primary_has_matches else 1.0),
            )
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
            "aggregation": aggregation.upper(),
            "operation": operation.upper(),
        }
        result: dict[str, Any] = {
            "success": True,
            "operation": operation.upper(),
            "aggregation": aggregation.upper(),
            "query": query,
            "count": len(selected),
            "resolution": resolution,
            "clarification_required": operation.upper() == "CONTROL" and resolution == "ambiguous",
            "items": [item.compact(confidence=score) for score, item in selected_pairs],
            "complete": len(plausible_pairs) <= safe_limit,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "context_projection": self._context_projection(selected, filters),
        }
        if operation.upper() == "CONTROL" and action and resolution == "unique":
            handle = uuid.uuid4().hex
            self._plans[handle] = _ActionPlan(
                handle=handle,
                principal_id=principal_id,
                conversation_id=conversation_id,
                request_id=request_id,
                action=action,
                canonical_ids=frozenset({selected[0].canonical_id}),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.PLAN_TTL_SECONDS),
            )
            result["action_plan"] = {
                "handle": handle,
                "action": action,
                "candidate_ids": [selected[0].canonical_id],
                "expires_in_seconds": self.PLAN_TTL_SECONDS,
            }
        runtime_metrics.observe("home_semantic_search_ms", (time.monotonic() - started) * 1000)
        return result

    async def inspect(self, canonical_id: str) -> dict[str, Any]:
        item = next(
            (value for value in await self.inventory() if value.canonical_id == canonical_id), None
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

        inventory = {item.canonical_id: item for item in await self.inventory()}
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
            refreshed = {item.canonical_id: item for item in await self.inventory()}
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


__all__ = ["CanonicalHomeObject", "HomeSemanticEngine"]
