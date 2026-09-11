"""Constants for the LocalDeck-Plus integration."""

DOMAIN = "localdeckplus"

# Config entry data keys
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_IDENTIFIER = "device_identifier"

# Options keys
OPT_BUTTON_ACTIONS = "button_actions"
OPT_LED_BINDINGS = "led_bindings"

# Platforms set up by the integration
PLATFORMS = ["switch"]

# Switch entity name
SWITCH_DISABLE_LEDS = "Disable LEDs"

# Button action keys
CONF_ACTION = "action"

# Import/export configuration text field
CONF_CONFIG = "config"

# LED binding condition-rule keys
CONF_CONDITIONS = "conditions"
CONF_CONDITION = "condition"
CONF_COLOR = "color"
CONF_BRIGHTNESS_PCT = "brightness_pct"
CONF_LIGHT_STATE = "light_state"
CONF_EFFECT = "effect"
# Follow-light rule key: holds the entity_id of the light to mirror. A rule
# that carries this key (and no condition) is a follow-light rule; when it is
# the first active rule in the priority list the LED follows that light's
# state, color, and brightness.
CONF_FOLLOW_LIGHT = "follow_light"
# Per-rule enable flag. A rule with this set to False keeps its position in
# the priority list but is skipped when the LED state is evaluated. Rules
# default to enabled when the field is absent.
CONF_ENABLED = "enabled"

# Defaults
DEFAULT_BRIGHTNESS_PCT = 100

# Value for the "no effect" option in the effect dropdown. It is also the
# value sent to light.turn_on to clear any active effect: ESPHome clears
# the effect when given a name that matches no configured effect, and an
# empty string is not reliably transmitted, so the non-empty string "None"
# is used. It must not collide with a real effect name.
EFFECT_NONE = "None"
# Effect names for the dropdown (must match the ESPHome effect names on
# the partition lights, see localdeck-rewrite.yml).
EFFECT_OPTIONS = ["Pulse", "Fast Pulse", "Slow Pulse"]


def switch_unique_id(entry_id: str) -> str:
    """Return the unique_id of the "Disable LEDs" switch for a config entry."""
    return f"{DOMAIN}_disable_leds_{entry_id}"
