"""Config flow for the LocalDeck-Plus integration."""

import json

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    ActionSelector,
    ColorRGBSelector,
    ConditionSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACTION,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR,
    CONF_CONFIG,
    CONF_CONDITION,
    CONF_CONDITIONS,
    CONF_DEVICE_ID,
    CONF_DEVICE_IDENTIFIER,
    CONF_DEVICE_NAME,
    CONF_EFFECT,
    CONF_ENABLED,
    CONF_FOLLOW_LIGHT,
    CONF_LIGHT_STATE,
    DEFAULT_BRIGHTNESS_PCT,
    DOMAIN,
    EFFECT_NONE,
    EFFECT_OPTIONS,
    OPT_BUTTON_ACTIONS,
    OPT_LED_BINDINGS,
)
from .discovery import (
    discover_localdeckplus_entities,
    get_button_names,
    get_esphome_devices,
    get_localdeckplus_devices,
)
from .light_state import (
    light_state_from_form_fields,
    light_state_to_form_fields,
    rule_enabled,
    rule_light_state,
)


def _action_key(button: int, event_type: str) -> str:
    return f"{button:02d}:{event_type}"


def _led_key(led: int) -> str:
    return f"led_{led:02d}"


# The four press event types a button can be configured for. Used when
# moving a button's configuration to another button.
_BUTTON_EVENT_TYPES = ("single", "double", "triple", "long")


def _light_state_hex(light_state) -> str | None:
    """Return a light state's target color as ``#RRGGBB``, or ``None``.

    Used to label condition rules in the LED conditions menu so the user
    can tell rules apart at a glance.
    """
    if not isinstance(light_state, dict):
        return None
    rgb = light_state.get("rgb_color")
    if (
        isinstance(rgb, (list, tuple))
        and len(rgb) == 3
        and all(isinstance(c, int) for c in rgb)
    ):
        return "#{:02X}{:02X}{:02X}".format(*rgb)
    return None


def _light_state_effect(light_state) -> str | None:
    """Return a light state's effect name, or ``None`` if none is set.

    Used to label condition rules in the LED conditions menu. The
    ``EFFECT_NONE`` sentinel (meaning "no effect") is treated as absent.
    """
    if not isinstance(light_state, dict):
        return None
    effect = light_state.get("effect")
    if isinstance(effect, str) and effect and effect != EFFECT_NONE:
        return effect
    return None


class LocalDeckPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for LocalDeck-Plus."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step: select the ESPHome device."""
        errors = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            device_registry = dr.async_get(self.hass)
            device = device_registry.async_get(device_id)
            if device is None:
                errors["base"] = "unknown_device"
            else:
                lights, events = discover_localdeckplus_entities(self.hass, device_id)
                if not lights:
                    errors["base"] = "no_lights_found"
                else:
                    identifiers = sorted(device.identifiers)
                    device_identifier = (
                        identifiers[0][1] if identifiers else device_id
                    )
                    title = device.name or "LocalDeck"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_DEVICE_ID: device_id,
                            CONF_DEVICE_NAME: title,
                            CONF_DEVICE_IDENTIFIER: device_identifier,
                        },
                    )

        localdeckplus_devices = get_localdeckplus_devices(self.hass)
        # Hide devices that already have a LocalDeck config entry so the
        # same LocalDeck cannot be added twice.
        existing_device_ids = {
            entry.data.get(CONF_DEVICE_ID)
            for entry in self._async_current_entries()
        }
        devices = [
            device
            for device in localdeckplus_devices
            if device.id not in existing_device_ids
        ]
        if not devices:
            if localdeckplus_devices:
                return self.async_abort(reason="all_devices_configured")
            if get_esphome_devices(self.hass):
                return self.async_abort(reason="no_localdeckplus_devices")
            return self.async_abort(reason="no_devices_found")

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": d.id, "label": d.name or d.id}
                            for d in sorted(devices, key=lambda d: d.name or d.id)
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this entry."""
        return LocalDeckPlusOptionsFlow()


class LocalDeckPlusOptionsFlow(OptionsFlow):
    """Options flow for LocalDeck-Plus.

    The main menu lists one entry per LocalDeck button event entity.
    Selecting a button opens a submenu with its four press actions
    (single, double, triple, long) and a "Set LED conditions" entry that
    manages the condition rules of the LED paired with the button.
    """

    async def async_step_init(self, user_input=None):
        return await self.async_step_menu()

    async def async_step_done(self, user_input=None):
        # data=None is required: OptionsFlowManager.async_finish_flow calls
        # async_update_entry(entry, options=result["data"]) when data is not
        # None, which would wipe the options we saved during the steps.
        return self.async_create_entry(title="", data=None)

    # ------------------------------------------------------------------
    # Main menu: one entry per button event entity
    # ------------------------------------------------------------------

    async def async_step_menu(self, user_input=None):
        """Show the main menu: one entry per LocalDeck button entity.

        A handler for each button entry is created on the fly (as an
        instance attribute) so any number of buttons is supported. The
        labels come straight from the ESPHome event entity names.
        """
        button_names = get_button_names(
            self.hass, self.config_entry.data[CONF_DEVICE_ID]
        )
        menu_options = {}
        for number in sorted(button_names):
            step_id = f"button_{number:02d}"

            async def _button_step(_user_input=None, _number=number):
                self._pending_button = _number
                return await self.async_step_button_menu()

            setattr(self, f"async_step_{step_id}", _button_step)
            menu_options[step_id] = button_names[number]
        menu_options["advanced"] = "Advanced configuration options"
        menu_options["done"] = "Done"
        return self.async_show_menu(step_id="menu", menu_options=menu_options)

    # ------------------------------------------------------------------
    # Per-button submenu
    # ------------------------------------------------------------------

    async def async_step_button_menu(self, user_input=None):
        """Show the submenu for the pending button."""
        menu_options = {
            "button_action_single": "Press action",
            "button_action_double": "Double press action",
            "button_action_triple": "Triple press action",
            "button_action_long": "Long press action",
            "set_led_conditions": "Set LED conditions",
            "move_configuration": "Move configuration to another button",
            "return_to_main": "Return to main menu",
        }
        return self.async_show_menu(step_id="button_menu", menu_options=menu_options)

    async def async_step_return_to_main(self, user_input=None):
        """Return to the main menu."""
        return await self.async_step_menu()

    async def async_step_button_action_single(self, user_input=None):
        return await self._button_action_form(
            "single", "button_action_single", user_input
        )

    async def async_step_button_action_double(self, user_input=None):
        return await self._button_action_form(
            "double", "button_action_double", user_input
        )

    async def async_step_button_action_triple(self, user_input=None):
        return await self._button_action_form(
            "triple", "button_action_triple", user_input
        )

    async def async_step_button_action_long(self, user_input=None):
        return await self._button_action_form(
            "long", "button_action_long", user_input
        )

    async def _button_action_form(self, event_type, step_id, user_input):
        """Show the action selector for the pending button + event type.

        The action is optional: leaving it empty clears the action, which
        makes the button inactive for that press type.
        """
        if user_input is not None:
            key = _action_key(self._pending_button, event_type)
            options = dict(self.config_entry.options)
            actions = dict(options.get(OPT_BUTTON_ACTIONS, {}))
            action = user_input.get(CONF_ACTION)
            if action:
                actions[key] = action
            else:
                actions.pop(key, None)
            options[OPT_BUTTON_ACTIONS] = actions
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=options
            )
            return await self.async_step_button_menu()
        key = _action_key(self._pending_button, event_type)
        existing = self.config_entry.options.get(OPT_BUTTON_ACTIONS, {}).get(key)
        suggested = {CONF_ACTION: existing} if existing else {}
        schema = self.add_suggested_values_to_schema(
            vol.Schema({vol.Optional(CONF_ACTION): ActionSelector()}),
            suggested,
        )
        return self.async_show_form(step_id=step_id, data_schema=schema)

    # ------------------------------------------------------------------
    # Move configuration to another button
    # ------------------------------------------------------------------

    async def async_step_move_configuration(self, user_input=None):
        """Show the target-button selection menu for moving configuration.

        One entry per other button (the source button is excluded). The
        step description warns that the target's existing configuration
        will be overwritten. Selecting a target moves the source button's
        configuration to it and lands on the target's button menu.
        """
        source = self._pending_button
        button_names = get_button_names(
            self.hass, self.config_entry.data[CONF_DEVICE_ID]
        )
        menu_options = {}
        for number in sorted(button_names):
            if number == source:
                continue
            step_id = f"move_target_{number:02d}"

            async def _target_step(_user_input=None, _target=number):
                self._move_button_configuration(source, _target)
                self._pending_button = _target
                return await self.async_step_button_menu()

            setattr(self, f"async_step_{step_id}", _target_step)
            menu_options[step_id] = button_names[number]
        menu_options["return_to_button_configuration"] = (
            "Return to button configuration"
        )
        return self.async_show_menu(
            step_id="move_configuration", menu_options=menu_options
        )

    async def async_step_return_to_button_configuration(self, user_input=None):
        """Return to the button configuration menu without moving."""
        return await self.async_step_button_menu()

    def _move_button_configuration(self, source: int, target: int) -> None:
        """Move all configuration from the source button to the target.

        The target's existing configuration (press actions and LED
        binding) is completely replaced by the source's, and the source
        is left with a cleared configuration.
        """
        options = dict(self.config_entry.options)
        actions = dict(options.get(OPT_BUTTON_ACTIONS, {}))
        bindings = dict(options.get(OPT_LED_BINDINGS, {}))

        # Capture the source's configuration before clearing it.
        source_actions = {
            event_type: actions[_action_key(source, event_type)]
            for event_type in _BUTTON_EVENT_TYPES
            if _action_key(source, event_type) in actions
        }
        source_binding = bindings.get(_led_key(source))

        # Clear the source's configuration.
        for event_type in _BUTTON_EVENT_TYPES:
            actions.pop(_action_key(source, event_type), None)
        bindings.pop(_led_key(source), None)

        # Overwrite the target's configuration with the source's.
        for event_type in _BUTTON_EVENT_TYPES:
            actions.pop(_action_key(target, event_type), None)
        bindings.pop(_led_key(target), None)
        for event_type, action in source_actions.items():
            actions[_action_key(target, event_type)] = action
        if source_binding is not None:
            bindings[_led_key(target)] = source_binding

        options[OPT_BUTTON_ACTIONS] = actions
        options[OPT_LED_BINDINGS] = bindings
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=options
        )

    # ------------------------------------------------------------------
    # Advanced configuration options (import / export / clear)
    # ------------------------------------------------------------------

    async def async_step_advanced(self, user_input=None):
        """Show the advanced configuration options menu."""
        menu_options = {
            "import_export": "Import / Export configuration",
            "clear_configuration": "Clear configuration",
            "return_to_main": "Return to main menu",
        }
        return self.async_show_menu(step_id="advanced", menu_options=menu_options)

    async def async_step_import_export(self, user_input=None):
        """Show the import/export form with the configuration text box.

        The text box is pre-filled with the current configuration (button
        actions and LED bindings, as JSON) so the user can copy it. Pasting
        a configuration into the box and submitting imports it, replacing
        the current button actions and LED bindings.
        """
        if user_input is not None:
            raw = user_input.get(CONF_CONFIG) or ""
            try:
                config = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, ValueError):
                return self.async_show_form(
                    step_id="import_export",
                    data_schema=self._config_text_schema(),
                    errors={"base": "invalid_json"},
                )
            if not isinstance(config, dict):
                return self.async_show_form(
                    step_id="import_export",
                    data_schema=self._config_text_schema(),
                    errors={"base": "invalid_config"},
                )
            for key in (OPT_BUTTON_ACTIONS, OPT_LED_BINDINGS):
                if key in config and not isinstance(config[key], dict):
                    return self.async_show_form(
                        step_id="import_export",
                        data_schema=self._config_text_schema(),
                        errors={"base": "invalid_config"},
                    )
            options = dict(self.config_entry.options)
            if OPT_BUTTON_ACTIONS in config:
                options[OPT_BUTTON_ACTIONS] = config[OPT_BUTTON_ACTIONS]
            if OPT_LED_BINDINGS in config:
                options[OPT_LED_BINDINGS] = config[OPT_LED_BINDINGS]
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=options
            )
            return await self.async_step_menu()
        schema = self.add_suggested_values_to_schema(
            self._config_text_schema(),
            {CONF_CONFIG: self._export_config_text()},
        )
        return self.async_show_form(step_id="import_export", data_schema=schema)

    async def async_step_clear_configuration(self, user_input=None):
        """Clear all button actions and LED bindings, then return to the menu."""
        options = dict(self.config_entry.options)
        options[OPT_BUTTON_ACTIONS] = {}
        options[OPT_LED_BINDINGS] = {}
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=options
        )
        return await self.async_step_menu()

    # ------------------------------------------------------------------
    # LED conditions (the LED paired with the pending button)
    # ------------------------------------------------------------------

    async def async_step_set_led_conditions(self, user_input=None):
        """Enter the LED conditions menu for the button's paired LED."""
        self._pending_led = self._pending_button
        return await self.async_step_led_conditions_menu()

    async def async_step_led_conditions_menu(self, user_input=None):
        """Menu to manage the condition rules for the pending LED.

        Each existing rule gets its own entry; selecting one opens that
        rule's configuration menu. A handler for each rule entry is
        created on the fly (as an instance attribute) so any number of
        rules is supported.
        """
        conditions = self._led_conditions()
        menu_options = {}
        for index in range(len(conditions)):
            step_id = f"led_condition_{index + 1:02d}"

            async def _rule_step(_user_input=None, _index=index):
                self._pending_condition_index = _index
                return await self.async_step_led_condition_menu()

            setattr(self, f"async_step_{step_id}", _rule_step)
            rule = conditions[index]
            # Disabled rules are labelled "Rule x (Disabled)" so the user
            # can tell them apart at a glance; enabled rules keep the
            # current label format.
            if rule_enabled(rule):
                label = f"Rule {index + 1}"
            else:
                label = f"Rule {index + 1} (Disabled)"
            if CONF_FOLLOW_LIGHT in rule:
                follow_entity = rule.get(CONF_FOLLOW_LIGHT)
                label += " - Follow Light"
                if follow_entity:
                    label += f" - {follow_entity}"
            else:
                light_state = rule.get(CONF_LIGHT_STATE)
                label += " - Condition"
                hex_color = _light_state_hex(light_state)
                if hex_color:
                    label += f" - {hex_color}"
                effect = _light_state_effect(light_state)
                if effect:
                    label += f" - {effect}"
            menu_options[step_id] = label
        menu_options["add_condition"] = "Add a new condition rule"
        # Only one follow-light rule per LED: hide the add option while one
        # exists; it reappears once that rule is deleted.
        has_follow_light = any(CONF_FOLLOW_LIGHT in rule for rule in conditions)
        if not has_follow_light:
            menu_options["add_follow_light"] = "Add a new follow light rule"
        menu_options["return_to_button"] = "Return to button menu"
        return self.async_show_menu(
            step_id="led_conditions_menu", menu_options=menu_options
        )

    async def async_step_return_to_button(self, user_input=None):
        """Return to the button submenu."""
        return await self.async_step_button_menu()

    async def async_step_add_condition(self, user_input=None):
        """Add a new condition rule for the pending LED."""
        if user_input is not None:
            conditions = self._led_conditions()
            conditions.append(
                {
                    CONF_CONDITION: user_input[CONF_CONDITION],
                    CONF_LIGHT_STATE: light_state_from_form_fields(user_input),
                    CONF_ENABLED: True,
                }
            )
            self._save_led_conditions(conditions)
            return await self.async_step_led_conditions_menu()
        return self.async_show_form(
            step_id="add_condition", data_schema=self._condition_rule_schema()
        )

    async def async_step_add_follow_light(self, user_input=None):
        """Add a new follow-light rule for the pending LED."""
        if user_input is not None:
            conditions = self._led_conditions()
            conditions.append(
                {
                    CONF_FOLLOW_LIGHT: user_input[CONF_FOLLOW_LIGHT],
                    CONF_ENABLED: True,
                }
            )
            self._save_led_conditions(conditions)
            return await self.async_step_led_conditions_menu()
        return self.async_show_form(
            step_id="add_follow_light",
            data_schema=self._follow_light_rule_schema(),
        )

    async def async_step_led_condition_menu(self, user_input=None):
        """Configuration menu for a single condition rule.

        The rules form a priority list (evaluated in order, first match
        wins), so a rule can be moved up or down the list. The first rule
        can only move down; the last rule can only move up.
        """
        conditions = self._led_conditions()
        index = self._pending_condition_index
        is_follow = CONF_FOLLOW_LIGHT in conditions[index]
        menu_options = {"led_condition_edit": "Configure this rule"}
        if index > 0:
            menu_options["led_condition_move_up"] = "Move up in priority"
        if index < len(conditions) - 1:
            menu_options["led_condition_move_down"] = "Move down in priority"
        # Enable/disable toggle, shown just above the delete option. The
        # label reflects the rule's current state so the user sees the
        # action they can take.
        if rule_enabled(conditions[index]):
            menu_options["led_condition_toggle_enabled"] = (
                "Disable this rule"
            )
        else:
            menu_options["led_condition_toggle_enabled"] = "Enable this rule"
        if is_follow:
            menu_options["led_condition_delete"] = (
                "Delete this follow light rule"
            )
        else:
            menu_options["led_condition_delete"] = "Delete this condition rule"
        menu_options["return_to_led_conditions"] = "Return to LED conditions menu"
        return self.async_show_menu(
            step_id="led_condition_menu", menu_options=menu_options
        )

    async def async_step_return_to_led_conditions(self, user_input=None):
        """Return to the LED conditions menu."""
        return await self.async_step_led_conditions_menu()

    async def async_step_led_condition_move_up(self, user_input=None):
        """Move the pending condition rule up in priority."""
        conditions = self._led_conditions()
        index = self._pending_condition_index
        conditions[index - 1], conditions[index] = (
            conditions[index],
            conditions[index - 1],
        )
        self._save_led_conditions(conditions)
        return await self.async_step_led_conditions_menu()

    async def async_step_led_condition_move_down(self, user_input=None):
        """Move the pending condition rule down in priority."""
        conditions = self._led_conditions()
        index = self._pending_condition_index
        conditions[index], conditions[index + 1] = (
            conditions[index + 1],
            conditions[index],
        )
        self._save_led_conditions(conditions)
        return await self.async_step_led_conditions_menu()

    async def async_step_led_condition_toggle_enabled(self, user_input=None):
        """Toggle the enabled state of the pending rule.

        A disabled rule keeps its position in the priority list but is
        skipped when the LED state is evaluated.
        """
        conditions = self._led_conditions()
        index = self._pending_condition_index
        conditions[index][CONF_ENABLED] = not rule_enabled(conditions[index])
        self._save_led_conditions(conditions)
        return await self.async_step_led_conditions_menu()

    async def async_step_led_condition_edit(self, user_input=None):
        """Edit the pending rule (branches on rule type)."""
        if user_input is not None:
            conditions = self._led_conditions()
            existing = conditions[self._pending_condition_index]
            conditions[self._pending_condition_index] = {
                CONF_CONDITION: user_input[CONF_CONDITION],
                CONF_LIGHT_STATE: light_state_from_form_fields(user_input),
                CONF_ENABLED: existing.get(CONF_ENABLED, True),
            }
            self._save_led_conditions(conditions)
            return await self.async_step_led_conditions_menu()
        existing = self._led_conditions()[self._pending_condition_index]
        if CONF_FOLLOW_LIGHT in existing:
            return await self.async_step_led_follow_light_edit()
        suggested = {CONF_CONDITION: existing.get(CONF_CONDITION)}
        suggested.update(
            {
                key: value
                for key, value in light_state_to_form_fields(
                    existing.get(CONF_LIGHT_STATE, {})
                ).items()
                if value is not None
            }
        )
        schema = self.add_suggested_values_to_schema(
            self._condition_rule_schema(), suggested
        )
        return self.async_show_form(
            step_id="led_condition_edit", data_schema=schema
        )

    async def async_step_led_follow_light_edit(self, user_input=None):
        """Edit the light entity of the pending follow-light rule."""
        if user_input is not None:
            conditions = self._led_conditions()
            existing = conditions[self._pending_condition_index]
            conditions[self._pending_condition_index] = {
                CONF_FOLLOW_LIGHT: user_input[CONF_FOLLOW_LIGHT],
                CONF_ENABLED: existing.get(CONF_ENABLED, True),
            }
            self._save_led_conditions(conditions)
            return await self.async_step_led_conditions_menu()
        existing = self._led_conditions()[self._pending_condition_index]
        suggested = {CONF_FOLLOW_LIGHT: existing.get(CONF_FOLLOW_LIGHT)}
        schema = self.add_suggested_values_to_schema(
            self._follow_light_rule_schema(), suggested
        )
        return self.async_show_form(
            step_id="led_follow_light_edit", data_schema=schema
        )

    async def async_step_led_condition_delete(self, user_input=None):
        """Delete the pending condition rule."""
        conditions = self._led_conditions()
        conditions.pop(self._pending_condition_index)
        self._save_led_conditions(conditions)
        return await self.async_step_led_conditions_menu()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _led_binding(self) -> dict:
        """Return the current binding dict for the pending LED."""
        key = _led_key(self._pending_led)
        return self.config_entry.options.get(OPT_LED_BINDINGS, {}).get(key, {})

    def _led_conditions(self) -> list:
        """Return the list of rules for the pending LED.

        Both rule types are preserved: condition rules are normalized to
        the current format (condition + target light state), and follow
        light rules keep their ``follow_light`` entity.

        A copy is returned so callers can append/edit/pop rules without
        mutating the live config entry options in place. In-place
        mutation would make ``async_update_entry`` see no change (the
        entry options and the new options compare equal), so the update
        listeners would not fire and the binding engine would not reload
        — newly added rules would not take effect until a manual reload.
        """
        binding = self._led_binding()
        conditions = binding.get(CONF_CONDITIONS)
        if isinstance(conditions, list):
            result = []
            for rule in conditions:
                if not isinstance(rule, dict):
                    continue
                if CONF_FOLLOW_LIGHT in rule:
                    result.append(
                        {
                            CONF_FOLLOW_LIGHT: rule.get(CONF_FOLLOW_LIGHT),
                            CONF_ENABLED: rule.get(CONF_ENABLED, True),
                        }
                    )
                else:
                    result.append(
                        {
                            CONF_CONDITION: rule.get(CONF_CONDITION),
                            CONF_LIGHT_STATE: rule_light_state(rule),
                            CONF_ENABLED: rule.get(CONF_ENABLED, True),
                        }
                    )
            return result
        return []

    def _save_led_conditions(self, conditions: list) -> None:
        """Persist the condition rules for the pending LED."""
        key = _led_key(self._pending_led)
        options = dict(self.config_entry.options)
        bindings = dict(options.get(OPT_LED_BINDINGS, {}))
        bindings[key] = {CONF_CONDITIONS: conditions}
        options[OPT_LED_BINDINGS] = bindings
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=options
        )

    def _condition_rule_schema(self) -> vol.Schema:
        """Schema for a single condition rule (condition + target light state).

        The target light state is entered with light-card-style controls:
        a color picker, a brightness slider, and an effect dropdown.
        """
        return vol.Schema(
            {
                vol.Required(CONF_CONDITION): ConditionSelector(),
                vol.Required(CONF_COLOR): ColorRGBSelector(),
                vol.Optional(
                    CONF_BRIGHTNESS_PCT, default=DEFAULT_BRIGHTNESS_PCT
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(CONF_EFFECT, default=EFFECT_NONE): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": EFFECT_NONE, "label": "None"}
                        ]
                        + [
                            {"value": option, "label": option}
                            for option in EFFECT_OPTIONS
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    def _follow_light_rule_schema(self) -> vol.Schema:
        """Schema for a follow-light rule (the light entity to mirror)."""
        return vol.Schema(
            {
                vol.Required(CONF_FOLLOW_LIGHT): EntitySelector(
                    EntitySelectorConfig(domain="light")
                )
            }
        )

    def _export_config_text(self) -> str:
        """Return the current configuration as an indented JSON string.

        Only the button actions and LED bindings are included — never the
        device-identifying data — so the text can be copied to another
        LocalDeck.
        """
        config = {
            OPT_BUTTON_ACTIONS: dict(
                self.config_entry.options.get(OPT_BUTTON_ACTIONS, {})
            ),
            OPT_LED_BINDINGS: dict(
                self.config_entry.options.get(OPT_LED_BINDINGS, {})
            ),
        }
        return json.dumps(config, indent=2)

    def _config_text_schema(self) -> vol.Schema:
        """Schema for the import/export configuration text box."""
        return vol.Schema(
            {
                vol.Required(CONF_CONFIG): TextSelector(
                    TextSelectorConfig(
                        multiline=True,
                        type=TextSelectorType.TEXT,
                    )
                )
            }
        )


