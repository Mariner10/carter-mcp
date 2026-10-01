"""Judge a layout's sync bindings against real frames with the app's own rules.

`carterkit.wire` (carterkit with the wire module) is the reference evaluator of the
app's sync `filter` / `valuePath` matching. When the installed kit has it, the
lint, `set_control_value` and `simulate` use it and say *where* a binding fails
(FILTER_MISS with near-misses, PATH_MISSING at a segment, TYPE_UNRENDERABLE).
Older kits (the MCP's floor is carterkit>=0.5.0) have no `wire`; then the lint
falls back to `autowire.live_data_lint` (paths never seen in traffic) and frames
are built locally. Pure functions; the socket sends stay in tools/wiring.py.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from carter_mcp import autowire
from carter_mcp import simulate as simulate_lib

try:
    from carterkit import wire
except ImportError:  # kit predates carterkit.wire
    wire = None

# Controls that render a single bool / number / string. Others (maps, charts,
# lists, logs, sparklines fed a series) also take objects / arrays, so a
# container value is not a type error for them.
SCALAR_TYPES = {"gauge", "progressRing", "label", "statusLight", "symbol",
                "button", "toggle", "slider", "stepper", "segmentedControl",
                "picker", "datePicker", "textInput", "colorPicker"}


def have_wire() -> bool:
    return wire is not None


def listen_syncs(layout: dict) -> list[tuple]:
    """[(control_id, control_type, sync), ...] for every listen sync (a sync with
    no `type` counts as listen) anywhere in the layout."""
    out: list[tuple] = []

    def collect(ch: dict):
        for s in ch.get("sync") or []:
            if isinstance(s, dict) and s.get("type", "listen") == "listen":
                out.append((ch.get("id"), ch.get("type"), s))

    for tab in layout.get("tabs", []):
        autowire._walk(tab.get("children"), collect)
    return out


def _listen_sync(control: dict) -> Optional[dict]:
    """The sync control_binding picks: the first listen sync with a valuePath."""
    for s in control.get("sync") or []:
        if isinstance(s, dict) and s.get("type") == "listen" and s.get("valuePath"):
            return s
    return None


def frame_for_control(control: dict, value: Any) -> Optional[tuple[str, dict]]:
    """(event, frame) that drives `control` to `value` — the sync's filter keys
    plus the value at its valuePath, so filtered controls actually match.
    None when the control has no listen sync with a valuePath."""
    s = _listen_sync(control)
    if s is None:
        return None
    event = s.get("event") or "broadcast"
    if wire is not None:
        return event, wire.frame_for(s, value)
    frame = copy.deepcopy(s["filter"]) if isinstance(s.get("filter"), dict) else {}
    path = ".".join(p for p in str(s["valuePath"]).split(".") if p)
    frame.update(simulate_lib.build_frame({path: value}) if path else {})
    return event, frame


def _short(v: Any) -> str:
    return "absent" if wire is not None and v is wire.ABSENT else json.dumps(v)[:60]


def describe(outcome) -> tuple[str, str]:
    """(code, detail) for a wire.Outcome."""
    if outcome.kind == wire.DELIVERED:
        return "DELIVERED", f"value {json.dumps(outcome.value)[:60]}"
    if outcome.kind == wire.FILTER_MISS:
        parts = [f"{m.key}: want {json.dumps(m.expected)[:40]}, got {_short(m.actual)}"
                 + (" (near miss)" if m.near else "") for m in outcome.filter_misses]
        return "FILTER_MISS", "; ".join(parts)
    if outcome.kind == wire.PATH_MISS:
        pm = outcome.path_miss
        return "PATH_MISSING", (f"segment '{pm.segment}' (#{pm.index + 1}): "
                                f"{pm.reason.replace('_', ' ')}")
    return "TYPE_UNRENDERABLE", f"value is {outcome.type_miss.kind}, not bool/number/string"


def _evaluate(frame: Any, ctype: Optional[str], sync: dict):
    return wire.evaluate(frame, sync, require_scalar=ctype in SCALAR_TYPES)


def _sync_finding(cid, ctype, sync: dict, frames: list) -> Optional[dict]:
    outcomes = [_evaluate(f, ctype, sync) for f in frames]
    if any(o.delivered for o in outcomes):
        return None
    base = {"id": cid, "valuePath": sync.get("valuePath"), "near": False}
    # A frame got past the filter: the path or the value type is the problem.
    for o in outcomes:
        if o.kind in (wire.PATH_MISS, wire.TYPE_MISS):
            code, detail = describe(o)
            return {**base, "code": code, "detail": detail}
    near = [o for o in outcomes if o.near]
    if near:
        code, detail = describe(near[0])
        return {**base, "code": code, "near": True,
                "detail": f"{detail} — likely meant for this control"}
    filt = sync.get("filter")
    return {**base, "code": "FILTER_MISS",
            "detail": f"no observed frame matched filter {json.dumps(filt)[:80]}"}


def lint_traffic(layout: dict, frames: list, event: str = "broadcast",
                 known_paths=None) -> list[dict]:
    """One finding per listen sync on `event` that no observed frame would deliver
    to. Each finding: {id, code, detail, valuePath, near}. Codes: FILTER_MISS,
    PATH_MISSING, TYPE_UNRENDERABLE; without carterkit.wire, PATH_NOT_SEEN (the
    old seen-paths check against `known_paths`)."""
    if wire is None:
        return [{**f, "code": "PATH_NOT_SEEN", "near": False}
                for f in autowire.live_data_lint(layout, known_paths or [])]
    findings: list[dict] = []
    for cid, ctype, sync in listen_syncs(layout):
        if (sync.get("event") or "broadcast") != event:
            continue
        f = _sync_finding(cid, ctype, sync, frames)
        if f:
            findings.append(f)
    return findings


def format_findings(findings: list[dict]) -> str:
    if not findings:
        return "✓ Every listen sync would receive data from observed traffic."
    if all(f["code"] == "PATH_NOT_SEEN" for f in findings):
        head = "⚠ Paths not seen in traffic"
    else:
        head = "⚠ Bindings that observed traffic would not reach"
    return head + ":\n" + "\n".join(
        f"  - {f['id']}: {f['code']} {f['detail']}" for f in findings)


def simulate_report(layout: dict, payload: Any, event: str = "broadcast") -> list[str]:
    """Per listen sync on `event`, what the emitted payload does to that control.
    Empty when there is no layout or no carterkit.wire."""
    if wire is None or not layout:
        return []
    lines = []
    for cid, ctype, sync in listen_syncs(layout):
        if (sync.get("event") or "broadcast") != event:
            continue
        code, detail = describe(_evaluate(payload, ctype, sync))
        lines.append(f"  - {cid}: {code} {detail}")
    return lines
