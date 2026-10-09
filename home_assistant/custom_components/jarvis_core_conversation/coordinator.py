"""Shared Jarvis HomeExperience coordinator for Home Assistant entities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_TIMEOUT,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_HOME_REFRESH_SECONDS,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


class JarvisHomeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the authoritative presentation model without re-deriving HA truth."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name="Jarvis Home",
            update_interval=timedelta(seconds=DEFAULT_HOME_REFRESH_SECONDS),
        )
        self.entry = entry
        self._etag = ""
        self._last_data: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        base_url = str(self.entry.data[CONF_URL]).rstrip("/")
        token = str(
            self.entry.options.get(
                CONF_TOKEN,
                self.entry.data.get(CONF_TOKEN, ""),
            )
        ).strip()
        if not token:
            raise UpdateFailed("Add the Jarvis mobile token in integration options")
        timeout = int(self.entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if self._etag:
            headers["If-None-Match"] = self._etag
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{base_url}/api/home",
                headers=headers,
                timeout=min(max(timeout, 5), 30),
            ) as response:
                if response.status == 304 and self._last_data is not None:
                    return self._last_data
                if response.status != 200:
                    raise UpdateFailed(f"Jarvis Home returned HTTP {response.status}")
                payload = await response.json()
                if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                    raise UpdateFailed("Jarvis Home returned an unsupported response")
                self._etag = str(response.headers.get("ETag") or "")
                self._last_data = payload
                return payload
        except UpdateFailed:
            raise
        except (TimeoutError, ClientError, ValueError) as exc:
            raise UpdateFailed("Jarvis Home is unavailable") from exc
