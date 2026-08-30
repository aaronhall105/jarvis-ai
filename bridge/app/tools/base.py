import asyncio
from typing import Any

from app.home_assistant import HomeAssistantClient
from app.registry import RegistryEngine


class BaseHomeAssistantTool:
    def __init__(
        self,
        client: HomeAssistantClient,
        registry: RegistryEngine,
    ) -> None:
        self.client = client
        self.registry = registry

    async def entities_in_area(
        self,
        area_id: str,
        domain: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        snapshot = await self.registry.ensure_loaded()

        valid_area_ids = {area.get("area_id") or area.get("id") for area in snapshot.areas}

        if area_id not in valid_area_ids:
            raise ValueError(f"Unknown area: {area_id}")

        device_areas = {device.get("id"): device.get("area_id") for device in snapshot.devices}

        state_lookup = {state.get("entity_id"): state for state in snapshot.states}

        results: list[dict[str, Any]] = []

        for entity in snapshot.entities:
            entity_id = entity.get("entity_id")

            if not entity_id or "." not in entity_id:
                continue

            entity_domain = entity_id.split(".", 1)[0]

            if domain and entity_domain != domain:
                continue

            if not include_disabled and entity.get("disabled_by") is not None:
                continue

            effective_area = entity.get("area_id")

            if not effective_area:
                effective_area = device_areas.get(entity.get("device_id"))

            if effective_area != area_id:
                continue

            current_state = state_lookup.get(
                entity_id,
                {},
            )

            state = current_state.get(
                "state",
                "unknown",
            )

            results.append(
                {
                    "entity_id": entity_id,
                    "domain": entity_domain,
                    "name": (
                        entity.get("name")
                        or entity.get("original_name")
                        or current_state.get(
                            "attributes",
                            {},
                        ).get("friendly_name")
                        or entity_id
                    ),
                    "state": state,
                    "available": state
                    not in {
                        "unavailable",
                        "unknown",
                        None,
                    },
                    "platform": entity.get("platform"),
                    "area_id": effective_area,
                }
            )

        return sorted(
            results,
            key=lambda item: item["entity_id"],
        )

    async def call_entity_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
    ) -> dict[str, Any]:
        if "." not in entity_id:
            raise ValueError(f"Invalid entity ID: {entity_id}")

        entity_domain = entity_id.split(".", 1)[0]

        if entity_domain != domain:
            raise ValueError(f"Entity {entity_id} does not belong to the {domain} domain.")

        snapshot = await self.registry.ensure_loaded()

        registry_entity = next(
            (entity for entity in snapshot.entities if entity.get("entity_id") == entity_id),
            None,
        )

        if registry_entity is None:
            raise ValueError(f"Unknown entity: {entity_id}")

        if registry_entity.get("disabled_by") is not None:
            raise ValueError(f"Entity is disabled: {entity_id}")

        state_lookup = {state.get("entity_id"): state for state in snapshot.states}

        current_state = state_lookup.get(
            entity_id,
            {},
        ).get("state")

        if current_state in {
            "unavailable",
            "unknown",
            None,
        }:
            return {
                "success": False,
                "entity_id": entity_id,
                "service": f"{domain}.{service}",
                "message": (f"{entity_id} is currently unavailable."),
                "entities": [],
            }

        await self.client.call_service(
            domain=domain,
            service=service,
            entity_ids=[entity_id],
        )

        await asyncio.sleep(0.4)

        latest_states = await self.client.get_states()
        self.registry.snapshot.states = latest_states

        latest_state = next(
            (state.get("state") for state in latest_states if state.get("entity_id") == entity_id),
            "unknown",
        )

        return {
            "success": True,
            "entity_id": entity_id,
            "service": f"{domain}.{service}",
            "entities": [
                {
                    "entity_id": entity_id,
                    "state": latest_state,
                }
            ],
        }

    async def call_area_service(
        self,
        area_id: str,
        domain: str,
        service: str,
    ) -> dict[str, Any]:
        entities = await self.entities_in_area(
            area_id=area_id,
            domain=domain,
        )

        controllable = [entity for entity in entities if entity["available"]]

        entity_ids = [entity["entity_id"] for entity in controllable]

        if not entity_ids:
            return {
                "success": False,
                "area_id": area_id,
                "service": f"{domain}.{service}",
                "message": (f"No available {domain} entities were found in this area."),
                "entities": [],
            }

        await self.client.call_service(
            domain=domain,
            service=service,
            entity_ids=entity_ids,
        )

        await asyncio.sleep(0.4)

        latest_states = await self.client.get_states()
        self.registry.snapshot.states = latest_states

        state_lookup = {state.get("entity_id"): state.get("state") for state in latest_states}

        return {
            "success": True,
            "area_id": area_id,
            "service": f"{domain}.{service}",
            "entities": [
                {
                    "entity_id": entity_id,
                    "state": state_lookup.get(
                        entity_id,
                        "unknown",
                    ),
                }
                for entity_id in entity_ids
            ],
        }
