"""Smart strategy: learn how the house heats and cools, then plan the cheapest heating.

No Home Assistant imports, so it can be unit tested on its own (Python 3.9+).

The house is two connected heat stores::

    floor heating --> concrete slab --> room air --> outdoors
                                          ^
                                         sun

    d(slab)/dt = heat*u + slab_air*(air - slab)
    d(air)/dt  = air_slab*(slab - air) + air_out*(outdoor - air) + air_sun*sun + air_base

``u`` is the share of time the heater is on (0..1) and ``sun`` is the sun on
the house relative to a clear sky with the sun straight overhead. Both
temperatures are measured, so each line is a plain linear regression on the
recorded history. Without a floor sensor a single air store is used:
``d(air)/dt = heat*u + air_out*(outdoor - air) + air_sun*sun + air_base``.

The planner then looks ahead over the known prices (at least 24 h) with the
weather forecast and the sun's path, and picks the heating steps that keep the
air above the minimum for the least money: the step with the lowest price per
degree it adds at the moment the air would get too cold. Because the slab is
slow, that is often hours earlier, in cheap hours, so the concrete stores the
heat through the expensive ones; and sunny hours need less heating.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .logic import (
    PRICE_LIST_ATTRIBUTES,
    REASON_ABOVE_MAX,
    REASON_BELOW_MIN,
    REASON_FLOOR_MAX,
    REASON_PLANNED_HEAT,
    REASON_PLANNED_OFF,
    REASON_TEMPERATURE_UNAVAILABLE,
    TOMORROW_LIST_ATTRIBUTES,
    Decision,
    extract_price_slots,
    slot_duration,
    to_float,
)

STEP = timedelta(minutes=15)
STEP_H = STEP.total_seconds() / 3600
STEPS_PER_HOUR = 4
PLAN_HOURS = 24  # plan at least this far; unknown prices repeat the day before
MIN_HOURS = 36  # usable hours of history before the model is trusted
MIN_SWITCH_HOURS = 2  # the history must have this much both on and off time
HISTORY_DAYS = 10

STATUS_READY = "ready"
STATUS_LEARNING = "learning"
STATUS_NEEDS_VARIATION = "needs_variation"
STATUS_NO_FIT = "no_fit"
STATUS_NO_INPUTS = "no_inputs"

PriceSlot = Tuple[datetime, datetime, float]


# --------------------------------------------------------------------------
# Sun and weather
# --------------------------------------------------------------------------

def solar_elevation(when: datetime, latitude: float, longitude: float) -> float:
    """Sun's height above the horizon in degrees (NOAA approximation)."""
    t = when.astimezone(timezone.utc)
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (t.timetuple().tm_yday - 1 + (hour - 12) / 24)
    decl = (
        0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    )
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g)
    )
    solar_minutes = hour * 60 + eqtime + 4 * longitude
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, cos_zenith))))


def clear_sky(elevation: float) -> float:
    """Sunshine under a clear sky, 1 with the sun straight overhead."""
    if elevation <= 0:
        return 0.0
    return math.sin(math.radians(elevation)) ** 1.15


# Cloud cover (%) for forecasts that only give a condition.
CONDITION_CLOUDS = {
    "sunny": 0, "clear-night": 0, "windy": 30, "partlycloudy": 50,
    "windy-variant": 60, "exceptional": 50, "cloudy": 90, "lightning": 90,
    "rainy": 95, "snowy": 95, "snowy-rainy": 95, "hail": 95,
    "lightning-rainy": 95, "pouring": 100, "fog": 100,
}
UNKNOWN_CLOUDS = 60.0


def cloud_factor(cloud: Optional[float]) -> float:
    """Share of clear-sky sun that gets through (Kasten & Czeplak)."""
    c = UNKNOWN_CLOUDS if cloud is None else min(100.0, max(0.0, cloud))
    return 1 - 0.75 * (c / 100) ** 3.4


