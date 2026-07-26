from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUDIO_GATE_ENABLED,
    CONF_AUDIO_GATE_MIGRATED,
    CONF_FOLLOW_UP_MODE,
    CONF_FOLLOW_UP_WINDOW,
    DEFAULT_AUDIO_GATE_ENABLED,
    DEFAULT_FOLLOW_UP_WINDOW,
    FOLLOW_UP_ALWAYS,
    FOLLOW_UP_SMART,
)

PLATFORMS: list[Platform] = [Platform.CONVERSATION]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Jarvis Core Conversation from a config entry."""

    options = dict(entry.options)
    if not options.get(CONF_AUDIO_GATE_MIGRATED):
        # Older Jarvis releases defaulted to an always-open microphone. Move that
        # legacy default to Smart once, while preserving explicit disabled/questions
        # choices and keeping the config-entry schema at version 2.
        if options.get(CONF_FOLLOW_UP_MODE, FOLLOW_UP_ALWAYS) == FOLLOW_UP_ALWAYS:
            options[CONF_FOLLOW_UP_MODE] = FOLLOW_UP_SMART
        options.setdefault(CONF_AUDIO_GATE_ENABLED, DEFAULT_AUDIO_GATE_ENABLED)
        options.setdefault(CONF_FOLLOW_UP_WINDOW, DEFAULT_FOLLOW_UP_WINDOW)
        options[CONF_AUDIO_GATE_MIGRATED] = True
        hass.config_entries.async_update_entry(entry, options=options)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a Jarvis Core Conversation config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
