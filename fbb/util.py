"""Small helpers shared across the package."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo


def safe_float(val: Any) -> float:
    """Convert Yahoo stat values to float, treating blanks and dashes as 0."""
    if val in (None, "", "-"):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def to_percent(value: float, digits: int = 1) -> float:
    return round(value * 100.0, digits)


def daterange(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def walk_values(obj: Any, key: str) -> Iterable[Any]:
    """Yield every value stored under `key` anywhere inside nested dicts/lists."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk_values(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_values(item, key)


def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def today_in_tz(tz_name: str) -> date:
    """The calendar date in the league's timezone (NBA days roll over on US Eastern time)."""
    return now_in_tz(tz_name).date()


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
