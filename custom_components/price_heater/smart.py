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
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .logic import (
    PRICE_LIST_ATTRIBUTES,
    REASON_ABOVE_MAX,
    REASON_ALLOWED_DIP,
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
MIN_HEAT = 0.1  # °C per hour: heating that moves the temperature less than this is noise
# Without a floor sensor, floor heating reaches the air with a delay; the fit tries these (hours).
HEAT_LAGS = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
SELECT_HOURS = 6  # the delay is chosen by how well it predicts this far ahead
OUTLIER_SPREAD = 4.0
# The heater must explain the room clearly: predictions with it must miss this much less
# than predictions that ignore it, or the heater entity is probably not the one heating.
MIN_HEATER_GAIN = 0.1  # windows missing by more than this many typical misses are left out
HISTORY_DAYS = 10

# Forecast caution: how much of the forecast sun the plan counts on far ahead
# (the next hour is trusted; it fades towards this share), until the forecast
# check has learned better.
DEFAULT_SUN_TRUST = 0.5
MIN_SUN_TRUST = 0.3
SUN_TRUST_HOURS = 3.0
# The forecast check compares each plan's prediction this far ahead with what happened.
CHECK_LEAD_STEPS = 12  # 3 h
CHECK_HOURS = 48
MAX_MARGIN = 1.0  # °C: the most the plan will aim above the minimum

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
    sun: List[float]  # what the plan counts on
    sun_forecast: Optional[List[float]] = None  # the forecast as is, for the forecast check


def sun_trust_at(hours_ahead: float, trust: float) -> float:
    """Share of the forecast sun the plan counts on: all of it now, ``trust`` far ahead."""
    return 1 - (1 - trust) * (1 - math.exp(-hours_ahead / SUN_TRUST_HOURS))


def build_inputs(
    now: datetime,
    slots: Sequence[PriceSlot],
    weather: Sequence[WeatherPoint],
    outdoor_now: Optional[float],
    latitude: float,
    longitude: float,
    sun_now: Optional[float] = None,
    sun_trust: float = 1.0,
) -> Optional[Inputs]:
    """Price, outdoor temperature and sun for every step of the plan.

    The forecast sun is counted at ``sun_trust`` far ahead (see ``sun_trust_at``),
    so a cloudier day than forecast doesn't leave the room short; sun measured
    now is trusted for the next hours.
    """
    start = step_start(now)
    known_end = max((end for _, end, _ in slots), default=start)
    end = max(known_end.astimezone(timezone.utc), start + timedelta(hours=PLAN_HOURS))

    forecast_now = _interpolate(weather, start, "temperature")
    bias = outdoor_now - forecast_now if outdoor_now is not None and forecast_now is not None else 0.0
    clear_now = clear_sky(solar_elevation(start, latitude, longitude))
    # A measured sun level now corrects the cloud forecast for the next hours.
    seen = min(1.2, sun_now / clear_now) if sun_now is not None and clear_now > 0.1 else None

    times, prices, outdoor, sun, forecast_sun = [], [], [], [], []
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
        forecast = cloud_factor(_interpolate(weather, t, "cloud"))
        factor = forecast * sun_trust_at(hours_ahead, sun_trust)
        if seen is not None:
            weight = math.exp(-hours_ahead / 1.5)
            factor = weight * seen + (1 - weight) * factor
        clear = clear_sky(solar_elevation(t + STEP / 2, latitude, longitude))
        times.append(t)
        prices.append(price)
        outdoor.append(temp)
        sun.append(clear * factor)
        forecast_sun.append(clear * forecast)
        t += STEP
    return Inputs(times, prices, outdoor, sun, forecast_sun)


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
    lag: float = 0.0  # hours: single store only, heating reaches the air this much later
    hidden: bool = False  # two stores, but the slab is estimated, not measured
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
                # ``slab`` carries the delayed heating share (0..1) when there is a lag.
                if self.lag > 0:
                    d_slab = (u - slab) / self.lag
                    drive = slab
                else:
                    d_slab = 0.0
                    drive = u
                d_air = self.heat * drive + self.air_out * (outdoor - air) + self.air_sun * sun + self.air_base
            air += h * d_air
            slab += h * d_slab
        return air, slab

    def without_inputs(self) -> "ThermalModel":
        """The same house with no outdoor, sun or base gains: the effect of heating alone."""
        return ThermalModel(
            heat=self.heat, air_out=self.air_out, air_sun=0.0, air_base=0.0,
            air_slab=self.air_slab, slab_air=self.slab_air, slab_base=0.0, two_node=self.two_node,
            lag=self.lag, hidden=self.hidden,
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


def fit_model(
    samples: Sequence[Sample], two_node: bool, report: Optional[dict] = None
) -> Tuple[Optional[ThermalModel], str]:
    """Fit the model on 1-hour windows of 15-minute samples.

    ``report``, if given, is filled with the coefficients each attempt found,
    to explain a fit that failed.
    """
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
                "k": k,
                "air": mean(lambda s: s.air),
                "u": mean(lambda s: s.on),
                "sun": mean(lambda s: s.sun),
                "out": mean(lambda s: s.outdoor - s.air),
                "slab": mean(lambda s: s.slab - s.air) if two_node else 0.0,
                "d_air": win[-1].air - win[0].air,
                "d_slab": (win[-1].slab - win[0].slab) if two_node else 0.0,
            }
        )
    hours = len(windows) / span
    if report is not None:
        report["hours"] = round(hours, 1)
    if hours < MIN_HOURS:
        return None, STATUS_LEARNING
    on_hours = sum(w["u"] for w in windows) / span
    if report is not None:
        report["heating_hours"] = round(on_hours, 1)
    if on_hours < MIN_SWITCH_HOURS or hours - on_hours < MIN_SWITCH_HOURS:
        return None, STATUS_NEEDS_VARIATION

    d_air = [w["d_air"] for w in windows]

    def robust(rows, positive, ys=d_air):
        """Least squares, then again without the windows that miss far more than usual
        (a sensor in the sun, an open door): returns (coef, typical miss, left out)."""
        coef = least_squares(rows, ys, positive=positive)
        if not coef:
            return None, None, 0
        res = [abs(sum(c * x for c, x in zip(coef, r)) - y) for r, y in zip(rows, ys)]
        typical = max(_quantile(res, 0.5), 0.01)
        keep = [i for i, e in enumerate(res) if e <= OUTLIER_SPREAD * typical]
        if len(keep) < len(rows):
            again = least_squares([rows[i] for i in keep], [ys[i] for i in keep], positive=positive)
            if again:
                coef = again
                res = [abs(sum(c * x for c, x in zip(coef, r)) - y) for r, y in zip(rows, ys)]
                typical = max(_quantile(res, 0.5), 0.01)
        return coef, typical, len(rows) - len(keep)

    def air_fit(first: Sequence[float], min_first: float, out: Optional[dict]):
        """Air equation; drops the constant if it hides the outdoor loss."""
        for constant in (True, False):
            rows = [[f, w["out"], w["sun"]] + ([1.0] if constant else []) for f, w in zip(first, windows)]
            coef, typical, dropped = robust(rows, (0, 1, 2))
            if coef and not constant:
                coef.append(0.0)
            if out is not None:
                out["air_" + ("with_constant" if constant else "without_constant")] = _rounded(coef)
                if dropped:
                    out["left_out"] = dropped
            if coef and coef[0] > min_first and coef[1] > 0:
                return coef, typical
        return None, None

    model = None
    if two_node:
        air, _typical = air_fit([w["slab"] for w in windows], 0.0, report)
        # No constant term: with the slab-air gap fairly steady it can't be
        # told apart from the coupling, so all slab losses go via the air.
        slab, _t, _d = robust([[w["u"], -w["slab"]] for w in windows], (0, 1), [w["d_slab"] for w in windows])
        if report is not None:
            report["slab"] = _rounded(slab)
        if air and slab and slab[0] > MIN_HEAT and slab[1] > 0:
            model = ThermalModel(
                heat=slab[0], air_out=air[1], air_sun=air[2], air_base=air[3],
                air_slab=air[0], slab_air=slab[1], two_node=True,
            )
    if model is None:
        # Without a floor sensor (or one that doesn't make sense) the concrete is
        # still there: estimate it. For a slab that settles in ``lag`` hours,
        #   slab = heat*lag*D + A
        # where D is the heating share and A the measured air, both smoothed over
        # ``lag`` hours. The air equation is then linear again:
        #   d_air = c0*D + c1*(A - air) + air_out*(outdoor - air) + air_sun*sun + base
        # with c1 = air_slab and c0 = air_slab*heat*lag. If c1 comes out zero the
        # heating simply reaches the air ``lag`` hours late. The lag is the one that
        # predicts best 6 hours ahead, which is what a plan needs.
        best = None
        tried = {}
        for lag in HEAT_LAGS:
            delayed = delayed_heating(samples, lag)
            smooth_air = smoothed_air(samples, lag)
            mean = lambda series, w: sum(series[w["k"] + i] for i in range(span)) / span  # noqa: E731
            if lag > 0:
                rows = [
                    [mean(delayed, w), mean(smooth_air, w) - w["air"], w["out"], w["sun"]]
                    for w in windows
                ]
            else:
                rows = [[w["u"], 0.0, w["out"], w["sun"]] for w in windows]
            attempt: dict = {}
            coef = _fit_air_rows(rows, d_air, robust, attempt)
            if not coef or coef[0] <= MIN_HEAT:
                tried["%g" % lag] = None
                continue
            c0, c1, out_rate, sun_rate, base = coef
            if lag > 0 and c1 > 1e-3:
                candidate = ThermalModel(
                    heat=c0 / (c1 * lag), slab_air=1.0 / lag, air_slab=c1, air_out=out_rate,
                    air_sun=sun_rate, air_base=base, two_node=True, hidden=True,
                )
                states = [candidate.heat * lag * d + a for d, a in zip(delayed, smooth_air)]
            else:
                candidate = ThermalModel(heat=c0, air_out=out_rate, air_sun=sun_rate, air_base=base, lag=lag)
                states = delayed if lag > 0 else None
            miss = _replay_rmse(candidate, samples, SELECT_HOURS * span, states=states)
            tried["%g" % lag] = round(miss, 3)
            if best is None or miss < best[0] - 1e-4:
                best = (miss, candidate, states, attempt, lag)
        # Baseline: the room explained by outdoor and sun alone.
        base_coef, _t, _d = robust([[w["out"], w["sun"], 1.0] for w in windows], (0, 1))
        baseline = float("inf")
        if base_coef:
            baseline = _replay_rmse(
                ThermalModel(heat=0.0, air_out=base_coef[0], air_sun=base_coef[1], air_base=base_coef[2]),
                samples, SELECT_HOURS * span,
            )
            tried["without_heater"] = round(baseline, 3)
            if best is not None and best[0] > (1 - MIN_HEATER_GAIN) * baseline:
                if report is not None:
                    report["heater_explains_little"] = True
                best = None
        if report is not None:
            report["lag_typical_miss_6h"] = tried
            if best:
                report.update(best[3])
                report["lag_hours"] = best[4]
        if best is None:
            if report is not None:
                report["failed"] = "two_node" if two_node else "one_node"
            return None, STATUS_NO_FIT
        _miss, model, states, _attempt, _lag = best
        model.hours = hours
        model.rmse = _replay_rmse(model, samples, span, [w["k"] for w in windows], states=states)
        return model, STATUS_READY

    model.hours = hours
    model.rmse = _replay_rmse(model, samples, span, [w["k"] for w in windows], states=[x.slab for x in samples])
    return model, STATUS_READY


