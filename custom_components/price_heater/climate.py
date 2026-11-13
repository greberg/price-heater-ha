"""Thermostat entity: the whole Price Heater as one climate control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PriceHeaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([PriceHeaterThermostat(entry.runtime_data, "thermostat")])


class PriceHeaterThermostat(PriceHeaterEntity, ClimateEntity):
    """Heat = automation on; Off = automation paused and heater off.

    The target range is the min/max band: always heat below the low end,
    never above the high end, and in between heat only when power is cheap.
    """

    _attr_name = None  # the device name, e.g. "Price Heater"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 0
    _attr_max_temp = 35

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.controller.enabled else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        on = self.controller.heater_on
        if on is None:
            return None
        if on:
            return HVACAction.HEATING
        return HVACAction.IDLE if self.controller.enabled else HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        return self.controller.temperature

    @property
    def target_temperature_low(self) -> float:
        return self.controller.min_temp

    @property
    def target_temperature_high(self) -> float:
        return self.controller.max_temp

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self.controller.decision
        info = self.controller.price_info
        return {
            "reason": decision.reason if decision else None,
            "current_price": info.current_price,
            "is_cheap": info.is_cheap,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.controller.async_set_heating(hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self) -> None:
        await self.controller.async_set_heating(True)

    async def async_turn_off(self) -> None:
        await self.controller.async_set_heating(False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        low = kwargs.get(ATTR_TARGET_TEMP_LOW, self.controller.min_temp)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH, self.controller.max_temp)
        await self.controller.async_set_range(float(low), float(high))
