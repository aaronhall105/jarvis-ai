from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FOLLOW_UP_MODE,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_FOLLOW_UP_MODE,
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    DOMAIN,
    FOLLOW_UP_MODES,
)


class JarvisCoreConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Configure Jarvis Core Conversation."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""

        return JarvisCoreOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Handle initial setup."""

        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{str(user_input[CONF_URL]).rstrip('/')}/health",
                    timeout=user_input[CONF_TIMEOUT],
                ) as response:
                    if response.status != 200:
                        raise RuntimeError("Unexpected health response")
            except Exception:  # noqa: BLE001 - config flow must show one UI error
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Jarvis Core",
                    data=user_input,
                    options={
                        CONF_FOLLOW_UP_MODE: DEFAULT_FOLLOW_UP_MODE,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL,
                        default=DEFAULT_URL,
                    ): str,
                    vol.Required(
                        CONF_TIMEOUT,
                        default=DEFAULT_TIMEOUT,
                    ): int,
                }
            ),
            errors=errors,
        )


class JarvisCoreOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage Jarvis Core Conversation options."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Manage follow-up listening."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_mode = self.config_entry.options.get(
            CONF_FOLLOW_UP_MODE,
            DEFAULT_FOLLOW_UP_MODE,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FOLLOW_UP_MODE,
                        default=current_mode,
                    ): vol.In(FOLLOW_UP_MODES),
                }
            ),
        )
