import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.home_assistant import HomeAssistantClient

logger = logging.getLogger("jarvis-core.registry")


@dataclass
class RegistrySnapshot:
    areas: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    states: list[dict[str, Any]] = field(default_factory=list)
    refreshed_at: str | None = None


class RegistryEngine:
    def __init__(self, client: HomeAssistantClient) -> None:
        self.client = client
        self.snapshot = RegistrySnapshot()
        self._lock = asyncio.Lock()

    async def refresh(self) -> RegistrySnapshot:
        async with self._lock:
            areas, devices, entities, states = await asyncio.gather(
                self.client.send_command(
                    {"type": "config/area_registry/list"}
                ),
                self.client.send_command(
                    {"type": "config/device_registry/list"}
                ),
                self.client.send_command(
                    {"type": "config/entity_registry/list"}
                ),
                self.client.get_states(),
            )

            self.snapshot = RegistrySnapshot(
                areas=areas or [],
                devices=devices or [],
                entities=entities or [],
                states=states or [],
                refreshed_at=datetime.now(timezone.utc).isoformat(),
            )

            logger.info(
                "Registry loaded: %d areas, %d devices, %d entities",
                len(self.snapshot.areas),
                len(self.snapshot.devices),
                len(self.snapshot.entities),
            )

            return self.snapshot

    async def ensure_loaded(self) -> RegistrySnapshot:
        if self.snapshot.refreshed_at is None:
            return await self.refresh()

        return self.snapshot

    async def summary(self) -> dict[str, Any]:
        snapshot = await self.ensure_loaded()

        enabled_entities = [
            entity
            for entity in snapshot.entities
            if entity.get("disabled_by") is None
        ]

        domain_counts = Counter(
            entity.get("entity_id", "unknown.unknown").split(".", 1)[0]
            for entity in enabled_entities
        )

        return {
            "areas": len(snapshot.areas),
            "devices": len(snapshot.devices),
            "entities": len(snapshot.entities),
            "enabled_entities": len(enabled_entities),
            "states": len(snapshot.states),
            "domains": dict(domain_counts.most_common()),
            "refreshed_at": snapshot.refreshed_at,
        }

    async def areas(self) -> list[dict[str, Any]]:
        snapshot = await self.ensure_loaded()

        devices_by_area = Counter(
            device.get("area_id")
            for device in snapshot.devices
            if device.get("area_id")
        )

        entities_by_area = Counter()

        device_areas = {
            device.get("id"): device.get("area_id")
            for device in snapshot.devices
        }

        for entity in snapshot.entities:
            area_id = entity.get("area_id")

            if not area_id:
                area_id = device_areas.get(entity.get("device_id"))

            if area_id:
                entities_by_area[area_id] += 1

        results = []

        for area in snapshot.areas:
            area_id = area.get("area_id") or area.get("id")

            results.append(
                {
                    "area_id": area_id,
                    "name": area.get("name"),
                    "aliases": area.get("aliases", []),
                    "icon": area.get("icon"),
                    "device_count": devices_by_area[area_id],
                    "entity_count": entities_by_area[area_id],
                }
            )

        return sorted(results, key=lambda item: item["name"].lower())

    async def room(self, area_id: str) -> dict[str, Any] | None:
        snapshot = await self.ensure_loaded()

        area = next(
            (
                item
                for item in snapshot.areas
                if (item.get("area_id") or item.get("id")) == area_id
            ),
            None,
        )

        if area is None:
            return None

        devices = [
            device
            for device in snapshot.devices
            if device.get("area_id") == area_id
        ]

        device_ids = {device.get("id") for device in devices}

        entities = []

        for entity in snapshot.entities:
            effective_area = entity.get("area_id")

            if not effective_area and entity.get("device_id") in device_ids:
                effective_area = area_id

            if effective_area == area_id:
                entities.append(
                    {
                        "entity_id": entity.get("entity_id"),
                        "name": (
                            entity.get("name")
                            or entity.get("original_name")
                            or entity.get("entity_id")
                        ),
                        "platform": entity.get("platform"),
                        "disabled": entity.get("disabled_by") is not None,
                    }
                )

        return {
            "area": {
                "area_id": area_id,
                "name": area.get("name"),
                "aliases": area.get("aliases", []),
                "icon": area.get("icon"),
            },
            "devices": [
                {
                    "device_id": device.get("id"),
                    "name": (
                        device.get("name_by_user")
                        or device.get("name")
                        or "Unnamed device"
                    ),
                    "manufacturer": device.get("manufacturer"),
                    "model": device.get("model"),
                }
                for device in devices
            ],
            "entities": sorted(
                entities,
                key=lambda item: item["entity_id"] or "",
            ),
        }
