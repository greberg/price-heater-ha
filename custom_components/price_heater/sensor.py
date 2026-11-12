"""Status sensor: shows why the heater is on or off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PriceHeaterEntity
from .logic import ALL_REASONS


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([PriceHeaterStatusSensor(entry.runtime_data, "status")])


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
