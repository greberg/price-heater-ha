"""Pure decision logic for Price Heater.

This module has no Home Assistant imports so it can be unit tested on its own.

Rules, in priority order:
1. Temperature unknown            -> do nothing (keep the heater as it is).
2. Temperature below ``min_temp`` -> heater ON regardless of price
   (once on, it keeps heating until ``min_temp + hysteresis``).
3. Temperature at/above ``max_temp`` -> heater OFF regardless of price.
4. In between: heat only while electricity is cheap, i.e. the current price is
   at/below ``price_threshold`` OR the current slot is among today's cheapest
   ``cheap_hours`` hours. When it is cheap and the heater is off, it only
   starts if the temperature is below ``max_temp - hysteresis``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, List, Mapping, Optional, Sequence, Tuple

REASON_TEMPERATURE_UNAVAILABLE = "temperature_unavailable"
REASON_BELOW_MIN = "below_min"
REASON_ABOVE_MAX = "above_max"
REASON_CHEAP = "cheap_price"
REASON_NEAR_MAX = "near_max"
REASON_EXPENSIVE = "expensive_price"
REASON_PRICE_UNAVAILABLE = "price_unavailable"

ALL_REASONS = [
    REASON_TEMPERATURE_UNAVAILABLE,
    REASON_BELOW_MIN,
    REASON_ABOVE_MAX,
    REASON_CHEAP,
    REASON_NEAR_MAX,
    REASON_EXPENSIVE,
    REASON_PRICE_UNAVAILABLE,
]

# Reasons that protect comfort/safety and may bypass the minimum cycle time.
OVERRIDE_REASONS = {REASON_BELOW_MIN, REASON_ABOVE_MAX}

# Attribute names used by popular price integrations for today's price list:
#   raw_today    - Nordpool (HACS), Energi Data Service
#   today        - Nordpool (HACS)
#   prices_today - ENTSO-e
PRICE_LIST_ATTRIBUTES = ("raw_today", "today", "prices_today")
VALUE_KEYS = ("value", "price", "total", "price_per_kwh")
START_KEYS = ("start", "startsAt", "time", "hour")

Slot = Tuple[Optional[datetime], Optional[float]]


@dataclass
class PriceInfo:
    """Result of analysing the price sensor."""

    current_price: Optional[float] = None
    is_cheap: Optional[bool] = None
    in_cheapest_hours: Optional[bool] = None
    below_threshold: Optional[bool] = None
    cheap_limit: Optional[float] = None
    rank: Optional[int] = None
    slots_today: int = 0


@dataclass
class Decision:
    """What the heater should do and why. ``turn_on`` None means leave it."""

    turn_on: Optional[bool]
    reason: str


def to_float(value: Any) -> Optional[float]:
    """Convert to float, returning None for anything unusable."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _to_datetime(value: Any, tz: Optional[tzinfo]) -> Optional[datetime]:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None and tz is not None:
        result = result.replace(tzinfo=tz)
    return result


def extract_price_slots(attributes: Mapping[str, Any], now: datetime) -> List[Slot]:
    """Read today's price list from known sensor attributes."""
    for key in PRICE_LIST_ATTRIBUTES:
        raw = attributes.get(key)
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        slots: List[Slot] = []
        for item in raw:
            if isinstance(item, Mapping):
                value = next(
                    (to_float(item[k]) for k in VALUE_KEYS if k in item), None
                )
                start = next(
                    (_to_datetime(item[k], now.tzinfo) for k in START_KEYS if k in item),
                    None,
                )
            else:
                value, start = to_float(item), None
            slots.append((start, value))
        return slots
    return []


def _has_starts(slots: Sequence[Slot]) -> bool:
    return all(start is not None for start, _ in slots)


def slot_duration(slots: Sequence[Slot]) -> timedelta:
    """Length of one price slot (1 h for hourly prices, 15 min for quarterly)."""
    if _has_starts(slots) and len(slots) >= 2:
        delta = slots[1][0] - slots[0][0]  # type: ignore[operator]
        if delta > timedelta(0):
            return delta
    return timedelta(minutes=1440 / len(slots))