@dataclass
class WeatherPoint:
    time: datetime  # UTC
    temperature: Optional[float]
    cloud: Optional[float]


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def parse_forecast(forecast: Sequence[Mapping[str, Any]]) -> List[WeatherPoint]:
    """The ``weather.get_forecasts`` hourly list as sorted points."""
    points = []
    for item in forecast or []:
        when = _parse_time(item.get("datetime"))
        if when is None:
            continue
        cloud = to_float(item.get("cloud_coverage"))
        if cloud is None:
            cloud = CONDITION_CLOUDS.get(str(item.get("condition")))
        points.append(WeatherPoint(when, to_float(item.get("temperature")), cloud))
    return sorted(points, key=lambda p: p.time)


def _interpolate(points: Sequence[WeatherPoint], when: datetime, attr: str) -> Optional[float]:
    known = [(p.time, getattr(p, attr)) for p in points if getattr(p, attr) is not None]
    if not known:
        return None
    if when <= known[0][0]:
        return known[0][1]
    for (t0, v0), (t1, v1) in zip(known, known[1:]):
        if t0 <= when <= t1:
            span = (t1 - t0).total_seconds()
            return v0 if span <= 0 else v0 + (v1 - v0) * (when - t0).total_seconds() / span
    return known[-1][1]


# --------------------------------------------------------------------------
# What lies ahead: prices, outdoor temperature and sun per 15 minutes
# --------------------------------------------------------------------------

