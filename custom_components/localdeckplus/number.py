"""Number platform for the LocalDeck-Plus integration.

Provides a single "Master Brightness" number entity: a 0-100 slider that
acts as a global multiplier on the brightness of every LED rule. While the
value is below 100, every LED's brightness is scaled down proportionally
(e.g. a rule at 50% with the master at 50% yields 25%); at 0 every LED is
turned off. The value is remembered across reloads and restarts via the
restore state and is intentionally not part of the import/export
configuration.
"""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IDENTIFIER,
    DEFAULT_MASTER_BRIGHTNESS,
    NUMBER_MASTER_BRIGHTNESS,
    number_master_brightness_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the LocalDeck-Plus number entity from a config entry."""
    async_add_entities([MasterBrightnessNumber(hass, entry)])


class MasterBrightnessNumber(NumberEntity, RestoreEntity):
    """Global brightness multiplier for all LocalDeck LED rules.

    A 0-100 slider. The value multiplies the brightness set by every LED
    rule (condition and follow-light). While the value is below 100 the
    LEDs are dimmed proportionally; at 0 they are turned off. Changing the
    value re-applies every rule so the LEDs reflect the new brightness
    immediately.
    """

    _attr_name = NUMBER_MASTER_BRIGHTNESS
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._runtime = entry.runtime_data
        self._attr_unique_id = number_master_brightness_unique_id(entry.entry_id)
        device_info = _device_info(hass, entry)
        if device_info is not None:
            self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Restore the last value so the engine uses it from first evaluation.

        The number platform is set up before the binding engine, so
        restoring here means the engine's first evaluation already applies
        the saved master brightness (no startup flicker).
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                value = float(last_state.state)
            except (TypeError, ValueError):
                value = DEFAULT_MASTER_BRIGHTNESS
            self._runtime.master_brightness = max(0.0, min(100.0, value))

    @property
    def native_value(self) -> float:
        """Return the current master brightness."""
        return self._runtime.master_brightness

    async def async_set_native_value(self, value: float) -> None:
        """Set the master brightness and re-apply every rule.

        Re-applying every binding makes the LEDs reflect the new brightness
        immediately.
        """
        self._runtime.master_brightness = max(0.0, min(100.0, float(value)))
        if self._runtime.apply_all is not None:
            await self._runtime.apply_all()
        self.async_write_ha_state()


def _device_info(hass: HomeAssistant, entry: ConfigEntry) -> DeviceInfo | None:
    """Link the number entity to the LocalDeck (ESPHome) device.

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