def current_slot_index(slots: Sequence[Slot], now: datetime) -> Optional[int]:
    """Index of the slot covering ``now``, or None if the list is not for today."""
    if not slots:
        return None
    if _has_starts(slots):
        duration = slot_duration(slots)
        index = None
        for i, (start, _) in enumerate(slots):
            if start <= now:  # type: ignore[operator]
                index = i
        if index is None or now >= slots[index][0] + duration:  # type: ignore[operator]
            return None
        return index
    minutes = now.hour * 60 + now.minute
    return min(len(slots) - 1, minutes * len(slots) // 1440)


def cheap_limit(slots: Sequence[Slot], cheap_hours: float) -> Optional[float]:
    """Highest price that still counts as one of the ``cheap_hours`` cheapest."""
    valid = sorted(v for _, v in slots if v is not None)
    if not valid or cheap_hours <= 0:
        return None
    hours_per_slot = slot_duration(slots).total_seconds() / 3600
    count = min(len(valid), max(1, round(cheap_hours / hours_per_slot)))
    return valid[count - 1]


def price_schedule(
    attributes: Mapping[str, Any],
    now: datetime,
    cheap_hours: float,
    price_threshold: Optional[float],
) -> List[dict]:
    """Today's slots as ``{"start", "price", "cheap"}`` for the cards."""
    slots = extract_price_slots(attributes, now)
    if not slots:
        return []
    limit = cheap_limit(slots, cheap_hours)
    duration = slot_duration(slots)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    timed = _has_starts(slots)
    result = []
    for i, (start, value) in enumerate(slots):
        begin = start if timed else midnight + duration * i
        cheap = None
        if value is not None:
            cheap = (limit is not None and value <= limit) or (
                price_threshold is not None and value <= price_threshold
            )
        result.append({"start": begin.isoformat(), "price": value, "cheap": cheap})
    return result


def analyze_prices(
    current_price: Optional[float],
    attributes: Mapping[str, Any],
    now: datetime,
    cheap_hours: float,
    price_threshold: Optional[float],
) -> PriceInfo:
    """Work out whether electricity is cheap right now."""
    info = PriceInfo(current_price=current_price)

    slots = extract_price_slots(attributes, now)
    valid = sorted(v for _, v in slots if v is not None)
    info.slots_today = len(valid)
    index = current_slot_index(slots, now) if slots else None

    if index is not None and slots[index][1] is not None and valid:
        slot_price = slots[index][1]
        if info.current_price is None:
            info.current_price = slot_price
        info.rank = 1 + sum(1 for v in valid if v < slot_price)
        if cheap_hours > 0:
            info.cheap_limit = cheap_limit(slots, cheap_hours)
            info.in_cheapest_hours = slot_price <= info.cheap_limit
        else:
            info.in_cheapest_hours = False

    if price_threshold is not None and info.current_price is not None:
        info.below_threshold = info.current_price <= price_threshold

    if info.in_cheapest_hours is None and info.below_threshold is None:
        info.is_cheap = None
    else:
        info.is_cheap = bool(info.in_cheapest_hours) or bool(info.below_threshold)
    return info


def decide(
    temperature: Optional[float],
    is_cheap: Optional[bool],
    heater_on: bool,
    min_temp: float,
    max_temp: float,
    hysteresis: float,
) -> Decision:
    """Decide whether the heater should be on."""
    if temperature is None:
        return Decision(None, REASON_TEMPERATURE_UNAVAILABLE)

    low_limit = min_temp + (hysteresis if heater_on else 0.0)
    if temperature < low_limit:
        return Decision(True, REASON_BELOW_MIN)

    if temperature >= max_temp:
        return Decision(False, REASON_ABOVE_MAX)

    if is_cheap is None:
        return Decision(False, REASON_PRICE_UNAVAILABLE)

    if not is_cheap:
        return Decision(False, REASON_EXPENSIVE)

    if heater_on or temperature < max_temp - hysteresis:
        return Decision(True, REASON_CHEAP)
    return Decision(False, REASON_NEAR_MAX)
