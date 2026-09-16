"""Diagnostics for the LocalDeck-Plus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, DEFAULT_MASTER_BRIGHTNESS
from .discovery import discover_localdeckplus_entities


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    lights, events = discover_localdeckplus_entities(hass, entry.data[CONF_DEVICE_ID])
    runtime = entry.runtime_data
    return {
        "entry_data": dict(entry.data),
        "options": dict(entry.options),
        "discovered_lights": {str(k): v for k, v in sorted(lights.items())},
        "discovered_events": {str(k): v for k, v in sorted(events.items())},
        "leds_disabled": bool(getattr(runtime, "disabled", False)),
        "master_brightness": float(
            getattr(runtime, "master_brightness", DEFAULT_MASTER_BRIGHTNESS)
        ),
    }
