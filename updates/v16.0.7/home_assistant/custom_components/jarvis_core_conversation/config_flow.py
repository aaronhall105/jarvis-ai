from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_AUDIO_GATE_ENABLED,
    CONF_AUDIO_GATE_MIGRATED,
    CONF_FOLLOW_UP_MODE,
    CONF_FOLLOW_UP_WINDOW,
    CONF_SHOW_PROGRESS_TEXT,
    CONF_SPOKEN_PROGRESS,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_AUDIO_GATE_ENABLED,
    DEFAULT_FOLLOW_UP_MODE,
    DEFAULT_FOLLOW_UP_WINDOW,
    DEFAULT_SHOW_PROGRESS_TEXT,
    DEFAULT_SPOKEN_PROGRESS,
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
                        CONF_AUDIO_GATE_ENABLED: DEFAULT_AUDIO_GATE_ENABLED,
                        CONF_AUDIO_GATE_MIGRATED: True,
                        CONF_FOLLOW_UP_WINDOW: DEFAULT_FOLLOW_UP_WINDOW,
                        CONF_SPOKEN_PROGRESS: DEFAULT_SPOKEN_PROGRESS,
                        CONF_SHOW_PROGRESS_TEXT: DEFAULT_SHOW_PROGRESS_TEXT,
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
        """Manage follow-up listening and spoken progress."""

        if user_input is not None:
            saved = dict(user_input)
            saved[CONF_AUDIO_GATE_MIGRATED] = True
            return self.async_create_entry(data=saved)

        current_mode = self.config_entry.options.get(
            CONF_FOLLOW_UP_MODE,
            DEFAULT_FOLLOW_UP_MODE,
        )
        audio_gate_enabled = self.config_entry.options.get(
            CONF_AUDIO_GATE_ENABLED,
            DEFAULT_AUDIO_GATE_ENABLED,
        )
        follow_up_window = self.config_entry.options.get(
            CONF_FOLLOW_UP_WINDOW,
            DEFAULT_FOLLOW_UP_WINDOW,
        )
        spoken_progress = self.config_entry.options.get(
            CONF_SPOKEN_PROGRESS,
            DEFAULT_SPOKEN_PROGRESS,
        )
        show_progress_text = self.config_entry.options.get(
            CONF_SHOW_PROGRESS_TEXT,
            DEFAULT_SHOW_PROGRESS_TEXT,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FOLLOW_UP_MODE,
                        default=current_mode,
                    ): vol.In(FOLLOW_UP_MODES),
                    vol.Required(
                        CONF_AUDIO_GATE_ENABLED,
                        default=audio_gate_enabled,
                    ): bool,
                    vol.Required(
                        CONF_FOLLOW_UP_WINDOW,
                        default=follow_up_window,
                    ): vol.All(vol.Coerce(int), vol.Range(min=3, max=20)),
                    vol.Required(
                        CONF_SPOKEN_PROGRESS,
                        default=spoken_progress,
                    ): bool,
                    vol.Required(
                        CONF_SHOW_PROGRESS_TEXT,
                        default=show_progress_text,
                    ): bool,
                }
            ),
        )