def price_slots(attributes: Mapping[str, Any], now: datetime) -> List[PriceSlot]:
    """Today's and (once published) tomorrow's prices as (start, end, price)."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    result: List[PriceSlot] = []
    for keys, day_start in (
        (PRICE_LIST_ATTRIBUTES, midnight),
        (TOMORROW_LIST_ATTRIBUTES, midnight + timedelta(days=1)),
    ):
        slots = extract_price_slots(attributes, now, keys)
        if not slots:
            continue
        duration = slot_duration(slots)
        for i, (start, value) in enumerate(slots):
            if value is None:
                continue
            begin = start if start is not None else day_start + duration * i
            result.append((begin, begin + duration, value))
    return sorted(result, key=lambda s: s[0])


def _price_at(slots: Sequence[PriceSlot], when: datetime) -> Optional[float]:
    for start, end, value in slots:
        if start <= when < end:
            return value
    return None


def step_start(now: datetime) -> datetime:
    """Start of the 15-minute step covering ``now``, in UTC."""
    t = now.astimezone(timezone.utc)
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


@dataclass
class Inputs:
    times: List[datetime]  # step starts, UTC
    price: List[float]
    outdoor: List[float]
    sun: List[float]


def build_inputs(
    now: datetime,
    slots: Sequence[PriceSlot],
    weather: Sequence[WeatherPoint],
    outdoor_now: Optional[float],
    latitude: float,
    longitude: float,
    sun_now: Optional[float] = None,
) -> Optional[Inputs]:
    """Price, outdoor temperature and sun for every step of the plan."""
    start = step_start(now)
    known_end = max((end for _, end, _ in slots), default=start)
    end = max(known_end.astimezone(timezone.utc), start + timedelta(hours=PLAN_HOURS))

    forecast_now = _interpolate(weather, start, "temperature")
    bias = outdoor_now - forecast_now if outdoor_now is not None and forecast_now is not None else 0.0
    clear_now = clear_sky(solar_elevation(start, latitude, longitude))
    # A measured sun level now corrects the cloud forecast for the next hours.
    seen = min(1.2, sun_now / clear_now) if sun_now is not None and clear_now > 0.1 else None

    times, prices, outdoor, sun = [], [], [], []
    t = start
    while t < end:
        price = _price_at(slots, t)
        for days in (1, 2):  # unknown yet: assume the same as a day earlier
            if price is None:
                price = _price_at(slots, t - timedelta(days=days))
        if price is None:
            return None
        hours_ahead = (t - start).total_seconds() / 3600
        temp = _interpolate(weather, t, "temperature")
        if temp is None:
            if outdoor_now is None:
                return None
            temp = outdoor_now
        else:
            temp += bias * math.exp(-hours_ahead / 6)
        factor = cloud_factor(_interpolate(weather, t, "cloud"))
        if seen is not None:
            weight = math.exp(-hours_ahead / 1.5)
            factor = weight * seen + (1 - weight) * factor
        times.append(t)
        prices.append(price)
        outdoor.append(temp)
        sun.append(clear_sky(solar_elevation(t + STEP / 2, latitude, longitude)) * factor)
        t += STEP
    return Inputs(times, prices, outdoor, sun)


# --------------------------------------------------------------------------
# The thermal model
# --------------------------------------------------------------------------

@dataclass
class ThermalModel:
    """Rates in °C per hour (see the module docstring)."""

    heat: float
    air_out: float
    air_sun: float
    air_base: float
    air_slab: float = 0.0
    slab_air: float = 0.0
    slab_base: float = 0.0
    two_node: bool = False
    hours: float = 0.0  # hours of history it was fitted on
    rmse: float = 0.0  # typical error of a 1-hour air prediction, °C

    def step(self, air: float, slab: float, outdoor: float, sun: float, u: float, hours: float = STEP_H):
        """Air and slab temperature one step later (three Euler sub-steps)."""
        h = hours / 3
        for _ in range(3):
            if self.two_node:
                d_slab = self.heat * u + self.slab_air * (air - slab) + self.slab_base
                d_air = (
                    self.air_slab * (slab - air) + self.air_out * (outdoor - air)
                    + self.air_sun * sun + self.air_base
                )
            else:
                d_slab = 0.0
                d_air = self.heat * u + self.air_out * (outdoor - air) + self.air_sun * sun + self.air_base
            air += h * d_air
            slab += h * d_slab
        return air, slab

    def without_inputs(self) -> "ThermalModel":
        """The same house with no outdoor, sun or base gains: the effect of heating alone."""
        return ThermalModel(
            heat=self.heat, air_out=self.air_out, air_sun=0.0, air_base=0.0,
            air_slab=self.air_slab, slab_air=self.slab_air, slab_base=0.0, two_node=self.two_node,
        )

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class Sample:
    """Measurements at one 15-minute grid point (``on`` covers the step after it)."""

    time: datetime
    air: Optional[float]
    slab: Optional[float]
    outdoor: Optional[float]
    sun: float
    on: Optional[float]


def _solve(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def least_squares(rows: Sequence[Sequence[float]], ys: Sequence[float], positive: Sequence[int] = ()) -> Optional[List[float]]:
    """Linear least squares; coefficients in ``positive`` that come out negative are dropped (set to 0)."""
    width = len(rows[0])
    active = list(range(width))
    while active:
        ata = [[sum(r[i] * r[j] for r in rows) for j in active] for i in active]
        for i in range(len(active)):
            ata[i][i] += 1e-9 * len(rows)
        aty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in active]
        solution = _solve(ata, aty)
        if solution is None:
            return None
        coef = [0.0] * width
        for c, value in zip(active, solution):
            coef[c] = value
        negative = [c for c in active if c in positive and coef[c] < 0]
        if not negative:
            return coef
        active = [c for c in active if c not in negative]
    return None


def fit_model(samples: Sequence[Sample], two_node: bool) -> Tuple[Optional[ThermalModel], str]:
    """Fit the model on 1-hour windows of 15-minute samples."""
    windows = []
    span = STEPS_PER_HOUR
    for k in range(len(samples) - span):
        win = samples[k : k + span + 1]
        if win[-1].time - win[0].time != timedelta(hours=1):
            continue
        if any(s.air is None or s.outdoor is None or s.on is None for s in win[:-1]) or win[-1].air is None:
            continue
        if two_node and any(s.slab is None for s in win):
            continue
        head = win[:-1]
        mean = lambda f: sum(f(s) for s in head) / span  # noqa: E731
        windows.append(
            {
                "u": mean(lambda s: s.on),
                "sun": mean(lambda s: s.sun),
                "out": mean(lambda s: s.outdoor - s.air),
                "slab": mean(lambda s: s.slab - s.air) if two_node else 0.0,
                "d_air": win[-1].air - win[0].air,
                "d_slab": (win[-1].slab - win[0].slab) if two_node else 0.0,
            }
        )
    hours = len(windows) / span
    if hours < MIN_HOURS:
        return None, STATUS_LEARNING
    on_hours = sum(w["u"] for w in windows) / span
    if on_hours < MIN_SWITCH_HOURS or hours - on_hours < MIN_SWITCH_HOURS:
        return None, STATUS_NEEDS_VARIATION

    if two_node:
        air = least_squares(
            [[w["slab"], w["out"], w["sun"], 1.0] for w in windows],
            [w["d_air"] for w in windows],
            positive=(0, 1, 2),
        )
        # No constant term: with the slab-air gap fairly steady it can't be
        # told apart from the coupling, so all slab losses go via the air.
        slab = least_squares(
            [[w["u"], -w["slab"]] for w in windows],
            [w["d_slab"] for w in windows],
            positive=(0, 1),
        )
        if not air or not slab or min(air[0], air[1], slab[0], slab[1]) <= 0:
            return None, STATUS_NO_FIT
        model = ThermalModel(
            heat=slab[0], air_out=air[1], air_sun=air[2], air_base=air[3],
            air_slab=air[0], slab_air=slab[1], two_node=True,
        )
        predicted = [air[0] * w["slab"] + air[1] * w["out"] + air[2] * w["sun"] + air[3] for w in windows]
    else:
        air = least_squares(
            [[w["u"], w["out"], w["sun"], 1.0] for w in windows],
            [w["d_air"] for w in windows],
            positive=(0, 1, 2),
        )
        if not air or min(air[0], air[1]) <= 0:
            return None, STATUS_NO_FIT
        model = ThermalModel(heat=air[0], air_out=air[1], air_sun=air[2], air_base=air[3])
        predicted = [air[0] * w["u"] + air[1] * w["out"] + air[2] * w["sun"] + air[3] for w in windows]

    model.hours = hours
    model.rmse = math.sqrt(sum((p - w["d_air"]) ** 2 for p, w in zip(predicted, windows)) / len(windows))
    return model, STATUS_READY


# --------------------------------------------------------------------------
# Turning recorded history into samples
# --------------------------------------------------------------------------

Change = Tuple[datetime, Optional[float]]


def history_grid(start: datetime, end: datetime) -> List[datetime]:
    t = step_start(start) + STEP
    grid = []
    while t < end:
        grid.append(t)
        t += STEP
    return grid


def resample(changes: Sequence[Change], grid: Sequence[datetime]) -> List[Optional[float]]:
    """The value in effect at each grid time (None before the first or while unavailable)."""
    result, i, current = [], 0, None
    for t in grid:
        while i < len(changes) and changes[i][0] <= t:
            current = changes[i][1]
            i += 1
        result.append(current)
    return result


def step_average(changes: Sequence[Change], grid: Sequence[datetime]) -> List[Optional[float]]:
    """Time-weighted average over each step after a grid time (None if any part is unknown)."""
    result, i, current = [], 0, None
    seconds = STEP.total_seconds()
    for t in grid:
        while i < len(changes) and changes[i][0] <= t:
            current = changes[i][1]
            i += 1
        end = t + STEP
        value, since, j, total, known = current, t, i, 0.0, True
        while True:
            upto = changes[j][0] if j < len(changes) and changes[j][0] < end else end
            if value is None:
                known = False
            else:
                total += value * (upto - since).total_seconds()
            if upto == end:
                break
            since, value = upto, changes[j][1]
            j += 1
        result.append(total / seconds if known else None)
    return result


def power_share(changes: Sequence[Change]) -> List[Change]:
    """Power readings as a share of the heater's full power (95th percentile when on)."""
    on = sorted(v for _, v in changes if v is not None and v > 0)
    if not on:
        return [(t, None if v is None else 0.0) for t, v in changes]
    full = on[int(0.95 * (len(on) - 1))]
    return [(t, None if v is None else max(0.0, min(1.0, v / full))) for t, v in changes]


