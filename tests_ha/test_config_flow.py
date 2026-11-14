"""The setup form, in a real Home Assistant core."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.price_heater.const import DOMAIN

BASE = {
    "name": "Floor",
    "heater_entity": "switch.floor",
    "temperature_sensor": "sensor.room",
    "price_sensor": "sensor.price",
    "min_temp": 20.0,
    "max_temp": 23.0,
    "hysteresis": 0.3,
    "cheap_hours": 6,
    "min_cycle_minutes": 5,
    "max_floor_temp": 28.0,
}


async def test_smart_needs_an_outdoor_source(hass: HomeAssistant) -> None:
    hass.config.components.update({"frontend", "http"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**BASE, "strategy": "smart"})
    assert result["errors"] == {"base": "smart_needs_outdoor"}

    with patch("custom_components.price_heater.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**BASE, "strategy": "smart", "weather_entity": "weather.home", "floor_sensor": "sensor.floor"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["strategy"] == "smart" and result["data"]["floor_sensor"] == "sensor.floor"
