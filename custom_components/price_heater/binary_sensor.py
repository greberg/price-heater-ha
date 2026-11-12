"""Binary sensors: heating demand and cheap-price indicator."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PriceHeaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller = entry.runtime_data
    async_add_entities(
        [
            HeatingDemandBinarySensor(controller, "heating_demand"),
            CheapPriceBinarySensor(controller, "cheap_price"),
        ]
    )


class HeatingDemandBinarySensor(PriceHeaterEntity, BinarySensorEntity):
    """On when the rules say the heater should be running."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool | None:
        decision = self.controller.decision
        if decision is None or decision.turn_on is None:
            return None
        return decision.turn_on


class CheapPriceBinarySensor(PriceHeaterEntity, BinarySensorEntity):
    """On when electricity counts as cheap right now."""

    @property
    def is_on(self) -> bool | None:
        return self.controller.price_info.is_cheap