def sun_scale(raw: Sequence[Optional[float]], clear: Sequence[float]) -> Optional[float]:
    """What the sun sensor reads under a clear sky with the sun overhead."""
    ratios = sorted(r / c for r, c in zip(raw, clear) if r is not None and r > 0 and c > 0.05)
    if len(ratios) < 8:
        return None
    return ratios[int(0.9 * (len(ratios) - 1))] or None


def build_samples(
    grid: Sequence[datetime],
    air: Sequence[Optional[float]],
    slab: Sequence[Optional[float]],
    outdoor: Sequence[Optional[float]],
    on: Sequence[Optional[float]],
    sun_raw: Optional[Sequence[Optional[float]]],
    latitude: float,
    longitude: float,
) -> Tuple[List[Sample], Optional[float]]:
    """Samples for ``fit_model`` plus the sun sensor's clear-sky scale."""
    clear = [clear_sky(solar_elevation(t, latitude, longitude)) for t in grid]
    scale = sun_scale(sun_raw, clear) if sun_raw is not None else None
    samples = []
    for k, t in enumerate(grid):
        if scale and sun_raw is not None and sun_raw[k] is not None:
            sun = min(1.5, max(0.0, sun_raw[k] / scale))
        else:
            sun = clear[k] * cloud_factor(None)
        samples.append(Sample(t, air[k], slab[k], outdoor[k], sun, on[k]))
    return samples, scale


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

