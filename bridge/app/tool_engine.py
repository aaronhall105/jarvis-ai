import asyncio
import re
from typing import Any

from app.device_resolver import DeviceResolver
from app.home_assistant import HomeAssistantClient
from app.presence import PresenceResolver
from app.registry import RegistryEngine
from app.tools.lights import LightsTool
from app.tools.switches import SwitchesTool


class ToolEngine:
    SAFE_CONTROL_DOMAINS = {
        "light",
        "switch",
    }

    STATE_VERIFY_DELAYS = (0.12, 0.20, 0.35)
    STATE_RETRY_VERIFY_DELAYS = (0.18, 0.30, 0.50, 0.75, 1.00)
    AVAILABLE_ON_STATES = {"on", "playing", "paused", "idle", "buffering"}
    STOPPED_MEDIA_STATES = {"idle", "off", "standby", "stopped"}

    MEDIA_SHORTCUTS = {
        "tv_on": {
            "script_entity_id": "script.tv_on",
            "state_entity_id": "media_player.samsung_7_series_55_ue55tu7020kxxu",
            "target_state": "on",
            "name": "TV",
            "result": "turned on",
        },
        "tv_off": {
            "script_entity_id": "script.tv_off",
            "state_entity_id": "media_player.samsung_7_series_55_ue55tu7020kxxu",
            "target_state": "off",
            "name": "TV",
            "result": "turned off",
        },
        "netflix": {
            "script_entity_id": "script.1781383353266",
            "name": "Netflix",
            "result": "launch command sent",
        },
        "youtube": {
            "script_entity_id": "script.open_bbc_youtube",
            "name": "YouTube",
            "result": "launch command sent",
        },
        "bbc_iplayer": {
            "script_entity_id": "script.open_bbc_iplayer",
            "name": "BBC iPlayer",
            "result": "launch command sent",
        },
        "prime_video": {
            "script_entity_id": "script.open_prime_video",
            "name": "Prime Video",
            "result": "launch command sent",
        },
    }

    MEDIA_PLAYER_ENTITIES = {
        "media_player.android_tv_192_168_1_167": "Living room Android TV",
        "media_player.bedroom_echo_pop": "Bedroom Echo Pop",
        "media_player.everywhere": "Everywhere",
        "media_player.home_assistant_voice_09f0ef_media_player": "Living room Home Assistant Voice",
        "media_player.kitchen_echo_pop": "Kitchen Echo Pop",
        "media_player.livingroom_echo_dot": "Living room Echo Dot",
        "media_player.samsung_7_series_55_ue55tu7020kxxu": "Living room TV",
        "media_player.tv_samsung_7_series_55": "Living room Samsung TV",
    }

    MEDIA_ACTION_SERVICES = {
        "play": ("media_play", {}),
        "pause": ("media_pause", {}),
        "play_pause": ("media_play_pause", {}),
        "stop": ("media_stop", {}),
        "next": ("media_next_track", {}),
        "previous": ("media_previous_track", {}),
        "volume_up": ("volume_up", {}),
        "volume_down": ("volume_down", {}),
        "mute": ("volume_mute", {"is_volume_muted": True}),
        "unmute": ("volume_mute", {"is_volume_muted": False}),
    }

    NOTIFICATION_SERVICES = {
        "aaron": ["mobile_app_aaron_s_phone"],
        "amber": ["mobile_app_amber_phone"],
        "both": [
            "mobile_app_aaron_s_phone",
            "mobile_app_amber_phone",
        ],
    }

    ANNOUNCEMENT_TARGETS = {
        "living_room": {
            "script_entity_id": "script.jarvis_living_room_announce",
            "name": "living room",
        },
    }

    # Read-only state access is intentionally broader than control access.
    # These domains expose ordinary Home Assistant state without permitting
    # Jarvis to operate the entity.
    # Domains that represent ordinary user-facing devices which can reasonably
    # be described as "on" in a room summary. Diagnostic sensors, helpers and
    # configuration controls are intentionally excluded.
    AREA_ACTIVE_DOMAINS = {
        "climate",
        "fan",
        "humidifier",
        "light",
        "media_player",
        "switch",
    }

    AREA_ACTIVE_STATES = {
        "climate": {"auto", "cool", "dry", "fan_only", "heat", "heat_cool"},
        "fan": {"on"},
        "humidifier": {"on"},
        "light": {"on"},
        "media_player": {"buffering", "on", "paused", "playing"},
        "switch": {"on"},
    }

    # These are integration settings or diagnostics rather than devices a person
    # means when asking "what is on in this room?". Entity category is the main
    # protection; keywords are a second guard for older integrations that do not
    # categorise entities correctly.
    AREA_SUMMARY_EXCLUDED_TERMS = {
        "audio recording",
        "debug",
        "diagnostic",
        "file editor",
        "ftp",
        "infrared",
        "night mode",
        "privacy",
        "record audio",
        "recording",
        "reboot",
        "restart",
        "rtsp",
        "subscribed",
        "subscription",
        "upload",
        "wake sound",
        "wake word",
    }

    READABLE_DOMAINS = {
        "alarm_control_panel",
        "automation",
        "script",
        "binary_sensor",
        "camera",
        "climate",
        "cover",
        "device_tracker",
        "fan",
        "humidifier",
        "input_boolean",
        "input_datetime",
        "input_number",
        "input_select",
        "light",
        "lock",
        "media_player",
        "number",
        "person",
        "remote",
        "select",
        "sensor",
        "siren",
        "sun",
        "switch",
        "timer",
        "update",
        "vacuum",
        "valve",
        "water_heater",
        "weather",
        "zone",
    }

    SAFE_STATE_ATTRIBUTE_KEYS = {
        "battery",
        "battery_level",
        "current_humidity",
        "current_position",
        "current_temperature",
        "device_class",
        "fan_mode",
        "friendly_name",
        "gps_accuracy",
        "latitude",
        "longitude",
        "hvac_action",
        "icon",
        "is_volume_muted",
        "media_album_name",
        "media_artist",
        "media_content_type",
        "media_title",
        "percentage",
        "position",
        "preset_mode",
        "source",
        "state_class",
        "swing_mode",
        "temperature",
        "tilt_position",
        "unit_of_measurement",
        "volume_level",
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
        self.presence = PresenceResolver(self)

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
        # Resolve the area membership against the live registry before acting.
        await self.registry.refresh()
        target_state = "on" if turn_on else "off"
        action_text = "turn on" if turn_on else "turn off"
        service = "turn_on" if turn_on else "turn_off"

        area_result = await self.list_area_states(
            area_id=area_id,
            domain="light",
            limit=50,
        )
        area_name = str(area_result.get("area_name") or area_id)
        entities = list(area_result.get("entities") or [])

        available = [
            entity
            for entity in entities
            if str(entity.get("state") or "").lower()
            not in {"unavailable", "unknown", ""}
        ]
        unavailable = [entity for entity in entities if entity not in available]
        already = [
            entity
            for entity in available
            if str(entity.get("state") or "").lower() == target_state
        ]
        pending = [entity for entity in available if entity not in already]

        if not available:
            return {
                "success": False,
                "area_id": area_id,
                "area_name": area_name,
                "domain": "light",
                "target_state": target_state,
                "changed": False,
                "verified": False,
                "already_in_target_state": False,
                "entities": [],
                "unavailable_entities": [
                    self._public_state(entity) for entity in unavailable
                ],
                "response_message": (
                    f"No available lights were found in the {area_name}."
                ),
            }

        if not pending:
            noun = "light" if len(available) == 1 else "lights"
            response_message = (
                f"The {area_name} {noun} {'is' if len(available) == 1 else 'are'} "
                f"already {target_state}."
            )
            if unavailable:
                response_message += (
                    f" {len(unavailable)} unavailable "
                    f"light{'' if len(unavailable) == 1 else 's'} could not be checked."
                )
            return {
                "success": True,
                "area_id": area_id,
                "area_name": area_name,
                "domain": "light",
                "target_state": target_state,
                "changed": False,
                "verified": True,
                "complete": not unavailable,
                "already_in_target_state": True,
                "already_count": len(already),
                "changed_count": 0,
                "entities": [self._public_state(entity) for entity in available],
                "unavailable_entities": [
                    self._public_state(entity) for entity in unavailable
                ],
                "response_message": response_message,
            }

        pending_ids = [str(entity["entity_id"]) for entity in pending]
        await self.client.call_service(
            domain="light",
            service=service,
            entity_ids=pending_ids,
        )

        final_lookup: dict[str, dict[str, Any]] = {}
        retried = False
        for attempt, delays in enumerate((
            self.STATE_VERIFY_DELAYS,
            self.STATE_RETRY_VERIFY_DELAYS,
        )):
            for delay in delays:
                await asyncio.sleep(delay)
                refreshed = await self.readable_entity_states(refresh=True)
                final_lookup = {
                    str(entity["entity_id"]): entity
                    for entity in refreshed
                    if entity.get("entity_id") in pending_ids
                }
                if all(
                    str(final_lookup.get(entity_id, {}).get("state") or "").lower()
                    == target_state
                    for entity_id in pending_ids
                ):
                    break

            remaining_ids = [
                entity_id
                for entity_id in pending_ids
                if str(final_lookup.get(entity_id, {}).get("state") or "").lower()
                != target_state
            ]
            if not remaining_ids:
                break
            if attempt == 0:
                await self.client.call_service(
                    domain="light",
                    service=service,
                    entity_ids=remaining_ids,
                )
                retried = True

        verified_ids = [
            entity_id
            for entity_id in pending_ids
            if str(final_lookup.get(entity_id, {}).get("state") or "").lower()
            == target_state
        ]
        failed_ids = [entity_id for entity_id in pending_ids if entity_id not in verified_ids]

        if not failed_ids:
            response_message = (
                f"The {area_name} lights are now {target_state}."
            )
            if already:
                response_message += (
                    f" {len(already)} "
                    f"{'was' if len(already) == 1 else 'were'} already {target_state}."
                )
            if unavailable:
                response_message += (
                    f" {len(unavailable)} unavailable "
                    f"light{'' if len(unavailable) == 1 else 's'} could not be checked."
                )
        else:
            response_message = (
                f"I sent the command to {action_text} the {area_name} lights. "
                f"Home Assistant confirmed {len(verified_ids)} of {len(pending_ids)} changed, "
                f"but {len(failed_ids)} still do not report {target_state}."
            )
            if unavailable:
                response_message += (
                    f" {len(unavailable)} unavailable "
                    f"light{'' if len(unavailable) == 1 else 's'} could not be checked."
                )

        final_entities = [
            self._public_state(final_lookup.get(entity_id, {"entity_id": entity_id, "state": "unknown"}))
            for entity_id in pending_ids
        ]
        return {
            "success": not failed_ids,
            "area_id": area_id,
            "area_name": area_name,
            "domain": "light",
            "target_state": target_state,
            "changed": bool(verified_ids),
            "command_sent": True,
            "retried": retried,
            "verified": not failed_ids,
            "complete": not failed_ids and not unavailable,
            "already_in_target_state": False,
            "already_count": len(already),
            "changed_count": len(verified_ids),
            "failed_count": len(failed_ids),
            "requested_entity_ids": pending_ids,
            "verified_entity_ids": verified_ids,
            "failed_entity_ids": failed_ids,
            "entities": final_entities,
            "unavailable_entities": [self._public_state(entity) for entity in unavailable],
            "response_message": response_message,
        }

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
        # Resolve the area membership against the live registry before acting.
        await self.registry.refresh()
        target_state = "on" if turn_on else "off"
        area_result = await self.list_area_states(
            area_id=area_id,
            domain="switch",
            limit=50,
        )
        area_name = str(area_result.get("area_name") or area_id)
        entities = list(area_result.get("entities") or [])
        if not entities:
            return {
                "success": False,
                "area_id": area_id,
                "area_name": area_name,
                "domain": "switch",
                "target_state": target_state,
                "changed": False,
                "verified": False,
                "entities": [],
                "response_message": (
                    f"No switches were found in the {area_name}."
                ),
            }

        results = [
            await self.control_device(
                entity_id=str(entity["entity_id"]),
                turn_on=turn_on,
            )
            for entity in entities
        ]
        changed_count = sum(1 for result in results if result.get("changed") is True)
        already_count = sum(
            1 for result in results if result.get("already_in_target_state") is True
        )
        failed = [
            result
            for result in results
            if not result.get("success") or not result.get("verified")
        ]

        if not failed:
            response_message = f"The {area_name} switches are now {target_state}."
            if already_count:
                response_message += (
                    f" {already_count} "
                    f"{'was' if already_count == 1 else 'were'} already {target_state}."
                )
        else:
            response_message = (
                f"I updated {changed_count} switch"
                f"{'' if changed_count == 1 else 'es'} in the {area_name}, but "
                f"{len(failed)} could not be confirmed {target_state}."
            )

        return {
            "success": True,
            "area_id": area_id,
            "area_name": area_name,
            "domain": "switch",
            "target_state": target_state,
            "changed": changed_count > 0,
            "verified": not failed,
            "complete": not failed,
            "changed_count": changed_count,
            "already_count": already_count,
            "failed_count": len(failed),
            "entities": results,
            "response_message": response_message,
        }

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

    async def inspect_presence(self, reference: str) -> dict[str, Any]:
        """Return fresh, structured person/tracker evidence only."""
        return await self.presence.inspect(reference)

    async def control_device(
        self,
        entity_id: str,
        turn_on: bool,
    ) -> dict[str, Any]:
        # A cache can help discovery, never authorise a mutation.
        await self.registry.refresh()
        entity = await self.devices.get(entity_id)
        if entity is None:
            raise ValueError(f"Unknown controllable entity: {entity_id}")

        domain = str(entity.get("domain") or "")
        if domain not in self.SAFE_CONTROL_DOMAINS:
            raise ValueError(f"Unsupported control domain: {domain}")

        name = str(entity.get("name") or entity_id)
        target_state = "on" if turn_on else "off"
        service = "turn_on" if turn_on else "turn_off"
        current = await self.get_entity_state(entity_id)
        current_entity = current.get("entity") or {}
        previous_state = str(current_entity.get("state") or "unknown").lower()

        if previous_state in {"unavailable", "unknown", ""}:
            return {
                "success": False,
                "entity_id": entity_id,
                "name": name,
                "area_id": entity.get("area_id"),
                "area_name": entity.get("area_name"),
                "domain": domain,
                "target_state": target_state,
                "changed": False,
                "verified": False,
                "previous_state": previous_state,
                "response_message": f"{name} is currently unavailable.",
                "entities": [],
            }

        if previous_state == target_state:
            return {
                "success": True,
                "entity_id": entity_id,
                "name": name,
                "area_id": entity.get("area_id"),
                "area_name": entity.get("area_name"),
                "domain": domain,
                "target_state": target_state,
                "changed": False,
                "verified": True,
                "already_in_target_state": True,
                "previous_state": previous_state,
                "current_state": previous_state,
                "response_message": f"{name} is already {target_state}.",
                "entities": [self._public_state(current_entity)],
            }

        await self.client.call_service(
            domain=domain,
            service=service,
            entity_ids=[entity_id],
        )

        current_state = previous_state
        final_entity: dict[str, Any] = current_entity
        verified = False
        retried = False
        for attempt, delays in enumerate((
            self.STATE_VERIFY_DELAYS,
            self.STATE_RETRY_VERIFY_DELAYS,
        )):
            for delay in delays:
                await asyncio.sleep(delay)
                refreshed = await self.get_entity_state(entity_id)
                final_entity = refreshed.get("entity") or {}
                current_state = str(final_entity.get("state") or "unknown").lower()
                if current_state == target_state:
                    verified = True
                    break
            if verified:
                break
            if attempt == 0:
                await self.client.call_service(
                    domain=domain,
                    service=service,
                    entity_ids=[entity_id],
                )
                retried = True

        if verified:
            response_message = f"{name} is now {target_state}."
        else:
            response_message = (
                f"{name} still has not reported {target_state}. "
                "I sent the safe command twice, but the device is not confirming the change."
            )

        return {
            "success": verified,
            "entity_id": entity_id,
            "name": name,
            "area_id": entity.get("area_id"),
            "area_name": entity.get("area_name"),
            "domain": domain,
            "target_state": target_state,
            "changed": verified,
            "command_sent": True,
            "retried": retried,
            "verified": verified,
            "already_in_target_state": False,
            "previous_state": previous_state,
            "current_state": current_state,
            "response_message": response_message,
            "entities": [self._public_state(final_entity)] if final_entity else [],
        }

    @staticmethod
    def _normalise(value: str) -> str:
        value = value.lower().strip().replace("_", " ")
        value = re.sub(r"[^a-z0-9%°\s'.-]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _display_value(state: str, unit: str | None) -> str:
        if not unit:
            return state

        compact_units = {"%", "°C", "°F"}
        separator = "" if unit in compact_units else " "
        return f"{state}{separator}{unit}"

    @classmethod
    def _safe_attributes(
        cls,
        attributes: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source = attributes or {}
        result: dict[str, Any] = {}

        for key in cls.SAFE_STATE_ATTRIBUTE_KEYS:
            if key not in source:
                continue

            value = source[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[key] = value

        return result

    async def _fresh_states(self) -> list[dict[str, Any]]:
        states = await self.client.get_states()
        self.registry.snapshot.states = states
        return states

    async def readable_entity_states(
        self,
        *,
        refresh: bool = True,
    ) -> list[dict[str, Any]]:
        snapshot = await self.registry.ensure_loaded()
        states = await self._fresh_states() if refresh else snapshot.states

        area_lookup = {
            str(area.get("area_id") or area.get("id")): str(area.get("name"))
            for area in snapshot.areas
            if (area.get("area_id") or area.get("id")) and area.get("name")
        }
        device_lookup = {
            str(device.get("id")): device
            for device in snapshot.devices
            if device.get("id")
        }
        registry_lookup = {
            str(entity.get("entity_id")): entity
            for entity in snapshot.entities
            if entity.get("entity_id")
        }

        results: list[dict[str, Any]] = []

        for state_object in states:
            entity_id = str(state_object.get("entity_id") or "")
            if "." not in entity_id:
                continue

            domain = entity_id.split(".", 1)[0]
            if domain not in self.READABLE_DOMAINS:
                continue

            registry_entity = registry_lookup.get(entity_id, {})
            if registry_entity.get("disabled_by") is not None:
                continue
            if registry_entity.get("hidden_by") is not None:
                continue

            device = device_lookup.get(
                str(registry_entity.get("device_id") or ""),
                {},
            )
            area_id_value = registry_entity.get("area_id") or device.get("area_id")
            area_id = str(area_id_value) if area_id_value else None
            area_name = area_lookup.get(area_id) if area_id else None

            attributes = self._safe_attributes(
                state_object.get("attributes")
                if isinstance(state_object.get("attributes"), dict)
                else {}
            )
            raw_attributes = (
                state_object.get("attributes")
                if isinstance(state_object.get("attributes"), dict)
                else {}
            )

            name = str(
                registry_entity.get("name")
                or raw_attributes.get("friendly_name")
                or registry_entity.get("original_name")
                or device.get("name_by_user")
                or device.get("name")
                or entity_id
            )
            state = str(state_object.get("state") or "unknown")
            unit_value = raw_attributes.get("unit_of_measurement")
            unit = str(unit_value) if unit_value not in {None, ""} else None
            device_class_value = raw_attributes.get("device_class")
            device_class = (
                str(device_class_value)
                if device_class_value not in {None, ""}
                else None
            )

            search_text = self._normalise(
                " ".join(
                    str(part)
                    for part in (
                        name,
                        entity_id,
                        device.get("name_by_user"),
                        device.get("name"),
                        area_id,
                        area_name,
                        domain,
                        device_class,
                        state,
                        unit,
                    )
                    if part
                )
            )

            results.append(
                {
                    "entity_id": entity_id,
                    "domain": domain,
                    "name": name,
                    "area_id": area_id,
                    "area_name": area_name,
                    "state": state,
                    "display_value": self._display_value(state, unit),
                    "available": state not in {"unavailable", "unknown", ""},
                    "device_class": device_class,
                    "entity_category": registry_entity.get("entity_category"),
                    "platform": registry_entity.get("platform"),
                    "device_id": registry_entity.get("device_id"),
                    "device_name": str(
                        device.get("name_by_user")
                        or device.get("name")
                        or ""
                    ),
                    "unit": unit,
                    "attributes": attributes,
                    "last_changed": state_object.get("last_changed"),
                    "last_updated": state_object.get("last_updated"),
                    "search_text": search_text,
                }
            )

        return sorted(
            results,
            key=lambda item: (
                item.get("area_name") or "",
                item.get("domain") or "",
                item.get("name") or "",
                item.get("entity_id") or "",
            ),
        )

    @staticmethod
    def _public_state(entity: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in entity.items()
            if key != "search_text"
        }

    async def runnable_routines(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return fresh script and automation entities available to run."""
        states = await self.client.get_states()
        routines: list[dict[str, Any]] = []

        for state in states:
            entity_id = str(state.get("entity_id") or "")
            domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
            if domain not in {"script", "automation"}:
                continue

            attributes = state.get("attributes") or {}
            config_key = (
                str(attributes.get("id") or "")
                if domain == "automation"
                else entity_id.split(".", 1)[1]
            )
            if not config_key:
                continue

            routines.append(
                {
                    "domain": domain,
                    "entity_id": entity_id,
                    "config_key": config_key,
                    "name": str(attributes.get("friendly_name") or entity_id),
                    "state": str(state.get("state") or "unknown"),
                    "mode": attributes.get("mode"),
                    "current": attributes.get("current"),
                    "last_triggered": attributes.get("last_triggered"),
                }
            )

        routines.sort(key=lambda item: (item["name"].lower(), item["entity_id"]))
        return routines[: max(1, min(int(limit), 200))]

    async def run_home_routine(
        self,
        entity_id: str,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Start one exact script or trigger one automation safely."""
        routines = {
            item["entity_id"]: item
            for item in await self.runnable_routines(limit=200)
        }
        routine = routines.get(entity_id)
        if routine is None:
            return {
                "success": False,
                "entity_id": entity_id,
                "response_message": "I couldn’t find that script or automation.",
            }

        domain = str(routine["domain"])
        routine_name = str(name or routine["name"])
        current_state = str(routine.get("state") or "unknown").lower()
        if current_state in {"unavailable", "unknown", ""}:
            return {
                "success": False,
                "domain": domain,
                "entity_id": entity_id,
                "name": routine_name,
                "response_message": f"{routine_name} is currently unavailable.",
            }

        if domain == "script":
            if current_state == "on":
                return {
                    "success": True,
                    "domain": domain,
                    "entity_id": entity_id,
                    "name": routine_name,
                    "changed": False,
                    "verified": True,
                    "already_running": True,
                    "response_message": f"{routine_name} is already running.",
                }

            await self.client.call_service(
                "script",
                "turn_on",
                entity_ids=[entity_id],
            )
            return {
                "success": True,
                "domain": domain,
                "entity_id": entity_id,
                "name": routine_name,
                "changed": True,
                "verified": False,
                "command_accepted": True,
                "response_message": f"{routine_name} started.",
            }

        await self.client.call_service(
            "automation",
            "trigger",
            entity_ids=[entity_id],
            service_data={"skip_condition": False},
        )
        return {
            "success": True,
            "domain": domain,
            "entity_id": entity_id,
            "name": routine_name,
            "changed": None,
            "verified": False,
            "conditions_respected": True,
            "command_accepted": True,
            "response_message": (
                f"{routine_name} was triggered with its conditions respected."
            ),
        }

    async def get_entity_state(
        self,
        entity_id: str,
    ) -> dict[str, Any]:
        match = next(
            (
                entity
                for entity in await self.readable_entity_states(refresh=True)
                if entity["entity_id"] == entity_id
            ),
            None,
        )

        if match is None:
            return {
                "success": False,
                "message": f"No readable Home Assistant entity matched {entity_id}.",
                "entity": None,
            }

        return {
            "success": True,
            "entity": self._public_state(match),
        }

    async def search_entity_states(
        self,
        query: str,
        *,
        domain: str | None = None,
        area_id: str | None = None,
        state_filter: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        normalised_query = self._normalise(query)
        normalised_state = self._normalise(state_filter or "")
        safe_limit = max(1, min(int(limit), 50))

        ranked: list[tuple[int, dict[str, Any]]] = []
        query_terms = [
            term
            for term in normalised_query.split()
            if len(term) >= 2
        ]

        for entity in await self.readable_entity_states(refresh=True):
            if domain and entity["domain"] != domain:
                continue
            if area_id and entity.get("area_id") != area_id:
                continue
            if normalised_state and self._normalise(entity["state"]) != normalised_state:
                continue

            search_text = entity["search_text"]
            normalised_name = self._normalise(entity["name"])
            normalised_entity_id = self._normalise(entity["entity_id"])
            score = 0

            if normalised_query:
                if normalised_query == normalised_name:
                    score += 140
                if normalised_query == normalised_entity_id:
                    score += 130
                if normalised_query in normalised_name:
                    score += 70
                if normalised_query in search_text:
                    score += 35
                score += sum(8 for term in query_terms if term in search_text)
            else:
                score = 1

            if score:
                ranked.append((score, entity))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1]["available"],
                item[1].get("area_name") or "",
                item[1]["name"],
            ),
            reverse=True,
        )
        entities = [
            self._public_state(entity)
            for _, entity in ranked[:safe_limit]
        ]

        return {
            "success": True,
            "query": query,
            "domain": domain,
            "area_id": area_id,
            "state_filter": state_filter,
            "count": len(entities),
            "entities": entities,
        }

    async def list_area_states(
        self,
        area_id: str,
        *,
        domain: str | None = None,
        state_filter: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        snapshot = await self.registry.ensure_loaded()
        area = next(
            (
                item
                for item in snapshot.areas
                if str(item.get("area_id") or item.get("id")) == area_id
            ),
            None,
        )
        if area is None:
            raise ValueError(f"Unknown area: {area_id}")

        normalised_state = self._normalise(state_filter or "")
        safe_limit = max(1, min(int(limit), 50))
        matches: list[dict[str, Any]] = []

        for entity in await self.readable_entity_states(refresh=True):
            if entity.get("area_id") != area_id:
                continue
            if domain and entity["domain"] != domain:
                continue
            if normalised_state and self._normalise(entity["state"]) != normalised_state:
                continue
            matches.append(self._public_state(entity))

        matches = matches[:safe_limit]
        return {
            "success": True,
            "area_id": area_id,
            "area_name": str(area.get("name") or area_id),
            "domain": domain,
            "state_filter": state_filter,
            "count": len(matches),
            "entities": matches,
        }
    @classmethod
    def _is_user_facing_active_entity(cls, entity: dict[str, Any]) -> bool:
        domain = str(entity.get("domain") or "")
        state = str(entity.get("state") or "").strip().lower()
        if domain not in cls.AREA_ACTIVE_DOMAINS:
            return False
        if state not in cls.AREA_ACTIVE_STATES.get(domain, set()):
            return False
        if str(entity.get("entity_category") or "").lower() in {
            "config",
            "diagnostic",
        }:
            return False

        combined = cls._normalise(
            " ".join(
                str(value)
                for value in (
                    entity.get("entity_id"),
                    entity.get("name"),
                    entity.get("device_name"),
                    entity.get("platform"),
                )
                if value
            )
        )
        if any(term in combined for term in cls.AREA_SUMMARY_EXCLUDED_TERMS):
            return False

        # Idle smart speakers and voice satellites are not "on" in the human
        # sense. Playing/paused/buffering devices remain visible.
        if domain == "media_player" and state == "on":
            if any(term in combined for term in {
                "home assistant voice",
                "voice satellite",
                "echo",
                "everywhere",
            }):
                return False

        return True

    @staticmethod
    def _area_summary_status(entity: dict[str, Any]) -> str:
        domain = str(entity.get("domain") or "")
        state = str(entity.get("state") or "unknown").lower()
        if domain == "media_player":
            return {
                "playing": "playing",
                "paused": "paused",
                "buffering": "buffering",
            }.get(state, "on")
        if domain == "climate":
            return f"set to {state.replace('_', ' ')}"
        return "on"

    @classmethod
    def _area_summary_priority(cls, entity: dict[str, Any]) -> tuple[int, str]:
        domain = str(entity.get("domain") or "")
        state = str(entity.get("state") or "").lower()
        score = {
            "light": 100,
            "media_player": 90,
            "fan": 80,
            "humidifier": 75,
            "switch": 70,
            "climate": 60,
        }.get(domain, 0)
        if domain == "media_player" and state == "playing":
            score += 15
        return (-score, str(entity.get("name") or entity.get("entity_id") or ""))

    @classmethod
    def _area_summary_key(cls, entity: dict[str, Any]) -> str:
        domain = str(entity.get("domain") or "")
        combined = cls._normalise(
            " ".join(
                str(value)
                for value in (
                    entity.get("name"),
                    entity.get("device_name"),
                    entity.get("entity_id"),
                )
                if value
            )
        )
        # Multiple integrations often expose the same physical television.
        if domain == "media_player" and re.search(r"\b(?:tv|television)\b", combined):
            return "media_player:television"
        return f"{domain}:{cls._normalise(str(entity.get('name') or entity.get('entity_id') or ''))}"

    async def list_active_area_devices(
        self,
        area_id: str,
        *,
        domains: set[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return only ordinary user-facing devices currently active in an area."""
        snapshot = await self.registry.ensure_loaded()
        area = next(
            (
                item
                for item in snapshot.areas
                if str(item.get("area_id") or item.get("id")) == area_id
            ),
            None,
        )
        if area is None:
            raise ValueError(f"Unknown area: {area_id}")

        allowed_domains = set(domains or self.AREA_ACTIVE_DOMAINS)
        allowed_domains &= self.AREA_ACTIVE_DOMAINS
        safe_limit = max(1, min(int(limit), 30))

        selected: dict[str, dict[str, Any]] = {}
        for entity in await self.readable_entity_states(refresh=True):
            if entity.get("area_id") != area_id:
                continue
            if str(entity.get("domain") or "") not in allowed_domains:
                continue
            if not self._is_user_facing_active_entity(entity):
                continue

            public = self._public_state(entity)
            public["summary_status"] = self._area_summary_status(entity)
            key = self._area_summary_key(entity)
            current = selected.get(key)
            if current is None or self._area_summary_priority(public) < self._area_summary_priority(current):
                selected[key] = public

        entities = sorted(selected.values(), key=self._area_summary_priority)[:safe_limit]
        return {
            "success": True,
            "area_id": area_id,
            "area_name": str(area.get("name") or area_id),
            "domains": sorted(allowed_domains),
            "count": len(entities),
            "entities": entities,
        }

    @staticmethod
    def _volume_percent(entity: dict[str, Any]) -> int | None:
        attributes = entity.get("attributes") or {}
        value = attributes.get("volume_level")
        if isinstance(value, (int, float)):
            return max(0, min(round(float(value) * 100), 100))
        return None

    @staticmethod
    def _muted_value(entity: dict[str, Any]) -> bool | None:
        attributes = entity.get("attributes") or {}
        value = attributes.get("is_volume_muted")
        return value if isinstance(value, bool) else None

    @classmethod
    def _media_action_matches(
        cls,
        action: str,
        entity: dict[str, Any],
        *,
        previous_entity: dict[str, Any] | None = None,
    ) -> bool | None:
        state = str(entity.get("state") or "unknown").lower()
        muted = cls._muted_value(entity)
        volume = cls._volume_percent(entity)

        if action in {"play", "resume"}:
            return state == "playing"
        if action == "pause":
            return state == "paused"
        if action == "stop":
            return state in cls.STOPPED_MEDIA_STATES
        if action == "mute":
            return muted is True
        if action == "unmute":
            return muted is False
        if action == "play_pause":
            if previous_entity is None:
                return None
            previous_state = str(previous_entity.get("state") or "unknown").lower()
            return state == ("paused" if previous_state == "playing" else "playing")
        if action in {"volume_up", "volume_down"}:
            if previous_entity is None:
                return None
            previous_volume = cls._volume_percent(previous_entity)
            if previous_volume is None or volume is None:
                return None
            return volume > previous_volume if action == "volume_up" else volume < previous_volume
        return None

    @staticmethod
    def _natural_media_action(action: str) -> str:
        return {
            "play": "play",
            "resume": "resume",
            "pause": "pause",
            "stop": "stop",
            "play_pause": "toggle playback on",
            "next": "skip to the next item on",
            "previous": "go to the previous item on",
            "volume_up": "turn up",
            "volume_down": "turn down",
            "mute": "mute",
            "unmute": "unmute",
        }.get(action, action.replace("_", " "))

    @staticmethod
    def _media_state_matches_target(
        current_state: str,
        target_state: str,
    ) -> bool:
        state = current_state.strip().lower()
        target = target_state.strip().lower()

        if target == "on":
            return state in {"on", "playing", "paused", "idle", "buffering"}
        return state == target

    async def run_media_shortcut(
        self,
        shortcut: str,
    ) -> dict[str, Any]:
        config = self.MEDIA_SHORTCUTS.get(shortcut)
        if config is None:
            raise ValueError(f"Unsupported media shortcut: {shortcut}")

        script_entity_id = str(config["script_entity_id"])
        name = str(config["name"])
        state_entity_id_value = config.get("state_entity_id")
        target_state_value = config.get("target_state")
        state_entity_id = str(state_entity_id_value) if state_entity_id_value else None
        target_state = str(target_state_value) if target_state_value else None
        previous_state: str | None = None

        if state_entity_id and target_state:
            current = await self.get_entity_state(state_entity_id)
            entity = current.get("entity") or {}
            previous_state = str(entity.get("state") or "unknown").lower()

            if previous_state == "unavailable":
                return {
                    "success": False,
                    "shortcut": shortcut,
                    "script_entity_id": script_entity_id,
                    "state_entity_id": state_entity_id,
                    "name": name,
                    "changed": False,
                    "verified": False,
                    "previous_state": previous_state,
                    "response_message": f"{name} is currently unavailable.",
                }

            if self._media_state_matches_target(previous_state, target_state):
                return {
                    "success": True,
                    "shortcut": shortcut,
                    "script_entity_id": script_entity_id,
                    "state_entity_id": state_entity_id,
                    "name": name,
                    "changed": False,
                    "verified": True,
                    "already_in_target_state": True,
                    "previous_state": previous_state,
                    "current_state": previous_state,
                    "response_message": f"{name} is already {target_state}.",
                }

        await self.client.call_service(
            "script",
            "turn_on",
            entity_ids=[script_entity_id],
        )

        if state_entity_id and target_state:
            current_state = previous_state or "unknown"
            verified = False
            for delay in self.STATE_VERIFY_DELAYS:
                await asyncio.sleep(delay)
                refreshed = await self.get_entity_state(state_entity_id)
                refreshed_entity = refreshed.get("entity") or {}
                current_state = str(refreshed_entity.get("state") or "unknown").lower()
                if self._media_state_matches_target(current_state, target_state):
                    verified = True
                    break

            response_message = (
                f"{name} is now {target_state}."
                if verified
                else (
                    f"I sent the command to turn the {name.lower()} {target_state}, "
                    f"but Home Assistant still reports it as {current_state}."
                )
            )
            return {
                "success": True,
                "shortcut": shortcut,
                "script_entity_id": script_entity_id,
                "state_entity_id": state_entity_id,
                "name": name,
                "changed": verified,
                "verified": verified,
                "already_in_target_state": False,
                "previous_state": previous_state,
                "current_state": current_state,
                "response_message": response_message,
            }

        # App scripts do not expose a reliable "app opened" state. Confirm only
        # that Home Assistant accepted the configured script call.
        return {
            "success": True,
            "shortcut": shortcut,
            "script_entity_id": script_entity_id,
            "name": name,
            "changed": None,
            "verified": False,
            "command_accepted": True,
            "response_message": f"{name} launch command sent.",
        }

    async def control_media_player(
        self,
        entity_id: str,
        action: str,
    ) -> dict[str, Any]:
        name = self.MEDIA_PLAYER_ENTITIES.get(entity_id)
        if name is None:
            raise ValueError(f"Unsupported media player: {entity_id}")

        service_config = self.MEDIA_ACTION_SERVICES.get(action)
        if service_config is None:
            raise ValueError(f"Unsupported media action: {action}")

        current = await self.get_entity_state(entity_id)
        previous_entity = current.get("entity") or {}
        previous_state = str(previous_entity.get("state") or "unknown").lower()
        if previous_state in {"unavailable", "unknown", ""}:
            return {
                "success": False,
                "entity_id": entity_id,
                "name": name,
                "action": action,
                "verified": False,
                "response_message": f"{name} is currently unavailable.",
            }

        already_matches = self._media_action_matches(action, previous_entity)
        previous_volume = self._volume_percent(previous_entity)
        if action == "volume_up" and previous_volume is not None and previous_volume >= 100:
            already_matches = True
        elif action == "volume_down" and previous_volume is not None and previous_volume <= 0:
            already_matches = True

        if already_matches is True:
            already_text = {
                "play": "already playing",
                "resume": "already playing",
                "pause": "already paused",
                "stop": "already stopped",
                "mute": "already muted",
                "unmute": "already unmuted",
                "volume_up": "already at maximum volume",
                "volume_down": "already at minimum volume",
            }.get(action, f"already set for {action.replace('_', ' ')}")
            return {
                "success": True,
                "entity_id": entity_id,
                "name": name,
                "action": action,
                "changed": False,
                "verified": True,
                "already_in_target_state": True,
                "previous_state": previous_state,
                "current_state": previous_state,
                "response_message": f"{name} is {already_text}.",
            }

        service, service_data = service_config
        await self.client.call_service(
            "media_player",
            service,
            entity_ids=[entity_id],
            service_data=dict(service_data),
        )

        # Next/previous cannot be verified reliably on every integration.
        if action in {"next", "previous"}:
            return {
                "success": True,
                "entity_id": entity_id,
                "name": name,
                "action": action,
                "changed": None,
                "verified": False,
                "command_accepted": True,
                "previous_state": previous_state,
                "response_message": (
                    f"{'Next-item' if action == 'next' else 'Previous-item'} command "
                    f"sent to {name}."
                ),
            }

        final_entity = previous_entity
        verified: bool | None = False
        for delay in self.STATE_VERIFY_DELAYS:
            await asyncio.sleep(delay)
            refreshed = await self.get_entity_state(entity_id)
            final_entity = refreshed.get("entity") or {}
            match = self._media_action_matches(
                action,
                final_entity,
                previous_entity=previous_entity,
            )
            if match is True:
                verified = True
                break
            if match is None:
                verified = None

        current_state = str(final_entity.get("state") or "unknown").lower()
        current_volume = self._volume_percent(final_entity)
        if verified is True:
            response_message = {
                "play": f"{name} is now playing.",
                "resume": f"{name} is now playing.",
                "pause": f"{name} is now paused.",
                "stop": f"{name} is now stopped.",
                "mute": f"{name} is now muted.",
                "unmute": f"{name} is now unmuted.",
                "play_pause": f"Playback was toggled on {name}.",
                "volume_up": (
                    f"{name} volume is now {current_volume}%."
                    if current_volume is not None
                    else f"{name} volume was turned up."
                ),
                "volume_down": (
                    f"{name} volume is now {current_volume}%."
                    if current_volume is not None
                    else f"{name} volume was turned down."
                ),
            }.get(action, f"{name} was updated.")
        elif verified is None:
            response_message = (
                f"The {self._natural_media_action(action)} command was sent to {name}, "
                "but this media player does not expose a state that confirms it."
            )
        else:
            response_message = (
                f"I sent the {self._natural_media_action(action)} command to {name}, "
                f"but Home Assistant still reports it as {current_state}."
            )

        return {
            "success": True,
            "entity_id": entity_id,
            "name": name,
            "action": action,
            "changed": verified is True,
            "verified": verified is True,
            "verification_available": verified is not None,
            "already_in_target_state": False,
            "previous_state": previous_state,
            "current_state": current_state,
            "previous_volume_percent": previous_volume,
            "current_volume_percent": current_volume,
            "response_message": response_message,
        }

    async def set_media_volume(
        self,
        entity_id: str,
        volume_percent: int,
    ) -> dict[str, Any]:
        name = self.MEDIA_PLAYER_ENTITIES.get(entity_id)
        if name is None:
            raise ValueError(f"Unsupported media player: {entity_id}")

        safe_volume = max(0, min(int(volume_percent), 100))
        current = await self.get_entity_state(entity_id)
        previous_entity = current.get("entity") or {}
        previous_state = str(previous_entity.get("state") or "unknown").lower()
        if previous_state in {"unavailable", "unknown", ""}:
            return {
                "success": False,
                "entity_id": entity_id,
                "name": name,
                "volume_percent": safe_volume,
                "verified": False,
                "response_message": f"{name} is currently unavailable.",
            }

        previous_volume = self._volume_percent(previous_entity)
        if previous_volume is not None and abs(previous_volume - safe_volume) <= 1:
            return {
                "success": True,
                "entity_id": entity_id,
                "name": name,
                "volume_percent": safe_volume,
                "changed": False,
                "verified": True,
                "already_in_target_state": True,
                "previous_volume_percent": previous_volume,
                "current_volume_percent": previous_volume,
                "response_message": f"{name} volume is already {previous_volume}%.",
            }

        await self.client.call_service(
            "media_player",
            "volume_set",
            entity_ids=[entity_id],
            service_data={"volume_level": safe_volume / 100},
        )

        current_volume: int | None = previous_volume
        verification_available = previous_volume is not None
        verified = False
        for delay in self.STATE_VERIFY_DELAYS:
            await asyncio.sleep(delay)
            refreshed = await self.get_entity_state(entity_id)
            current_entity = refreshed.get("entity") or {}
            current_volume = self._volume_percent(current_entity)
            if current_volume is None:
                verification_available = False
                continue
            verification_available = True
            if abs(current_volume - safe_volume) <= 2:
                verified = True
                break

        if verified:
            response_message = f"{name} volume is now {current_volume}%."
        elif not verification_available:
            response_message = (
                f"The {safe_volume}% volume command was sent to {name}, but this "
                "media player does not report its volume level."
            )
        else:
            response_message = (
                f"I sent the {safe_volume}% volume command to {name}, but Home "
                f"Assistant still reports {current_volume}%."
            )

        return {
            "success": True,
            "entity_id": entity_id,
            "name": name,
            "volume_percent": safe_volume,
            "changed": verified,
            "verified": verified,
            "verification_available": verification_available,
            "already_in_target_state": False,
            "previous_volume_percent": previous_volume,
            "current_volume_percent": current_volume,
            "response_message": response_message,
        }

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]:
        services = self.NOTIFICATION_SERVICES.get(recipient)
        if services is None:
            raise ValueError(f"Unsupported notification recipient: {recipient}")

        clean_message = message.strip()
        clean_title = title.strip() or "Jarvis"
        if not clean_message:
            raise ValueError("Notification message must not be empty")
        if len(clean_message) > 1000:
            raise ValueError("Notification message is too long")
        if len(clean_title) > 100:
            raise ValueError("Notification title is too long")

        completed: list[str] = []
        for service in services:
            await self.client.call_service(
                "notify",
                service,
                service_data={
                    "title": clean_title,
                    "message": clean_message,
                },
            )
            completed.append(f"notify.{service}")

        recipient_text = {
            "aaron": "your phone",
            "amber": "Amber's phone",
            "both": "both phones",
        }.get(recipient, recipient)
        return {
            "success": True,
            "recipient": recipient,
            "services": completed,
            "title": clean_title,
            "notification_message": clean_message,
            "verified": False,
            "delivery_confirmed": False,
            "command_accepted": True,
            "response_message": f"Notification sent to {recipient_text}.",
        }

    async def announce_message(
        self,
        target: str,
        message: str,
    ) -> dict[str, Any]:
        config = self.ANNOUNCEMENT_TARGETS.get(target)
        if config is None:
            raise ValueError(f"Unsupported announcement target: {target}")

        clean_message = message.strip()
        if not clean_message:
            raise ValueError("Announcement message must not be empty")
        if len(clean_message) > 500:
            raise ValueError("Announcement message is too long")

        entity_id = str(config["script_entity_id"])
        target_name = str(config["name"])
        await self.client.call_service(
            "script",
            "turn_on",
            entity_ids=[entity_id],
            service_data={
                "variables": {
                    "message": clean_message,
                }
            },
        )
        return {
            "success": True,
            "target": target,
            "target_name": target_name,
            "script_entity_id": entity_id,
            "announcement_message": clean_message,
            "verified": False,
            "heard_confirmed": False,
            "command_accepted": True,
            "response_message": f"Announcement sent to the {target_name}.",
        }
