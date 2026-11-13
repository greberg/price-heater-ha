"""The thermostat entity, running in a real Home Assistant core."""

from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.price_heater.const import DOMAIN

HEATER = "input_boolean.heater"
TEMP = "sensor.room"
PRICE = "sensor.price"
CLIMATE = "climate.price_heater"
SWITCH = "switch.price_heater_automation"


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    # The cards need the frontend, which the test environment does not ship.
    hass.config.components.update({"frontend", "http"})
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"heater": {}}})
    hass.states.async_set(TEMP, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    # Every hour expensive: the heater only runs below the minimum.
    hass.states.async_set(PRICE, "2.0", {"unit_of_measurement": "SEK/kWh", "today": [2.0] * 24})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Price Heater",
        unique_id=HEATER,
        data={
            "heater_entity": HEATER,
            "temperature_sensor": TEMP,
            "price_sensor": PRICE,
            "min_temp": 19.0,
            "max_temp": 22.0,
            "hysteresis": 0.5,
            "cheap_hours": 0,
            "min_cycle_minutes": 0,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.price_heater.async_register_cards"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_thermostat_reflects_controller(hass: HomeAssistant, entry) -> None:
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    assert state.attributes["current_temperature"] == 20.0
    assert state.attributes["target_temp_low"] == 19.0
    assert state.attributes["target_temp_high"] == 22.0
    assert state.attributes["hvac_action"] == HVACAction.IDLE
    assert state.attributes["reason"] == "expensive_price"


async def test_raising_minimum_heats_without_reload(hass: HomeAssistant, entry) -> None:
    controller = entry.runtime_data
    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": CLIMATE, "target_temp_low": 21.0, "target_temp_high": 23.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.runtime_data is controller  # no reload
    assert entry.options["min_temp"] == 21.0 and entry.options["max_temp"] == 23.0
    assert entry.options["price_sensor"] == PRICE  # other settings kept
    assert hass.states.get(HEATER).state == "on"
    state = hass.states.get(CLIMATE)
    assert state.attributes["target_temp_low"] == 21.0
    assert state.attributes["hvac_action"] == HVACAction.HEATING


async def test_invalid_range_is_refused(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate", "set_temperature",
            {"entity_id": CLIMATE, "target_temp_low": 21.8, "target_temp_high": 22.0},
            blocking=True,
        )
    assert entry.runtime_data.min_temp == 19.0
    assert not entry.options


async def test_off_pauses_and_turns_heater_off(hass: HomeAssistant, entry) -> None:
    hass.states.async_set(TEMP, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "on"

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "off"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "off"
    assert hass.states.get(SWITCH).state == "off"
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_action"] == HVACAction.OFF

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": CLIMATE}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"
    assert hass.states.get(HEATER).state == "on"  # 18 °C is below the minimum


async def test_options_change_still_reloads(hass: HomeAssistant, entry) -> None:
    controller = entry.runtime_data
    hass.config_entries.async_update_entry(
        entry, options={**entry.data, "hysteresis": 0.3}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not controller
    assert entry.runtime_data.hysteresis == 0.3
