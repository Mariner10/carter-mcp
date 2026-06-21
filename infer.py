"""Schema-to-UI inference — turn a real JSON payload into a wired layout.

Given a sample payload from the user's service (pasted, or captured by the service
probe), infer a sensible control per field and bind each one to the right nested
`valuePath`, so a first-draft dashboard appears from real data. Pure logic; placement
is delegated to LayoutBuffer (auto grid packing).

Mapping:
    bool                      -> toggle
    number in [0,1]           -> progressRing
    number otherwise          -> gauge (min 0, max a nice round bound)
    status-ish string         -> statusLight
    other string              -> label
    object with lat/lng       -> map
    list of numbers           -> sparkline
    list of objects           -> cardList
    nested object             -> recurse (dotted valuePath)
"""

from __future__ import annotations

import math
import re
from typing import Callable, Optional

from buffer import LayoutBuffer

STATUS_WORDS = {
    "online", "offline", "idle", "error", "ok", "okay", "connected",
    "disconnected", "active", "inactive", "up", "down", "ready", "busy",
    "running", "stopped", "healthy", "unhealthy", "warning", "alarm",
}
_LAT_KEYS = {"lat", "latitude"}
_LNG_KEYS = {"lng", "lon", "long", "longitude"}


def humanize(segment: str) -> str:
    """'cpu_temp' / 'cpuTemp' / 'cpu.temp' -> 'Cpu Temp'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", segment)
    s = s.replace("_", " ").replace("-", " ").replace(".", " ")
    return " ".join(w.capitalize() for w in s.split())


def sanitize_id(path: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()
    return s or "field"


def nice_max(v: float) -> float:
    """A clean upper bound for a gauge given an observed value."""
    if v <= 1:
        return 1
    if v <= 100:
        return 100
    mag = 10 ** math.floor(math.log10(v))
    for mult in (1, 2, 5, 10):
        if v <= mult * mag:
            return mult * mag
    return 10 * mag


def _is_geo(d: dict) -> bool:
    keys = {k.lower() for k in d.keys()}
    return bool(keys & _LAT_KEYS) and bool(keys & _LNG_KEYS)


def _scalar_control(value) -> dict:
    if isinstance(value, bool):
        return {"type": "toggle"}
    if isinstance(value, (int, float)):
        if 0 <= value <= 1:
            return {"type": "progressRing", "progressStyle": "ring"}
        return {"type": "gauge", "min": 0, "max": nice_max(float(value))}
    if isinstance(value, str):
        if value.strip().lower() in STATUS_WORDS:
            return {"type": "statusLight"}
        return {"type": "label"}
    return {"type": "label"}


def _walk(value, path: str, out: list[dict]):
    if isinstance(value, dict):
        if _is_geo(value):
            out.append({"type": "map", "_path": path})
            return
        for k, v in value.items():
            _walk(v, f"{path}.{k}" if path else k, out)
        return
    if isinstance(value, list):
        if value and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
            out.append({"type": "sparkline", "_path": path})
        elif value and all(isinstance(x, dict) for x in value):
            out.append({"type": "cardList", "_path": path})
        else:
            out.append({"type": "label", "_path": path})
        return
    ctrl = _scalar_control(value)
    ctrl["_path"] = path
    out.append(ctrl)


def infer_controls(payload: dict, event: str = "telemetry") -> list[dict]:
    """List of control dicts (no position) inferred from a payload, each wired with a
    sync binding to its nested valuePath."""
    raw: list[dict] = []
    _walk(payload, "", raw)
    controls: list[dict] = []
    for item in raw:
        path = item.pop("_path")
        if not path:
            continue
        control = dict(item)
        control["id"] = sanitize_id(path)
        control["label"] = humanize(path.split(".")[-1])
        control["sync"] = [{
            "method": "meshsocket", "type": "listen",
            "event": event, "valuePath": path,
        }]
        controls.append(control)
    return controls


def build_layout(payload: dict, name: str = "Inferred", event: str = "telemetry",
                 columns: int = 4, rows: int = 8,
                 default_span_fn: Optional[Callable[[str], Optional[list]]] = None) -> dict:
    """Build a full single-tab layout from a payload, auto-placing each inferred control."""
    buf = LayoutBuffer.blank(name=name, columns=columns, rows=rows)
    for control in infer_controls(payload, event=event):
        span = default_span_fn(control["type"]) if default_span_fn else None
        try:
            buf.add_control(control, default_span=span)
        except Exception:
            # Out of room — grow rows so nothing is silently dropped.
            buf.tabs[0]["grid"]["rows"] += 2
            rows += 2
            buf.add_control(control, default_span=span)
    return buf.layout
