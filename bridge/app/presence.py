"""Authoritative, live Home Assistant presence evidence.

This module deliberately returns data rather than an explanation.  Callers may
format it for a user, but cannot turn a guessed tracker or friendly name into a
presence source.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol


class PresenceStateReader(Protocol):
    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]: ...

    async def person_configurations(self) -> list[dict[str, Any]]: ...


def _normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _public(entity: dict[str, Any]) -> dict[str, Any]:
    attributes = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    result = {
        key: entity.get(key)
        for key in (
            "entity_id",
            "name",
            "domain",
            "state",
            "available",
            "device_id",
            "device_name",
            "platform",
            "last_changed",
            "last_updated",
        )
    }
    result["attributes"] = {
        key: attributes[key]
        for key in ("source", "source_type", "gps_accuracy")
        if key in attributes
    }
    return result


class PresenceResolver:
    """Resolve people and trackers from one fresh HA state read."""

    def __init__(self, reader: PresenceStateReader) -> None:
        self.reader = reader

    async def inspect(self, reference: str) -> dict[str, Any]:
        entities = await self.reader.readable_entity_states(refresh=True)
        person_configurations: list[dict[str, Any]] = []
        person_graph_available = False
        configuration_reader = getattr(self.reader, "person_configurations", None)
        if configuration_reader is not None:
            try:
                raw_configurations = await configuration_reader()
                if not isinstance(raw_configurations, list) or not all(
                    isinstance(item, Mapping) for item in raw_configurations
                ):
                    raise TypeError("Home Assistant returned an invalid person graph")
                person_configurations = [dict(item) for item in raw_configurations]
                person_graph_available = True
            except Exception:
                # Live person state remains useful when HA does not grant access
                # to the storage-person graph. Never infer missing relationships.
                person_configurations = []
        people = [item for item in entities if item.get("domain") == "person"]
        query = _normalise(reference)
        exact = [
            item
            for item in people
            if query
            in {
                _normalise(item.get("name")),
                _normalise(item.get("entity_id")).replace("person ", ""),
            }
        ]
        if len(exact) != 1:
            candidates = [
                item
                for item in people
                if query
                and (
                    query in _normalise(item.get("name"))
                    or query in _normalise(item.get("entity_id"))
                )
            ]
            # "Aaron" may map to one live entity named "Aaron Hall".  That is
            # safe only when the live registry yields exactly one candidate.
            if len(candidates) == 1:
                exact = candidates
            else:
                return {
                    "success": False,
                    "resolution": "ambiguous" if len(candidates) > 1 else "not_found",
                    "reference": reference,
                    "candidates": [_public(item) for item in candidates],
                    "person": None,
                    "source": None,
                    "trackers": [],
                    "conflicts": [],
                }

        person = exact[0]
        attributes = person.get("attributes") if isinstance(person.get("attributes"), dict) else {}
        source_id = attributes.get("source")
        source_id = (
            str(source_id)
            if isinstance(source_id, str) and source_id.startswith("device_tracker.")
            else None
        )
        trackers = [item for item in entities if item.get("domain") == "device_tracker"]
        source = next((item for item in trackers if item.get("entity_id") == source_id), None)
        person_slug = str(person.get("entity_id") or "").split(".", 1)[-1]
        person_name = _normalise(person.get("name"))
        id_matches = [
            item
            for item in person_configurations
            if str(item.get("id") or "").strip() == person_slug
        ]
        name_matches = [
            item
            for item in person_configurations
            if person_name and _normalise(item.get("name")) == person_name
        ]
        person_config: dict[str, Any] | None = None
        person_configuration_resolution = "unavailable"
        if len(id_matches) == 1:
            # An authoritative entity/config ID match always outranks a friendly
            # name match, regardless of the order returned by Home Assistant.
            person_config = id_matches[0]
            person_configuration_resolution = "exact_id"
        elif len(id_matches) > 1:
            person_configuration_resolution = "ambiguous"
        elif len(name_matches) == 1:
            person_config = name_matches[0]
            person_configuration_resolution = "unique_name"
        elif len(name_matches) > 1:
            person_configuration_resolution = "ambiguous"
        elif person_graph_available:
            person_configuration_resolution = "not_found"

        tracker_values = (person_config or {}).get("device_trackers") or []
        if not isinstance(tracker_values, list):
            tracker_values = []
        configured_tracker_ids = [
            str(item)
            for item in tracker_values
            if str(item).startswith("device_tracker.")
        ]
        related_ids = list(dict.fromkeys([*configured_tracker_ids, *([source_id] if source_id else [])]))
        tracker_lookup = {str(item.get("entity_id")): item for item in trackers}
        related: list[dict[str, Any]] = []
        for tracker_id in related_ids:
            live_tracker = tracker_lookup.get(tracker_id)
            if live_tracker is not None:
                related.append(_public(live_tracker))
            else:
                related.append(
                    {
                        "entity_id": tracker_id,
                        "name": tracker_id,
                        "domain": "device_tracker",
                        "state": None,
                        "available": False,
                        "relationship": "configured_for_person",
                        "live_state_missing": True,
                    }
                )
        conflicts: list[dict[str, Any]] = []
        person_state = str(person.get("state") or "")
        for tracker in related:
            tracker_state = tracker.get("state")
            if tracker_state is None:
                conflicts.append(
                    {
                        "entity_id": tracker["entity_id"],
                        "name": tracker.get("name"),
                        "state": None,
                        "reason": "configured_tracker_missing_from_live_states",
                    }
                )
            elif str(tracker_state) != person_state:
                conflicts.append(
                    {
                        "entity_id": tracker["entity_id"],
                        "name": tracker.get("name"),
                        "state": tracker_state,
                        "reason": (
                            "source_state_differs_from_person"
                            if tracker.get("entity_id") == source_id
                            else "associated_tracker_differs_from_person"
                        ),
                    }
                )
        return {
            "success": True,
            "resolution": "exact",
            "reference": reference,
            "person": _public(person),
            "source": _public(source) if source is not None else None,
            "source_entity_id": source_id,
            "source_established": source is not None,
            "trackers": related,
            "tracker_count": len(related),
            "person_graph_exposed": person_graph_available,
            "person_configuration_matched": person_config is not None,
            "person_configuration_resolution": person_configuration_resolution,
            "conflicts": conflicts,
            "causal_explanation": (
                "Home Assistant exposes the exact source tracker."
                if source is not None
                else "Home Assistant does not expose a resolvable source tracker."
            ),
        }
