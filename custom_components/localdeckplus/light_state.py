"""Helpers for LocalDeck LED binding light states.

A binding rule's output is a *target light state*: a dict of
``light.turn_on`` attributes (color, brightness, transition, ...) picked
with a light-card-style input. These helpers pack and unpack that format
and filter states down to the attributes the ``light.turn_on`` service
accepts.
"""

from .const import (
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR,
    CONF_EFFECT,
    CONF_ENABLED,
    CONF_LIGHT_STATE,
    EFFECT_NONE,
)

# Attributes accepted by the light.turn_on service. The light card may
# include extra keys (e.g. color_mode) that are not valid service
# arguments, so states are filtered down to these before the service is
# called.
_VALID_TURN_ON_ATTRIBUTES = frozenset(
    {
        "brightness",
        "brightness_pct",
        "color_name",
        "color_temp_kelvin",
        "color_temp_mireds",
        "effect",
        "flash",
        "hs_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "transition",
        "white",
        "xy_color",
    }
)


def rule_light_state(rule: dict) -> dict:
    """Return the target light state for a binding rule."""
    light_state = rule.get(CONF_LIGHT_STATE)
    if isinstance(light_state, dict):
        return light_state
    return {}


def rule_enabled(rule: dict) -> bool:
    """Return whether a binding rule is enabled.

    Rules default to enabled when the field is absent (e.g. rules created
    before the enable/disable option existed).
    """
    return bool(rule.get(CONF_ENABLED, True))


def sanitize_light_state(light_state: dict | None) -> dict:
    """Filter a light state down to valid light.turn_on attributes."""
    if not isinstance(light_state, dict):
        return {}
    return {
        key: value
        for key, value in light_state.items()
        if key in _VALID_TURN_ON_ATTRIBUTES
    }


def scale_light_state_brightness(
    light_state: dict, master_brightness_pct: float
) -> dict:
    """Scale a light state's brightness by the master brightness factor.

    ``master_brightness_pct`` is 0-100. The state's ``brightness`` (0-255)
    and/or ``brightness_pct`` (0-100) are scaled down by the factor; a
    state without a brightness is returned unchanged. Returns a new dict;
    the input is not mutated. A scaled brightness of 0 turns the light off
    when sent to ``light.turn_on``.
    """
    if not isinstance(light_state, dict):
        return {}
    factor = max(0.0, min(100.0, float(master_brightness_pct))) / 100.0
    if factor >= 1.0:
        return light_state
    result = dict(light_state)
    brightness = result.get("brightness")
    if isinstance(brightness, (int, float)) and 0 <= brightness <= 255:
        result["brightness"] = int(round(brightness * factor))
    brightness_pct = result.get("brightness_pct")
    if isinstance(brightness_pct, (int, float)) and 0 <= brightness_pct <= 100:
        result["brightness_pct"] = int(round(brightness_pct * factor))
    return result


def light_state_from_form_fields(user_input: dict) -> dict:
    """Build a light state dict from the config form fields.

    The form exposes light-card-style controls (color, brightness,
    effect); this packs the ones that were set into a light state
    dict of light.turn_on attributes.
    """
    light_state: dict = {}
    color = user_input.get(CONF_COLOR)
    if (
        isinstance(color, (list, tuple))
        and len(color) == 3
        and all(isinstance(c, int) for c in color)
    ):
        light_state["rgb_color"] = list(color)
    brightness_pct = user_input.get(CONF_BRIGHTNESS_PCT)
    if brightness_pct is not None:
        light_state["brightness_pct"] = int(brightness_pct)
    effect = user_input.get(CONF_EFFECT)
    if effect is not None:
        light_state["effect"] = effect
    return light_state


def light_state_to_form_fields(light_state: dict | None) -> dict:
    """Decompose a light state dict into the config form fields.

    Used to pre-fill the edit form. Missing attributes come back as
    None so the form falls back to its defaults.
    """
    if not isinstance(light_state, dict):
        light_state = {}
    effect = light_state.get("effect", "")
    return {
        CONF_COLOR: light_state.get("rgb_color"),
        CONF_BRIGHTNESS_PCT: light_state.get("brightness_pct"),
        CONF_EFFECT: EFFECT_NONE if effect in ("", EFFECT_NONE) else effect,
    }
