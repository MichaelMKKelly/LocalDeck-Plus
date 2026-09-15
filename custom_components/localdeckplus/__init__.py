"""The LocalDeck-Plus integration."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import (
    ATTR_BEHAVIOR,
    BEHAVIOR_ANY,
    async_extract_entities,
    async_from_config,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONDITION,
    CONF_CONDITIONS,
    CONF_DEVICE_ID,
    CONF_FOLLOW_LIGHT,
    DEFAULT_MASTER_BRIGHTNESS,
    EFFECT_NONE,
    OPT_BUTTON_ACTIONS,
    OPT_LED_BINDINGS,
    PLATFORMS,
)
from .discovery import discover_localdeckplus_entities
from .light_state import (
    rule_enabled,
    rule_light_state,
    sanitize_light_state,
    scale_light_state_brightness,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class _LocalDeckPlusRuntime:
    """Runtime state shared between the binding engine and the platforms.

    ``disabled`` gates the binding engine: while True the engine stops
    driving the LEDs so they stay off regardless of their rules.
    ``light_entity_ids`` is the full set of discovered LED lights, used to
    turn them all off when the switch is engaged. ``apply_all`` re-evaluates
    every binding and is called when the switch is released (or the master
    brightness changes) so the LEDs return to their rule-driven state.
    ``master_brightness`` is the global 0-100 multiplier applied to every
    rule's brightness; it is restored by the number platform before the
    engine's first evaluation.
    """

    disabled: bool = False
    light_entity_ids: list[str] = field(default_factory=list)
    apply_all: Callable[[], Awaitable[None]] | None = None
    master_brightness: float = DEFAULT_MASTER_BRIGHTNESS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LocalDeck-Plus from a config entry."""
    device_id = entry.data[CONF_DEVICE_ID]
    lights, events = discover_localdeckplus_entities(hass, device_id)

    if not lights:
        _LOGGER.warning(
            "No LocalDeck lights discovered for device %s; "
            "LED bindings will be unavailable",
            device_id,
        )

    # Shared runtime state for the binding engine and the switch platform.
    runtime = _LocalDeckPlusRuntime()
    runtime.light_entity_ids = [eid for eid in lights.values() if eid]
    entry.runtime_data = runtime

    # Button event engine
    event_cleanup = _setup_event_engine(hass, entry, events)
    entry.async_on_unload(event_cleanup)

    # Switch platform (Disable LEDs). Set up before the binding engine so
    # the switch restores its last state first; the engine then respects
    # runtime.disabled from its very first evaluation (no startup flicker).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # LED binding engine
    binding_cleanup = await _setup_binding_engine(hass, entry, lights, runtime)
    entry.async_on_unload(binding_cleanup)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    _LOGGER.debug("Reloading LocalDeck-Plus entry %s", entry.entry_id)
    hass.config_entries.async_schedule_reload(entry.entry_id)


# ----------------------------------------------------------------------
# Button event engine
# ----------------------------------------------------------------------

# Event entities restore their last event state when they (re)become
# available, e.g. after an ESPHome restart. A real press reaches HA well
# within this window, so anything older is treated as a stale restore.
_EVENT_FRESHNESS = timedelta(seconds=4)


def _is_fresh_event(state: str | None) -> bool:
    """Return True if the event timestamp in `state` is recent."""
    if not state:
        return False
    event_time = dt_util.parse_datetime(state)
    if event_time is None:
        return False
    if event_time.tzinfo is None:
        event_time = dt_util.as_utc(event_time)
    return dt_util.utcnow() - event_time <= _EVENT_FRESHNESS


def _setup_event_engine(hass: HomeAssistant, entry: ConfigEntry, events: dict):
    """Listen to ESPHome button events and run the configured actions."""
    actions = entry.options.get(OPT_BUTTON_ACTIONS, {})
    unsubs = []

    for key, action in actions.items():
        try:
            button_str, event_type = key.split(":", 1)
            button = int(button_str)
        except (ValueError, AttributeError):
            _LOGGER.warning("Invalid button action key: %s", key)
            continue

        event_entity_id = events.get(button)
        if not event_entity_id:
            _LOGGER.debug(
                "No event entity found for button %d; skipping action %s",
                button,
                key,
            )
            continue

        async def _handle_state_change(
            event, event_type=event_type, action=action
        ):
            # In HA 2026.9.0 event entities no longer publish bus events.
            # They change state instead: state = ISO timestamp of the last
            # event, and the "event_type" attribute carries which type fired.
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            received_type = new_state.attributes.get("event_type")
            if received_type != event_type:
                return
            old_state = event.data.get("old_state")
            if old_state is None or old_state.state in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                # The entity just became available. The new state may be a
                # restored (stale) last event rather than a fresh trigger,
                # so only act on a recent timestamp.
                if not _is_fresh_event(new_state.state):
                    _LOGGER.debug(
                        "Ignoring stale restored event on %s (state=%s)",
                        new_state.entity_id,
                        new_state.state,
                    )
                    return
            _LOGGER.debug(
                "Button event %s (%s) -> executing action",
                new_state.entity_id,
                event_type,
            )
            await _execute_action(hass, action)

        unsubs.append(
            async_track_state_change_event(
                hass, [event_entity_id], _handle_state_change
            )
        )

    _LOGGER.info(
        "Button event engine: %d listener(s) for %d configured action(s)",
        len(unsubs),
        len(actions),
    )

    @callback
    def _cleanup():
        for unsub in unsubs:
            unsub()

    return _cleanup


