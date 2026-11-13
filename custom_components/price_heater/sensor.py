"""Sensors: status, and (with a power source) power, energy and cost today."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .controller import PriceHeaterController
from .entity import PriceHeaterEntity
from .logic import ALL_REASONS

ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: PriceHeaterController = entry.runtime_data
    entities: list[SensorEntity] = [PriceHeaterStatusSensor(controller, "status")]
    if controller.has_power:
        entities += [
            PowerSensor(controller, "power"),
            EnergyTodaySensor(controller, "energy_today"),
            CostTodaySensor(controller, "cost_today"),
        ]
    async_add_entities(entities)


class PriceHeaterStatusSensor(PriceHeaterEntity, SensorEntity):
    """The reason behind the current decision."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ALL_REASONS
    # The price list and entity map are for the cards; keep them out of history.
    _unrecorded_attributes = frozenset({"prices_today", "entities"})

    @property
    def native_value(self) -> str | None:
        decision = self.controller.decision
        return decision.reason if decision else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.controller.attributes


class PowerSensor(PriceHeaterEntity, SensorEntity):
    """The heater's power now (metered, or rated power while on)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        return self.controller.power

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": "sensor" if self.controller.power_sensor else "rated"}


class EnergyTodaySensor(PriceHeaterEntity, SensorEntity, RestoreEntity):
    """Energy the heater used today; resets at midnight (Energy dashboard ready)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.controller.meter.restore(
                dt_util.now(), last.attributes.get("day"), last.state, last.attributes.get("cost")
            )

    @property
    def native_value(self) -> float:
        return round(self.controller.meter.energy_kwh, 3)

    @property
    def last_reset(self) -> datetime | None:
        return dt_util.start_of_local_day()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meter = self.controller.meter
        # day and cost let today's totals survive a restart
        return {"day": meter.day, "cost": round(meter.cost, 4)}


class CostTodaySensor(PriceHeaterEntity, SensorEntity):
    """What the heater's electricity cost today, at the spot price."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        # The currency comes from the price sensor; wait for the first check
        # so the unit never changes under the statistics.
        return self.controller.decision is not None

    @property
    def native_value(self) -> float:
        return round(self.controller.meter.cost, 2)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.controller.currency

    @property
    def device_class(self) -> SensorDeviceClass | None:
        currency = self.controller.currency
        return SensorDeviceClass.MONETARY if currency and ISO_CURRENCY.match(currency) else None

    @property
    def last_reset(self) -> datetime | None:
        return dt_util.start_of_local_day()
