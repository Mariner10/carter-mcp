#!/usr/bin/env python3
"""
CAR-TER MCP Server

A thin, LOCAL tool layer that gives an LLM access to CAR-TER's live control
documentation and the ability to push layout updates to a paired iPhone/iPad over
MeshSocket. It does not vendor the control vocabulary — it pulls it from the
current sources at call time:

  • Control DEFINITIONS (the catalog + doc prose) come from the WEBSITE
    (carterbeaudoin.net/CAR-TER/catalog.json), cached locally with offline fallback.
  • Authoring DEMOS ("how to write code": example snippets + the codegen/builder
    engine) come from the installed carterkit, with a PyPI check for newer releases.
  • check_sources reconciles those against the paired device's app version so the
    model can detect and report drift.

See `sources.py` for the resolution/cache logic and `PROTOCOL.md` for the wire
contract (including the get-device-info readback).

Tools:
  Sources & versioning:
    - check_sources        Where definitions/demos come from + drift verdict
    - get_device_info      Paired app's version / protocol (drift detection)

  Documentation:
    - list_controls        List all control types
    - get_control_doc      Get full doc for a control/system feature
    - list_sample_layouts  List available sample layouts
    - get_sample_layout    Get JSON of a sample layout
    - get_layout_schema    Get the layout structure reference

  Editor:
    - connect              Connect to MeshSocket relay, get QR pairing payload
    - push_layout          Push a layout (routed w/ render echo, or broadcast)
    - save_layout          Send layout-save so device persists to disk
    - disconnect           Tear down MeshSocket connection

  Read-back (see what's on the phone, instead of pushing blind):
    - get_device_layout    Read the layout currently live on the device
    - get_control_state    Read current control values on the device
    - get_connection_status Read the device's relay connection status

  Device:
    - list_device_layouts  List all layout files on the paired device
    - save_device_layout   Create or update a layout file on the device

  Catalog & examples:
    - get_control_catalog  Machine-readable schema for all controls (one call)
    - list_control_examples / get_control_example   Documented example snippets

  Incremental editing (working buffer — no more 800-line re-emits):
    - begin_edit (blank / from_sample / from_device), preview_buffer, get_buffer_json
    - add_control, insert_example, update_control, remove_control, move_control
    - add_tab, add_group, show_grid, push_buffer, save_buffer, discard_buffer
    - validate_layout / validate_buffer   Schema + grid lint before pushing
    - snapshot_buffer / list_snapshots / revert_buffer   Experiment fearlessly

  Human-in-the-loop & live service:
    - customize_on_phone   Hand a control to the phone's configurator, read it back
    - probe_service        Sniff live traffic → discovered events/paths
    - autowire_buffer / lint_against_traffic   Bind/verify sync against real data
    - simulate / set_control_value   Drive controls live (MCP-as-service)
    - run_scenario         Scripted emit→assert UI testing over the mesh
    - show_mesh_graph      Visualize the live MeshSocket roster
    - say_in_chat / read_chat   LLM as a chat peer inside a layout

  Generators:
    - infer_layout         Real JSON payload → wired first-draft layout
    - autotune_gauge       Gauge range + color zones from observed samples
    - generate_theme / list_theme_vibes   Vibe/brand → ThemeConfig
    - generate_service / generate_adapter  Runnable backend for a layout
"""

import sys
import os
import copy
import json
import asyncio
import glob
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
import qrcode

import autowire
import chat
import meshgraph
import qa
import probe
import simulate as simulate_lib  # aliased: the `simulate` @mcp.tool() below shadows this module name
import sources  # live control DEFINITIONS (website) + authoring DEMOS (latest carterkit)

# Layout-authoring engine now lives in the carterkit package (pip install carterkit) —
# catalog/builder/validate/codegen/infer/theming/tune are no longer vendored here.
from carterkit import catalog, codegen, grid, infer, theming, tune, validate, dynamic
from carterkit.buffer import LayoutBuffer, BufferError

# MeshSocket client (PyPI: `pip install meshsocket`)
from meshsocket import MeshSocket

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
# Control definitions and example/demo snippets are no longer read from a fixed
# local checkout — they're resolved live by `sources` (website catalog + latest
# carterkit), which falls back to this repo path only as a last resort in dev.
CONTROL_DOCS_DIR = PROJECT_ROOT / "CAR-TER" / "CAR-TER" / "ControlDocs"
SAMPLE_LAYOUTS_DIR = PROJECT_ROOT / "CAR-TER" / "CAR-TER" / "SampleLayouts"
DOCS_DIR = PROJECT_ROOT / "CAR-TER" / "docs"


def _definitions_dir() -> Path:
    """Docs dir for the control catalog + doc prose (website-sourced, cached)."""
    return sources.definitions_docs_dir()


def _demos_dir() -> Path:
    """Docs dir for example snippets / authoring demos (latest installed carterkit)."""
    return sources.demos_docs_dir()

RELAY_URL = os.environ.get("CARTER_RELAY_URL", "wss://carterbeaudoin.com/coms/")
RELAY_TOKEN = os.environ.get("CARTER_MESH_TOKEN", "")

# ─── QR helpers ──────────────────────────────────────────────────────────────

QR_IMAGE_PATH = Path("/tmp/carter-pairing-qr.png")

def _make_qr_image(data: str) -> Path:
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(QR_IMAGE_PATH))
    return QR_IMAGE_PATH

# ─── State ────────────────────────────────────────────────────────────────────

socket: Optional[MeshSocket] = None
socket_task: Optional[asyncio.Task] = None
current_channel: Optional[str] = None
peer_list: list[dict] = []
device_connected_event: Optional[asyncio.Event] = None
# The server-held draft for incremental editing (begin_edit / add_control / …).
# Persists across tool calls within a session; pushed to the device as a full layout.
work_buffer: Optional[LayoutBuffer] = None
# Resolved by the `control-edit-response` listener when the user taps "Send to
# Editor" on the phone's configurator (the customize_on_phone round-trip).
control_edit_future: Optional[asyncio.Future] = None
# Aggregated traffic schema from the last probe_service run (event -> path -> stats),
# consumed by autowire_buffer / lint_against_traffic.
last_probe_events: dict = {}
# Raw decoded payloads from the last probe_service run, consumed by
# lint_dynamic_traffic (which needs the actual `children` arrays, not the aggregate).
last_probe_frames: list = []
# Saved buffer states for snapshot / revert (experiment fearlessly).
buffer_snapshots: list[tuple[str, dict]] = []
# Active session connection target, set by connect(); lets the relay URL/token be
# overridden per call (the relay URL can change) instead of being hardcoded.
# show_qr reads these so the device pairs to the exact relay the editor used.
active_url: Optional[str] = None
active_token: Optional[str] = None
# What the *device* scans. For the local relay this is the Mac's LAN IP (the
# editor itself dials loopback), so the two can differ.
active_qr_url: Optional[str] = None
# Zero-config authoring transport: an in-process, auth-free MeshSocket relay.
local_relay = None
local_relay_task: Optional[asyncio.Task] = None
LOCAL_RELAY_PORT = int(os.environ.get("CARTER_LOCAL_RELAY_PORT", "8765"))
# Optional dev-validator base URL, used only by the gateway path to auto-mint a
# token so authoring never needs a hand-pasted one.
VALIDATOR_URL = os.environ.get("CARTER_VALIDATOR_URL", "")

# ─── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    "carter",
    instructions="""You are a CAR-TER layout editor. You read CAR-TER's live control
documentation to understand every available control type, then build and push
layouts to a paired iPhone/iPad in real time over MeshSocket.

Where your knowledge comes from (don't guess from memory — pull it):
- Control DEFINITIONS (catalog, fields, doc prose) are fetched from the CAR-TER
  website and cached locally. list_controls / get_control_doc / get_control_catalog
  reflect what is *currently published*, not a bundled snapshot.
- Authoring DEMOS (example snippets, generators) come from the installed carterkit.
- These can drift from each other and from the phone's installed app.

Workflow:
1. Run check_sources first. It confirms the website catalog is reachable/fresh,
   the carterkit is current (offer `pip install -U carterkit` if not), and — once a
   device is paired — that the app's version/protocol match the definitions. If it
   reports drift, surface it to the user before authoring.
2. Use list_controls and get_control_doc to learn the available controls; use
   get_control_catalog for the machine-readable schema in one call.
3. Use get_layout_schema for the overall layout structure; get_sample_layout and
   get_control_example for real, ready-to-tweak snippets.
4. Use connect to pair with a device — the user scans the QR code in CAR-TER
   (Settings → Live Edit / scan). Then get_device_info to confirm the app version.
5. Build a layout JSON and push_layout to see it live on the device.
6. Iterate — each push_layout updates the device instantly. If a control or field
   is ignored on the phone, re-run check_sources: the app is likely older than the
   published definitions.

Key layout concepts:
- A layout has tabs, each with a grid of children (controls or groups)
- The grid is true 2-D by default (mode "grid"): a child fills a row x col rectangle
  (position [row,col] + span [rowSpan,colSpan]); colSpan=width, rowSpan x rowHeight=height.
  Span more cells to make a control bigger. Use grid mode "flow" for a full-page
  map/chat/cardList or a plain form.
- Controls have type, id, position [row, col], and optional span [rowSpan, colSpan]
- Groups are containers with their own sub-grid (and their own mode)
- Connection config enables real-time sync with a server
- Controls can have actions (send commands) and sync (receive data)
""",
)