def _fit_air_rows(rows, d_air, robust, out: Optional[dict]) -> Optional[List[float]]:
    """[heating, slab term, outdoor, sun] rows -> coefficients plus a constant;
    without the constant if it hides the outdoor loss."""
    for constant in (True, False):
        full = [r + [1.0] for r in rows] if constant else rows
        coef, _typical, dropped = robust(full, (0, 1, 2, 3))
        if coef and not constant:
            coef = coef + [0.0]
        if out is not None:
            out["air_" + ("with_constant" if constant else "without_constant")] = _rounded(coef)
            if dropped:
                out["left_out"] = dropped
        if coef and coef[0] > 0 and coef[2] > 0:
            return coef
    return None


def smoothed_air(samples: Sequence[Sample], lag: float) -> List[float]:
    """The measured air temperature smoothed over ``lag`` hours (what a slab would follow)."""
    out: List[float] = []
    state: Optional[float] = None
    keep = 1.0 if lag <= 0 else 1 - math.exp(-STEP_H / lag)
    for s in samples:
        if state is None:
            state = s.air
        out.append(state if state is not None else 0.0)
        if s.air is not None and state is not None:
            state += (s.air - state) * keep
    return out


def model_state(model: ThermalModel, samples: Sequence[Sample]) -> Optional[float]:
    """The hidden state at the end of the history: estimated slab, or delayed heating."""
    if model.two_node and not model.hidden:
        return None
    if model.hidden:
        lag = 1.0 / model.slab_air
        return model.heat * lag * delayed_heating(samples, lag)[-1] + smoothed_air(samples, lag)[-1]
    if model.lag > 0:
        return delayed_heating(samples, model.lag)[-1]
    return None


