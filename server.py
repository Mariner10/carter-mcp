#!/usr/bin/env python3
"""
CAR-TER MCP Server

Gives an LLM access to CAR-TER control documentation and the ability
to push live layout updates to a paired device over MeshSocket.

Tools:
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
"""

import sys
import os
import json
import asyncio
import glob
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
import qrcode

# MeshSocket library
sys.path.insert(0, "/Users/carter/Desktop/Programming/MeshSocket/Python")
from socketCore import MeshSocket

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONTROL_DOCS_DIR = PROJECT_ROOT / "CAR-TER" / "CAR-TER" / "ControlDocs"
SAMPLE_LAYOUTS_DIR = PROJECT_ROOT / "CAR-TER" / "CAR-TER" / "SampleLayouts"
DOCS_DIR = PROJECT_ROOT / "CAR-TER" / "docs"

RELAY_URL = "wss://carterbeaudoin.com/coms/"
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

# ─── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    "carter",
    instructions="""You are a CAR-TER layout editor. You can read the full control documentation
to understand every available control type, then build and push layouts to a
paired iPhone/iPad in real time over MeshSocket.

Workflow:
1. Use list_controls and get_control_doc to learn the available controls
2. Use get_layout_schema for the overall layout structure
3. Use get_sample_layout to see real examples
4. Use connect to pair with a device (user scans QR code)
5. Build a layout JSON and push_layout to see it live on device
6. Iterate — each push_layout updates the device instantly

Key layout concepts:
- A layout has tabs, each with a grid of children (controls or groups)
- Controls have type, id, position [row, col], and optional span [rows, cols]
- Groups are containers with their own sub-grid
- Connection config enables real-time sync with a server
- Controls can have actions (send commands) and sync (receive data)
""",
)


# ─── Documentation Tools ─────────────────────────────────────────────────────

def _load_doc(doc_id: str) -> Optional[str]:
    path = CONTROL_DOCS_DIR / f"{doc_id}.md"
    if path.exists():
        return path.read_text()
    return None


def _list_doc_files() -> list[dict]:
    results = []
    for f in sorted(CONTROL_DOCS_DIR.glob("*.md")):
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
        available = [f.stem for f in CONTROL_DOCS_DIR.glob("*.md")]
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
  "grid": { "columns": int, "rows": int },
  "children": [ ChildDefinition, ... ]
}
```

## ChildDefinition

Either a control or a group:

### ControlDefinition
```json
{
  "type": "button|toggle|slider|stepper|segmentedControl|picker|datePicker|textInput|colorPicker|label|image|gauge|sparkline|progressRing|map|graph|chat|cardList",
  "id": "string (required, unique)",
  "position": [row, col],
  "span": [rowSpan, colSpan] (default [1, 1]),
  "label": "string",
  "defaultValue": any,
  "icon": "string — SF Symbol",
  "tint": "#hex",
  "hideLabel": bool,
  "hideBackground": bool,

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
  "grid": { "columns": int, "rows": int },
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


# ─── Editor Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def connect(channel: str = "editor", role: str = "editor") -> str:
    """Connect to the MeshSocket relay as a layout editor.

    After connecting, the user needs to scan the returned QR payload on their
    device to pair. Then you can push layouts that update live on the device.

    Args:
        channel: MeshSocket channel name (default: 'editor')
        role: MeshSocket role (default: 'editor')
    """
    global socket, socket_task, current_channel, device_connected_event

    if socket and socket.is_running:
        return f"Already connected on channel '{current_channel}'. Call disconnect first to reconnect."

    current_channel = channel
    device_connected_event = asyncio.Event()

    socket = MeshSocket(
        url=RELAY_URL,
        name="carter-mcp-editor",
        auth_token=RELAY_TOKEN,
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

    socket_task = asyncio.create_task(_run_socket())

    try:
        await asyncio.wait_for(socket.wait_until_ready(), timeout=10)
    except asyncio.TimeoutError:
        await _cleanup_socket()
        return "Failed to connect to MeshSocket relay within 10 seconds."

    qr_payload = json.dumps({
        "url": RELAY_URL,
        "token": RELAY_TOKEN,
        "channel": channel,
        "role": "viewer",
    })

    qr_path = _make_qr_image(qr_payload)

    return f"""Connected to MeshSocket relay on channel '{channel}'.

QR code saved to: {qr_path}
Open that image and scan it in CAR-TER to pair.

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
        "url": RELAY_URL,
        "token": RELAY_TOKEN,
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

    if peer_list:
        names = ", ".join(p.get("name", "unknown") for p in peer_list)
        return f"Device already paired: {names}"

    try:
        await asyncio.wait_for(device_connected_event.wait(), timeout=timeout)
        names = ", ".join(p.get("name", "unknown") for p in peer_list)
        return f"Device paired: {names}"
    except asyncio.TimeoutError:
        return f"No device connected within {timeout}s. Make sure to scan the QR code in CAR-TER."


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

    device_id = await _get_device_id()

    if should_push_routed(device_id):
        # Truthful push: the device applies the layout and echoes back what it
        # actually rendered (or an error), so a broken layout can't masquerade
        # as success.
        result = await socket.request("route_msg", {
            "target_id": device_id,
            "type": "apply-layout",
            "payload": build_apply_layout_request(layout),
        }, timeout=5.0)
        return format_routed_response(result, on_ok=format_apply_result, verb="apply-layout")

    # No resolvable device — broadcast to all viewers (multi-viewer / demo path).
    layout["msg_type"] = "layout-update"
    try:
        await socket.send("broadcast_request", layout)
        tab_count = len(layout.get("tabs", []))
        control_count = sum(
            len(tab.get("children", []))
            for tab in layout.get("tabs", [])
        )
        return f"Layout broadcast to all viewers. {tab_count} tabs, {control_count} top-level controls. (No single device paired, so no render confirmation.)"
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost. Try reconnecting."
    except Exception as e:
        return f"Failed to push layout: {e}"


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

    layout["msg_type"] = "layout-save"

    try:
        await socket.send("broadcast_request", layout)
        return f"Layout save request sent. Device will persist '{layout.get('name', 'Untitled')}' to disk."
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost."
    except Exception as e:
        return f"Failed to save layout: {e}"


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