# ─── Documentation Tools ─────────────────────────────────────────────────────

def _load_doc(doc_id: str) -> Optional[str]:
    path = _definitions_dir() / f"{doc_id}.md"
    if path.exists():
        return path.read_text()
    return None


def _list_doc_files() -> list[dict]:
    results = []
    for f in sorted(_definitions_dir().glob("*.md")):
        node_id = f.stem
        content = f.read_text()
        label = node_id
        category = "unknown"
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("label:"):
                label = line.split(":", 1)[1].strip()
            elif line.startswith("category:"):
                category = line.split(":", 1)[1].strip()
        results.append({"id": node_id, "label": label, "category": category})
    return results


@mcp.tool()
def list_controls() -> str:
    """List all available CAR-TER control types and system features with their categories."""
    docs = _list_doc_files()
    by_category: dict[str, list] = {}
    for d in docs:
        by_category.setdefault(d["category"], []).append(d)

    lines = []
    for cat in ["controls", "display", "system", "models"]:
        if cat not in by_category:
            continue
        lines.append(f"\n## {cat.title()}")
        for d in by_category[cat]:
            lines.append(f"  - **{d['label']}** (`{d['id']}`)")

    remaining = set(by_category.keys()) - {"controls", "display", "system", "models"}
    for cat in sorted(remaining):
        lines.append(f"\n## {cat.title()}")
        for d in by_category[cat]:
            lines.append(f"  - **{d['label']}** (`{d['id']}`)")

    return "\n".join(lines)


@mcp.tool()
def get_control_doc(control_id: str) -> str:
    """Get the full documentation for a specific control type or system feature.

    Args:
        control_id: The control identifier (e.g. 'button', 'gauge', 'sync', 'layout-config')
    """
    content = _load_doc(control_id)
    if content is None:
        available = [f.stem for f in _definitions_dir().glob("*.md")]
        return f"No doc found for '{control_id}'. Available: {', '.join(sorted(available))}"
    return content


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
    cat = catalog.build_catalog(_definitions_dir(), types=types, include_theme=include_theme)
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
    examples = catalog.get_examples(_demos_dir(), control_id)
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
    examples = catalog.get_examples(_demos_dir(), control_id)
    if not examples:
        return f"No examples found for '{control_id}'."
    if not name:
        ex = examples[0]
    else:
        ex = catalog.find_example(_demos_dir(), control_id, name)
        if not ex:
            avail = ", ".join(e["name"] for e in examples)
            return f"No example '{name}' for '{control_id}'. Available: {avail}"
    return f"**{ex['name']}**\n\n```json\n{ex['json']}\n```"


# ─── Read-back protocol (pure logic) ──────────────────────────────────────────
#
# Routed RPC over `route_msg` — the device answers these via handle(event:).
# These helpers are pure (no I/O) so the protocol shapes and response formatting
# are testable without a live device. The async tools below are thin wrappers.


def format_routed_response(result, on_ok, verb: str) -> str:
    """Render a routed-RPC reply, handling the three failure surfaces uniformly.

    - None         → device did not answer within the relay timeout.
    - {"error": …} → the relay could not route / errored.
    - otherwise    → delegate to on_ok(result).
    """
    if result is None:
        return f"Device did not respond to '{verb}' (timeout). Make sure it's paired and running."
    if isinstance(result, dict) and "error" in result and not result.get("ok"):
        return f"Relay error on '{verb}': {result['error']}"
    return on_ok(result)


def _format_summary(summary: dict) -> str:
    """One structural-echo summary block → readable lines (tabs + controls)."""
    lines = []
    name = summary.get("name", "?")
    accent = summary.get("accentColor")
    lines.append(f"**{name}**" + (f" · accent {accent}" if accent else ""))
    for tab in summary.get("tabs", []):
        title = tab.get("title", "?")
        icon = tab.get("icon", "")
        lines.append(f"  Tab: {title}" + (f" ({icon})" if icon else ""))
        for c in tab.get("controls", []):
            pos = c.get("position")
            span = c.get("span")
            extras = []
            if pos is not None:
                extras.append(f"pos {pos}")
            if span is not None and span != [1, 1]:
                extras.append(f"span {span}")
            suffix = f" — {', '.join(extras)}" if extras else ""
            lines.append(f"    - {c.get('id','?')} ({c.get('type','?')}){suffix}")
    return "\n".join(lines)


# get-current-layout

def build_get_layout_request(full: bool = False) -> dict:
    return {"include": "full" if full else "summary"}


def format_current_layout(resp: dict) -> str:
    summary = resp.get("summary")
    if not summary:
        return "Device reports no layout loaded."
    header_bits = []
    if resp.get("activeFile"):
        header_bits.append(f"file `{resp['activeFile']}`")
    if resp.get("isLiveEditSession"):
        header_bits.append("live-edit session")
    header = (" · ".join(header_bits) + "\n") if header_bits else ""
    body = _format_summary(summary)
    full = resp.get("layout")
    if full is not None:
        body += "\n\n```json\n" + json.dumps(full, indent=2) + "\n```"
    return header + body


# get-control-state

def build_control_state_request(ids=None):
    if ids:
        return {"ids": ids}
    return None


def format_control_state(resp: dict) -> str:
    values = resp.get("values", {})
    if not values:
        return "Device reports no control values."
    return "\n".join(f"- {k}: {json.dumps(v)}" for k, v in values.items())


# get-connection-status

def format_connection_status(resp: dict) -> str:
    connected = resp.get("connected", False)
    phase = resp.get("phase", "?")
    state = "Connected" if connected else "Not connected"
    lines = [f"{state} (phase: {phase})"]
    if resp.get("channel"):
        lines.append(f"  channel: {resp['channel']} · role: {resp.get('role','?')}")
    if resp.get("account"):
        lines.append(f"  account: {resp['account']}")
    listening = resp.get("listening") or []
    if listening:
        lines.append(f"  listening: {', '.join(listening)}")
    return "\n".join(lines)


# get-device-info  (app version / protocol / catalog fingerprint — for drift checks)
#
# The device echoes which CAR-TER app build the user is running so the model can
# confirm the phone understands the control definitions it's authoring against.
# An older app may simply drop controls or fields it doesn't know; comparing the
# device's reported protocol/fingerprint to the website catalog catches that early.
# The Swift responder is a separate device-side track (see PROTOCOL.md); the MCP
# tolerates its absence (older apps just don't reply / omit the fields).

_DEVICE_INFO_KEYS = ("appVersion", "build", "protocolVersion",
                     "catalogFingerprint", "model", "osVersion")


def extract_device_info(resp) -> Optional[dict]:
    """Pull version fields out of a get-device-info / get-connection-status reply."""
    if not isinstance(resp, dict):
        return None
    info = {k: resp[k] for k in _DEVICE_INFO_KEYS if k in resp}
    return info or None


def format_device_info(resp: dict) -> str:
    info = extract_device_info(resp) or {}
    if not info:
        return ("Device did not report version info — it's an older app that "
                "predates the device-info verb. Ask the user to update CAR-TER "
                "to enable version drift detection.")
    bits = []
    if info.get("appVersion"):
        v = f"app v{info['appVersion']}"
        if info.get("build"):
            v += f" (build {info['build']})"
        bits.append(v)
    if info.get("protocolVersion") is not None:
        bits.append(f"protocol v{info['protocolVersion']}")
    if info.get("model"):
        bits.append(str(info["model"]))
    if info.get("osVersion"):
        bits.append(f"iOS {info['osVersion']}")
    out = "Paired device: " + " · ".join(bits)
    if info.get("catalogFingerprint"):
        out += f"\n  catalog fingerprint: {str(info['catalogFingerprint'])[:23]}…"
    return out


