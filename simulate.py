"""Puppeteer / simulate — let the MCP act as the user's service.

Builds the data frames a device's sync listeners expect, so gauges move, sparklines
fill, and maps animate on demand — for live demos or to drive a control while the
real backend doesn't exist yet. Pure frame construction; the socket send is in server.
"""

from __future__ import annotations

from typing import Optional


def build_frame(values: dict) -> dict:
    """Merge {dot.path: value} entries into one nested frame.
    {'a.b': 1, 'a.c': 2, 'x': 3} -> {'a': {'b': 1, 'c': 2}, 'x': 3}."""
    frame: dict = {}
    for path, value in values.items():
        parts = str(path).split(".")
        cur = frame
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value
    return frame


def control_binding(control: dict) -> Optional[tuple[str, str]]:
    """(event, valuePath) from a control's first listen sync, or None if unbound."""
    for s in control.get("sync") or []:
        if s.get("type") == "listen" and s.get("valuePath"):
            return (s.get("event") or "broadcast", s["valuePath"])
    return None
