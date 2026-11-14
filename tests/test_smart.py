"""Tests for the smart strategy: sun, model fitting and planning (no Home Assistant needed)."""

from __future__ import annotations

import importlib
import math
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load the integration's pure modules as a package, without its __init__ (which needs Home Assistant).
_PKG = "price_heater_pure"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(Path(__file__).parent.parent / "custom_components" / "price_heater")]
sys.modules[_PKG] = _pkg
smart = importlib.import_module(f"{_PKG}.smart")
logic = importlib.import_module(f"{_PKG}.logic")

UTC = timezone.utc
STOCKHOLM = (59.33, 18.07)
HOUSE = smart.ThermalModel(
    heat=1.2, slab_air=0.15, slab_base=0.0,
    air_slab=0.35, air_out=0.04, air_sun=1.5, air_base=0.05, two_node=True,
)


def start_of(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


# --- sun --------------------------------------------------------------------

def test_solar_elevation_midsummer_and_midwinter():
    june = [smart.solar_elevation(datetime(2026, 6, 21, 0, tzinfo=UTC) + timedelta(minutes=m), *STOCKHOLM) for m in range(0, 1440, 5)]
    assert 53.5 < max(june) < 54.7  # 90 - 59.33 + 23.44
    december = [smart.solar_elevation(datetime(2026, 12, 21, 0, tzinfo=UTC) + timedelta(minutes=m), *STOCKHOLM) for m in range(0, 1440, 5)]
    assert 6.5 < max(december) < 7.8
    assert smart.solar_elevation(datetime(2026, 12, 21, 23, tzinfo=UTC), *STOCKHOLM) < 0


def test_clouds_cut_the_sun():
    assert smart.cloud_factor(0) == 1.0
    assert abs(smart.cloud_factor(100) - 0.25) < 1e-9
    assert smart.cloud_factor(50) > smart.cloud_factor(90)
    points = smart.parse_forecast([
        {"datetime": "2026-11-14T10:00:00+00:00", "temperature": 2, "condition": "sunny"},
        {"datetime": "2026-11-14T11:00:00Z", "temperature": 4, "cloud_coverage": 80},
    ])
    assert points[0].cloud == 0 and points[1].cloud == 80
    assert smart._interpolate(points, datetime(2026, 11, 14, 10, 30, tzinfo=UTC), "temperature") == 3


# --- inputs -------------------------------------------------------------------

def test_inputs_cover_a_day_and_repeat_known_prices():
    now = datetime(2026, 11, 14, 20, 7, tzinfo=UTC)
    today = [(start_of(14, h), start_of(14, h + 1) if h < 23 else start_of(15), float(h)) for h in range(24)]
    weather = smart.parse_forecast(
        [{"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 0.0, "cloud_coverage": 100} for h in range(30)]
    )
    inputs = smart.build_inputs(now, today, weather, outdoor_now=3.0, latitude=59.33, longitude=18.07)
    assert inputs.times[0] == datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
    assert len(inputs.times) == 24 * 4
    # tomorrow 03:00 is not published yet: yesterday's 03:00 price is used
    assert inputs.price[inputs.times.index(start_of(15, 3))] == 3.0
    # the sensor reads 3° where the forecast says 0: the gap fades over hours
    assert abs(inputs.outdoor[0] - 3.0) < 1e-9
    assert inputs.outdoor[-1] < 0.1
    assert all(s == 0 for s in inputs.sun[:8])  # dark in the evening


def test_price_slots_today_and_tomorrow_plain_lists():
    local = timezone(timedelta(hours=1))
    now = datetime(2026, 11, 14, 15, 0, tzinfo=local)
    slots = smart.price_slots({"today": [1.0] * 24, "tomorrow": [2.0] * 24}, now)
    assert len(slots) == 48
    assert slots[24][0] == datetime(2026, 11, 15, 0, 0, tzinfo=local) and slots[24][2] == 2.0


# --- history ------------------------------------------------------------------

def test_step_average_and_resample():
    t0 = start_of(14)
    grid = [t0, t0 + smart.STEP]
    changes = [(t0 - timedelta(hours=1), 0.0), (t0 + timedelta(minutes=5), 1.0), (t0 + timedelta(minutes=20), None)]
    avg = smart.step_average(changes, grid)
    assert abs(avg[0] - 10 / 15) < 1e-9
    assert avg[1] is None
    assert smart.resample(changes, grid) == [0.0, 1.0]


def test_power_share_uses_full_power():
    changes = [(start_of(14, h), w) for h, w in enumerate([0, 1000, 980, 1010, 0, None])]
    share = [v for _, v in smart.power_share(changes)]
    assert share[0] == 0 and share[-1] is None and 0.95 < share[1] <= 1


def _simulated_history(model, days=4, step_noise=True):
    """A week of a house following a plain price-blind thermostat, sampled every 15 min."""
    samples = []
    air, slab = 20.0, 22.0
    t = start_of(1)
    for k in range(days * 96):
        hour = k / 4
        outdoor = 2 + 5 * math.sin(2 * math.pi * (hour - 9) / 24)
        sun = smart.clear_sky(smart.solar_elevation(t, *STOCKHOLM)) * (0.3 if (k // 96) % 2 else 1.0)
        on = 1.0 if (k // 6) % 3 == 0 else 0.0  # 1.5 h on, 3 h off
        samples.append(smart.Sample(t, round(air, 1), round(slab, 1), outdoor, sun, on))
        air, slab = model.step(air, slab, outdoor, sun, on)
        t += smart.STEP
    return samples


def test_fit_recovers_the_house():
    fitted, status = smart.fit_model(_simulated_history(HOUSE), two_node=True)
    assert status == smart.STATUS_READY
    for name in ("heat", "air_slab", "slab_air", "air_out"):
        assert abs(getattr(fitted, name) - getattr(HOUSE, name)) < 0.25 * getattr(HOUSE, name), name
    assert fitted.rmse < 0.1


def test_fit_needs_enough_history_and_switching():
    history = _simulated_history(HOUSE)
    assert smart.fit_model(history[:96], two_node=True) == (None, smart.STATUS_LEARNING)
    always_off = [smart.Sample(s.time, s.air, s.slab, s.outdoor, s.sun, 0.0) for s in history]
    assert smart.fit_model(always_off, two_node=True) == (None, smart.STATUS_NEEDS_VARIATION)


def test_one_node_fit_without_floor_sensor():
    simple = smart.ThermalModel(heat=0.8, air_out=0.06, air_sun=1.0, air_base=0.1)
    history = _simulated_history(simple)
    history = [smart.Sample(s.time, s.air, None, s.outdoor, s.sun, s.on) for s in history]
    fitted, status = smart.fit_model(history, two_node=False)
    assert status == smart.STATUS_READY and not fitted.two_node
    assert abs(fitted.heat - 0.8) < 0.15


def test_least_squares_drops_negative_coefficients():
    rows = [[1.0, x, 1.0] for x in range(10)]
    ys = [5.0 - 0.0 * x for x in range(10)]
    coef = smart.least_squares(rows, ys, positive=(1,))
    assert coef[1] >= 0


# --- planning -----------------------------------------------------------------

def _inputs(prices, outdoor=0.0, sun=None):
    n = len(prices)
    return smart.Inputs(
        times=[start_of(14) + smart.STEP * k for k in range(n)],
        price=list(prices),
        outdoor=[outdoor] * n,
        sun=list(sun) if sun else [0.0] * n,
    )


def _hourly(values):
    return [v for v in values for _ in range(4)]


# Cheap night, very expensive day and evening.
SPIKY = _hourly([0.3] * 6 + [3.0] * 18)


def test_plan_keeps_minimum_and_charges_concrete_when_cheap():
    plan = smart.make_plan(HOUSE, _inputs(SPIKY), air=20.5, slab=23.0, low=20.0, high=23.0, slab_max=29.0)
    assert plan.shortfall is None
    assert min(plan.air[1:]) >= 20.0 - 1e-6
    night = sum(plan.on[: 6 * 4])
    day = sum(plan.on[6 * 4 :])
    assert night > day  # the heat is bought at night
    assert max(s for s in plan.slab if s is not None) <= 29.0 + 1e-6


def test_plan_is_cheaper_than_heating_on_demand():
    inputs = _inputs(SPIKY)
    plan = smart.make_plan(HOUSE, inputs, air=20.5, slab=23.0, low=20.0, high=23.0, slab_max=29.0)
    # A plain thermostat: heat whenever the air is below 20.
    air, slab, cost = 20.5, 23.0, 0.0
    for k in range(len(inputs.times)):
        on = 1.0 if air < 20.0 else 0.0
        cost += on * inputs.price[k] * smart.STEP_H
        air, slab = HOUSE.step(air, slab, 0.0, 0.0, on)
    assert plan.price_hours() < 0.7 * cost


def test_sun_means_less_heating():
    noon_sun = _hourly([0.0] * 9 + [0.2, 0.4, 0.5, 0.5, 0.4, 0.2] + [0.0] * 9)
    flat = _hourly([1.0] * 24)
    cloudy = smart.make_plan(HOUSE, _inputs(flat), air=20.2, slab=22.0, low=20.0, high=23.0)
    sunny = smart.make_plan(HOUSE, _inputs(flat, sun=noon_sun), air=20.2, slab=22.0, low=20.0, high=23.0)
    assert sunny.heat_hours() < cloudy.heat_hours()


def test_plan_reports_when_minimum_cannot_be_kept():
    weak = smart.ThermalModel(heat=0.05, air_out=0.2, air_sun=0, air_base=0, air_slab=0.3, slab_air=0.1, two_node=True)
    plan = smart.make_plan(weak, _inputs(_hourly([1.0] * 24), outdoor=-20), air=20.0, slab=20.0, low=20.0, high=23.0)
    assert plan.shortfall is not None
    assert all(plan.on[: 4 * 12])  # it still heats all it can


def test_negative_prices_are_used():
    prices = _hourly([1.0] * 3 + [-0.1] + [1.0] * 20)
    plan = smart.make_plan(HOUSE, _inputs(prices), air=21.5, slab=24.0, low=20.0, high=23.0, slab_max=29.0)
    assert all(plan.on[12:16])
    summary = plan.summary(power_kw=1.0)
    assert summary["heat_hours"] >= 1.0 and summary["until"]


def test_smart_decide_limits_win_over_plan():
    plan = smart.make_plan(HOUSE, _inputs(SPIKY), air=20.5, slab=23.0, low=20.0, high=23.0)
    args = dict(min_temp=20.0, max_temp=23.0, hysteresis=0.3, slab_max=28.0)
    assert smart.smart_decide(plan, None, 25, False, **args).turn_on is None
    assert smart.smart_decide(plan, 21, 28.5, True, **args).reason == logic.REASON_FLOOR_MAX
    assert smart.smart_decide(plan, 19.5, 25, False, **args).reason == logic.REASON_BELOW_MIN
    assert smart.smart_decide(plan, 23.5, 25, False, **args).reason == logic.REASON_ABOVE_MAX
    planned = smart.smart_decide(plan, 21.0, 25, False, **args)
    assert planned.reason in (logic.REASON_PLANNED_HEAT, logic.REASON_PLANNED_OFF)
    assert planned.turn_on == plan.on[0]
    assert smart.smart_decide(None, 21.0, 25, False, **args) is None
