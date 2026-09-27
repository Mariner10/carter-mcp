"""Documentation tools — browse the control catalog, docs, examples, and samples."""

from __future__ import annotations

import json
from typing import Optional

from carterkit import catalog

from carter_mcp import content
from carter_mcp.app import mcp
from carter_mcp.paths import SAMPLE_LAYOUTS_DIR


@mcp.tool()
def list_controls() -> str:
    """List all available CAR-TER control types and system features.

    For placeable controls the **`type`** shown is the exact camelCase token to put in
    a layout (`{"type": "statusLight"}`). The `docs:` id in parentheses is the kebab-case
    key for get_control_doc / list_control_examples / insert_example — they differ for
    multi-word controls (type `statusLight` ⟷ docs `status-light`), so don't put the
    docs id in a layout.
    """
    docs = content.list_doc_files()
    by_category: dict[str, list] = {}
    for d in docs:
        by_category.setdefault(d["category"], []).append(d)

    def _fmt(d: dict) -> str:
        # Placeable controls: lead with the camelCase `type` (what goes in the layout);
        # note the kebab docs id only when it differs. System/model docs have no control
        # `type` — show just the docs id.
        if d.get("type"):
            if d["type"] != d["id"]:
                return f"  - **{d['label']}** — type `{d['type']}`  ·  docs `{d['id']}`"
            return f"  - **{d['label']}** — type `{d['type']}`"
        return f"  - **{d['label']}**  ·  docs `{d['id']}`"

    lines = []
    ordered = ["controls", "display", "system", "models"]
    remaining = sorted(set(by_category.keys()) - set(ordered))
    for cat in ordered + remaining:
        if cat not in by_category:
            continue
        lines.append(f"\n## {cat.title()}")
        for d in by_category[cat]:
            lines.append(_fmt(d))

    return "\n".join(lines)


@mcp.tool()
def get_control_doc(control_id: str) -> str:
    """Get the full documentation for a specific control type or system feature.

    Args:
        control_id: The control identifier (e.g. 'button', 'gauge', 'sync', 'layout-config')
    """
    doc = content.load_doc(control_id)
    if doc is None:
        available = [f.stem for f in content.definitions_dir().glob("*.md")]
        return f"No doc found for '{control_id}'. Available: {', '.join(sorted(available))}"
    return doc


@mcp.tool()
def list_sample_layouts() -> str:
    """List all available sample layout files that can be used as references."""
    layouts = []
    for f in sorted(SAMPLE_LAYOUTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            name = data.get("name", f.stem)
            tabs = len(data.get("tabs", []))
            has_connection = "connection" in data
            layouts.append(f"- **{name}** (`{f.name}`) — {tabs} tabs, {'connected' if has_connection else 'offline'}")
        except Exception:
            layouts.append(f"- `{f.name}` — (parse error)")
    return "\n".join(layouts) if layouts else "No sample layouts found."


@mcp.tool()
def get_sample_layout(name: str) -> str:
    """Get the full JSON of a sample layout file.

    Args:
        name: Filename (e.g. 'demo-offline.json') or name without extension
    """
    if not name.endswith(".json"):
        name += ".json"
    path = SAMPLE_LAYOUTS_DIR / name
    if not path.exists():
        available = [f.name for f in SAMPLE_LAYOUTS_DIR.glob("*.json")]
        return f"Layout '{name}' not found. Available: {', '.join(sorted(available))}"
    return path.read_text()


#: The docs get_layout_schema stitches together, in order. INTERIM (carter-m7s.6): the
#: generated JSON Schema supersedes this; until then the reference is the source of
#: truth itself instead of a hand-kept string that drifted (it once listed 8 keys).
LAYOUT_SCHEMA_DOCS = ("document-contract", "layout-config", "group-def", "control-def")


def _schema_doc(doc_id: str) -> Optional[str]:
    """A doc from the website definitions, else the installed carterkit's copy (a doc
    newer than the last site deploy is still found)."""
    text = content.load_doc(doc_id)
    if text is None:
        path = content.demos_dir() / f"{doc_id}.md"
        if path.exists():
            text = path.read_text()
    return text


@mcp.tool()
def get_layout_schema() -> str:
    """Get the layout JSON structure reference: the document contract (schemaVersion vs
    version, extensions{}, reserved keys, identity, limits, assets, the sectioned form)
    plus the layout, group and control field references — straight from ControlDocs."""
    parts = ["# CAR-TER Layout Schema\n\nFrom ControlDocs, the source of truth. "
             "Per-control fields: get_control_doc / get_control_catalog."]
    missing = []
    for doc_id in LAYOUT_SCHEMA_DOCS:
        text = _schema_doc(doc_id)
        if text is None:
            missing.append(doc_id)
            continue
        parts.append(f"<!-- {doc_id}.md -->\n{text.strip()}")
    if missing:
        parts.append("(Not in the current definitions: " + ", ".join(missing)
                     + " — run check_sources; the site or kit may predate them.)")
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def get_control_catalog(types: Optional[list[str]] = None,
                        include_theme: bool = False) -> str:
    """Machine-readable schema for every placeable control, in ONE call.

    Returns compact JSON keyed by control `type` (the value used in a layout), each
    with its fields (name/type/enum values/default), defaultSpan, and example names.
    Prefer this over reading individual control docs when authoring — it's the whole
    control vocabulary at once.

    Args:
        types: Optional list of control types/node-ids to filter to (e.g. ['gauge','button']).
        include_theme: Also include each control's per-control theme override fields.
    """
    cat = catalog.build_catalog(content.definitions_dir(), types=types,
                                include_theme=include_theme)
    if not cat:
        return (f"No controls matched {types}." if types else "No controls found.")
    return json.dumps(cat, indent=2)


@mcp.tool()
def list_control_examples(control_id: str) -> str:
    """List the named example snippets available in a control's documentation.

    Use get_control_example to fetch one as a ready-to-tweak config.

    Args:
        control_id: Control type or doc node-id (e.g. 'gauge', 'color-picker').
    """
    examples = catalog.get_examples(content.demos_dir(), control_id)
    if not examples:
        return f"No examples found for '{control_id}'. Try list_controls or get_control_doc."
    lines = [f"Examples for `{control_id}`:"]
    lines += [f"  - {e['name']}" for e in examples]
    return "\n".join(lines)


@mcp.tool()
def get_control_example(control_id: str, name: str = "") -> str:
    """Get a ready-to-customize JSON config for a documented control example.

    Copy it, tweak the fields, then place it into a layout (or use insert_example to
    drop it straight into the working buffer with a fresh id + free grid slot).

    Args:
        control_id: Control type or doc node-id (e.g. 'button', 'gauge').
        name: Example name (prefix match, case-insensitive). Omit for the first example.
    """
    examples = catalog.get_examples(content.demos_dir(), control_id)
    if not examples:
        return f"No examples found for '{control_id}'."
    if not name:
        ex = examples[0]
    else:
        ex = catalog.find_example(content.demos_dir(), control_id, name)
        if not ex:
            avail = ", ".join(e["name"] for e in examples)
            return f"No example '{name}' for '{control_id}'. Available: {avail}"
    return f"**{ex['name']}**\n\n```json\n{ex['json']}\n```"