def advance_state(model: ThermalModel, state: Optional[float], air: Optional[float], on: float, hours: float) -> Optional[float]:
    """Move the hidden state forward with the measured air and the heater."""
    if state is None or hours <= 0:
        return state
    if model.hidden:
        if air is None:
            return state
        target = air + model.heat * on / model.slab_air
        return target + (state - target) * math.exp(-model.slab_air * hours)
    if model.lag > 0:
        return on + (state - on) * math.exp(-hours / model.lag)
    return state


def delayed_heating(samples: Sequence[Sample], lag: float) -> List[float]:
    """The heating share as the air feels it, ``lag`` hours later (first-order delay)."""
    out = []
    state = 0.0
    keep = 1.0 if lag <= 0 else 1 - math.exp(-STEP_H / lag)
    for s in samples:
        out.append(state if lag > 0 else (s.on or 0.0))
        if lag > 0:
            state += ((s.on or 0.0) - state) * keep
    return out


def _replay_rmse(
    model: ThermalModel,
    samples: Sequence[Sample],
    steps: int,
    starts: Optional[Sequence[int]] = None,
    states: Optional[Sequence[Optional[float]]] = None,
) -> float:
    """Typical miss of the air predicted ``steps`` ahead from the measured start,
    with the measured heating, outdoor and sun on the way; outliers left out."""
    if starts is None:
        starts = range(0, len(samples) - steps, STEPS_PER_HOUR)
    misses = []
    for k in starts:
        if k + steps >= len(samples):
            continue
        path = samples[k : k + steps + 1]
        if any(p.air is None for p in (path[0], path[-1])) or any(
            p.outdoor is None or p.on is None for p in path[:-1]
        ):
            continue
        slab = states[k] if states is not None else 0.0
        if slab is None:
            continue
        air = path[0].air
        for p in path[:-1]:
            air, slab = model.step(air, slab, p.outdoor, p.sun, p.on)
        misses.append((air - path[-1].air) ** 2)
    if not misses:
        return float("inf")
    cut = _quantile(misses, 0.5) * OUTLIER_SPREAD ** 2 + 1e-6
    kept = [m for m in misses if m <= cut]
    return math.sqrt(sum(kept) / len(kept))


