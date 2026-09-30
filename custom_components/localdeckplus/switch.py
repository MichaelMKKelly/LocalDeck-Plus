"""Switch platform for the LocalDeck-Plus integration.

Provides a single "Disable LEDs" switch. While the switch is on, every
deck LED is turned off and the binding engine stops driving them, no
matter the state of their rules. Turning the switch off re-enables the
engine and re-applies every rule so the LEDs return to the correct state.
"""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SWITCH_DISABLE_LEDS, switch_unique_id
from .device import device_info_for_entry


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the LocalDeck-Plus switch from a config entry."""
    async_add_entities([DisableLedsSwitch(hass, entry)])


class DisableLedsSwitch(SwitchEntity, RestoreEntity):
    """Switch that disables all LocalDeck LEDs at once.

    While on, every LED is turned off and the binding engine stops driving
    them regardless of their rules. While off, the LEDs follow their rules
    again and are set to the correct state.
    """

    _attr_name = SWITCH_DISABLE_LEDS
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._runtime = entry.runtime_data
        self._attr_unique_id = switch_unique_id(entry.entry_id)
        device_info = device_info_for_entry(hass, entry)
        if device_info is not None:
            self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Restore the switch state so the LEDs start in the correct state.

        The binding engine is set up after this platform, so restoring here
        (before the engine's first evaluation) means the LEDs start in the
        correct state with no flicker.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_ON:
            self._runtime.disabled = True
            if self._runtime.light_entity_ids:
                await self._hass.services.async_call(
                    "light",
                    "turn_off",
                    {"entity_id": self._runtime.light_entity_ids},
                    blocking=False,
                )

    @property
    def is_on(self) -> bool:
        """Return True while the LEDs are disabled."""
        return self._runtime.disabled

    async def async_turn_on(self, **kwargs) -> None:
        """Disable the LEDs: turn them all off and stop the binding engine."""
        self._runtime.disabled = True
        if self._runtime.light_entity_ids:
            await self._hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": self._runtime.light_entity_ids},
                blocking=False,
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Re-enable the LEDs: resume the engine and re-apply every rule."""
        self._runtime.disabled = False
        if self._runtime.apply_all is not None:
            await self._runtime.apply_all()
        self.async_write_ha_state()