@dataclass
class Plan:
    times: List[datetime]
    on: List[bool]
    price: List[float]
    air: List[float]  # predicted at the start of each step, plus the end
    slab: List[Optional[float]]
    shortfall: Optional[datetime] = None  # first time the minimum cannot be kept

    def heat_hours(self) -> float:
        return sum(self.on) * STEP_H

    def price_hours(self) -> float:
        """Sum of price x hours on; times the heater's kW gives the cost."""
        return sum(p for p, o in zip(self.price, self.on) if o) * STEP_H

    def next_change(self) -> Optional[datetime]:
        for t, o in zip(self.times, self.on):
            if o != self.on[0]:
                return t
        return None

    def summary(self, power_kw: Optional[float] = None) -> dict:
        cost = self.price_hours() * power_kw if power_kw else None
        return {
            "heat_hours": round(self.heat_hours(), 2),
            "cost": round(cost, 2) if cost is not None else None,
            "next_change": _iso(self.next_change()),
            "lowest_temperature": round(min(self.air), 2),
            "highest_temperature": round(max(self.air), 2),
            "shortfall": _iso(self.shortfall),
            "until": _iso(self.times[-1] + STEP),
        }

    def steps(self) -> List[dict]:
        return [
            {
                "start": t.isoformat(),
                "on": o,
                "price": p,
                "temperature": round(a, 2),
                "floor": round(s, 2) if s is not None else None,
            }
            for t, o, p, a, s in zip(self.times, self.on, self.price, self.air, self.slab)
        ]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _simulate(model: ThermalModel, inputs: Inputs, air: float, slab: float, on: Sequence[float]):
    airs, slabs = [air], [slab]
    for k in range(len(inputs.times)):
        air, slab = model.step(air, slab, inputs.outdoor[k], inputs.sun[k], on[k])
        airs.append(air)
        slabs.append(slab)
    return airs, slabs


