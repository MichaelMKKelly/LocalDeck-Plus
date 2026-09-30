"""Shared device-linking helper for the LocalDeck-Plus platforms.

Both the switch and number platforms link their entity to the LocalDeck
(ESPHome) device. The logic is identical, so it lives here once instead of
being duplicated in each platform module.
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_DEVICE_ID, CONF_DEVICE_IDENTIFIER

_LOGGER = logging.getLogger(__name__)


def device_info_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> DeviceInfo | None:
    """Link an entity to the LocalDeck (ESPHome) device.

    Prefers the device's own identifiers from the registry. If the lookup
    yields a device with no identifiers (e.g. a stale registry id), falls
    back to the identifier stored at setup time under the ESPHome
    namespace. Returns ``None`` if no usable identifier is available, in
    which case the entity is added without a device link.
    """
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(entry.data[CONF_DEVICE_ID])
    if device is not None and device.identifiers:
        return DeviceInfo(identifiers=device.identifiers)
    identifier = entry.data.get(CONF_DEVICE_IDENTIFIER)
    if identifier:
        _LOGGER.debug(
            "Device %s has no registry identifiers; falling back to stored "
            "identifier %r",
            entry.data[CONF_DEVICE_ID],
            identifier,
        )
        return DeviceInfo(identifiers={("esphome", identifier)})
    return None