def _rounded(coef: Optional[Sequence[float]]) -> Optional[List[float]]:
    return [round(c, 4) for c in coef] if coef else None


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
    dip: Optional[datetime] = None  # first time it lets the air dip rather than heat at a high price

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
            "dip_from": _iso(self.dip),
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
    hard_low: Optional[float] = None,
) -> Plan:
    """Cheapest on/off steps that keep the air at or above ``low``.

    With ``hard_low`` below ``low``, the air may dip between the two rather
    than be held up by heating at an above-median price; below ``hard_low`` it
    heats at any price.

    The model is linear, so heating one step adds a fixed response to the
    temperatures after it. Repeatedly take the first moment the air would be
    too cold and turn on the step before it with the lowest price per degree
    it adds then, unless that pushes the air over ``high`` or the floor over
    ``slab_max``.
    """
    n = len(inputs.times)
    two = model.two_node and slab is not None
    start_slab = slab if slab is not None and (two or model.lag > 0) else 0.0
    airs, slabs = _simulate(model, inputs, air, start_slab, [0.0] * n)

    # Response to one step of heating, j steps after it ends.
    pulse = model.without_inputs()
    zero = Inputs(inputs.times, inputs.price, [0.0] * n, [0.0] * n)
    pulse_air, pulse_slab = _simulate(pulse, zero, 0.0, 0.0, [1.0] + [0.0] * (n - 1))
    pulse_air, pulse_slab = pulse_air[1:], pulse_slab[1:]

    on = [False] * n
    banned = [False] * n
    given_up: set = set()
    tolerated: set = set()
    floor_low = low if hard_low is None else min(low, hard_low)
    ordered = sorted(inputs.price)
    affordable = ordered[(n - 1) // 2] if n else 0.0  # median price of the plan

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
        cold = next(
            (j for j in range(1, n + 1)
             if airs[j] < low - 1e-6 and j not in given_up and j not in tolerated),
            None,
        )
        if cold is None:
            break
        must = airs[cold] < floor_low - 1e-6
        candidates = sorted(
            (inputs.price[k] / pulse_air[cold - k - 1], -k)
            for k in range(cold)
            if not on[k] and not banned[k] and pulse_air[cold - k - 1] > 1e-3
        )
        chosen = None
        for _score, neg_k in candidates:
            k = -neg_k
            if not must and inputs.price[k] > affordable:
                continue
            if fits(k):
                chosen = k
                break
            banned[k] = True  # temperatures only rise, so it will never fit later
        if chosen is not None:
            turn_on(chosen)
        elif must:
            given_up.add(cold)
        else:
            tolerated.add(cold)

    # Free or paid-to-use electricity: store it while it fits.
    for k in range(n):
        if inputs.price[k] <= 0 and not on[k] and fits(k):
            turn_on(k)

    shortfall = min(given_up) if given_up else None
    dips = [j for j in tolerated if airs[j] < low - 1e-6]
    return Plan(
        times=list(inputs.times),
        on=on,
        price=list(inputs.price),
        air=airs,
        slab=slabs if two and not model.hidden else [None] * (n + 1),  # an estimated slab isn't shown
        shortfall=inputs.times[shortfall - 1] + STEP if shortfall else None,
        dip=inputs.times[min(dips) - 1] + STEP if dips else None,
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
    allowed_dip: float = 0.0,
) -> Optional[Decision]:
    """Follow the plan, inside the hard limits. None: no plan, use the simple rules.

    With a plan the air may dip ``allowed_dip`` below the minimum while the plan
    waits for a cheaper price; below that it always heats.
    """
    if air is None:
        return Decision(None, REASON_TEMPERATURE_UNAVAILABLE)
    if slab_max is not None and slab is not None and slab >= slab_max - (0 if heater_on else hysteresis):
        return Decision(False, REASON_FLOOR_MAX)
    keep = hysteresis if heater_on else 0.0
    dip = max(0.0, allowed_dip) if plan is not None else 0.0
    if air < min_temp - dip + keep:
        return Decision(True, REASON_BELOW_MIN)
    if air >= max_temp:
        return Decision(False, REASON_ABOVE_MAX)
    if plan is None:
        return None
    if plan.on[0]:
        return Decision(True, REASON_PLANNED_HEAT)
    return Decision(False, REASON_ALLOWED_DIP if air < min_temp else REASON_PLANNED_OFF)


class ForecastCheck:
    """Compares what each plan predicted 3 hours ahead with what happened.

    When the plan is often too optimistic it aims higher above the minimum;
    when the forecast sun often fails to show, the plan counts on less of it.
    Kept in memory: it starts over after a restart.
    """

    def __init__(self) -> None:
        self.pending: dict = {}  # target time -> (predicted air, forecast sun)
        self.errors: deque = deque(maxlen=CHECK_HOURS * STEPS_PER_HOUR)  # predicted - actual
        self.sun_ratios: deque = deque(maxlen=CHECK_HOURS * STEPS_PER_HOUR)  # actual / forecast

    def record(self, plan: Plan, inputs: Inputs) -> None:
        k = CHECK_LEAD_STEPS
        if len(plan.times) <= k or not inputs.sun_forecast:
            return
        self.pending.setdefault(plan.times[k], (plan.air[k], inputs.sun_forecast[k]))

    def observe(self, now: datetime, air: Optional[float], sun: Optional[float]) -> None:
        for t in sorted(self.pending):
            if t > now:
                break
            predicted_air, forecast_sun = self.pending.pop(t)
            if now - t >= STEP:  # missed (Home Assistant was busy or stopped)
                continue
            if air is not None:
                self.errors.append(predicted_air - air)
            if sun is not None and forecast_sun >= 0.1:
                self.sun_ratios.append(sun / forecast_sun)

    def margin(self, base: float) -> float:
        """How far above the minimum to aim: covers 4 in 5 of the recent misses."""
        if len(self.errors) < 16:
            return base
        return min(MAX_MARGIN, max(base, _quantile(self.errors, 0.8)))

    def sun_trust(self) -> float:
        """Share of the forecast sun that shows up on a poor day (1 in 4)."""
        if len(self.sun_ratios) < 8:
            return DEFAULT_SUN_TRUST
        return min(1.0, max(MIN_SUN_TRUST, _quantile(self.sun_ratios, 0.25)))

    def as_dict(self, base: float) -> dict:
        return {
            "checked": len(self.errors),
            "mean_error": round(sum(self.errors) / len(self.errors), 2) if self.errors else None,
            "margin": round(self.margin(base), 2),
            "sun_checked": len(self.sun_ratios),
            "sun_trust": round(self.sun_trust(), 2),
        }


def _quantile(values, q: float) -> float:
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]