def make_plan(
    model: ThermalModel,
    inputs: Inputs,
    air: float,
    slab: Optional[float],
    low: float,
    high: float,
    slab_max: Optional[float] = None,
) -> Plan:
    """Cheapest on/off steps that keep the air at or above ``low``.

    The model is linear, so heating one step adds a fixed response to the
    temperatures after it. Repeatedly take the first moment the air would be
    too cold and turn on the step before it with the lowest price per degree
    it adds then, unless that pushes the air over ``high`` or the floor over
    ``slab_max``.
    """
    n = len(inputs.times)
    two = model.two_node and slab is not None
    start_slab = slab if two else 0.0
    airs, slabs = _simulate(model, inputs, air, start_slab, [0.0] * n)

    # Response to one step of heating, j steps after it ends.
    pulse = model.without_inputs()
    zero = Inputs(inputs.times, inputs.price, [0.0] * n, [0.0] * n)
    pulse_air, pulse_slab = _simulate(pulse, zero, 0.0, 0.0, [1.0] + [0.0] * (n - 1))
    pulse_air, pulse_slab = pulse_air[1:], pulse_slab[1:]

    on = [False] * n
    banned = [False] * n
    given_up: set = set()

    def fits(k: int) -> bool:
        for j in range(k + 1, n + 1):
            rise = pulse_air[j - k - 1]
            # Don't heat into an overshoot (sun-made warmth above max is not ours to fix).
            if airs[j] <= high < airs[j] + rise - 0.05:
                return False
            if two and slab_max is not None and slabs[j] + pulse_slab[j - k - 1] > slab_max:
                return False
        return True

    def turn_on(k: int) -> None:
        on[k] = True
        for j in range(k + 1, n + 1):
            airs[j] += pulse_air[j - k - 1]
            slabs[j] += pulse_slab[j - k - 1]

    for _ in range(4 * n):
        cold = next((j for j in range(1, n + 1) if airs[j] < low - 1e-6 and j not in given_up), None)
        if cold is None:
            break
        candidates = sorted(
            (inputs.price[k] / pulse_air[cold - k - 1], -k)
            for k in range(cold)
            if not on[k] and not banned[k] and pulse_air[cold - k - 1] > 1e-3
        )
        chosen = None
        for _score, neg_k in candidates:
            if fits(-neg_k):
                chosen = -neg_k
                break
            banned[-neg_k] = True  # temperatures only rise, so it will never fit later
        if chosen is None:
            given_up.add(cold)
        else:
            turn_on(chosen)

    # Free or paid-to-use electricity: store it while it fits.
    for k in range(n):
        if inputs.price[k] <= 0 and not on[k] and fits(k):
            turn_on(k)

    shortfall = min(given_up) if given_up else None
    return Plan(
        times=list(inputs.times),
        on=on,
        price=list(inputs.price),
        air=airs,
        slab=slabs if two else [None] * (n + 1),
        shortfall=inputs.times[shortfall - 1] + STEP if shortfall else None,
    )


def smart_decide(
    plan: Optional[Plan],
    air: Optional[float],
    slab: Optional[float],
    heater_on: bool,
    min_temp: float,
    max_temp: float,
    hysteresis: float,
    slab_max: Optional[float],
) -> Optional[Decision]:
    """Follow the plan, inside the hard limits. None: no plan, use the simple rules."""
    if air is None:
        return Decision(None, REASON_TEMPERATURE_UNAVAILABLE)
    if slab_max is not None and slab is not None and slab >= slab_max - (0 if heater_on else hysteresis):
        return Decision(False, REASON_FLOOR_MAX)
    if air < min_temp + (hysteresis if heater_on else 0.0):
        return Decision(True, REASON_BELOW_MIN)
    if air >= max_temp:
        return Decision(False, REASON_ABOVE_MAX)
    if plan is None:
        return None
    return Decision(plan.on[0], REASON_PLANNED_HEAT if plan.on[0] else REASON_PLANNED_OFF)
