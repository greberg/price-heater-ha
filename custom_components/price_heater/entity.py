"""Base entity for Price Heater."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import PriceHeaterController


class PriceHeaterEntity(Entity):
    """Entity that refreshes whenever the controller re-evaluates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: PriceHeaterController, key: str) -> None:
        self.controller = controller
        entry = controller.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Price Heater",
            model="Price-aware heater control",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.controller.signal, self._handle_controller_update
            )
        )

    @callback
    def _handle_controller_update(self) -> None:
        self.async_write_ha_state()
