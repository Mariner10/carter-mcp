"""Schema-driven layout validation — catch broken layouts before pushing.

Drives off the control catalog (catalog.build_catalog(..., include_theme=True)) so
the field/enum schema stays in sync with the docs. Reports structural problems
(missing required keys), id collisions, unknown control types, unknown fields,
bad enum values, and grid overlaps/out-of-bounds (via grid.validate_placement).

Findings are dicts: {"severity": "error"|"warn", "kind", "where", "detail"}.
"""

from __future__ import annotations

from typing import Optional

import grid as gridmod

# Base/shared properties every control may carry (from the layout schema /
# ChildDefinition), independent of its type. Type-specific fields come from the catalog.
SHARED_FIELDS = {
    "type", "id", "position", "span", "label", "defaultValue", "icon", "tint",
    "hideLabel", "hideBackground", "action", "sync", "visible", "haptic",
    "animation", "longPressGroup", "longPressAction", "theme", "config",
}
GROUP_FIELDS = {
    "type", "id", "position", "span", "label", "grid", "children", "dynamic",
    "visible", "theme",
}


def _f(severity: str, kind: str, where: str, detail: str) -> dict:
    return {"severity": severity, "kind": kind, "where": where, "detail": detail}


def validate_layout(layout: dict, catalog: dict) -> list[dict]:
    """Validate a full layout against the catalog. `catalog` should be built with
    include_theme=True so per-control theme fields are recognized."""
    findings: list[dict] = []
    if not isinstance(layout, dict):
        return [_f("error", "structure", "root", "layout must be a JSON object")]
    for key in ("name", "version", "tabs"):
        if key not in layout:
            findings.append(_f("error", "missing_field", "root", f"missing top-level '{key}'"))
    tabs = layout.get("tabs")
    if not isinstance(tabs, list):
        findings.append(_f("error", "structure", "root", "'tabs' must be an array"))
        return findings

    seen_ids: dict[str, str] = {}
    for ti, tab in enumerate(tabs):
        where = f"tab[{ti}]"
        if not isinstance(tab, dict):
            findings.append(_f("error", "structure", where, "tab must be an object"))
            continue
        g = tab.get("grid") or {}
        cols, rows = int(g.get("columns", 4)), int(g.get("rows", 8))
        children = tab.get("children") or []
        _grid_findings(children, cols, rows, where, findings)
        for ch in children:
            _validate_child(ch, catalog, where, findings, seen_ids)
    return findings


def _grid_findings(children, cols, rows, where, findings):
    for issue in gridmod.validate_placement(children, cols, rows):
        ids = issue.get("ids") or [issue.get("id")]
        findings.append(_f("error", issue["kind"], where,
                           f"{', '.join(str(i) for i in ids)}: {issue['detail']}"))


def _validate_child(ch, catalog, where, findings, seen_ids):
    if not isinstance(ch, dict):
        findings.append(_f("error", "structure", where, "child must be an object"))
        return
    ctype = ch.get("type")
    cid = ch.get("id")
    spot = f"{where}/{cid or ctype or '?'}"

    if not ctype:
        findings.append(_f("error", "missing_field", spot, "control missing 'type'"))
    if not cid:
        findings.append(_f("error", "missing_field", spot, "control missing 'id'"))
    elif cid in seen_ids:
        findings.append(_f("error", "duplicate_id", spot,
                           f"id '{cid}' already used at {seen_ids[cid]}"))
    elif cid:
        seen_ids[cid] = spot

    if ctype == "group":
        for k in ch:
            if k not in GROUP_FIELDS:
                findings.append(_f("warn", "unknown_field", spot, f"group: unknown field '{k}'"))
        sub_children = ch.get("children") or []
        g = ch.get("grid") or {}
        _grid_findings(sub_children, int(g.get("columns", 4)), int(g.get("rows", 8)), spot, findings)
        for sub in sub_children:
            _validate_child(sub, catalog, spot, findings, seen_ids)
        return

    if not ctype:
        return
    entry = catalog.get(ctype)
    if entry is None:
        findings.append(_f("error", "unknown_type", spot, f"unknown control type '{ctype}'"))
        return

    fields = {f["name"]: f for f in entry.get("fields", [])}
    theme_names = {f["name"] for f in entry.get("themeFields", [])}
    allowed = SHARED_FIELDS | set(fields) | theme_names

    for k, v in ch.items():
        if k not in allowed:
            findings.append(_f("warn", "unknown_field", spot, f"{ctype}: unknown field '{k}'"))
            continue
        fd = fields.get(k)
        if fd and fd.get("type") == "enum" and fd.get("values") and isinstance(v, str):
            if v not in fd["values"]:
                findings.append(_f("error", "bad_enum", spot,
                                   f"{ctype}.{k} = '{v}' is not one of {fd['values']}"))


def format_findings(findings: list[dict]) -> str:
    """Render findings as a readable report."""
    if not findings:
        return "✓ No issues found."
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]
    lines = [f"{len(errors)} error(s), {len(warns)} warning(s):"]
    for f in errors:
        lines.append(f"  ✗ [{f['kind']}] {f['where']} — {f['detail']}")
    for f in warns:
        lines.append(f"  ⚠ [{f['kind']}] {f['where']} — {f['detail']}")
    return "\n".join(lines)
