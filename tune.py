"""Auto-tune controls from observed data.

Given numeric samples for a field (captured by the service probe), pick a sensible
gauge range and color zones at percentiles, and guess a unit from the field name — so
gauges are configured from reality instead of guesswork.

Pure logic; the MCP tools feed it samples and apply the result to buffer controls.
"""

from __future__ import annotations

import math
import re
from typing import Optional

GREEN = "#34C759"
AMBER = "#FF9500"
RED = "#FF3B30"

# field-name hint -> (unit/format label)
_UNIT_HINTS = [
    (re.compile(r"temp|celsius|°c", re.I), "°C"),
    (re.compile(r"pct|percent|ratio|humidity|load|usage|level|battery", re.I), "%"),
    (re.compile(r"volt|voltage", re.I), "V"),
    (re.compile(r"amp|current", re.I), "A"),
    (re.compile(r"watt|power", re.I), "W"),
    (re.compile(r"rpm|speed", re.I), "rpm"),
    (re.compile(r"ms|latency|millis", re.I), "ms"),
    (re.compile(r"mph", re.I), "mph"),
    (re.compile(r"kmh|kph", re.I), "km/h"),
    (re.compile(r"bytes|mem|memory", re.I), "B"),
]

# field-name hint -> higher values are *better* (battery, signal) vs *worse* (temp, load)
_HIGHER_IS_BETTER = re.compile(r"battery|signal|charge|fuel|health|strength|uptime", re.I)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def _round_nice(v: float, up: bool) -> float:
    if v == 0:
        return 0.0
    sign = -1 if v < 0 else 1
    v = abs(v)
    mag = 10 ** math.floor(math.log10(v))
    n = v / mag
    if up:
        nice = next((m for m in (1, 2, 2.5, 5, 10) if n <= m), 10)
    else:
        nice = next((m for m in (10, 5, 2.5, 2, 1) if n >= m), 1)
    return sign * nice * mag


def suggest_range(samples: list[float]) -> tuple[float, float]:
    """A clean [min, max] bound that pads the observed extremes."""
    vals = sorted(float(s) for s in samples if isinstance(s, (int, float)) and not isinstance(s, bool))
    if not vals:
        return (0.0, 100.0)
    lo, hi = vals[0], vals[-1]
    if lo >= 0 and lo <= hi * 0.3:
        lo_bound = 0.0
    else:
        lo_bound = _round_nice(lo - (hi - lo) * 0.1, up=False)
    hi_bound = _round_nice(hi + (hi - lo) * 0.1 + (1e-9 if hi == lo else 0), up=True)
    if hi_bound <= lo_bound:
        hi_bound = lo_bound + (abs(lo_bound) or 1)
    return (lo_bound, hi_bound)


def suggest_segments(samples: list[float], higher_is_worse: bool = True) -> list[dict]:
    """Three color zones placed at the 60th/85th percentiles of the data."""
    vals = sorted(float(s) for s in samples if isinstance(s, (int, float)) and not isinstance(s, bool))
    if not vals:
        return []
    lo, hi = suggest_range(vals)
    p60 = _percentile(vals, 0.60)
    p85 = _percentile(vals, 0.85)
    if higher_is_worse:
        colors = [GREEN, AMBER, RED]
        limits = [round(p60, 3), round(p85, 3), hi]
    else:
        colors = [RED, AMBER, GREEN]
        p15 = _percentile(vals, 0.15)
        p40 = _percentile(vals, 0.40)
        limits = [round(p15, 3), round(p40, 3), hi]
    segs = []
    last = None
    for limit, color in zip(limits, colors):
        if last is not None and limit <= last:
            limit = last + (hi - lo) * 0.01 + 1e-6
        segs.append({"limit": limit, "color": color})
        last = limit
    segs[-1]["limit"] = hi
    return segs


def infer_unit(field_name: str) -> Optional[str]:
    for pattern, unit in _UNIT_HINTS:
        if pattern.search(field_name):
            return unit
    return None


def higher_is_worse(field_name: str) -> bool:
    return not bool(_HIGHER_IS_BETTER.search(field_name))


def tune_gauge(control: dict, samples: list[float],
               field_name: Optional[str] = None) -> dict:
    """Return a patch (min/max/segments) for a gauge from observed samples."""
    name = field_name or control.get("id", "")
    lo, hi = suggest_range(samples)
    patch: dict = {
        "min": lo,
        "max": hi,
        "segments": suggest_segments(samples, higher_is_worse=higher_is_worse(name)),
    }
    return patch
