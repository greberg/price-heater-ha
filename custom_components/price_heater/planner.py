"""Home Assistant side of the smart strategy: history, weather forecast and the plan."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Mapping, Optional

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from . import smart
from .const import PLAN_MARGIN
from .logic import power_watts, to_float

_LOGGER = logging.getLogger(__name__)

REFIT_INTERVAL = timedelta(hours=6)
FORECAST_INTERVAL = timedelta(minutes=30)
UNUSABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)
STATUS_NO_HISTORY = "no_history"


class SmartPlanner:
    """Learns the house from the recorder and plans heating ahead."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        name: str,
        heater: str,
        air_sensor: str,
        weather: Optional[str],
        outdoor_sensor: Optional[str],
        floor_sensor: Optional[str],
        solar_sensor: Optional[str],
        power_sensor: Optional[str],
        floor_max: Optional[float],
    ) -> None:
        self.hass = hass
        self.name = name
        self.heater = heater
        self.air_sensor = air_sensor
        self.weather = weather
        self.outdoor_sensor = outdoor_sensor
        self.floor_sensor = floor_sensor
        self.solar_sensor = solar_sensor
        self.power_sensor = power_sensor
        self.floor_max = floor_max if floor_sensor else None

        self.model: Optional[smart.ThermalModel] = None
        self.model_status = smart.STATUS_LEARNING
        self.plan: Optional[smart.Plan] = None
        self.weather_points: list = []
        self.sun_scale: Optional[float] = None
        self.full_power_w: Optional[float] = None
        self.fitted_at: Optional[datetime] = None
        self.forecast_at: Optional[datetime] = None
        self.outdoor: Optional[float] = None
        self.floor: Optional[float] = None
        self.sun: Optional[float] = None  # 0..1 of clear sky overhead, from the sensor
        self._plan_key: Any = None

    @property
    def tracked_entities(self) -> list[str]:
        return [e for e in (self.outdoor_sensor, self.floor_sensor, self.solar_sensor) if e]

    @property
    def status(self) -> str:
        if self.model is None:
            return self.model_status
        return smart.STATUS_READY if self.plan else smart.STATUS_NO_INPUTS

    def invalidate(self) -> None:
        """Plan again at the next check."""
        self._plan_key = None

    # ------------------------------------------------------------------ learning
    async def async_refit(self) -> None:
        """Fit the model on the last days of recorded history."""
        end = dt_util.utcnow()
        start = end - timedelta(days=smart.HISTORY_DAYS)
        entity_ids = [
            e
            for e in (
                self.air_sensor, self.floor_sensor, self.outdoor_sensor, self.solar_sensor,
                self.power_sensor or self.heater,
                None if self.outdoor_sensor else self.weather,
            )
            if e
        ]
        try:
            from homeassistant.components.recorder import get_instance, history

            states = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass, start, end, entity_ids,
                    significant_changes_only=False,
                )
            )
        except Exception as err:  # noqa: BLE001 - no recorder, or it failed: keep the simple rules
            _LOGGER.warning("%s: could not read history to learn the house: %s", self.name, err)
            self.model_status = STATUS_NO_HISTORY
            return
        model, status, scale, full = await self.hass.async_add_executor_job(
            self._fit, states, start, end
        )
        if model is not None or self.model is None:
            self.model, self.model_status = model, status
        self.sun_scale = scale or self.sun_scale
        self.full_power_w = full or self.full_power_w
        self.fitted_at = end
        self.invalidate()
        _LOGGER.debug("%s: model %s %s", self.name, status, model.as_dict() if model else None)

    def _fit(self, states: Mapping[str, list[State]], start: datetime, end: datetime):
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        grid = smart.history_grid(start, end)
        none = [None] * len(grid)

        def numeric(entity: Optional[str], attribute: Optional[str] = None):
            changes = []
            for s in states.get(entity, []) if entity else []:
                if s.state in UNUSABLE_STATES:
                    value = None
                else:
                    value = to_float(s.attributes.get(attribute) if attribute else s.state)
                changes.append((s.last_updated, value))
            return changes

        air = smart.resample(numeric(self.air_sensor), grid)
        slab = smart.resample(numeric(self.floor_sensor), grid) if self.floor_sensor else none
        if self.outdoor_sensor:
            outdoor = smart.resample(numeric(self.outdoor_sensor), grid)
        elif self.weather:
            outdoor = smart.resample(numeric(self.weather, "temperature"), grid)
        else:
            outdoor = none

        full = None
        if self.power_sensor:
            watts = [
                (s.last_updated, None if s.state in UNUSABLE_STATES else power_watts(s.state, s.attributes.get("unit_of_measurement")))
                for s in states.get(self.power_sensor, [])
            ]
            on = smart.step_average(smart.power_share(watts), grid)
            positive = sorted(w for _, w in watts if w and w > 0)
            full = positive[int(0.95 * (len(positive) - 1))] if positive else None
        else:
            on = smart.step_average(
                [(s.last_updated, _heating_share(s)) for s in states.get(self.heater, [])], grid
            )

        sun_raw = smart.resample(numeric(self.solar_sensor), grid) if self.solar_sensor else None
        samples, scale = smart.build_samples(grid, air, slab, outdoor, on, sun_raw, lat, lon)
        model, status = smart.fit_model(samples, two_node=bool(self.floor_sensor))
        return model, status, scale, full

    # ------------------------------------------------------------------ forecast
    async def async_update_forecast(self) -> None:
        if not self.weather:
            return
        for kind in ("hourly", "twice_daily", "daily"):
            try:
                response = await self.hass.services.async_call(
                    "weather", "get_forecasts",
                    {"entity_id": self.weather, "type": kind},
                    blocking=True, return_response=True,
                )
            except HomeAssistantError as err:
                _LOGGER.debug("%s: no %s forecast from %s: %s", self.name, kind, self.weather, err)
                continue
            forecast = ((response or {}).get(self.weather) or {}).get("forecast") or []
            if forecast:
                self.weather_points = smart.parse_forecast(forecast)
                self.forecast_at = dt_util.utcnow()
                self.invalidate()
                return

    # ------------------------------------------------------------------ planning
    def read_sensors(self) -> None:
        self.floor = _state_float(self.hass.states.get(self.floor_sensor)) if self.floor_sensor else None
        if self.outdoor_sensor:
            self.outdoor = _state_float(self.hass.states.get(self.outdoor_sensor))
        elif self.weather and (weather := self.hass.states.get(self.weather)):
            self.outdoor = to_float(weather.attributes.get("temperature"))
        else:
            self.outdoor = None
        raw = _state_float(self.hass.states.get(self.solar_sensor)) if self.solar_sensor else None
        self.sun = min(1.5, max(0.0, raw / self.sun_scale)) if raw is not None and self.sun_scale else None

    def update(
        self,
        now: datetime,
        air: Optional[float],
        price_attributes: Mapping[str, Any],
        min_temp: float,
        max_temp: float,
    ) -> None:
        """Plan again when a new 15-minute step starts or anything it used changed."""
        self.read_sensors()
        if self.model is None:
            self.plan = None
            return
        slots = smart.price_slots(price_attributes, now)
        key = (smart.step_start(now), len(slots), slots[-1] if slots else None, min_temp, max_temp)
        if self.plan is not None and key == self._plan_key:
            return
        self._plan_key = key
        two = self.model.two_node
        inputs = smart.build_inputs(
            now, slots, self.weather_points, self.outdoor,
            self.hass.config.latitude, self.hass.config.longitude, self.sun,
        )
        if inputs is None or air is None or (two and self.floor is None):
            self.plan = None
            return
        self.plan = smart.make_plan(
            self.model, inputs, air, self.floor if two else None,
            min_temp + PLAN_MARGIN, max_temp, self.floor_max,
        )

    def attributes(self, rated_power_w: Optional[float]) -> dict[str, Any]:
        power = rated_power_w or self.full_power_w
        return {
            "status": self.status,
            "model": self.model.as_dict() if self.model else None,
            "learned_at": self.fitted_at.isoformat() if self.fitted_at else None,
            "forecast_at": self.forecast_at.isoformat() if self.forecast_at else None,
            "plan": self.plan.summary(power / 1000 if power else None) if self.plan else None,
        }


def _state_float(state: Optional[State]) -> Optional[float]:
    if state is None or state.state in UNUSABLE_STATES:
        return None
    return to_float(state.state)


def _heating_share(state: State) -> Optional[float]:
    """1 while the heater heats, 0 while not, None if unknown."""
    if state.state in UNUSABLE_STATES:
        return None
    if state.domain == "climate":
        action = state.attributes.get("hvac_action")
        if action is not None:
            return 1.0 if action == "heating" else 0.0
        return 0.0 if state.state == STATE_OFF else 1.0
    return 1.0 if state.state == STATE_ON else 0.0
