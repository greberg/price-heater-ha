"""Smart strategy in a real Home Assistant core, with a simulated house as history."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State, SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.price_heater import smart
from custom_components.price_heater.const import DOMAIN

HEATER = "input_boolean.heater"
AIR = "sensor.room"
FLOOR = "sensor.floor"
OUTDOOR = "sensor.outdoor"
SUN = "sensor.lux"
PRICE = "sensor.price"
WEATHER = "weather.home"
STATUS = "sensor.price_heater_status"

HOUSE = smart.ThermalModel(
    heat=1.2, slab_air=0.15, air_slab=0.35, air_out=0.04, air_sun=1.5, air_base=0.05, two_node=True,
)


def simulated_history(end, days=3):
    """Recorder rows for a house on a price-blind timer, ending at ``end``."""
    rows = {AIR: [], FLOOR: [], OUTDOOR: [], SUN: [], HEATER: []}
    air, slab = 20.0, 22.0
    t = end - timedelta(days=days)
    k = 0
    while t < end:
        outdoor = 0.0
        sun = smart.clear_sky(smart.solar_elevation(t, 59.33, 18.07))
        on = (k // 6) % 3 == 0
        for entity, value in ((AIR, round(air, 1)), (FLOOR, round(slab, 1)), (OUTDOOR, outdoor), (SUN, round(sun * 80000))):
            rows[entity].append(State(entity, str(value), {}, last_changed=t, last_updated=t))
        rows[HEATER].append(State(HEATER, "on" if on else "off", {}, last_changed=t, last_updated=t))
        air, slab = HOUSE.step(air, slab, outdoor, sun, 1.0 if on else 0.0)
        t += smart.STEP
        k += 1
    return rows


class FakeRecorder:
    def __init__(self, hass):
        self.hass = hass

    async def async_add_executor_job(self, func, *args):
        return await self.hass.async_add_executor_job(func, *args)


@pytest.fixture
async def setup(hass: HomeAssistant):
    hass.config.latitude, hass.config.longitude = 59.33, 18.07
    hass.config.components.update({"frontend", "http"})
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"heater": {}}})
    hass.states.async_set(AIR, "20.6", {"device_class": "temperature"})
    hass.states.async_set(FLOOR, "23.0", {"device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "1.0", {"device_class": "temperature"})
    hass.states.async_set(SUN, "0")
    # Cheap tonight and early morning, expensive the rest.
    now = dt_util.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    raw = [
        {"start": (midnight + timedelta(hours=h)).isoformat(), "end": (midnight + timedelta(hours=h + 1)).isoformat(),
         "value": 0.3 if h % 24 < 6 else 3.0}
        for h in range(48)
    ]
    hass.states.async_set(PRICE, "1.0", {"unit_of_measurement": "SEK/kWh", "raw_today": raw[:24], "raw_tomorrow": raw[24:]})

    forecast_calls = []

    async def get_forecasts(call):
        forecast_calls.append(call.data["type"])
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        return {
            WEATHER: {
                "forecast": [
                    {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": 0.0, "cloud_coverage": 100}
                    for h in range(48)
                ]
            }
        }

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Price Heater",
        unique_id=HEATER,
        data={
            "heater_entity": HEATER, "temperature_sensor": AIR, "price_sensor": PRICE,
            "min_temp": 20.0, "max_temp": 23.0, "hysteresis": 0.3, "cheap_hours": 6,
            "min_cycle_minutes": 0, "heater_power": 1000,
            "strategy": "smart", "weather_entity": WEATHER, "outdoor_sensor": OUTDOOR,
            "floor_sensor": FLOOR, "solar_sensor": SUN, "max_floor_temp": 28.0,
        },
    )
    entry.add_to_hass(hass)
    history = simulated_history(dt_util.utcnow())
    with patch("custom_components.price_heater.async_register_cards"), patch(
        "homeassistant.components.recorder.get_instance", return_value=FakeRecorder(hass)
    ), patch(
        "homeassistant.components.recorder.history.get_significant_states",
        side_effect=lambda hass_, start, end, ids, **kw: {e: history[e] for e in ids if e in history},
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry, forecast_calls


async def test_learns_the_house_and_plans(hass: HomeAssistant, setup) -> None:
    entry, forecast_calls = setup
    planner = entry.runtime_data.smart
    assert forecast_calls == ["hourly"]
    assert planner.model is not None and planner.model.two_node
    assert abs(planner.model.heat - HOUSE.heat) < 0.3
    assert planner.sun_scale and 60000 < planner.sun_scale < 100000
    assert planner.plan is not None

    state = hass.states.get(STATUS)
    assert state.state in ("planned_heat", "planned_off")
    attrs = state.attributes
    assert attrs["strategy"] == "smart"
    assert attrs["floor_temperature"] == 23.0 and attrs["outdoor_temperature"] == 1.0
    assert attrs["smart"]["status"] == "ready"
    assert attrs["smart"]["plan"]["cost"] is not None
    assert len(attrs["plan"]) >= 96
    assert attrs["entities"]["floor"] == FLOOR
    # The heater follows the plan.
    assert hass.states.get(HEATER).state == ("on" if planner.plan.on[0] else "off")


async def test_floor_limit_stops_heating(hass: HomeAssistant, setup) -> None:
    hass.states.async_set(AIR, "19.0", {"device_class": "temperature"})
    hass.states.async_set(FLOOR, "28.5", {"device_class": "temperature"})
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "floor_max"
    assert hass.states.get(HEATER).state == "off"


async def test_falls_back_to_simple_without_history(hass: HomeAssistant, setup) -> None:
    entry, _ = setup
    planner = entry.runtime_data.smart
    planner.model = None
    planner.model_status = smart.STATUS_LEARNING
    await entry.runtime_data.async_evaluate()
    state = hass.states.get(STATUS)
    assert state.state in ("cheap_price", "expensive_price", "near_max")
    assert state.attributes["smart"]["status"] == "learning"