async def _execute_action(hass: HomeAssistant, action) -> None:
    """Execute a configured action (service call, scene, script or event).

    The ActionSelector stores actions in the standard HA action format:
      {"action": "domain.service", "target": {...}, "data": {...}}
      {"action": "scene", "scene": "scene.id"}
      {"action": "script", "script": "script.id"}
      {"action": "event", "event": "my_event"}

    The ActionSelector may return a single action dict or a list of
    action dicts; both are supported.
    """
    if isinstance(action, list):
        for item in action:
            await _execute_action(hass, item)
        return
    if not isinstance(action, dict):
        _LOGGER.warning("Ignoring non-dict action: %r", action)
        return

    # Scene reference
    if isinstance(action.get("scene"), str):
        await _run_service(
            hass, "scene", "turn_on", {"scene_id": action["scene"]}
        )
        return

    # Script reference
    if isinstance(action.get("script"), str):
        await _run_service(
            hass, "script", "turn_on", {"entity_id": action["script"]}
        )
        return

    # Event
    if isinstance(action.get("event"), str):
        hass.bus.async_fire(action["event"])
        return

    # Service call: {"action": "domain.service", "target": {...}, "data": {...}}
    service = action.get("action")
    if not isinstance(service, str) or "." not in service:
        _LOGGER.warning("Unsupported action type: %r", action)
        return
    domain, service_name = service.split(".", 1)
    data = action.get("data") or {}
    target = action.get("target")
    await _run_service(hass, domain, service_name, data, target)


async def _run_service(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict,
    target: dict | None = None,
) -> None:
    """Call a service and log any failure.

    The target (entity_id/device_id/...) is merged into the data dict,
    since 2026.9.0 service schema validation requires the target keys to
    be present in the service data itself rather than passed separately.
    """
    data = dict(data)
    if target:
        data.update(target)
    try:
        await hass.services.async_call(domain, service, data, blocking=False)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to execute action %s.%s", domain, service)


# ----------------------------------------------------------------------
# LED binding engine
# ----------------------------------------------------------------------


def _normalize_binding(binding: dict) -> list[dict]:
    """Return the list of rules for a binding."""
    if not isinstance(binding, dict):
        return []
    conditions = binding.get(CONF_CONDITIONS)
    if isinstance(conditions, list):
        return [rule for rule in conditions if isinstance(rule, dict)]
    return []


def _condition_to_config(condition) -> dict:
    """Normalize a condition (list or dict) to a single condition config.

    The ConditionSelector stores a list of conditions (implicitly ANDed);
    wrap it in an "and" composite. A single dict is used as-is.
    """
    if isinstance(condition, list):
        return {"condition": "and", "conditions": condition}
    return condition


def _normalize_condition(config):
    """Recursively normalize a condition config for ``async_from_config``.

    - Convert ``for`` duration fields (stored as strings/numbers by the
      condition editor) to ``timedelta`` for duration math.
    - Ensure non-composite conditions carry the ``behavior`` option.
      ``async_from_config`` does NOT run the condition schema, so the
      schema's ``behavior`` default (``"any"``) is never applied; the
      ``EntityConditionBase`` constructor reads ``options["behavior"]``
      directly and raises ``KeyError``/``TypeError`` when it is absent.
    """
    if isinstance(config, dict):
        result = {}
        for key, value in config.items():
            if (
                key == "for"
                and value is not None
                and not isinstance(value, timedelta)
            ):
                try:
                    value = cv.time_period(value)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "Invalid 'for' duration %r in a condition; ignoring it",
                        value,
                    )
                    value = None
            result[key] = _normalize_condition(value)
        condition = result.get("condition")
        if isinstance(condition, str) and condition not in (
            "and",
            "or",
            "not",
        ):
            options = result.get("options")
            if not isinstance(options, dict):
                options = {}
                result["options"] = options
            options.setdefault(ATTR_BEHAVIOR, BEHAVIOR_ANY)
        return result
    if isinstance(config, list):
        return [_normalize_condition(item) for item in config]
    return config


