"""Device tools — push and save layouts, read the phone back, check drift.

The read-back tools are thin async wrappers over the pure builders/formatters in
`protocol` (tested without a device) and the routed-RPC plumbing in `mesh`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from carterkit.buffer import BufferError

from carter_mcp import content, mesh, protocol, sources, state
from carter_mcp.app import mcp


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
    if err := mesh.connection_error():
        return err

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    required = ["name", "version", "tabs"]
    missing = [f for f in required if f not in layout]
    if missing:
        return f"Layout missing required fields: {', '.join(missing)}"

    return await mesh.apply_or_broadcast(layout)


@mcp.tool()
async def save_layout(layout_json: str) -> str:
    """Push a layout and tell the device to save it to disk permanently.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig.
    """
    if err := mesh.connection_error():
        return err

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    return await mesh.save_layout_obj(layout)


@mcp.tool()
async def push_buffer() -> str:
    """Push the working buffer to the paired device (truthful apply with a rendered
    echo when a single device is paired). Edits stay in the buffer for further work."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    if err := mesh.connection_error():
        return err
    return await mesh.apply_or_broadcast(state.work_buffer.layout)


@mcp.tool()
async def save_buffer(filename: str = "") -> str:
    """Push the working buffer AND tell the device to persist it to disk.

    Args:
        filename: Optional filename (derived from the layout name if omitted).
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    if err := mesh.connection_error():
        return err
    return await mesh.persist_layout_obj(state.work_buffer.layout, filename=filename)


@mcp.tool()
async def get_device_layout(full: bool = False) -> str:
    """Read the layout currently live on the paired device (structural summary).

    Lets you SEE what's on the phone before editing, instead of pushing blind.

    Args:
        full: If true, also include the complete layout JSON (default: summary only).
    """
    return await mesh.routed_request(
        "get-current-layout",
        protocol.build_get_layout_request(full=full),
        on_ok=protocol.format_current_layout,
    )


@mcp.tool()
async def get_control_state(ids: Optional[list[str]] = None) -> str:
    """Read the current values of controls on the paired device.

    Args:
        ids: Optional list of control ids to filter to. Omit for all controls.
    """
    return await mesh.routed_request(
        "get-control-state",
        protocol.build_control_state_request(ids),
        on_ok=protocol.format_control_state,
    )


@mcp.tool()
async def get_connection_status() -> str:
    """Read the paired device's relay connection status — whether it's connected,
    on which channel/account, and which events it's listening on."""
    return await mesh.routed_request(
        "get-connection-status",
        None,
        on_ok=protocol.format_connection_status,
    )


@mcp.tool()
async def get_device_info() -> str:
    """Read the paired device's CAR-TER app version, build, and protocol version.

    Use this to confirm the phone's installed app understands the control
    definitions you're authoring against. An older app silently drops controls or
    fields it doesn't recognize — see check_sources for a full drift verdict.
    """
    return await mesh.routed_request("get-device-info", None,
                                     on_ok=protocol.format_device_info)


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
    device_info = await mesh.device_info_if_connected()
    status = sources.sources_status(device_info=device_info, refresh=refresh)
    return sources.format_status(status)


@mcp.tool()
async def list_device_layouts() -> str:
    """List all layout files stored on the paired device.

    Returns the name, filename, tabs, and accent color of each layout.
    The device must be connected and paired first via connect.
    """
    def _fmt(result: dict) -> str:
        layouts = result.get("layouts", []) if isinstance(result, dict) else []
        if not layouts:
            return "No layouts found on device."
        lines = []
        for l in layouts:
            name = l.get("name", "?")
            file = l.get("file", "?")
            tab_count = len(l.get("tabs", []))
            accent = l.get("accentColor", "")
            lines.append(f"- **{name}** (`{file}`) — {tab_count} tabs"
                         + (f", accent {accent}" if accent else ""))
        return "\n".join(lines)

    return await mesh.routed_request("list-layouts", None, on_ok=_fmt)


@mcp.tool()
async def save_device_layout(layout_json: str, filename: str = "") -> str:
    """Create or update a layout file on the paired device.

    The layout is written to the device's Layouts folder. If a file with the
    same name already exists, it is overwritten.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig with a "name" field.
        filename: Optional filename (e.g. 'my-layout.json'). Derived from the layout name if omitted.
    """
    if err := mesh.connection_error():
        return err

    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if "name" not in layout:
        return "Layout must have a 'name' field."

    if not await mesh.get_device_id():
        return mesh.no_device_error()
    return await mesh.persist_layout_obj(layout, filename=filename)


@mcp.tool()
async def customize_on_phone(control_json: str, timeout: int = 120,
                             add_to_buffer: bool = False, tab_index: int = 0) -> str:
    """Hand a control to the phone's on-screen configurator so the USER customizes it
    by hand (live preview + field editor), then read back exactly what they shaped.

    "Shape it on glass, wire it on the model": the human does the look/feel, you do the
    plumbing. The user taps "Send to Editor" on the phone to return the control.
    Requires a paired device in a Studio Session (connect + scan QR).

    Args:
        control_json: The control to hand off (e.g. {"type":"gauge","label":"Battery"}).
        timeout: Seconds to wait for the user to finish (default 120).
        add_to_buffer: If true, drop the returned control into the working buffer.
        tab_index: Tab to add to when add_to_buffer is set.
    """
    if err := mesh.connection_error():
        return err
    try:
        control = json.loads(control_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(control, dict) or "type" not in control:
        return "control_json must be an object with a 'type'."
    control.setdefault("id", control["type"])

    # Open the configurator on the phone — editor → device works via broadcast.
    try:
        await state.socket.send("broadcast_request",
                                protocol.build_control_edit_request(control))
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
        device_id = await mesh.get_device_id()
        if device_id:
            result = await state.socket.request("route_msg", {
                "target_id": device_id,
                "type": "get-pending-edit",
                "payload": {},
            }, timeout=5.0)
            if isinstance(result, dict) and isinstance(result.get("control"), dict):
                edited = protocol.extract_edited_control(result)
                if edited:
                    break
        await asyncio.sleep(1.0)

    if not edited:
        return (f"No response within {timeout}s — the configurator may not have opened "
                f"(is the device in a Studio Session?) or the user didn't tap 'Send to Editor'.")

    pretty = json.dumps(edited, indent=2)
    if add_to_buffer:
        if state.work_buffer is None:
            return ("User-shaped control received, but there's no active buffer to add it to "
                    f"(call begin_edit). Control:\n```json\n{pretty}\n```")
        try:
            added = state.work_buffer.add_control(
                edited, tab_index=tab_index,
                default_span=content.default_span_for(edited.get("type")))
        except BufferError as e:
            return f"Received the control but couldn't add it: {e}\n```json\n{pretty}\n```"
        return (f"User-shaped control added as {added['id']} at {added['position']} on tab "
                f"{tab_index}.\n```json\n{pretty}\n```")
    return f"User-shaped control received:\n```json\n{pretty}\n```"
