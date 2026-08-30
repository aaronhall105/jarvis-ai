import re
from typing import Any

from app.registry import RegistryEngine


class DeviceResolver:
    CONTROLLABLE_DOMAINS = {
        "light",
        "switch",
    }

    def __init__(
        self,
        registry: RegistryEngine,
    ) -> None:
        self.registry = registry

    @staticmethod
    def normalise(value: str) -> str:
        value = value.lower().strip()
        value = value.replace("_", " ")
        value = re.sub(r"[^a-z0-9\s'-]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value

    async def controllable_entities(
        self,
    ) -> list[dict[str, Any]]:
        snapshot = await self.registry.ensure_loaded()

        area_lookup = {
            (area.get("area_id") or area.get("id")): area.get("name") for area in snapshot.areas
        }

        device_lookup = {device.get("id"): device for device in snapshot.devices}

        state_lookup = {state.get("entity_id"): state for state in snapshot.states}

        results: list[dict[str, Any]] = []

        for entity in snapshot.entities:
            entity_id = entity.get("entity_id")

            if not entity_id or "." not in entity_id:
                continue

            domain = entity_id.split(".", 1)[0]

            if domain not in self.CONTROLLABLE_DOMAINS:
                continue

            if entity.get("disabled_by") is not None:
                continue

            device = device_lookup.get(
                entity.get("device_id"),
                {},
            )

            area_id = entity.get("area_id") or device.get("area_id")

            state_object = state_lookup.get(
                entity_id,
                {},
            )

            state = state_object.get(
                "state",
                "unknown",
            )

            name = (
                entity.get("name")
                or entity.get("original_name")
                or state_object.get(
                    "attributes",
                    {},
                ).get("friendly_name")
                or device.get("name_by_user")
                or device.get("name")
                or entity_id
            )

            area_name = area_lookup.get(area_id) if area_id else None

            results.append(
                {
                    "entity_id": entity_id,
                    "domain": domain,
                    "name": name,
                    "area_id": area_id,
                    "area_name": area_name,
                    "state": state,
                    "available": state
                    not in {
                        "unavailable",
                        "unknown",
                        None,
                    },
                    "search_text": self.normalise(
                        " ".join(
                            part
                            for part in [
                                name,
                                entity_id,
                                area_name,
                                domain,
                            ]
                            if part
                        )
                    ),
                }
            )

        return sorted(
            results,
            key=lambda item: (
                item.get("area_name") or "",
                item["name"],
                item["entity_id"],
            ),
        )

    async def available_entities(
        self,
    ) -> list[dict[str, Any]]:
        return [entity for entity in await self.controllable_entities() if entity["available"]]

    async def get(
        self,
        entity_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                entity
                for entity in await self.controllable_entities()
                if entity["entity_id"] == entity_id
            ),
            None,
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalised_query = self.normalise(query)

        if not normalised_query:
            return []

        terms = [term for term in normalised_query.split() if len(term) >= 2]

        ranked: list[tuple[int, dict[str, Any]]] = []

        for entity in await self.controllable_entities():
            score = 0
            search_text = entity["search_text"]
            normalised_name = self.normalise(entity["name"])

            if normalised_query == normalised_name:
                score += 100

            if normalised_query in normalised_name:
                score += 50

            if normalised_query in search_text:
                score += 25

            score += sum(5 for term in terms if term in search_text)

            if score:
                ranked.append(
                    (
                        score,
                        entity,
                    )
                )

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1]["available"],
                item[1]["name"],
            ),
            reverse=True,
        )

        return [entity for _, entity in ranked[: max(1, min(limit, 50))]]