# apply-layout (truthful push)

def build_apply_layout_request(layout: dict) -> dict:
    """Routed apply-layout carries the layout itself, WITHOUT broadcast msg_type
    framing (that belongs only to the broadcast layout-update path)."""
    req = dict(layout)
    req.pop("msg_type", None)
    return req


def format_apply_result(resp: dict) -> str:
    if not resp.get("ok"):
        return f"Device rejected the layout: {resp.get('error', 'unknown error')}"
    rendered = resp.get("rendered", {})
    return "Device rendered the layout:\n" + _format_summary(rendered)


def should_push_routed(device_id) -> bool:
    """Push truthfully (routed apply-layout, gets a rendered echo) when a single
    device is resolvable; else broadcast layout-update to all viewers."""
    return device_id is not None


# ─── Control-edit handoff (pure logic) ───────────────────────────────────────
#
# "Shape it on glass": push a control to the phone's configurator, the user tweaks
# it by hand, taps "Send to Editor", and the edited control comes back. Wire contract
# in PROTOCOL.md (control-edit-request / control-edit-response).


def build_control_edit_request(control: dict) -> dict:
    """Frame a control for the phone's configurator (broadcast layout-edit message)."""
    return {"msg_type": "control-edit-request", "control": control}


def extract_edited_control(payload) -> Optional[dict]:
    """Pull the user-edited control out of a control-edit-response payload. Accepts
    either the wrapper ({msg_type, control}) or a bare control object."""
    if not isinstance(payload, dict):
        return None
    inner = payload.get("control")
    if isinstance(inner, dict):
        return inner
    if "type" in payload:  # already the bare control
        return {k: v for k, v in payload.items() if k != "msg_type"}
    return None


# ─── Connection helpers (local relay + token mint) ───────────────────────────

def _lan_ip() -> str:
    """Best-effort primary LAN IP of this Mac, for the device's QR (the phone
    dials over Wi-Fi, not loopback). Falls back to 127.0.0.1."""
    import socket as _s
    sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


async def _ensure_local_relay() -> int:
    """Start an in-process, auth-free MeshSocket relay once; return its port.
    The zero-config authoring transport — no gateway, no token, no AWS."""
    global local_relay, local_relay_task
    if local_relay is not None and local_relay_task and not local_relay_task.done():
        return LOCAL_RELAY_PORT
    from socket_server import MeshServer
    ready = asyncio.Event()
    local_relay = MeshServer(
        host="0.0.0.0",
        port=LOCAL_RELAY_PORT,
        auth_handler=lambda token, ip: True,  # authoring relay accepts any pairing
        on_startup=ready.set,
    )
    local_relay_task = asyncio.create_task(local_relay.start())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(0.3)  # on_startup fires just before serve() binds the port
    return LOCAL_RELAY_PORT


async def _mint_token(validator_url: str, account: str, product: str) -> str:
    """Mint a short-lived token from the dev validator (POST /validate), so the
    gateway path needs no hand-pasted token. Dev validator trusts the claims."""
    import urllib.request
    import time
    body = json.dumps({
        "account": account,
        "product": product,
        "expiresAtMs": int((time.time() + 3600) * 1000),
    }).encode()
    req = urllib.request.Request(
        validator_url.rstrip("/") + "/validate",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )

    def _do():
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    data = await asyncio.to_thread(_do)
    tok = data.get("token")
    if not tok:
        raise RuntimeError(f"validator returned no token: {data}")
    return tok


# ─── Editor Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def connect(channel: str = "editor", role: str = "editor",
                  target: str = "local", url: str = "", token: str = "",
                  validator_url: str = "") -> str:
    """Pair a device for live layout authoring. Hassle-free by default.

    target="local" (default): spins up an in-process, auth-free MeshSocket relay
    on this Mac and builds a QR with the LAN IP — no gateway, no token, no AWS.
    Just have the phone on the same Wi-Fi (the app allows ws:// on the LAN).

    target="relay": use the Connect+ gateway instead (phone can be on any
    network, wss). The token is taken from `token` / CARTER_MESH_TOKEN, or
    auto-minted from the dev validator (`validator_url` / CARTER_VALIDATOR_URL)
    so you still don't hand-paste one.

    Args:
        channel: MeshSocket channel name (default 'editor').
        role: MeshSocket role for the editor (default 'editor').
        target: 'local' (default, zero-config) or 'relay' (gateway).
        url: gateway ws/wss URL (target='relay' only); else CARTER_RELAY_URL/default.
        token: gateway token (target='relay' only); else env, else auto-mint.
        validator_url: dev-validator base URL to mint a token from (target='relay').
    """
    global socket, socket_task, current_channel, device_connected_event
    global active_url, active_token, active_qr_url

    if socket and socket.is_running:
        return f"Already connected on channel '{current_channel}'. Call disconnect first to reconnect."

    if target == "local":
        port = await _ensure_local_relay()
        lan = _lan_ip()
        active_url = f"ws://127.0.0.1:{port}"      # editor dials loopback
        active_qr_url = f"ws://{lan}:{port}"        # phone dials the Mac over Wi-Fi
        active_token = ""
        transport_note = (f"local relay at {active_qr_url} — no gateway, no token "
                          f"(phone must share this Wi-Fi)")
    else:
        active_url = url or RELAY_URL
        active_qr_url = active_url
        active_token = token or RELAY_TOKEN
        if not active_token:
            vurl = validator_url or VALIDATOR_URL
            if not vurl:
                return ("Gateway target needs a token: pass token=… or set "
                        "CARTER_VALIDATOR_URL (validator_url=…) so I can auto-mint one.")
            try:
                active_token = await _mint_token(vurl, f"mcp-{os.urandom(4).hex()}",
                                                 "CARTER.connectplus.duo")
            except Exception as e:
                return f"Failed to auto-mint a token from {vurl}: {e}"
            transport_note = f"gateway {active_url} (auto-minted token)"
        else:
            transport_note = f"gateway {active_url}"
    current_channel = channel
    device_connected_event = asyncio.Event()

    socket = MeshSocket(
        url=active_url,
        name="carter-mcp-editor",
        auth_token=active_token,
        channel=channel,
        role=role,
        can_broadcast=True,
        can_route=True,
        can_monitor=True,
    )

    @socket.on("server_client_list")
    async def _on_client_list(payload):
        global peer_list
        clients = payload.get("clients", [])
        peer_list = [c for c in clients if c.get("name") != "carter-mcp-editor"]
        if peer_list:
            device_connected_event.set()

    @socket.on("control-edit-response")
    async def _on_control_edit_response(payload):
        # The phone tapped "Send to Editor" in the configurator (customize_on_phone).
        if control_edit_future and not control_edit_future.done():
            control_edit_future.set_result(payload)

    socket_task = asyncio.create_task(_run_socket())

    try:
        await asyncio.wait_for(socket.wait_until_ready(), timeout=10)
    except asyncio.TimeoutError:
        await _cleanup_socket()
        return "Failed to connect to MeshSocket relay within 10 seconds."

    qr_payload = json.dumps({
        "url": active_qr_url,
        "token": active_token,
        "channel": channel,
        "role": "viewer",
    })

    qr_path = _make_qr_image(qr_payload)

    return f"""Connected — {transport_note}.
Channel '{channel}'. QR saved to: {qr_path}
Scan it in CAR-TER to pair.

Pairing payload (manual entry):

```json
{qr_payload}
```

Once paired, use push_layout to send layouts to the device."""


async def _run_socket():
    try:
        await socket.start()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[carter-mcp] socket error: {e}", file=sys.stderr)


async def _cleanup_socket():
    global socket, socket_task, current_channel, peer_list, device_connected_event
    if socket:
        try:
            await socket.stop()
        except Exception:
            pass
    if socket_task:
        socket_task.cancel()
        try:
            await socket_task
        except (asyncio.CancelledError, Exception):
            pass
    socket = None
    socket_task = None
    current_channel = None
    peer_list = []
    device_connected_event = None


@mcp.tool()
async def show_qr() -> str:
    """Show the QR code for pairing a device to the current editing session.

    Must be connected first via the connect tool.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    qr_payload = json.dumps({
        "url": active_qr_url or RELAY_URL,
        "token": active_token or "",
        "channel": current_channel,
        "role": "viewer",
    })

    qr_path = _make_qr_image(qr_payload)

    return f"""QR code saved to: {qr_path}
Open that image and scan it in CAR-TER to pair.

