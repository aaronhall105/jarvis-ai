from typing import Any

from app.device_resolver import DeviceResolver
from app.home_assistant import HomeAssistantClient
from app.registry import RegistryEngine
from app.tools.lights import LightsTool
from app.tools.switches import SwitchesTool


class ToolEngine:
    SAFE_CONTROL_DOMAINS = {
        "light",
        "switch",
    }

    def __init__(
        self,
        client: HomeAssistantClient,
        registry: RegistryEngine,
    ) -> None:
        self.client = client
        self.registry = registry

        self.devices = DeviceResolver(
            registry=registry,
        )

        self.lights = LightsTool(
            client=client,
            registry=registry,
        )

        self.switches = SwitchesTool(
            client=client,
            registry=registry,
        )

    async def entities_in_area(
        self,
        area_id: str,
        domain: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        return await self.lights.entities_in_area(
            area_id=area_id,
            domain=domain,
            include_disabled=include_disabled,
        )

    async def lights_in_area(
        self,
        area_id: str,
    ) -> list[dict[str, Any]]:
        return await self.lights.list_in_area(
            area_id=area_id,
        )

    async def control_area_lights(
        self,
        area_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        return await self.lights.control(
            area_id=area_id,
            turn_on=turn_on,
        )

    async def switches_in_area(
        self,
        area_id: str,
    ) -> list[dict[str, Any]]:
        return await self.switches.list_in_area(
            area_id=area_id,
        )

    async def control_area_switches(
        self,
        area_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        return await self.switches.control(
            area_id=area_id,
            turn_on=turn_on,
        )


    async def search_devices(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self.devices.search(
            query=query,
            limit=limit,
        )

    async def controllable_devices(
        self,
    ) -> list[dict[str, Any]]:
        return await self.devices.available_entities()

    async def control_device(
        self,
        entity_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        entity = await self.devices.get(
            entity_id
        )

        if entity is None:
            raise ValueError(
                f"Unknown controllable entity: {entity_id}"
            )

        if not entity["available"]:
            return {
                "success": False,
                "entity_id": entity_id,
                "message": (
                    f'{entity["name"]} is currently unavailable.'
                ),
                "entities": [],
            }

        domain = entity["domain"]

        if domain == "light":
            result = await self.lights.control_entity(
                entity_id=entity_id,
                turn_on=turn_on,
            )
        elif domain == "switch":
            result = await self.switches.control_entity(
                entity_id=entity_id,
                turn_on=turn_on,
            )
        else:
            raise ValueError(
                f"Unsupported control domain: {domain}"
            )

        return {
            **result,
            "name": entity["name"],
            "area_id": entity.get("area_id"),
            "area_name": entity.get("area_name"),
            "domain": domain,
        }

