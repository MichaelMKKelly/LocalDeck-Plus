"""Number platform for the LocalDeck-Plus integration.

Provides a single "Master Brightness" number entity: a 0-100 slider that
acts as a global multiplier on the brightness of every LED rule. While the
value is below 100, every LED's brightness is scaled down proportionally
(e.g. a rule at 50% with the master at 50% yields 25%); at 0 every LED is
turned off. The value is remembered across reloads and restarts via the
restore state and is intentionally not part of the import/export
configuration.
"""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEFAULT_MASTER_BRIGHTNESS,
    NUMBER_MASTER_BRIGHTNESS,
    number_master_brightness_unique_id,
)
from .device import device_info_for_entry


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
        device_info = device_info_for_entry(hass, entry)
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
