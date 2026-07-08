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


@mcp.tool()
def get_layout_schema() -> str:
    """Get the layout JSON structure reference showing all fields and their types."""
    return """# CAR-TER Layout Schema

## Top Level (LayoutConfig)

```json
{
  "name": "string (required) — display name",
  "headerTitle": "string — header bar title (defaults to name)",
  "version": 1,
  "accentColor": "#hex — app accent color (default #667eea)",
  "connection": { ... } | null,
  "tabs": [ TabDefinition, ... ],
  "pollGroups": { "name": { "event": "string", "interval": number, "payload": any } },
  "dynamicTabs": [ { "event": "string" } ]
}
```

## ConnectionConfig

```json
{
  "url": "wss://... (required)",
  "token": "string | null",
  "identity": {
    "name": "string — device display name",
    "channel": "string — mesh channel",
    "role": "string — mesh role",
    "canBroadcast": false,
    "canRoute": false
  }
}
```

## TabDefinition

```json
{
  "title": "string (required)",
  "icon": "string — SF Symbol name (required)",
  "grid": { "columns": int, "rows": int, "mode": "grid|flow", "rowHeight": int },
  "children": [ ChildDefinition, ... ]
}
```

**Grid modes:** a grid is `columns × rows`. The default `mode: "grid"` is true 2-D —
each child fills a `row × col` rectangle (`colSpan`=width, `rowSpan × rowHeight`=height,
default `rowHeight` 56pt), so a tall control can sit beside two stacked shorter ones.
Use `mode: "flow"` for the legacy row-banded layout (a full-page `map`/`chat`/`cardList`,
or a plain form). A square control (ring, full gauge) reads best at ~3 `rowSpan`; inputs at 1.

## ChildDefinition

Either a control or a group:

### ControlDefinition
```json
{
  "type": "button|toggle|slider|stepper|segmentedControl|picker|datePicker|textInput|colorPicker|label|image|gauge|sparkline|progressRing|map|graph|chat|cardList",
  "id": "string (required, unique)",
  "position": [row, col],
  "span": [rowSpan, colSpan] (default [1, 1]) — 2-D grid: colSpan=width, rowSpan=height,
  "controlHeight": number — override grid-derived height (rarely needed),
  "label": "string",
  "defaultValue": any,
  "icon": "string — SF Symbol",
  "tint": "#hex",
  "hideLabel": bool,
  "hideValue": bool — ring/gauge: hide the center number (a compact, scaling visual),
  "hideBackground": bool — drop the card; the control floats and fills its cell,

  // Type-specific fields:
  "min": number, "max": number, "step": number,
  "minIcon": "string", "maxIcon": "string",
  "options": ["string", ...],
  "placeholder": "string",
  "text": "string (for label type)",
  "systemName": "string (for image type — SF Symbol)",

  // Gauge
  "gaugeStyle": "full|three_quarter|half",
  "segments": [{ "limit": number, "color": "#hex" }],

  // ProgressRing
  "progressStyle": "ring|bar",

  // Sparkline
  "sparklinePoints": int,
  "sparklineFill": bool,

  // Picker
  "pickerStyle": "menu|wheel|inline",

  // DatePicker
  "datePickerStyle": "compact|wheel|graphical",
  "datePickerMode": "date|time|dateAndTime",

  // Map
  "mapStyle": "standard|satellite|hybrid",
  "mapInteractive": bool,

  // Graph
  "graphConfig": { ... see graph doc },

  // Chat
  "config": { "target": string|null, "showTypingIndicators": bool, "historyCount": int },

  // Behavior
  "action": ActionDefinition,
  "sync": [ SyncDefinition, ... ],
  "visible": { "when": "control-id", "operator": "eq|neq|gt|lt|gte|lte", "value": any },
  "haptic": "light|medium|heavy|success|warning|error|selection",
  "animation": "snappy|smooth|bouncy|gentle|instant",

  // Long press
  "longPressGroup": GroupDefinition,
  "longPressAction": ActionDefinition
}
```

### GroupDefinition
```json
{
  "type": "group",
  "id": "string (required)",
  "label": "string",
  "position": [row, col],
  "span": [rowSpan, colSpan],
  "grid": { "columns": int, "rows": int, "mode": "grid|flow", "rowHeight": int },
  "children": [ ChildDefinition, ... ],
  "dynamic": "string — event name for dynamic content",
  "visible": { ... }
}
```

### ActionDefinition
```json
{
  "method": "meshsocket",
  "mode": "request|broadcast",
  "event": "string",
  "payload": { "key": "{{value}}" }
}
```

### SyncDefinition
```json
{
  "method": "meshsocket",
  "type": "listen",
  "event": "string — event to listen for",
  "filter": { "key": "value" },
  "valuePath": "string — dot-notation path to extract value"
}
```

## Grid Positioning

- `position: [row, col]` — zero-indexed
- `span: [rowSpan, colSpan]` — how many cells the control occupies
- Grid is defined per-tab and per-group
- Children must fit within the parent grid dimensions

## SF Symbol Icons

Common icons: slider.horizontal.3, bolt.fill, sun.max, gauge.with.dots.needle.67percent,
map.fill, bubble.left.and.bubble.right.fill, gearshape.fill, house.fill, chart.line.uptrend.xyaxis,
antenna.radiowaves.left.and.right, hand.tap.fill, arrow.clockwise, star.fill, heart.fill
"""


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
