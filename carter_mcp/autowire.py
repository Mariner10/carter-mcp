"""Auto-wire controls to probe-discovered traffic, and lint bindings against reality.

After the probe learns what the service emits, match each unbound display control to
the most likely field and fill in its sync. live_data_lint flags sync valuePaths that
never appear in observed traffic — the #1 cause of a silently blank control.
"""

from __future__ import annotations

import re
from typing import Optional

# Controls that consume data (vs. input controls that send it).
DISPLAY_TYPES = {"gauge", "sparkline", "progressRing", "label", "statusLight",
                 "map", "graph", "cardList", "list"}

# Controls whose whole point is firing an `action` — the "triggers" half of wiring.
# (Drag-pack boards carry their sends in config `events`, so they're not listed.)
INPUT_TYPES = {"button", "toggle", "slider", "stepper", "segmentedControl",
               "picker", "datePicker", "textInput", "colorPicker", "joystick"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def best_path(name: str, paths: list[str]) -> Optional[str]:
    """Pick the path whose leaf best matches a control's name (token overlap +
    exact/substring bonuses). None if nothing overlaps."""
    name_l = (name or "").lower()
    name_tok = _tokens(name)
    best, best_score = None, 0
    for p in paths:
        leaf = p.split(".")[-1]
        score = 0
        if name_l and name_l == leaf.lower():
            score = 100
        else:
            score = len(name_tok & (_tokens(p) | _tokens(leaf))) * 10
            if name_l and (name_l in p.lower() or leaf.lower() in name_l):
                score += 5
        if score > best_score:
            best, best_score = p, score
    return best if best_score > 0 else None


def _control_name(control: dict) -> str:
    return control.get("label") or control.get("id") or ""


def _walk(children, fn):
    """Visit every control, recursing through groups, container pages
    (carousel/flipCard/accordion `panels`), and canvas-hosted controls — the same
    nesting the phone's Layout Editor produces."""
    for ch in children or []:
        if not isinstance(ch, dict):
            continue
        fn(ch)
        if ch.get("type") == "group":
            _walk(ch.get("children"), fn)
        for panel in ch.get("panels") or []:
            if isinstance(panel, dict):
                _walk(panel.get("children"), fn)
        cfg = ch.get("canvasConfig")
        if isinstance(cfg, dict):
            for item in cfg.get("items") or []:
                if isinstance(item, dict) and isinstance(item.get("control"), dict):
                    fn(item["control"])


def autowire_layout(layout: dict, paths: list[str], event: str = "broadcast") -> list[tuple]:
    """Bind unbound display controls to the best-matching observed path. Mutates the
    layout; returns [(control_id, valuePath), ...] of what was wired."""
    wired: list[tuple] = []
    used: set[str] = set()

    def maybe_wire(ch: dict):
        if ch.get("type") in DISPLAY_TYPES and not ch.get("sync"):
            cand = best_path(_control_name(ch), [p for p in paths if p not in used])
            if not cand:
                cand = best_path(_control_name(ch), paths)
            if cand:
                ch["sync"] = [{"method": "meshsocket", "type": "listen",
                               "event": event, "valuePath": cand}]
                used.add(cand)
                wired.append((ch.get("id"), cand))

    for tab in layout.get("tabs", []):
        _walk(tab.get("children"), maybe_wire)
    return wired


def collect_sync_paths(layout: dict) -> list[tuple]:
    """[(control_id, event, valuePath), ...] for every listen sync in the layout."""
    out: list[tuple] = []

    def collect(ch: dict):
        for s in ch.get("sync") or []:
            if s.get("valuePath"):
                out.append((ch.get("id"), s.get("event") or "broadcast", s["valuePath"]))

    for tab in layout.get("tabs", []):
        _walk(tab.get("children"), collect)
    return out


def unwired_report(layout: dict) -> dict:
    """What still needs a human (or Claude) after autowire: display controls with no
    sync (values), and input controls with no action (triggers). The checklist the
    phone-editor handoff works down."""
    no_sync: list[str] = []
    no_action: list[str] = []

    def check(ch: dict):
        t = ch.get("type")
        if t in DISPLAY_TYPES and not ch.get("sync"):
            no_sync.append(ch.get("id") or "?")
        if t in INPUT_TYPES and not ch.get("action"):
            no_action.append(ch.get("id") or "?")

    for tab in layout.get("tabs", []):
        _walk(tab.get("children"), check)
    return {"values": no_sync, "triggers": no_action}


def live_data_lint(layout: dict, known_paths) -> list[dict]:
    """Warn for each sync valuePath not present in observed traffic."""
    known = set(known_paths)
    findings: list[dict] = []
    for cid, _event, path in collect_sync_paths(layout):
        if path not in known:
            findings.append({"id": cid, "valuePath": path,
                             "detail": f"'{path}' was never seen in observed traffic"})
    return findings