Channel: {current_channel}"""


@mcp.tool()
async def wait_for_device(timeout: int = 30) -> str:
    """Wait for a device to pair after scanning the QR code.

    Blocks until a device joins the editing channel or the timeout expires.

    Args:
        timeout: Max seconds to wait (default: 30)
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    # Poll the roster rather than wait on the identify push: the relay registers a
    # client into self.clients only AFTER broadcasting the roster, so a later joiner
    # (the device — the editor always connects first) is never pushed. `get_nodes`
    # reads the live roster at request time, by which point the device is registered.
    deadline = asyncio.get_event_loop().time() + max(1, timeout)
    while True:
        peers = await _poll_peers()
        if peers:
            names = ", ".join(p.get("name", "unknown") for p in peers)
            return f"Device paired: {names}"
        if asyncio.get_event_loop().time() >= deadline:
            return f"No device connected within {timeout}s. Make sure to scan the QR code in CAR-TER."
        await asyncio.sleep(1.0)


async def _apply_or_broadcast(layout: dict) -> str:
    """Send a layout to the device truthfully when a single device is resolvable
    (routed apply-layout with a rendered echo), else broadcast to all viewers.
    Shared by push_layout and push_buffer."""
    device_id = await _get_device_id()
    if should_push_routed(device_id):
        result = await socket.request("route_msg", {
            "target_id": device_id,
            "type": "apply-layout",
            "payload": build_apply_layout_request(layout),
        }, timeout=5.0)
        return format_routed_response(result, on_ok=format_apply_result, verb="apply-layout")

    payload = dict(layout)
    payload["msg_type"] = "layout-update"
    try:
        await socket.send("broadcast_request", payload)
        tab_count = len(layout.get("tabs", []))
        control_count = sum(len(tab.get("children", [])) for tab in layout.get("tabs", []))
        return (f"Layout broadcast to all viewers. {tab_count} tabs, {control_count} "
                f"top-level controls. (No single device paired, so no render confirmation.)")
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost. Try reconnecting."
    except Exception as e:
        return f"Failed to push layout: {e}"


async def _save_layout_obj(layout: dict) -> str:
    """Broadcast a layout-save so the device persists the layout to disk."""
    payload = dict(layout)
    payload["msg_type"] = "layout-save"
    try:
        await socket.send("broadcast_request", payload)
        return f"Save request sent. Device will persist '{layout.get('name', 'Untitled')}' to disk."
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost."
    except Exception as e:
        return f"Failed to save layout: {e}"


@mcp.tool()
async def push_layout(layout_json: str) -> str:
    """Push a layout to the paired device. The device renders it immediately.

    When a single device is paired, this pushes over routed RPC and reports back
    exactly what the device rendered (or why it rejected the layout) — no more
    blind "pushed successfully". With no resolvable device it falls back to a
    broadcast so every paired viewer updates at once.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    required = ["name", "version", "tabs"]
    missing = [f for f in required if f not in layout]
    if missing:
        return f"Layout missing required fields: {', '.join(missing)}"

    return await _apply_or_broadcast(layout)


@mcp.tool()
async def save_layout(layout_json: str) -> str:
    """Push a layout and tell the device to save it to disk permanently.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    return await _save_layout_obj(layout)


# ─── Buffer (incremental editing) Tools ──────────────────────────────────────
#
# A server-held working draft so the LLM edits surgically (add one control, tweak
# one field) instead of re-emitting the whole layout. Each op mutates `work_buffer`;
# push_buffer sends the full result to the device (which already renders full layouts).

_catalog_cache: dict[bool, dict] = {}


def _catalog(include_theme: bool = False) -> dict:
    if include_theme not in _catalog_cache:
        _catalog_cache[include_theme] = catalog.build_catalog(
            _definitions_dir(), include_theme=include_theme)
    return _catalog_cache[include_theme]


# 2-D grid default spans [rowSpan, colSpan], tuned so each control reads at its
# natural aspect in the default ~4-column grid (rowHeight ~56pt): square visuals get
# ~3 rows, wide visuals span columns, inputs sit in 1 row, content panels are tall.
# infer auto-grows rows, so tall spans are safe; colSpan stays <= 4 to fit the
# default grid width (a narrower grid will report "grow the grid").
_SPAN_2D: dict[str, list[int]] = {
    # square visuals
    "progressRing": [3, 2], "gauge": [2, 3], "joystick": [3, 3], "qrCode": [3, 3],
    # wide visual
    "sparkline": [2, 4],
    # tall content panels (infer grows rows to fit)
    "map": [4, 4], "graph": [4, 4], "chat": [5, 4], "list": [4, 4],
    "cardList": [4, 4], "logConsole": [3, 4], "webView": [4, 4], "image": [3, 3],
    # container controls (hold groups)
    "carousel": [4, 4], "flipCard": [4, 4], "accordion": [4, 4],
    # row inputs + text (1 row tall)
    "slider": [1, 2], "stepper": [1, 2], "segmentedControl": [1, 2],
    "picker": [1, 2], "datePicker": [1, 2], "colorPicker": [1, 2],
    "textInput": [1, 2], "toggle": [1, 2], "button": [1, 2],
    "label": [1, 2], "statusLight": [1, 2],
    # structural
    "divider": [1, 4], "spacer": [1, 1],
}


def _default_span_for(control_type: str) -> Optional[list[int]]:
    """2-D-tuned default span for a control type (see `_SPAN_2D`) — squares get a
    near-square footprint, inputs a single row, content a tall panel. Falls back to
    the control doc's catalog `defaultSpan` for anything not mapped."""
    span = _SPAN_2D.get(control_type)
    if span is not None:
        return list(span)
    entry = _catalog().get(control_type)
    return entry.get("defaultSpan") if entry else None


async def _fetch_device_layout_obj() -> Optional[dict]:
    """Pull the device's live layout (full) as a raw dict, for begin_edit(from_device)."""
    device_id = await _get_device_id()
    if not device_id:
        return None
    result = await socket.request("route_msg", {
        "target_id": device_id,
        "type": "get-current-layout",
        "payload": {"include": "full"},
    }, timeout=5.0)
    if isinstance(result, dict):
        return result.get("layout")
    return None


async def _persist_layout_obj(layout: dict, filename: str = "") -> str:
    """Persist a layout to the device's disk — routed save-layout (with a file name
    and an ok/file reply) when a device is resolvable, else a broadcast layout-save."""
    device_id = await _get_device_id()
    if device_id:
        payload: dict = {"layout": layout}
        if filename:
            payload["file"] = filename
        result = await socket.request("route_msg", {
            "target_id": device_id, "type": "save-layout", "payload": payload,
        }, timeout=5.0)
        if result is None:
            return "Device did not respond to save (timeout)."
        if isinstance(result, dict) and result.get("ok"):
            return f"Saved on device as `{result.get('file', '?')}`."
        if isinstance(result, dict) and "error" in result:
            return f"Save error: {result['error']}"
        return f"Unexpected save response: {result}"
    return await _save_layout_obj(layout)


@mcp.tool()
async def begin_edit(name: str = "Untitled", columns: int = 4, rows: int = 8,
                     accent: str = "#667eea", from_sample: str = "",
                     from_device: bool = False,
                     mode: Optional[str] = None, row_height: Optional[int] = None) -> str:
    """Start (or restart) the working layout buffer for incremental editing.

    Then use add_control / insert_example / update_control / move_control / etc. to
    edit surgically, preview_buffer to see it, and push_buffer to send it live —
    without ever re-emitting the whole layout.

    Args:
        name: Layout name (blank-buffer mode).
        columns, rows: Grid of the first tab (blank-buffer mode).
        accent: Accent color hex (blank-buffer mode).
        mode, row_height: First tab's grid mode ("grid" default 2-D / "flow") and 2-D
            row-unit height in points (blank-buffer mode).
        from_sample: Seed from a sample layout filename (e.g. 'demo-offline.json').
        from_device: Seed from the layout currently live on the paired device (read-modify-write).
    """
    global work_buffer
    if from_device:
        if not socket or not socket.is_running:
            return "Not connected — can't read the device. Call connect first (or omit from_device)."
        layout = await _fetch_device_layout_obj()
        if not layout:
            return "Couldn't read a layout off the device (none loaded, or it didn't respond)."
        try:
            work_buffer = LayoutBuffer.from_layout(layout)
        except BufferError as e:
            return f"Device layout couldn't be loaded into the buffer: {e}"
        return "Buffer seeded from the live device layout.\n\n" + work_buffer.summary()
    if from_sample:
        fname = from_sample if from_sample.endswith(".json") else from_sample + ".json"
        path = SAMPLE_LAYOUTS_DIR / fname
        if not path.exists():
            avail = ", ".join(f.name for f in SAMPLE_LAYOUTS_DIR.glob("*.json"))
            return f"Sample '{fname}' not found. Available: {avail}"
        try:
            work_buffer = LayoutBuffer.from_layout(json.loads(path.read_text()))
        except (json.JSONDecodeError, BufferError) as e:
            return f"Couldn't load sample: {e}"
        return f"Buffer seeded from {fname}.\n\n" + work_buffer.summary()
    work_buffer = LayoutBuffer.blank(name=name, columns=columns, rows=rows, accent=accent)
    if mode is not None:
        work_buffer.tabs[0]["grid"]["mode"] = mode
    if row_height is not None:
        work_buffer.tabs[0]["grid"]["rowHeight"] = row_height
    return f"New blank buffer '{name}' ({rows}x{columns}, mode={mode or 'grid'}).\n\n" + work_buffer.summary()


