"""Authoritative, live Home Assistant presence evidence.

This module deliberately returns data rather than an explanation.  Callers may
format it for a user, but cannot turn a guessed tracker or friendly name into a
presence source.
"""

from __future__ import annotations

import re
from typing import Any, Protocol


class PresenceStateReader(Protocol):
    async def readable_entity_states(self, *, refresh: bool = True) -> list[dict[str, Any]]: ...


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
        # A tracker is related only when HA explicitly names it as the source.
        related = [_public(source)] if source is not None else []
        conflicts: list[dict[str, Any]] = []
        if source is not None and str(source.get("state") or "") != str(person.get("state") or ""):
            conflicts.append(
                {
                    "entity_id": source["entity_id"],
                    "name": source.get("name"),
                    "state": source.get("state"),
                    "reason": "source_state_differs_from_person",
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
            "conflicts": conflicts,
            "causal_explanation": (
                "Home Assistant exposes the exact source tracker."
                if source is not None
                else "Home Assistant does not expose a resolvable source tracker."
            ),
        }
