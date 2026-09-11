"""Discovery helpers for the LocalDeck-Plus integration."""

import logging
import re

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

LED_NAME_RE = re.compile(r"LED\s+(\d+)", re.IGNORECASE)
BUTTON_NAME_RE = re.compile(r"Button\s+(\d+)", re.IGNORECASE)


def get_esphome_devices(hass):
    """Return all devices belonging to the ESPHome integration."""
    device_registry = dr.async_get(hass)
    esphome_entry_ids = {
        entry.entry_id for entry in hass.config_entries.async_entries("esphome")
    }
    return [
        device
        for device in device_registry.devices
        if device.config_entries & esphome_entry_ids
    ]


def get_localdeckplus_devices(hass):
    """Return ESPHome devices that expose at least one LocalDeck LED light.

    A device is considered a LocalDeck device when it has at least one
    light entity whose name matches the LocalDeck LED naming pattern
    (e.g. ``"LED 01 R1C1"``). This filters the device list down to the
    devices the integration can actually configure, so the config flow
    does not offer unrelated ESPHome devices.
    """
    esphome_devices = get_esphome_devices(hass)
    if not esphome_devices:
        return []
    device_ids = {device.id for device in esphome_devices}
    entity_registry = er.async_get(hass)
    localdeckplus_device_ids = set()
    for entry in entity_registry.entities.values():
        if entry.domain != "light":
            continue
        if entry.device_id in device_ids and LED_NAME_RE.search(
            entry.original_name or ""
        ):
            localdeckplus_device_ids.add(entry.device_id)
    return [
        device for device in esphome_devices if device.id in localdeckplus_device_ids
    ]


def discover_localdeckplus_entities(hass, device_id):
    """Discover LocalDeck light and event entities for a device.

    Returns (lights, events):
    - lights: {led_number: entity_id}
    - events: {button_number: entity_id}
    """
    entity_registry = er.async_get(hass)
    lights = {}
    events = {}
    for entity_id, entry in entity_registry.entities.items():
        if entry.device_id != device_id:
            continue
        name = entry.original_name or ""
        if entry.domain == "light":
            match = LED_NAME_RE.search(name)
            if match:
                lights[int(match.group(1))] = entity_id
        elif entry.domain == "event":
            match = BUTTON_NAME_RE.search(name)
            if match:
                events[int(match.group(1))] = entity_id
    _LOGGER.debug(
        "Discovered %d lights and %d events for device %s",
        len(lights),
        len(events),
        device_id,
    )
    return lights, events


def get_button_names(hass, device_id):
    """Return ``{button_number: name}`` for LocalDeck button event entities.

    The names are the ESPHome event entity names (e.g. ``"Button 01 R1C1"``)
    and are used to label the per-button entries in the options flow.
    """
    entity_registry = er.async_get(hass)
    names = {}
    for entity_id, entry in entity_registry.entities.items():
        if entry.device_id != device_id:
            continue
        if entry.domain == "event":
            name = entry.original_name or ""
            match = BUTTON_NAME_RE.search(name)
            if match:
                names[int(match.group(1))] = name
    return names
