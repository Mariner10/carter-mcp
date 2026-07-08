"""Service probe — sniff the user's live mesh traffic and infer its schema.

The MCP joins the user's channel and records emitted frames; this module aggregates
them into a menu of discovered events, paths, value types, ranges, and example values,
so wiring a control becomes a pick-list instead of a guess. Pure aggregation.
"""

from __future__ import annotations

from typing import Any


def flatten_paths(payload: Any, prefix: str = "") -> dict[str, dict]:
    """Map dotted path -> {"kind": "scalar"|"array", ...} for every leaf."""
    out: dict[str, dict] = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            out.update(flatten_paths(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(payload, list):
        if prefix:
            out[prefix] = {"kind": "array", "len": len(payload)}
    else:
        if prefix:
            out[prefix] = {"kind": "scalar", "value": payload}
    return out


def aggregate(frames: list[tuple[str, Any]]) -> dict[str, dict]:
    """frames: list of (event, payload). Returns {event: {path: stats}} where stats
    is {count, types, examples, min, max}."""
    events: dict[str, dict] = {}
    for event, payload in frames:
        ev = events.setdefault(event, {})
        for path, info in flatten_paths(payload).items():
            slot = ev.setdefault(path, {"count": 0, "types": set(),
                                        "examples": [], "min": None, "max": None})
            slot["count"] += 1
            if info["kind"] == "array":
                slot["types"].add("array")
                continue
            v = info["value"]
            slot["types"].add("bool" if isinstance(v, bool) else type(v).__name__)
            if len(slot["examples"]) < 3 and v not in slot["examples"]:
                slot["examples"].append(v)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                slot["min"] = v if slot["min"] is None else min(slot["min"], v)
                slot["max"] = v if slot["max"] is None else max(slot["max"], v)
    return events


def candidate_paths(events: dict[str, dict]) -> list[str]:
    """All distinct leaf paths observed (across events), sorted."""
    paths: set[str] = set()
    for ev in events.values():
        paths.update(ev.keys())
    return sorted(paths)


def format_discovery(events: dict[str, dict]) -> str:
    if not events:
        return "No traffic observed. Is the service emitting on this channel?"
    lines: list[str] = []
    for event in sorted(events):
        paths = events[event]
        lines.append(f"event '{event}' — {len(paths)} field(s):")
        for path in sorted(paths):
            s = paths[path]
            types = "/".join(sorted(s["types"])) or "?"
            bit = f"  - {path} ({types}, x{s['count']})"
            if s["min"] is not None:
                bit += f" range {s['min']}..{s['max']}"
            elif s["examples"]:
                ex = ", ".join(repr(e) for e in s["examples"])
                bit += f" e.g. {ex}"
            lines.append(bit)
    return "\n".join(lines)
