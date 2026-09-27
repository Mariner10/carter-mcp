"""Forward-compat preview: what an older phone will do with a layout (carter-5sn.6).

A newer app reports the features it speaks in its `get-device-info` reply
(`features`, carter-5sn.4). Names: `control.<type>`, `sync.<method>`,
`action.<method>`, `layout.<key>`; each may carry a version suffix `name@N`
(absent = 1), matching the app's DeviceCapabilities grammar. Given that list,
these pure helpers say which controls the phone will draw as an "Update CAR-TER"
placeholder or as their `fallback`, which bindings it ignores, and whether the
layout's `requires` block triggers the update banner.

A device reply WITHOUT `features` (CAR-TER 1.2.4 and earlier) is "unknown":
every helper returns an empty result and callers fall back to the old
version/protocol comparison.
"""

from __future__ import annotations

from typing import Optional

#: Fallback hops the app follows before a node counts as unsupported
#: (LayoutForwardCompat.maxFallbackDepth in the app; carterkit MAX_FALLBACK_DEPTH).
MAX_FALLBACK_DEPTH = 4


def ver_tuple(v) -> tuple:
    """Dotted version → int tuple ("1.3" → (1, 3)); same rule as sources._ver_tuple."""
    if not v:
        return ()
    out = []
    for part in str(v).split("+")[0].split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def _cmp_versions(a: tuple, b: tuple) -> int:
    """Compare version tuples with zero padding (1.3 == 1.3.0)."""
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def parse_feature(feature: str) -> tuple[str, int]:
    """`name` / `name@N` → (name, N). A bad suffix keeps the whole string at v1."""
    s = feature.strip()
    name, sep, ver = s.rpartition("@")
    if sep and ver.isdigit() and int(ver) > 0:
        return name, int(ver)
    return s, 1


def feature_map(features) -> dict[str, int]:
    """The device's feature list → {name: highest version}. Non-strings ignored."""
    out: dict[str, int] = {}
    for f in features or ():
        if isinstance(f, str) and f.strip():
            name, ver = parse_feature(f)
            out[name] = max(out.get(name, 0), ver)
    return out


def device_features(device_info) -> Optional[dict[str, int]]:
    """The device's features as a map, or None when unknown (absent/empty/not a list)."""
    if not isinstance(device_info, dict):
        return None
    feats = device_info.get("features")
    if not isinstance(feats, list):
        return None
    have = feature_map(feats)
    return have or None


def supports(feature: str, have: dict[str, int]) -> bool:
    name, ver = parse_feature(feature)
    return have.get(name, 0) >= ver


def walk_controls(children, fn, where: str = "", in_canvas: bool = False) -> None:
    """Call fn(control, where, in_canvas) for every placed control: tab/group
    children (groups recurse and are not yielded), container `panels`,
    `longPressGroup`, and canvas items. Same nesting as carterkit/_walk_controls."""
    if not isinstance(children, list):
        return
    for i, ch in enumerate(children):
        if not isinstance(ch, dict):
            continue
        spot = f"{where}/{ch.get('id') or ch.get('type') or i}"
        if ch.get("type") == "group":
            walk_controls(ch.get("children"), fn, spot)
            continue
        fn(ch, spot, in_canvas)
        panels = ch.get("panels")
        for p, panel in enumerate(panels if isinstance(panels, list) else []):
            if isinstance(panel, dict):
                walk_controls(panel.get("children"), fn, f"{spot}/panels[{p}]")
        lpg = ch.get("longPressGroup")
        if isinstance(lpg, dict):
            walk_controls(lpg.get("children"), fn, f"{spot}/longPressGroup")
        canvas = ch.get("canvasConfig")
        items = canvas.get("items") if isinstance(canvas, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("control"), dict):
                walk_controls([item["control"]], fn, f"{spot}/canvas", in_canvas=True)


