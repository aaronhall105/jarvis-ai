from typing import Any

from app.tools.base import BaseHomeAssistantTool


class LightsTool(BaseHomeAssistantTool):
    domain = "light"

    async def list_in_area(
        self,
        area_id: str,
    ) -> list[dict[str, Any]]:
        return await self.entities_in_area(
            area_id=area_id,
            domain=self.domain,
        )

    async def turn_on(
        self,
        area_id: str,
    ) -> dict[str, Any]:
        return await self.call_area_service(
            area_id=area_id,
            domain=self.domain,
            service="turn_on",
        )

    async def turn_off(
        self,
        area_id: str,
    ) -> dict[str, Any]:
        return await self.call_area_service(
            area_id=area_id,
            domain=self.domain,
            service="turn_off",
        )

    async def turn_entity_on(
        self,
        entity_id: str,
    ) -> dict[str, Any]:
        return await self.call_entity_service(
            entity_id=entity_id,
            domain=self.domain,
            service="turn_on",
        )

    async def turn_entity_off(
        self,
        entity_id: str,
    ) -> dict[str, Any]:
        return await self.call_entity_service(
            entity_id=entity_id,
            domain=self.domain,
            service="turn_off",
        )

    async def control_entity(
        self,
        entity_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        if turn_on:
            return await self.turn_entity_on(entity_id)

        return await self.turn_entity_off(entity_id)

    async def control(
        self,
        area_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        if turn_on:
            return await self.turn_on(area_id)

        return await self.turn_off(area_id)