@mcp.tool()
def preview_buffer(show_grids: bool = True) -> str:
    """Show the current working buffer: structure, per-tab grid maps, and any
    placement issues. Never re-dumps the whole JSON (use get_buffer_json for that)."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return work_buffer.summary(show_grids=show_grids)


@mcp.tool()
def get_buffer_json() -> str:
    """Return the full JSON of the working buffer (for inspection or manual save)."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return json.dumps(work_buffer.layout, indent=2)


@mcp.tool()
def add_control(control_json: str, tab_index: int = 0,
                position: Optional[list[int]] = None) -> str:
    """Add one control to the buffer. Auto-assigns a unique id and (if no position
    is given) the next free grid slot honoring the control's default span.

    Args:
        control_json: A single control object, e.g. {"type":"gauge","label":"Battery"}.
        tab_index: Which tab to add to (default 0).
        position: Optional [row, col]; omit to auto-place.
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        control = json.loads(control_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(control, dict) or "type" not in control:
        return "control_json must be an object with a 'type'."
    try:
        added = work_buffer.add_control(
            control, tab_index=tab_index, position=position,
            default_span=_default_span_for(control.get("type")))
    except BufferError as e:
        return f"Couldn't add control: {e}"
    span = f" span {added['span']}" if added.get("span") else ""
    return f"Added {added['id']} ({added['type']}) at {added['position']}{span} on tab {tab_index}."


@mcp.tool()
def insert_example(control_id: str, name: str = "", tab_index: int = 0,
                   position: Optional[list[int]] = None) -> str:
    """Drop a documented control example straight into the buffer, with a fresh id
    and a free grid slot. Browse with list_control_examples, then place one here.

    Args:
        control_id: Control type or doc node-id (e.g. 'gauge').
        name: Example name (prefix match); omit for the first example.
        tab_index: Which tab to add to.
        position: Optional [row, col]; omit to auto-place.
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    ex = catalog.find_example(_demos_dir(), control_id, name) if name \
        else (catalog.get_examples(_demos_dir(), control_id) or [None])[0]
    if not ex:
        return f"No matching example for '{control_id}'. Try list_control_examples."
    obj = catalog.example_as_obj(ex)
    if not obj:
        return f"Example '{ex['name']}' didn't parse as a control object."
    try:
        added = work_buffer.add_control(
            obj, tab_index=tab_index, position=position,
            default_span=_default_span_for(obj.get("type")))
    except BufferError as e:
        return f"Couldn't insert example: {e}"
    return f"Inserted '{ex['name']}' as {added['id']} at {added['position']} on tab {tab_index}."


@mcp.tool()
def update_control(control_id: str, patch_json: str) -> str:
    """Merge fields into an existing control. Set a field to null to remove it.

    Args:
        control_id: id of the control to edit.
        patch_json: Object of fields to merge, e.g. {"tint":"#FF0000","style":"ghost"}.
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        patch = json.loads(patch_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(patch, dict):
        return "patch_json must be an object."
    try:
        work_buffer.update_control(control_id, patch)
    except BufferError as e:
        return f"Couldn't update: {e}"
    return f"Updated {control_id}: {', '.join(patch.keys())}."


@mcp.tool()
def remove_control(control_id: str) -> str:
    """Remove a control from the buffer by id."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        work_buffer.remove_control(control_id)
    except BufferError as e:
        return f"Couldn't remove: {e}"
    return f"Removed {control_id}."


@mcp.tool()
def move_control(control_id: str, position: Optional[list[int]] = None,
                 span: Optional[list[int]] = None,
                 tab_index: Optional[int] = None) -> str:
    """Reposition / resize a control, or move it to another tab.

    Args:
        control_id: id of the control.
        position: New [row, col].
        span: New [rowSpan, colSpan].
        tab_index: Move to this tab index.
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        ch = work_buffer.move_control(control_id, position=position, span=span, tab_index=tab_index)
    except BufferError as e:
        return f"Couldn't move: {e}"
    return f"Moved {control_id} → position {ch.get('position')} span {ch.get('span', [1, 1])}."


@mcp.tool()
def add_tab(title: str, icon: str = "square.grid.2x2",
            columns: int = 4, rows: int = 8,
            mode: Optional[str] = None, row_height: Optional[int] = None) -> str:
    """Add a tab to the buffer. Returns its index.

    mode: "grid" (default 2-D — controls span row x col) or "flow" (legacy
    row-banded; use for a full-page map/chat/cardList or a plain form).
    row_height: points per row-unit in 2-D mode (default 56)."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    # Only forward mode/row_height when set, so the default path still works against
    # a pre-0.5.0 carterkit (its add_tab doesn't accept those kwargs).
    extra = {}
    if mode is not None:
        extra["mode"] = mode
    if row_height is not None:
        extra["row_height"] = row_height
    idx = work_buffer.add_tab(title, icon=icon, columns=columns, rows=rows, **extra)
    return f"Added tab {idx}: '{title}' ({rows}x{columns}, mode={mode or 'grid'})."


@mcp.tool()
def add_group(group_json: str, tab_index: int = 0,
              position: Optional[list[int]] = None) -> str:
    """Add a group container to the buffer (auto-placed unless position given).

    Args:
        group_json: A group object, e.g. {"label":"Lights","grid":{"columns":2,"rows":2},"children":[...]}.
        tab_index: Which tab to add to.
        position: Optional [row, col].
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        group = json.loads(group_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(group, dict):
        return "group_json must be an object."
    try:
        added = work_buffer.add_group(group, tab_index=tab_index, position=position)
    except BufferError as e:
        return f"Couldn't add group: {e}"
    return f"Added group {added['id']} at {added['position']} on tab {tab_index}."


@mcp.tool()
def show_grid(tab_index: int = 0) -> str:
    """ASCII occupancy map of a tab's grid — which cells are filled (by which
    control) and which are free. 'See' the layout spatially before editing."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        tab = work_buffer._tab(tab_index)
    except BufferError as e:
        return str(e)
    cols, rows = LayoutBuffer._grid_dims(tab)
    return (f"Tab {tab_index}: {tab.get('title', '?')} ({rows}x{cols})\n"
            + grid.render_grid(tab.get("children", []), cols, rows))


@mcp.tool()
async def push_buffer() -> str:
    """Push the working buffer to the paired device (truthful apply with a rendered
    echo when a single device is paired). Edits stay in the buffer for further work."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    return await _apply_or_broadcast(work_buffer.layout)


@mcp.tool()
async def save_buffer(filename: str = "") -> str:
    """Push the working buffer AND tell the device to persist it to disk.

    Args:
        filename: Optional filename (derived from the layout name if omitted).
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    return await _persist_layout_obj(work_buffer.layout, filename=filename)


@mcp.tool()
def discard_buffer() -> str:
    """Discard the working buffer without sending anything."""
    global work_buffer
    if work_buffer is None:
        return "No active buffer."
    work_buffer = None
    return "Buffer discarded."


@mcp.tool()
def validate_layout(layout_json: str) -> str:
    """Lint a layout against the control schema WITHOUT pushing it: duplicate ids,
    unknown control types, unknown/bad-enum fields, and grid overlaps/out-of-bounds.

    Args:
        layout_json: Complete layout JSON string.
    """
    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    return validate.format_findings(validate.validate_layout(layout, _catalog(include_theme=True)))