def usable_fallback(node: dict, have: dict[str, int], in_canvas: bool = False) -> Optional[str]:
    """The type the app draws from `node`'s `fallback` chain, or None (placeholder)."""
    current = node
    for _ in range(MAX_FALLBACK_DEPTH):
        nxt = current.get("fallback")
        if not isinstance(nxt, dict):
            return None
        t = nxt.get("type")
        if t == "group":
            return None if in_canvas else "group"   # a canvas card hosts one control
        if isinstance(t, str) and supports(f"control.{t}", have):
            return t
        current = nxt
    return None


def _methods(binding) -> list[str]:
    """`method` values of a sync/action binding (a dict or a list of dicts)."""
    items = binding if isinstance(binding, list) else [binding]
    return [b["method"] for b in items
            if isinstance(b, dict) and isinstance(b.get("method"), str) and b["method"]]


def _label(ch: dict) -> str:
    cid = ch.get("id")
    ctype = ch.get("type", "?")
    return f"'{cid}' ({ctype})" if cid else f"a {ctype} control"


def layout_forward_compat(layout, device_info) -> list[str]:
    """One human line per thing the paired phone will degrade in `layout`.

    Missing `control.<type>` → placeholder, or the fallback it draws instead;
    missing `sync.<m>` / `action.<m>` → that binding is ignored; unmet
    `requires.features` or `requires.app` newer than the device's appVersion →
    the layout shows an update banner. No `features` on the device → []."""
    have = device_features(device_info)
    if have is None or not isinstance(layout, dict):
        return []
    lines: list[str] = []

    def visit(ch: dict, where: str, in_canvas: bool) -> None:
        ctype = ch.get("type")
        if isinstance(ctype, str) and not supports(f"control.{ctype}", have):
            fb = usable_fallback(ch, have, in_canvas)
            if fb:
                lines.append(f"{_label(ch)} at {where}: phone doesn't know "
                             f"'{ctype}', falls back to {fb}")
            else:
                lines.append(f"{_label(ch)} at {where}: phone doesn't know "
                             f"'{ctype}', renders as an 'Update CAR-TER' placeholder")
            return
        for key in ("sync", "action"):
            for m in _methods(ch.get(key)):
                if not supports(f"{key}.{m}", have):
                    lines.append(f"{_label(ch)} at {where}: {key} method '{m}' "
                                 f"not supported, binding ignored")

    tabs = layout.get("tabs")
    for t, tab in enumerate(tabs if isinstance(tabs, list) else []):
        if isinstance(tab, dict):
            walk_controls(tab.get("children"), visit, f"tabs[{t}]")

    req = layout.get("requires")
    if isinstance(req, dict):
        unmet: list[str] = []
        need_app = req.get("app")
        dev_app = device_info.get("appVersion")
        if isinstance(need_app, str) and need_app and dev_app and \
                _cmp_versions(ver_tuple(need_app), ver_tuple(dev_app)) > 0:
            unmet.append(f"app {need_app} (device has {dev_app})")
        feats = req.get("features")
        for f in feats if isinstance(feats, list) else []:
            if isinstance(f, str) and f.strip() and not supports(f, have):
                unmet.append(f.strip())
        if unmet:
            lines.append("layout requires " + ", ".join(unmet)
                         + ": layout shows an update banner")
    return lines


def format_forward_compat(lines: list[str], limit: int = 20) -> str:
    """The 'Forward-compat' block appended to a routed push reply ('' if clean)."""
    if not lines:
        return ""
    out = ["Forward-compat (the paired phone's app is older than this layout):"]
    out += [f"  - {ln}" for ln in lines[:limit]]
    if len(lines) > limit:
        out.append(f"  … and {len(lines) - limit} more")
    return "\n".join(out)


def missing_catalog_features(device_info, catalog_types, extra_features=()) -> Optional[list[str]]:
    """Catalog control types (as `control.<type>`) plus manifest `features` the
    device does not speak, sorted. None when the device reports no features."""
    have = device_features(device_info)
    if have is None:
        return None
    wanted = {f"control.{t}" for t in catalog_types or () if isinstance(t, str) and t}
    wanted |= {f for f in extra_features or () if isinstance(f, str) and f.strip()}
    return sorted(f for f in wanted if not supports(f, have))