async def _setup_binding_engine(
    hass: HomeAssistant,
    entry: ConfigEntry,
    lights: dict,
    runtime: _LocalDeckPlusRuntime,
):
    """Listen to external entities and drive the deck LEDs.

    Each binding is a priority-ordered list of rules. The rules are
    evaluated in order and the first active rule wins: a condition rule is
    active when its condition is true and sets the LED to that rule's
    target light state; a follow-light rule is always active when reached
    and mirrors the selected light's state, color, and brightness. If no
    rule is active, the LED is turned off.

    While ``runtime.disabled`` is True (the "Disable LEDs" switch is on)
    the engine stops driving the LEDs so they stay off; ``apply_all`` is
    exposed on the runtime so the switch can re-evaluate every binding when
    it is released.
    """
    bindings = entry.options.get(OPT_LED_BINDINGS, {})
    unsubs = []
    checkers = []  # all ConditionCheckers, for cleanup
    apply_leds: list[Callable[[], Awaitable[None]]] = []

    for led_key, binding in bindings.items():
        try:
            led = int(led_key.split("_")[1])
        except (IndexError, ValueError):
            _LOGGER.warning("Invalid LED binding key: %s", led_key)
            continue

        light_entity_id = lights.get(led)
        if not light_entity_id:
            _LOGGER.debug("Skipping binding %s (no light entity)", led_key)
            continue

        rules = _normalize_binding(binding)
        if not rules:
            _LOGGER.debug("Skipping binding %s (no conditions)", led_key)
            continue

        # Rule descriptors in priority order. Each is a tuple:
        #   ("condition", checker, rule)  — active when the condition matches
        #   ("follow_light", entity_id, rule) — always active when reached;
        #     the LED mirrors the selected light's state/color/brightness
        led_rules = []
        watched_entities: set[str] = set()
        for rule in rules:
            if CONF_FOLLOW_LIGHT in rule:
                follow_entity = rule.get(CONF_FOLLOW_LIGHT)
                if not isinstance(follow_entity, str) or not follow_entity:
                    _LOGGER.warning(
                        "Binding %s: skipping follow-light rule without a "
                        "valid light entity",
                        led_key,
                    )
                    continue
                led_rules.append(("follow_light", follow_entity, rule))
                watched_entities.add(follow_entity)
                continue
            cond_config = _condition_to_config(rule.get(CONF_CONDITION))
            if not isinstance(cond_config, dict):
                _LOGGER.warning(
                    "Binding %s: skipping rule without a valid condition",
                    led_key,
                )
                continue
            cond_config = _normalize_condition(cond_config)
            try:
                checker = await async_from_config(hass, cond_config)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Binding %s: invalid condition %r", led_key, cond_config
                )
                continue
            led_rules.append(("condition", checker, rule))
            checkers.append(checker)
            watched_entities.update(async_extract_entities(cond_config))

        if not led_rules:
            continue

        async def _apply_led(
            light_entity_id=light_entity_id, led_rules=led_rules
        ):
            """Evaluate rules in priority order; the first active rule wins.

            A condition rule is active when its condition matches and sets
            the LED to its target light state. A follow-light rule is always
            active when reached, so it mirrors the selected light's state,
            color, and brightness (turning the LED off when that light is
            off); any rules below it never take effect. If no rule is
            active, the LED is turned off.

            Disabled rules keep their position in the list but are skipped
            when the LED state is evaluated.

            While the "Disable LEDs" switch is on (``runtime.disabled``)
            the LED is left untouched so it stays off. When the master
            brightness is zero the LED is turned off; otherwise the rule's
            brightness is scaled by the master brightness.
            """
            if runtime.disabled:
                return
            if runtime.master_brightness <= 0:
                # Master brightness at zero: every LED is off.
                await _set_light(hass, light_entity_id, None, False)
                return
            for descriptor in led_rules:
                rule = descriptor[2]
                if not rule_enabled(rule):
                    continue
                if descriptor[0] == "follow_light":
                    await _mirror_light(
                        hass,
                        light_entity_id,
                        descriptor[1],
                        runtime.master_brightness,
                    )
                    return
                checker = descriptor[1]
                try:
                    matched = checker.async_check()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Error evaluating condition for %s", light_entity_id
                    )
                    continue
                if matched:
                    light_state = scale_light_state_brightness(
                        sanitize_light_state(rule_light_state(rule)),
                        runtime.master_brightness,
                    )
                    await _set_light(hass, light_entity_id, light_state, True)
                    return
            await _set_light(hass, light_entity_id, None, False)

        async def _handle_state_change(event, _apply=_apply_led):
            await _apply()

        if watched_entities:
            unsubs.append(
                async_track_state_change_event(
                    hass, sorted(watched_entities), _handle_state_change
                )
            )
        else:
            _LOGGER.debug(
                "Binding %s: condition references no entities; "
                "LED will only be evaluated at setup",
                led_key,
            )

        # Apply the current condition result immediately so the LED
        # reflects the current state right after (re)load.
        hass.async_create_task(
            _apply_led(), name=f"localdeckplus binding init {led_key}"
        )
        apply_leds.append(_apply_led)

    async def _apply_all():
        """Re-evaluate every binding (called when the switch is released)."""
        for apply_led in apply_leds:
            await apply_led()

    runtime.apply_all = _apply_all

    # Watch the ESPHome LED light entities for availability. The binding
    # engine only re-evaluates when an external watched entity changes, so
    # it does not notice the ESPHome LED lights themselves becoming
    # available. When the ESPHome device (re)becomes available — e.g. HA
    # restarted and this integration set up before ESPHome, or the device
    # went offline and came back — the ESPHome LED lights may be in a stale
    # state that the engine has not driven. Re-apply every binding so the
    # LEDs return to their rule-driven state.
    _reapply_task: asyncio.Task | None = None

    async def _handle_esphome_availability(event):
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
            return
        if old_state is not None and old_state.state in (STATE_ON, STATE_OFF):
            # Already available; not an availability transition.
            return
        nonlocal _reapply_task
        if _reapply_task is not None and not _reapply_task.done():
            return  # a re-apply is already pending
        _LOGGER.debug(
            "ESPHome LED %s became available; re-applying bindings",
            new_state.entity_id,
        )
        _reapply_task = hass.async_create_task(
            _async_reapply_on_available(),
            name="localdeckplus reapply on esphome available",
        )

    async def _async_reapply_on_available():
        # Give ESPHome a moment to bring all of its entities online so the
        # re-apply runs once against the settled state rather than once per
        # entity.
        await asyncio.sleep(1)
        await _apply_all()

    if runtime.light_entity_ids:
        unsubs.append(
            async_track_state_change_event(
                hass, runtime.light_entity_ids, _handle_esphome_availability
            )
        )

    _LOGGER.info(
        "LED binding engine: %d listener(s) for %d configured binding(s)",
        len(unsubs),
        len(bindings),
    )

    @callback
    def _cleanup():
        for unsub in unsubs:
            unsub()
        for checker in checkers:
            checker.async_unload()
        if _reapply_task is not None and not _reapply_task.done():
            _reapply_task.cancel()

    return _cleanup


