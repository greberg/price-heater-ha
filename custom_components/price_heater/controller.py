"""Controller that watches temperature + price and switches the heater."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHEAP_HOURS,
    CONF_HEATER,
    CONF_HYSTERESIS,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_PRICE_SENSOR,
    CONF_HEATER_POWER,
    CONF_POWER_SENSOR,
    CONF_PRICE_THRESHOLD,
    CONF_TEMP_SENSOR,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_HYSTERESIS,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_CYCLE,
    DEFAULT_MIN_TEMP,
    DOMAIN,
)
from .logic import (
    OVERRIDE_REASONS,
    Decision,
    EnergyMeter,
    PriceInfo,
    analyze_prices,
    currency_of,
    decide,
    heater_power,
    power_watts,
    price_schedule,
    to_float,
)

_LOGGER = logging.getLogger(__name__)

EVALUATE_INTERVAL = timedelta(minutes=1)
UNUSABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)


class PriceHeaterController:
    """Evaluates the rules and drives the heater entity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        config = {**entry.data, **entry.options}

        self.heater_entity: str = config[CONF_HEATER]
        self.temp_sensor: str = config[CONF_TEMP_SENSOR]
        self.price_sensor: str = config[CONF_PRICE_SENSOR]
        self.min_temp = float(config.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self.max_temp = float(config.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self.hysteresis = float(config.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS))
        self.cheap_hours = float(config.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS))
        # Optional field: once options are saved, a cleared threshold must win
        # over the value from the initial setup.
        threshold_source = entry.options if entry.options else entry.data
        self.price_threshold = to_float(threshold_source.get(CONF_PRICE_THRESHOLD))
        # Optional: a power meter on the heater, else its rated power.
        source = entry.options if entry.options else entry.data
        self.power_sensor: str | None = source.get(CONF_POWER_SENSOR) or None
        rated = to_float(source.get(CONF_HEATER_POWER))
        self.rated_power = rated if rated and rated > 0 else None
        self.min_cycle = timedelta(
            minutes=float(config.get(CONF_MIN_CYCLE, DEFAULT_MIN_CYCLE))
        )

        self.enabled = True
        self.temperature: float | None = None
        self.price_info = PriceInfo()
        self.schedule: list[dict] = []
        self.price_unit: str | None = None
        self.power: float | None = None
        self.meter = EnergyMeter()
        self.heater_on: bool | None = None
        self.decision: Decision | None = None
        self.waiting_for_cycle = False
        self.last_action: datetime | None = None

        self.signal = f"{DOMAIN}_{entry.entry_id}_update"
        self._lock = asyncio.Lock()
        self._unsubs: list = []

    # ------------------------------------------------------------------ lifecycle
    @callback
    def async_start(self) -> None:
        """Start listening once Home Assistant is up."""
        self._unsubs.append(async_at_started(self.hass, self._async_started))

    async def _async_started(self, _hass: HomeAssistant) -> None:
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [
                    entity
                    for entity in (
                        self.heater_entity,
                        self.temp_sensor,
                        self.price_sensor,
                        self.power_sensor,
                    )
                    if entity
                ],
                self._handle_state_change,
            )
        )
        # Re-check every minute so we react when a new price slot starts.
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._handle_interval, EVALUATE_INTERVAL
            )
        )
        await self.async_evaluate()

    @callback
    def async_stop(self) -> None:
        """Remove all listeners."""
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _handle_state_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    @callback
    def _handle_interval(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate())

    async def async_set_enabled(self, enabled: bool) -> None:
        """Turn the automation on/off (from the switch entity)."""
        self.enabled = enabled
        await self.async_evaluate()

    # ------------------------------------------------------------------ logic
    async def async_evaluate(self) -> None:
        """Read the inputs, decide, and act."""
        async with self._lock:
            now = dt_util.now()

            self.temperature = _state_float(self.hass.states.get(self.temp_sensor))

            price_state = self.hass.states.get(self.price_sensor)
            self.price_info = analyze_prices(
                _state_float(price_state),
                price_state.attributes if price_state else {},
                now,
                self.cheap_hours,
                self.price_threshold,
            )
            attrs = price_state.attributes if price_state else {}
            self.schedule = price_schedule(
                attrs, now, self.cheap_hours, self.price_threshold
            )
            self.price_unit = attrs.get("unit_of_measurement")

            heater_state = self.hass.states.get(self.heater_entity)
            self.heater_on = _heater_is_on(heater_state)

            self.decision = decide(
                self.temperature,
                self.price_info.is_cheap,
                bool(self.heater_on),
                self.min_temp,
                self.max_temp,
                self.hysteresis,
            )
            self.waiting_for_cycle = False

            self.power = heater_power(self._sensor_watts(), self.rated_power, self.heater_on)
            self.meter.update(now, self.power, self.price_info.current_price)

            if (
                self.enabled
                and self.decision.turn_on is not None
                and self.heater_on is not None
                and self.decision.turn_on != self.heater_on
            ):
                if self._cycle_blocked(heater_state, now) and (
                    self.decision.reason not in OVERRIDE_REASONS
                ):
                    self.waiting_for_cycle = True
                    _LOGGER.debug(
                        "%s: wants heater %s (%s) but minimum cycle time not reached",
                        self.entry.title,
                        "on" if self.decision.turn_on else "off",
                        self.decision.reason,
                    )
                else:
                    await self._async_set_heater(self.decision.turn_on)
                    self.last_action = now

        async_dispatcher_send(self.hass, self.signal)

    @property
    def has_power(self) -> bool:
        """Whether power, energy and cost can be shown."""
        return bool(self.power_sensor or self.rated_power)

    @property
    def currency(self) -> str | None:
        return currency_of(self.price_unit)

    def _sensor_watts(self) -> float | None:
        if not self.power_sensor:
            return None
        state = self.hass.states.get(self.power_sensor)
        if state is None or state.state in UNUSABLE_STATES:
            return None
        return power_watts(state.state, state.attributes.get("unit_of_measurement"))

    def _cycle_blocked(self, heater_state: State | None, now: datetime) -> bool:
        if heater_state is None or self.min_cycle <= timedelta(0):
            return False
        return now - heater_state.last_changed < self.min_cycle

    async def _async_set_heater(self, turn_on: bool) -> None:
        domain = self.heater_entity.split(".", 1)[0]
        _LOGGER.info(
            "%s: turning %s %s (%s, temp=%s, price=%s)",
            self.entry.title,
            self.heater_entity,
            "on" if turn_on else "off",
            self.decision.reason if self.decision else None,
            self.temperature,
            self.price_info.current_price,
        )
        try:
            if domain == "climate":
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {
                        ATTR_ENTITY_ID: self.heater_entity,
                        "hvac_mode": "heat" if turn_on else "off",
                    },
                    blocking=True,
                )
            else:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on" if turn_on else "turn_off",
                    {ATTR_ENTITY_ID: self.heater_entity},
                    blocking=True,
                )
        except HomeAssistantError as err:
            _LOGGER.error(
                "%s: could not switch %s: %s", self.entry.title, self.heater_entity, err
            )

    @property
    def attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the status sensor."""
        info = self.price_info
        return {
            "automation_enabled": self.enabled,
            "temperature": self.temperature,
            "min_temp": self.min_temp,
            "max_temp": self.max_temp,
            "current_price": info.current_price,
            "is_cheap": info.is_cheap,
            "in_cheapest_hours": info.in_cheapest_hours,
            "below_price_threshold": info.below_threshold,
            "price_threshold": self.price_threshold,
            "cheap_price_limit": info.cheap_limit,
            "price_rank_today": info.rank,
            "price_slots_today": info.slots_today,
            "heater_on": self.heater_on,
            "waiting_for_min_cycle": self.waiting_for_cycle,
            "last_action": self.last_action.isoformat() if self.last_action else None,
            "power": self.power,
            "power_source": "sensor" if self.power_sensor else "rated" if self.rated_power else None,
            "energy_today": round(self.meter.energy_kwh, 3) if self.has_power else None,
            "cost_today": round(self.meter.cost, 2) if self.has_power else None,
            "currency": self.currency,
            "hysteresis": self.hysteresis,
            "cheap_hours": self.cheap_hours,
            "price_unit": self.price_unit,
            "prices_today": self.schedule,
            "entities": self._entity_map(),
        }

    def _entity_map(self) -> dict[str, str]:
        """Entity ids the cards need, keyed by role."""
        registry = er.async_get(self.hass)
        result = {
            "heater": self.heater_entity,
            "temperature": self.temp_sensor,
            "price": self.price_sensor,
        }
        if self.power_sensor:
            result["power_meter"] = self.power_sensor
        for platform, key in (
            ("sensor", "status"),
            ("switch", "automation"),
            ("binary_sensor", "heating_demand"),
            ("binary_sensor", "cheap_price"),
            ("sensor", "power"),
            ("sensor", "energy_today"),
            ("sensor", "cost_today"),
        ):
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{self.entry.entry_id}_{key}"
            )
            if entity_id:
                result[key] = entity_id
        return result


def _state_float(state: State | None) -> float | None:
    if state is None or state.state in UNUSABLE_STATES:
        return None
    return to_float(state.state)


def _heater_is_on(state: State | None) -> bool | None:
    """Return True/False, or None if the heater state is unknown."""
    if state is None or state.state in UNUSABLE_STATES:
        return None
    if state.domain == "climate":
        return state.state != STATE_OFF
    return state.state == STATE_ON
