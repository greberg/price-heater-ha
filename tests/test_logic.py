"""Tests for the pure decision logic (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).parent.parent / "custom_components" / "price_heater" / "logic.py"
_spec = importlib.util.spec_from_file_location("price_heater_logic", _PATH)
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic  # dataclasses need the module registered
_spec.loader.exec_module(logic)

TZ = timezone(timedelta(hours=1))
MIDNIGHT = datetime(2026, 11, 12, tzinfo=TZ)
# Hourly prices: cheapest at 02-05, expensive in the evening.
HOURLY = [1.0, 0.8, 0.3, 0.2, 0.25, 0.35, 1.2, 1.5, 1.4, 1.0, 0.9, 0.8,
          0.7, 0.7, 0.8, 0.9, 1.3, 1.8, 2.0, 1.9, 1.5, 1.2, 1.1, 1.0]


def at(hour, minute=0):
    return MIDNIGHT + timedelta(hours=hour, minutes=minute)


def decide(temp, cheap, on=False):
    return logic.decide(temp, cheap, on, min_temp=19, max_temp=22, hysteresis=0.5)


# --- decide ---------------------------------------------------------------

def test_unknown_temperature_leaves_heater_alone():
    assert decide(None, True).turn_on is None


def test_below_min_heats_even_when_expensive():
    d = decide(18.0, False)
    assert d.turn_on is True and d.reason == logic.REASON_BELOW_MIN


def test_hysteresis_keeps_heating_just_above_min():
    assert decide(19.3, False, on=True).turn_on is True
    assert decide(19.3, False, on=False).turn_on is False
    assert decide(19.6, False, on=True).turn_on is False


def test_above_max_stops_even_when_cheap():
    d = decide(22.0, True, on=True)
    assert d.turn_on is False and d.reason == logic.REASON_ABOVE_MAX


def test_cheap_heats_in_band():
    assert decide(20.0, True).reason == logic.REASON_CHEAP


def test_cheap_does_not_start_close_to_max():
    assert decide(21.7, True, on=False).reason == logic.REASON_NEAR_MAX
    assert decide(21.7, True, on=True).turn_on is True


def test_expensive_waits_in_band():
    assert decide(20.0, False, on=True).reason == logic.REASON_EXPENSIVE


def test_unknown_price_only_keeps_minimum():
    assert decide(20.0, None).reason == logic.REASON_PRICE_UNAVAILABLE
    assert decide(18.0, None).turn_on is True


# --- analyze_prices -------------------------------------------------------

def test_plain_list_cheapest_hours():
    attrs = {"today": HOURLY}
    assert logic.analyze_prices(None, attrs, at(3, 30), 4, None).is_cheap is True
    info = logic.analyze_prices(None, attrs, at(18), 4, None)
    assert info.is_cheap is False
    assert info.current_price == 2.0
    assert info.rank == 24
    assert info.cheap_limit == 0.35


def test_raw_today_with_iso_strings_and_quarter_hours():
    raw = []
    for i in range(96):
        start = MIDNIGHT + timedelta(minutes=15 * i)
        raw.append({"start": start.isoformat(), "value": 0.1 if 8 <= i < 12 else 1.0})
    attrs = {"raw_today": raw}
    # 1 cheap hour == four 15-min slots (02:00-03:00).
    assert logic.analyze_prices(1.0, attrs, at(2, 20), 1, None).in_cheapest_hours is True
    assert logic.analyze_prices(1.0, attrs, at(3, 5), 1, None).in_cheapest_hours is False


def test_raw_today_with_datetimes_and_price_key():
    raw = [{"time": at(h), "price": p} for h, p in enumerate(HOURLY)]
    assert logic.analyze_prices(None, {"prices_today": raw}, at(4), 3, None).is_cheap


def test_stale_price_list_is_ignored():
    raw = [{"start": at(h) - timedelta(days=1), "value": p} for h, p in enumerate(HOURLY)]
    info = logic.analyze_prices(0.5, {"raw_today": raw}, at(3), 6, None)
    assert info.in_cheapest_hours is None and info.is_cheap is None


def test_threshold_without_price_list():
    assert logic.analyze_prices(0.4, {}, at(12), 6, 0.5).is_cheap is True
    assert logic.analyze_prices(0.6, {}, at(12), 6, 0.5).is_cheap is False
    assert logic.analyze_prices(0.6, {}, at(12), 6, None).is_cheap is None


def test_threshold_or_cheapest_hours():
    attrs = {"today": HOURLY}
    # 12:00 costs 0.7: not among 4 cheapest, but under threshold 0.75.
    assert logic.analyze_prices(None, attrs, at(12), 4, 0.75).is_cheap is True


def test_none_values_in_list_are_skipped():
    prices = HOURLY[:20] + [None] * 4
    info = logic.analyze_prices(None, {"today": prices}, at(3), 2, None)
    assert info.is_cheap is True and info.slots_today == 20


def test_zero_cheap_hours_never_cheap_without_threshold():
    assert logic.analyze_prices(None, {"today": HOURLY}, at(3), 0, None).is_cheap is False


def test_price_schedule_marks_cheap_slots():
    schedule = logic.price_schedule({"today": HOURLY}, at(10), 4, 0.75)
    assert len(schedule) == 24
    assert schedule[3]["start"] == at(3).isoformat()
    assert [s["cheap"] for s in schedule[:6]] == [False, False, True, True, True, True]
    assert schedule[12]["cheap"] is True  # under threshold 0.75
    assert schedule[18]["cheap"] is False
    assert logic.price_schedule({}, at(10), 4, None) == []


# --- power, energy and cost -------------------------------------------------

def test_power_units():
    assert logic.power_watts("1.5", "kW") == 1500
    assert logic.power_watts("800", "W") == 800
    assert logic.power_watts("800", None) == 800
    assert logic.power_watts("x", "W") is None
    assert logic.power_watts("1", "kWh") is None


def test_heater_power_prefers_meter_then_rated():
    assert logic.heater_power(950.0, 1000.0, True) == 950.0
    assert logic.heater_power(None, 1000.0, True) == 1000.0
    assert logic.heater_power(None, 1000.0, False) == 0.0
    assert logic.heater_power(None, None, True) is None
    assert logic.heater_power(-3.0, None, True) == 0.0


def test_currency_from_price_unit():
    assert logic.currency_of("SEK/kWh") == "SEK"
    assert logic.currency_of("öre/kWh") == "öre"
    assert logic.currency_of(None) is None


def test_energy_meter_integrates_power_and_cost():
    meter = logic.EnergyMeter()
    meter.update(at(10), 2000.0, 1.5)
    for minute in range(1, 61):  # one hour at 2 kW and 1.5/kWh
        meter.update(at(10, 0) + timedelta(minutes=minute), 2000.0, 1.5)
    assert abs(meter.energy_kwh - 2.0) < 1e-9
    assert abs(meter.cost - 3.0) < 1e-9


def test_energy_meter_skips_gaps_and_resets_at_midnight():
    meter = logic.EnergyMeter()
    meter.update(at(10), 1000.0, 1.0)
    meter.update(at(11), 1000.0, 1.0)  # an hour gap: Home Assistant was down
    assert meter.energy_kwh == 0.0
    meter.update(at(23, 58), 1200.0, 2.0)
    meter.update(at(23, 59), 1200.0, 2.0)
    assert meter.energy_kwh > 0
    next_day = at(23, 59) + timedelta(minutes=3)  # 00:02
    meter.update(next_day, 1200.0, 2.0)
    assert meter.day == next_day.date().isoformat()
    # only the two minutes after midnight count for the new day
    assert abs(meter.energy_kwh - 1200 * 120 / 3_600_000) < 1e-9


def test_energy_meter_restore_only_same_day():
    meter = logic.EnergyMeter()
    meter.restore(at(12), at(0).date().isoformat(), "3.5", "4.2")
    assert meter.energy_kwh == 3.5 and meter.cost == 4.2
    other = logic.EnergyMeter()
    other.restore(at(12), "2020-01-01", "3.5", "4.2")
    assert other.energy_kwh == 0.0
    meter.update(at(12), 1000.0, 1.0)
    meter.update(at(12, 6), 1000.0, 1.0)
    assert abs(meter.energy_kwh - 3.6) < 1e-9