@mcp.tool()
def validate_buffer() -> str:
    """Lint the working buffer against the control schema (same checks as
    validate_layout) before you push or save it."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return validate.format_findings(
        validate.validate_layout(work_buffer.layout, _catalog(include_theme=True)))


@mcp.tool()
async def customize_on_phone(control_json: str, timeout: int = 120,
                             add_to_buffer: bool = False, tab_index: int = 0) -> str:
    """Hand a control to the phone's on-screen configurator so the USER customizes it
    by hand (live preview + field editor), then read back exactly what they shaped.

    "Shape it on glass, wire it on the model": the human does the look/feel, you do the
    plumbing. The user taps "Send to Editor" on the phone to return the control.
    Requires a paired device in a live-edit session (connect + scan QR).

    Args:
        control_json: The control to hand off (e.g. {"type":"gauge","label":"Battery"}).
        timeout: Seconds to wait for the user to finish (default 120).
        add_to_buffer: If true, drop the returned control into the working buffer.
        tab_index: Tab to add to when add_to_buffer is set.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    try:
        control = json.loads(control_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(control, dict) or "type" not in control:
        return "control_json must be an object with a 'type'."
    control.setdefault("id", control["type"])

    # Open the configurator on the phone — editor → device works via broadcast.
    try:
        await socket.send("broadcast_request", build_control_edit_request(control))
    except Exception as e:
        return f"Failed to open the configurator on the phone: {e}"

    # The live-edit viewer can't broadcast/route, so it can't push the shaped control
    # back. Poll the device's routed `get-pending-edit` until the user taps "Send to
    # Editor" (mirrors wait_for_device's get_nodes poll). The configurator stashes the
    # result device-side; this routed read drains it.
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max(1, timeout)
    edited = None
    while loop.time() < deadline:
        device_id = await _get_device_id()
        if device_id:
            result = await socket.request("route_msg", {
                "target_id": device_id,
                "type": "get-pending-edit",
                "payload": {},
            }, timeout=5.0)
            if isinstance(result, dict) and isinstance(result.get("control"), dict):
                edited = extract_edited_control(result)
                if edited:
                    break
        await asyncio.sleep(1.0)

    if not edited:
        return (f"No response within {timeout}s — the configurator may not have opened "
                f"(is the device in a live-edit session?) or the user didn't tap 'Send to Editor'.")

    pretty = json.dumps(edited, indent=2)
    if add_to_buffer:
        if work_buffer is None:
            return ("User-shaped control received, but there's no active buffer to add it to "
                    f"(call begin_edit). Control:\n```json\n{pretty}\n```")
        try:
            added = work_buffer.add_control(
                edited, tab_index=tab_index,
                default_span=_default_span_for(edited.get("type")))
        except BufferError as e:
            return f"Received the control but couldn't add it: {e}\n```json\n{pretty}\n```"
        return (f"User-shaped control added as {added['id']} at {added['position']} on tab "
                f"{tab_index}.\n```json\n{pretty}\n```")
    return f"User-shaped control received:\n```json\n{pretty}\n```"


@mcp.tool()
def infer_layout(payload_json: str, name: str = "Inferred", event: str = "telemetry",
                 columns: int = 4, rows: int = 8, into_buffer: bool = True) -> str:
    """Infer a wired first-draft layout from a real JSON payload (paste a sample from
    your service, or use probe_service to capture one). Numbers→gauges, 0..1→progress
    rings, bools→toggles, lat/lng→map, arrays→sparklines/cardlists, strings→labels —
    each bound to the right nested valuePath.

    Args:
        payload_json: A sample JSON object emitted by your service.
        name: Layout name.
        event: The mesh event the controls should listen on (default 'telemetry').
        columns, rows: Grid of the inferred tab.
        into_buffer: Load the result into the working buffer for further editing (default).
    """
    global work_buffer
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(payload, dict):
        return "payload_json must be a JSON object."
    layout = infer.build_layout(payload, name=name, event=event, columns=columns,
                                rows=rows, default_span_fn=_default_span_for)
    if into_buffer:
        work_buffer = LayoutBuffer.from_layout(layout)
        return ("Inferred layout loaded into the buffer (edit, then push_buffer):\n\n"
                + work_buffer.summary())
    return json.dumps(layout, indent=2)


@mcp.tool()
def autotune_gauge(control_id: str, samples_json: str, field_name: str = "") -> str:
    """Tune a buffer gauge from observed samples: sets min/max and color zones at the
    data's percentiles, oriented by whether higher is better (battery) or worse (temp),
    inferred from the field name.

    Args:
        control_id: id of a gauge in the working buffer.
        samples_json: JSON array of numeric samples, e.g. [40,52,61,73,95].
        field_name: Metric name for unit/direction hints (defaults to the id).
    """
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        samples = json.loads(samples_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(samples, list) or not samples:
        return "samples_json must be a non-empty array of numbers."
    found = work_buffer.find(control_id)
    if not found:
        return f"No control '{control_id}' in the buffer."
    ch = found[2]
    if ch.get("type") != "gauge":
        return f"'{control_id}' is a {ch.get('type')}, not a gauge."
    patch = tune.tune_gauge(ch, samples, field_name=field_name or control_id)
    work_buffer.update_control(control_id, patch)
    unit = tune.infer_unit(field_name or control_id)
    return (f"Tuned {control_id}: min {patch['min']}, max {patch['max']}, "
            f"{len(patch['segments'])} color zone(s)" + (f", unit {unit}" if unit else "") + ".")


@mcp.tool()
def list_theme_vibes() -> str:
    """List the built-in theme vibes you can generate with generate_theme."""
    return ("Vibes: " + ", ".join(sorted(theming.VIBES.keys()))
            + ". Or pass an accent hex color for a custom brand theme.")


@mcp.tool()
def generate_theme(description: str = "", accent: str = "",
                   apply_to_buffer: bool = True) -> str:
    """Generate a layout theme from a vibe description or a brand color, and (by
    default) apply it to the working buffer.

    Args:
        description: Free text, e.g. 'fighter jet HUD', 'synthwave neon', 'clean apple'.
        accent: A brand hex color to build the theme around (overrides description).
        apply_to_buffer: Set the buffer layout's theme + accentColor (default true).
    """
    if accent:
        try:
            theme = theming.brand_theme(accent)
        except ValueError as e:
            return f"Bad accent color: {e}"
    elif description:
        theme = theming.theme_for(description)
    else:
        return "Provide a description (e.g. 'synthwave') or an accent hex color."
    pretty = json.dumps(theme, indent=2)
    if apply_to_buffer:
        if work_buffer is None:
            return f"No active buffer to apply to. Theme:\n```json\n{pretty}\n```"
        work_buffer.layout["theme"] = theme
        if theme.get("accentColor"):
            work_buffer.layout["accentColor"] = theme["accentColor"]
        return (f"Applied theme (accent {theme.get('accentColor')}). push_buffer to see it.\n"
                f"```json\n{pretty}\n```")
    return pretty


# ─── Live service tools (probe / simulate / auto-wire) ───────────────────────


@mcp.tool()
async def probe_service(seconds: int = 8, event: str = "broadcast") -> str:
    """Listen to the live mesh and report the service's data schema: which events and
    fields it emits, their value types, ranges, and examples. Run this, then
    autowire_buffer / lint_against_traffic / infer_layout can use what was discovered.

    Args:
        seconds: How long to listen (default 8).
        event: The mesh event to sniff (default 'broadcast', the data channel).
    """
    global last_probe_events, last_probe_frames
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    frames: list = []
    prior = socket.handlers.get(event)

    async def _rec(payload):
        frames.append((event, payload))

    socket.on(event, _rec)
    try:
        await asyncio.sleep(max(1, seconds))
    finally:
        if prior is not None:
            socket.handlers[event] = prior
        else:
            socket.handlers.pop(event, None)
    last_probe_events = probe.aggregate(frames)
    last_probe_frames = [payload for _ev, payload in frames]
    return (f"Observed {len(frames)} frame(s) on '{event}' over {seconds}s.\n\n"
            + probe.format_discovery(last_probe_events))


@mcp.tool()
async def simulate(payload_json: str, event: str = "broadcast") -> str:
    """Emit a data frame onto the channel as if from the service — drives any synced
    controls so gauges move and sparklines fill (live demo / no-backend testing).

    Args:
        payload_json: The frame to emit, e.g. {"battery":82,"cpu_temp":61}.
        event: Event to emit on (default 'broadcast').
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    try:
        await socket.send("broadcast_request" if event == "broadcast" else event, payload)
    except Exception as e:
        return f"Failed to emit: {e}"
    return f"Emitted on '{event}': {json.dumps(payload)[:200]}"


@mcp.tool()
async def set_control_value(control_id: str, value_json: str) -> str:
    """Drive one buffer control live by emitting a frame at its bound valuePath.

    Args:
        control_id: id of a control in the working buffer (must have a listen sync).
        value_json: The value to push, e.g. 82 or "online" or true.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    found = work_buffer.find(control_id)
    if not found:
        return f"No control '{control_id}' in the buffer."
    binding = simulate_lib.control_binding(found[2])
    if not binding:
        return f"'{control_id}' has no listen sync to drive. Wire it first (autowire_buffer)."
    event, path = binding
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        value = value_json  # treat as a bare string
    frame = simulate_lib.build_frame({path: value})
    try:
        await socket.send("broadcast_request" if event == "broadcast" else event, frame)
    except Exception as e:
        return f"Failed: {e}"
    return f"Drove {control_id} → {path}={value!r} on '{event}'."


@mcp.tool()
def autowire_buffer(event: str = "broadcast") -> str:
    """Bind unbound display controls in the buffer to fields discovered by the last
    probe_service run (matched by name). Run probe_service first."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    paths = probe.candidate_paths(last_probe_events)
    if not paths:
        return "No discovered fields yet. Run probe_service first."
    wired = autowire.autowire_layout(work_buffer.layout, paths, event=event)
    if not wired:
        return "Nothing to wire (no unbound display controls matched a discovered field)."
    return "Wired:\n" + "\n".join(f"  - {cid} → {p}" for cid, p in wired)


@mcp.tool()
def lint_against_traffic() -> str:
    """Check the buffer's sync valuePaths against fields seen by the last probe; flags
    bindings that would silently show no data (wrong path / namespace)."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    paths = probe.candidate_paths(last_probe_events)
    if not paths:
        return "No probe data yet. Run probe_service first."
    findings = autowire.live_data_lint(work_buffer.layout, paths)
    if not findings:
        return "✓ Every synced valuePath was seen in observed traffic."
    return "⚠ Paths not seen in traffic:\n" + "\n".join(
        f"  - {f['id']}: {f['detail']}" for f in findings)


@mcp.tool()
def lint_dynamic_traffic() -> str:
    """Check the buffer's `dynamic=` groups against the broadcasts seen by the last
    probe: events that never arrive, payloads missing a `children` array, and injected
    children that won't render (unknown type, bad enum, id clash, off-grid placement).
    Run probe_service first. The dynamic-content counterpart to lint_against_traffic."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    groups = dynamic.dynamic_groups(work_buffer.layout)
    if not groups:
        return "No dynamic groups in the buffer (no group has a `dynamic` event)."
    if not last_probe_frames:
        return "No probe data yet. Run probe_service first."
    findings = dynamic.lint_dynamic_traffic(work_buffer.layout, last_probe_frames)
    if not findings:
        return f"✓ All {len(groups)} dynamic group(s) get well-formed children from observed traffic."
    return validate.format_findings(findings)


# ─── Backend codegen (service stub / adapter) ────────────────────────────────


def _layout_or_buffer(layout_json: str):
    """Resolve a layout from JSON arg, else the working buffer. Returns (layout, error)."""
    if layout_json:
        try:
            return json.loads(layout_json), None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"
    if work_buffer is not None:
        return work_buffer.layout, None
    return None, "No layout given and no active buffer. Pass layout_json or begin_edit."


@mcp.tool()
def generate_service(layout_json: str = "") -> str:
    """Generate a runnable Python MeshSocket service that speaks to a layout — handles
    the events its controls fire and emits the telemetry they listen for. Uses the
    working buffer if no layout_json is given.

    Args:
        layout_json: Optional layout JSON; defaults to the working buffer.
    """
    layout, err = _layout_or_buffer(layout_json)
    if err:
        return err
    return "```python\n" + codegen.generate_service_stub(layout) + "\n```"


@mcp.tool()
def generate_adapter(base_url: str = "https://api.example.com",
                     layout_json: str = "") -> str:
    """Generate a REST-poll → MeshSocket adapter that maps an API's fields to a
    layout's synced valuePaths. Uses the working buffer if no layout_json is given.

    Args:
        base_url: The REST endpoint to poll.
        layout_json: Optional layout JSON; defaults to the working buffer.
    """
    layout, err = _layout_or_buffer(layout_json)
    if err:
        return err
    return "```python\n" + codegen.generate_rest_adapter(layout, base_url=base_url) + "\n```"


@mcp.tool()
async def show_mesh_graph(name: str = "Mesh") -> str:
    """Visualize the live MeshSocket network on the device — builds a graph layout and
    pushes the current roster as animated nodes/edges. "Show me my mesh."

    Replaces the working buffer with the mesh layout.

    Args:
        name: Layout name.
    """
    global work_buffer
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    try:
        result = await socket.request("get_nodes", timeout=3.0)
    except Exception as e:
        return f"Couldn't read the roster: {e}"
    clients = result.get("clients", []) if isinstance(result, dict) else []
    graphdata = meshgraph.roster_to_graph(clients)
    work_buffer = LayoutBuffer.from_layout(meshgraph.build_mesh_layout(name))
    push_result = await _apply_or_broadcast(work_buffer.layout)
    frame = simulate_lib.build_frame({meshgraph.MESH_VALUE_PATH: json.dumps(graphdata)})
    try:
        await socket.send("broadcast_request", frame)
    except Exception:
        pass
    return f"Pushed a mesh graph with {len(graphdata['nodes'])} node(s). {push_result}"


# ─── QA / testing over the mesh ──────────────────────────────────────────────


async def _fetch_control_state(ids: Optional[list[str]] = None) -> Optional[dict]:
    """Raw control values from the device (routed get-control-state)."""
    device_id = await _get_device_id()
    if not device_id:
        return None
    payload = {"ids": ids} if ids else None
    result = await socket.request("route_msg", {
        "target_id": device_id, "type": "get-control-state", "payload": payload,
    }, timeout=5.0)
    if isinstance(result, dict):
        return result.get("values", {})
    return None


@mcp.tool()
async def run_scenario(steps_json: str) -> str:
    """Drive the device through a scripted scenario and assert control states — UI
    testing over the mesh, no XCUITest.

    Each step: {"emit": {...frame...}, "event": "broadcast", "wait": 0.5,
                "expect": {"control_id": expected_value, ...}}.
    `emit` pushes a data frame; after `wait` seconds the named controls are read back
    and compared to `expect`.

    Args:
        steps_json: JSON array of step objects.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(steps, list):
        return "steps_json must be an array of step objects."

    lines, passed, failed = [], 0, 0
    for i, step in enumerate(steps):
        emit = step.get("emit")
        event = step.get("event", "broadcast")
        if emit is not None:
            await socket.send("broadcast_request" if event == "broadcast" else event, emit)
        await asyncio.sleep(step.get("wait", 0.5))
        expect = step.get("expect")
        if not expect:
            lines.append(f"step {i}: emitted")
            continue
        actual = await _fetch_control_state(list(expect.keys())) or {}
        fails = qa.assert_values(expect, actual)
        if fails:
            failed += 1
            lines.append(f"step {i}: FAIL — " + "; ".join(f"{f['id']}: {f['detail']}" for f in fails))
        else:
            passed += 1
            lines.append(f"step {i}: ok ({', '.join(expect.keys())})")
    return f"Scenario: {passed} passed, {failed} failed.\n" + "\n".join(lines)


# ─── Chat agent surface ──────────────────────────────────────────────────────


@mcp.tool()
async def say_in_chat(text: str, sender_name: str = "Claude") -> str:
    """Post a message into a layout's channel chat control as the LLM — the in-app
    agent surface. Requires a paired device showing a chat control.

    Args:
        text: The message to send.
        sender_name: Display name to send as (default 'Claude').
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    msg = chat.build_chat_message(text, sender_name=sender_name, channel=current_channel or "")
    try:
        await socket.send("chat_message", msg)
    except Exception as e:
        return f"Failed to send: {e}"
    return f"Sent to chat as {sender_name}: {text!r}"


@mcp.tool()
async def read_chat(seconds: int = 15) -> str:
    """Listen for incoming chat messages for a while and return them — so the LLM can
    answer what users type in an in-app chat control.

    Args:
        seconds: How long to listen (default 15).
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    msgs: list = []
    prior = socket.handlers.get("chat_message")

    async def _rec(payload):
        m = chat.parse_incoming(payload)
        if m:
            msgs.append(m)

    socket.on("chat_message", _rec)
    try:
        await asyncio.sleep(max(1, seconds))
    finally:
        if prior is not None:
            socket.handlers["chat_message"] = prior
        else:
            socket.handlers.pop("chat_message", None)
    if not msgs:
        return f"No chat messages in {seconds}s."
    return "\n".join(f"{m['sender']}: {m['text']}" for m in msgs)


# ─── Snapshot / revert (experiment fearlessly) ───────────────────────────────


@mcp.tool()
def snapshot_buffer(label: str = "") -> str:
    """Save a snapshot of the working buffer so you can revert later."""
    if work_buffer is None:
        return "No active buffer. Call begin_edit first."
    name = label or f"snapshot {len(buffer_snapshots) + 1}"
    buffer_snapshots.append((name, copy.deepcopy(work_buffer.layout)))
    return f"Saved '{name}' ({len(buffer_snapshots)} snapshot(s) total)."


@mcp.tool()
def list_snapshots() -> str:
    """List saved buffer snapshots."""
    if not buffer_snapshots:
        return "No snapshots yet. Use snapshot_buffer."
    return "\n".join(f"  {i}: {label}" for i, (label, _) in enumerate(buffer_snapshots))


@mcp.tool()
def revert_buffer(index: int = -1) -> str:
    """Restore the working buffer from a snapshot (default the most recent).

    Args:
        index: Snapshot index (see list_snapshots); -1 = latest.
    """
    global work_buffer
    if not buffer_snapshots:
        return "No snapshots to revert to."
    try:
        label, layout = buffer_snapshots[index]
    except IndexError:
        return f"No snapshot at index {index}."
    work_buffer = LayoutBuffer.from_layout(layout)
    return f"Reverted buffer to '{label}'.\n\n" + work_buffer.summary()


async def _poll_peers() -> list[dict]:
    """Fetch the live roster via the `get_nodes` request and refresh the cache.
    Polling is the source of truth: the relay's identify-time roster push races
    client registration, so later joiners never arrive via the push."""
    global peer_list
    if not (socket and socket.is_running):
        return []
    try:
        result = await socket.request("get_nodes", timeout=3.0)
    except Exception:
        return peer_list
    if isinstance(result, dict):
        peer_list = [c for c in result.get("clients", []) if c.get("name") != "carter-mcp-editor"]
    return peer_list


async def _get_device_id() -> Optional[str]:
    """Get the first paired device's server-assigned ID."""
    if peer_list:
        return peer_list[0].get("id")
    if socket and socket.is_running:
        result = await socket.request("get_nodes")
        if result and isinstance(result, dict):
            clients = result.get("clients", [])
            for c in clients:
                if c.get("name") != "carter-mcp-editor":
                    return c.get("id")
    return None


async def _routed_request(verb: str, payload, on_ok):
    """Resolve the paired device, send a routed `verb` request, and format the
    reply via `format_routed_response` (handles not-connected / no-device /
    timeout / relay-error uniformly). `on_ok(result)` renders a successful reply.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."
    device_id = await _get_device_id()
    if not device_id:
        return "No device paired. Scan the QR code in CAR-TER first."
    result = await socket.request("route_msg", {
        "target_id": device_id,
        "type": verb,
        "payload": payload,
    }, timeout=5.0)
    return format_routed_response(result, on_ok=on_ok, verb=verb)


@mcp.tool()
async def get_device_layout(full: bool = False) -> str:
    """Read the layout currently live on the paired device (structural summary).

    Lets you SEE what's on the phone before editing, instead of pushing blind.

    Args:
        full: If true, also include the complete layout JSON (default: summary only).
    """
    return await _routed_request(
        "get-current-layout",
        build_get_layout_request(full=full),
        on_ok=format_current_layout,
    )


@mcp.tool()
async def get_control_state(ids: Optional[list[str]] = None) -> str:
    """Read the current values of controls on the paired device.

    Args:
        ids: Optional list of control ids to filter to. Omit for all controls.
    """
    return await _routed_request(
        "get-control-state",
        build_control_state_request(ids),
        on_ok=format_control_state,
    )


@mcp.tool()
async def get_connection_status() -> str:
    """Read the paired device's relay connection status — whether it's connected,
    on which channel/account, and which events it's listening on."""
    return await _routed_request(
        "get-connection-status",
        None,
        on_ok=format_connection_status,
    )


@mcp.tool()
async def get_device_info() -> str:
    """Read the paired device's CAR-TER app version, build, and protocol version.

    Use this to confirm the phone's installed app understands the control
    definitions you're authoring against. An older app silently drops controls or
    fields it doesn't recognize — see check_sources for a full drift verdict.
    """
    return await _routed_request("get-device-info", None, on_ok=format_device_info)


async def _device_info_if_connected() -> Optional[dict]:
    """Best-effort device version readback for check_sources. Returns None (never
    raises) when no device is paired or the app is too old to answer."""
    if not socket or not socket.is_running:
        return None
    try:
        device_id = await _get_device_id()
    except Exception:
        return None
    if not device_id:
        return None
    # get-device-info is the dedicated verb; get-connection-status may also carry
    # the version fields on newer apps, so try it as a fallback.
    for verb in ("get-device-info", "get-connection-status"):
        try:
            result = await socket.request("route_msg", {
                "target_id": device_id, "type": verb, "payload": None,
            }, timeout=4.0)
        except Exception:
            result = None
        info = extract_device_info(result)
        if info:
            return info
    return None


@mcp.tool()
async def check_sources(refresh: bool = False) -> str:
    """Report where the MCP is pulling truth from, and flag any drift.

    CAR-TER's MCP is a thin local tool layer over live sources: control
    DEFINITIONS come from the website catalog (carterbeaudoin.net/CAR-TER), and
    authoring DEMOS come from the installed carterkit. This shows the catalog
    version/freshness/fingerprint, installed-vs-latest carterkit, and — when a
    device is paired — the phone's app/protocol version, then gives an alignment
    verdict.

    Run this at the start of a session, and again whenever a pushed layout behaves
    unexpectedly, to catch the case where the site, the kit, and the app have
    drifted out of sync.

    Args:
        refresh: Force a fresh fetch of the website catalog + PyPI version,
                 bypassing the local cache.
    """
    device_info = await _device_info_if_connected()
    status = sources.sources_status(device_info=device_info, refresh=refresh)
    return sources.format_status(status)


@mcp.tool()
async def list_device_layouts() -> str:
    """List all layout files stored on the paired device.

    Returns the name, filename, tabs, and accent color of each layout.
    The device must be connected and paired first via connect.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    device_id = await _get_device_id()
    if not device_id:
        return "No device paired. Scan the QR code in CAR-TER first."

    result = await socket.request("route_msg", {
        "target_id": device_id,
        "type": "list-layouts",
        "payload": None,
    }, timeout=5.0)

    if result is None:
        return "Device did not respond (timeout). Make sure it's running a layout with a connection."

    if isinstance(result, dict) and "error" in result:
        return f"Relay error: {result['error']}"

    layouts = result.get("layouts", []) if isinstance(result, dict) else []
    if not layouts:
        return "No layouts found on device."

    lines = []
    for l in layouts:
        name = l.get("name", "?")
        file = l.get("file", "?")
        tabs = l.get("tabs", [])
        tab_count = len(tabs)
        accent = l.get("accentColor", "")
        lines.append(f"- **{name}** (`{file}`) — {tab_count} tabs{f', accent {accent}' if accent else ''}")
    return "\n".join(lines)


@mcp.tool()
async def save_device_layout(layout_json: str, filename: str = None) -> str:
    """Create or update a layout file on the paired device.

    The layout is written to the device's Layouts folder. If a file with the
    same name already exists, it is overwritten.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig with a "name" field.
        filename: Optional filename (e.g. 'my-layout.json'). Derived from the layout name if omitted.
    """
    if not socket or not socket.is_running:
        return "Not connected. Call connect first."

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if "name" not in layout:
        return "Layout must have a 'name' field."

    device_id = await _get_device_id()
    if not device_id:
        return "No device paired. Scan the QR code in CAR-TER first."

    payload = {"layout": layout}
    if filename:
        payload["file"] = filename

    result = await socket.request("route_msg", {
        "target_id": device_id,
        "type": "save-layout",
        "payload": payload,
    }, timeout=5.0)

    if result is None:
        return "Device did not respond (timeout)."

    if isinstance(result, dict) and "error" in result and not result.get("ok"):
        return f"Error: {result['error']}"

    if isinstance(result, dict) and result.get("ok"):
        return f"Layout saved on device as `{result.get('file', '?')}`."

    return f"Unexpected response: {result}"


@mcp.tool()
async def disconnect() -> str:
    """Disconnect from the MeshSocket relay and end the editing session."""
    if not socket or not socket.is_running:
        return "Not currently connected."

    channel = current_channel
    await _cleanup_socket()
    return f"Disconnected from channel '{channel}'."


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