# Color descriptor keys accepted by light.turn_on. They form a single
# exclusion group in the service schema, so at most ONE may be sent in a
# single call. A light's state attributes can carry several of them at
# once (e.g. rgb_color + hs_color, or color_temp_kelvin +
# color_temp_mireds), so a mirror picks a single one in this priority
# order (RGB first, since the deck LEDs are RGB).
_COLOR_DESCRIPTOR_PRIORITY = (
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "color_temp_kelvin",
    "color_temp_mireds",
    "hs_color",
    "xy_color",
    "white",
    "color_name",
)


async def _mirror_light(
    hass: HomeAssistant,
    led_entity_id: str,
    source_entity_id: str,
    master_brightness_pct: float,
) -> None:
    """Set a deck LED to mirror the state of a source light.

    When the source light is off, unavailable, or unknown the LED is turned
    off. Otherwise the LED is turned on with the source light's color and
    brightness, scaled by the master brightness. Only a single color
    descriptor is sent (the light.turn_on service rejects more than one),
    chosen in priority order from the source's state attributes. Any active
    effect is cleared so the LED always shows a static color while
    following the light.
    """
    source = hass.states.get(source_entity_id)
    if (
        source is None
        or source.state in (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN)
    ):
        await _set_light(hass, led_entity_id, None, False)
        return
    attributes = source.attributes
    light_state: dict = {}
    for key in _COLOR_DESCRIPTOR_PRIORITY:
        if key in attributes:
            light_state[key] = attributes[key]
            break
    brightness = attributes.get("brightness")
    if isinstance(brightness, (int, float)) and 1 <= brightness <= 255:
        light_state["brightness"] = int(brightness)
    # Clear any active effect (same mechanism the condition rules use).
    light_state["effect"] = EFFECT_NONE
    light_state = scale_light_state_brightness(
        light_state, master_brightness_pct
    )
    await _set_light(hass, led_entity_id, light_state, True)


async def _set_light(
    hass: HomeAssistant,
    light_entity_id: str,
    light_state: dict | None,
    is_on: bool,
) -> None:
    """Set a deck LED to the given target light state, or off.

    ``light_state`` is a dict of light.turn_on attributes (color,
    brightness, transition, ...) as picked with the light-card input.
    An empty dict simply turns the LED on with its default state.
    """
    if not is_on:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": light_entity_id}
        )
        return
    data = {"entity_id": light_entity_id}
    if isinstance(light_state, dict):
        data.update(light_state)
    await hass.services.async_call("light", "turn_on", data)
