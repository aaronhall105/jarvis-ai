"""Home Assistant sensor projection of the shared Jarvis HomeExperience."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JarvisHomeCoordinator


def _data(coordinator: JarvisHomeCoordinator) -> dict[str, Any]:
    return coordinator.data if isinstance(coordinator.data, dict) else {}


def _array(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _energy_value(data: dict[str, Any]) -> str:
    values = _array(data, "energy")
    for kind in ("POWER", "ENERGY", "COST"):
        selected = next((item for item in values if item.get("kind") == kind), None)
        if selected is not None:
            return str(selected.get("display_value") or selected.get("value") or "available")
    return "unavailable"


class JarvisHomeSensor(CoordinatorEntity[JarvisHomeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JarvisHomeCoordinator,
        *,
        key: str,
        name: str,
        value: Callable[[dict[str, Any]], Any],
        attributes: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_home_{key}"
        self._value = value
        self._attributes = attributes

    @property
    def native_value(self) -> Any:
        return self._value(_data(self.coordinator))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes(_data(self.coordinator))


class JarvisRoomSensor(CoordinatorEntity[JarvisHomeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: JarvisHomeCoordinator, area_id: str, name: str) -> None:
        super().__init__(coordinator)
        self.area_id = area_id
        self._attr_name = f"Jarvis {name} Occupancy"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_room_{area_id}"
        # Preserve alpha37/38 entity IDs so existing dashboards and explicit
        # automations are not silently broken by the richer state semantics.
        self._attr_suggested_object_id = f"jarvis_{area_id}"
        self._attr_icon = "mdi:account-group"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"room:{area_id}")},
            name=f"Jarvis {name} Occupancy",
            manufacturer="Jarvis",
            model="Room Occupancy Intelligence",
            suggested_area=name,
        )

    def _room(self) -> dict[str, Any]:
        return next(
            (
                item
                for item in _array(_data(self.coordinator), "rooms")
                if item.get("area_id") == self.area_id
            ),
            {},
        )

    @property
    def native_value(self) -> str:
        return str(self._room().get("occupancy_state") or "UNKNOWN").casefold()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self._room()
        evidence = [
            item for item in room.get("occupancy_evidence", []) if isinstance(item, dict)
        ]
        person = next((item for item in evidence if item.get("kind") == "person_presence"), {})
        motion = next((item for item in evidence if item.get("kind") == "motion"), {})
        return {
            "area_id": self.area_id,
            "occupancy_state": room.get("occupancy_state", "UNKNOWN"),
            "confidence": room.get("occupancy_confidence", 0),
            "last_changed": room.get("occupancy_last_changed_at"),
            "last_strong_evidence": room.get("occupancy_last_strong_evidence_at"),
            "clear_candidate_since": room.get("occupancy_clear_candidate_since"),
            "freshness": room.get("occupancy_freshness", "UNAVAILABLE"),
            "source_health": room.get("occupancy_source_health", "MISSING"),
            "reason_code": room.get("occupancy_reason_code", "NO_OCCUPANCY_SOURCES"),
            "last_person_detection": person.get("observed_at"),
            "last_motion": motion.get("observed_at"),
            "evidence_count": len(evidence),
            "lights_on_count": room.get("lights_on_count", 0),
            "lights_total": room.get("lights_total", 0),
            "cameras": [
                {
                    "name": item.get("name"),
                    "availability": item.get("availability"),
                    "recent_activity": item.get("recent_activity"),
                }
                for item in room.get("cameras", [])[:8]
                if isinstance(item, dict)
            ],
            "important_incident_count": len(room.get("important_incidents", [])),
            "recent_events": room.get("recent_events", [])[:5],
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JarvisHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    sensors: list[SensorEntity] = [
        JarvisHomeSensor(
            coordinator,
            key="status",
            name="Jarvis Home Status",
            value=lambda data: str(
                (data.get("overall_status") or {}).get("status") or "UNAVAILABLE"
            ).lower(),
            attributes=lambda data: {
                "headline": (data.get("overall_status") or {}).get("headline"),
                "detail": (data.get("overall_status") or {}).get("detail"),
                "attention_count": (data.get("overall_status") or {}).get("attention_count", 0),
                "freshness": (data.get("source_freshness") or {}).get("status", "UNAVAILABLE"),
                "observed_at": (data.get("source_freshness") or {}).get("observed_at"),
                "generated_at": data.get("generated_at"),
                "revision": data.get("revision"),
            },
        ),
        JarvisHomeSensor(
            coordinator,
            key="lights_on",
            name="Jarvis Lights On",
            value=lambda data: int((data.get("lights") or {}).get("on_count", 0)),
            attributes=lambda data: {
                "total_count": (data.get("lights") or {}).get("total_count", 0),
                "lights": [
                    {
                        "name": item.get("name"),
                        "state": item.get("state"),
                        "room": item.get("area_name"),
                    }
                    for item in (data.get("lights") or {}).get("items", [])[:50]
                    if isinstance(item, dict)
                ],
            },
        ),
        JarvisHomeSensor(
            coordinator,
            key="unavailable_devices",
            name="Jarvis Unavailable Devices",
            value=lambda data: int((data.get("devices") or {}).get("unavailable_count", 0)),
            attributes=lambda data: {
                "devices": [
                    {"name": item.get("name"), "room": item.get("area_name")}
                    for item in (data.get("devices") or {}).get("unavailable", [])[:30]
                    if isinstance(item, dict)
                ],
                "partial_count": (data.get("devices") or {}).get("partial_count", 0),
            },
        ),
        JarvisHomeSensor(
            coordinator,
            key="people_home",
            name="Jarvis People Home",
            value=lambda data: sum(
                1 for item in _array(data, "people") if item.get("presence") == "HOME"
            ),
            attributes=lambda data: {
                "people": [
                    {"name": item.get("name"), "presence": item.get("presence", "UNKNOWN")}
                    for item in _array(data, "people")[:20]
                ]
            },
        ),
        JarvisHomeSensor(
            coordinator,
            key="active_incidents",
            name="Jarvis Active Incidents",
            value=lambda data: len(_array(data, "current_incidents")),
            attributes=lambda data: {"incidents": _array(data, "current_incidents")[:15]},
        ),
        JarvisHomeSensor(
            coordinator,
            key="camera_status",
            name="Jarvis Camera Status",
            value=lambda data: sum(
                1 for item in _array(data, "cameras") if item.get("availability") == "UNAVAILABLE"
            ),
            attributes=lambda data: {
                "unavailable_count": sum(
                    1
                    for item in _array(data, "cameras")
                    if item.get("availability") == "UNAVAILABLE"
                ),
                "cameras": [
                    {
                        "name": item.get("name"),
                        "room": item.get("area_name"),
                        "availability": item.get("availability"),
                    }
                    for item in _array(data, "cameras")[:20]
                ],
            },
        ),
        JarvisHomeSensor(
            coordinator,
            key="energy_summary",
            name="Jarvis Energy Summary",
            value=_energy_value,
            attributes=lambda data: {"items": _array(data, "energy")[:20]},
        ),
        JarvisHomeSensor(
            coordinator,
            key="recent_activity",
            name="Jarvis Recent Activity",
            value=lambda data: (
                str(_array(data, "recent_events")[0].get("title") or "Recent activity")
                if _array(data, "recent_events")
                else "None"
            ),
            attributes=lambda data: {"events": _array(data, "recent_events")[:15]},
        ),
    ]
    sensors.extend(
        JarvisRoomSensor(coordinator, str(room["area_id"]), str(room.get("name") or "Room"))
        for room in _array(_data(coordinator), "rooms")
        if room.get("area_id")
    )
    async_add_entities(sensors)
