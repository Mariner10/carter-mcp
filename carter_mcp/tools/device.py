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
from carter_mcp.layoutio import load_layout_arg


@mcp.tool()
async def push_layout(layout_json: str = "", layout_path: str = "") -> str:
    """Push a layout to the paired device. The device renders it immediately.

    When a single device is paired, this pushes over routed RPC and reports back
    exactly what the device rendered (or why it rejected the layout) — no more
    blind "pushed successfully". With no resolvable device it falls back to a
    broadcast so every paired viewer updates at once.

    For a layout that already exists as a file, pass layout_path — the file is
    read and sent directly without the document transiting the model. Lint it
    first with validate_layout(layout_path=...).

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig.
        layout_path: Path to a layout .json file on disk (alternative to layout_json).
    """
    if err := mesh.connection_error():
        return err

    layout, err = load_layout_arg(layout_json, layout_path,
                                  require=("name", "version", "tabs"))
    if err:
        return err

    return await mesh.apply_or_broadcast(layout)


@mcp.tool()
async def save_layout(layout_json: str = "", layout_path: str = "") -> str:
    """Push a layout and tell the device to save it to disk permanently.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig.
        layout_path: Path to a layout .json file on disk (alternative to layout_json).
    """
    if err := mesh.connection_error():
        return err

    layout, err = load_layout_arg(layout_json, layout_path)
    if err:
        return err

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
async def save_device_layout(layout_json: str = "", filename: str = "",
                             layout_path: str = "") -> str:
    """Create or update a layout file on the paired device.

    The layout is written to the device's Layouts folder. If a file with the
    same name already exists, it is overwritten.

    For a layout that already exists as a file, pass layout_path — the file is
    read and sent directly without the document transiting the model.

    Args:
        layout_json: Complete layout JSON string. Must be valid LayoutConfig with a "name" field.
        filename: Optional filename (e.g. 'my-layout.json'). Derived from the layout name if omitted.
        layout_path: Path to a layout .json file on disk (alternative to layout_json).
    """
    if err := mesh.connection_error():
        return err

    layout, err = load_layout_arg(layout_json, layout_path, require=("name",))
    if err:
        return err

    if not await mesh.get_device_id():
        return mesh.no_device_error()
    return await mesh.persist_layout_obj(layout, filename=filename)


@mcp.tool()
async def watch_traffic(seconds: int = 10, filter: str = "") -> str:
    """Watch the live data flowing into the paired device's layout for a window and
    return a per-control digest (last value + rate).

    The device is the wire tap: it forwards each layout-socket sync value it
    dispatches (post-decrypt, pre-render) over the studio link, so you see exactly
    what the layout's server/hub is feeding each control — including E2EE-room
    traffic — without this editor ever holding room credentials or keys. Sampled
    device-side (>=250ms per control) and values truncated to 1KB.

    Use it to verify a room layout is actually receiving data ("waiting for
    server"?), to see real field values before wiring, or to confirm a hub came
    alive after a push. Requires a Studio Session (connect + QR) and an app that
    speaks protocol v2+ (older apps time out on the verb).

    Args:
        seconds: How long to collect (default 10, clamped 1-60).
        filter: Optional control-id substring — only matching controls are forwarded.
    """
    if err := mesh.connection_error():
        return err
    device_id = await mesh.get_device_id()
    if not device_id:
        return mesh.no_device_error()
    seconds = max(1, min(60, seconds))

    async def _tap(enable: bool):
        return await state.socket.request("route_msg", {
            "target_id": device_id,
            "type": "watch-traffic",
            "payload": protocol.build_watch_traffic_request(enable, filter=filter),
        }, timeout=5.0)

    reply = await _tap(True)
    if reply is None:
        return ("Device did not respond to 'watch-traffic' (timeout) — the installed "
                "app likely predates the wire tap (needs protocol v2+). "
                "Run check_sources for a drift verdict.")
    if not (isinstance(reply, dict) and reply.get("ok")):
        return f"Device refused watch-traffic: {reply}"

    # Collect forwarded studio.traffic broadcasts for the window. One handler per
    # event on this socket — save and restore any prior broadcast handler, the
    # same dance probe_service does.
    frames: list = []
    prior = state.socket.handlers.get("broadcast")

    async def _collect(payload):
        rec = protocol.extract_traffic_frame(payload)
        if rec is not None:
            frames.append(rec)
        if prior is not None:
            await prior(payload)  # don't starve a standing listener mid-window

    state.socket.on("broadcast", _collect)
    try:
        await asyncio.sleep(seconds)
    finally:
        if prior is not None:
            state.socket.handlers["broadcast"] = prior
        else:
            state.socket.handlers.pop("broadcast", None)
        try:
            await _tap(False)   # always switch the tap off, even if collection died
        except Exception:
            pass

    digest = protocol.aggregate_traffic(frames, seconds)
    return protocol.format_traffic_digest(digest, seconds, len(frames))


PROVISION_ROLES = ("hub", "controller", "viewer")
# Outlives the device's 60s approval-sheet timeout, so a last-second Approve's
# mint still lands inside the poll window.
PROVISION_WAIT_SECONDS = 70


def write_credential_file(out_path: str, credential: dict,
                          overwrite: bool = False) -> tuple:
    """Write the credential JSON verbatim to `out_path` with mode 0600, creating
    parent dirs. Returns (warning, error) — exactly one may be non-None, or neither.
    Refuses to overwrite unless asked; when overwriting a same-channel credential
    whose `k` differs, the warning is the loud room-key-changed banner."""
    import os
    path = os.path.abspath(os.path.expanduser(out_path))
    warning = None
    if os.path.exists(path):
        if not overwrite:
            return (None, f"Refusing to overwrite existing file {path} — pass "
                          f"overwrite=True if you really mean to replace it.")
        try:
            with open(path, "r") as f:
                warning = protocol.provision_key_change_warning(f.read(), credential)
        except OSError:
            pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = json.dumps(credential, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # a pre-existing file keeps its old mode otherwise
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    return (warning, None)


@mcp.tool()
async def provision_device(channel: str, out_path: str, role: str = "hub",
                           reuse_room_key: bool = True, overwrite: bool = False) -> str:
    """Mint a Hub credential for a channel through the paired phone — human-approved,
    with the secret going straight to a file on THIS machine, never through the model.

    The phone shows an approval sheet (channel, role, who's asking); the owner's tap
    gates the mint. On approve, the device mints exactly as its Add Device flow does
    and hands the credential back over the local studio link. This tool writes it
    verbatim to `out_path` (mode 0600) and reports only non-secret metadata — the
    token, refresh secret, and room key NEVER appear in the result. Point the hub at
    the file (e.g. `Hub("layout.json", connection="device.json")`).

    By default the mint reuses the channel's existing E2EE room key when the device
    knows one (layouts pin `connection.e2eeKey` — a changed key breaks them), and
    only generates a fresh key for a channel that has none.

    Requires a Studio Session (connect + QR) and an active Connect+ session on the
    phone. The approval sheet times out after 60s.

    Args:
        channel: The channel to pin the credential to (required, non-empty).
        out_path: File to write the credential JSON to (e.g. ./device.json).
        role: What the machine may do — hub (drive with data), controller, or viewer.
        reuse_room_key: Reuse the channel's existing E2EE key when known (default).
                        False forces a brand-new key.
        overwrite: Allow replacing an existing file at out_path (default: refuse).
    """
    import os
    if err := mesh.connection_error():
        return err
    if not channel.strip():
        return "channel is required and must be non-empty."
    if role not in PROVISION_ROLES:
        return f"role must be one of {', '.join(PROVISION_ROLES)}."
    # Fail the path check BEFORE bothering the owner with an approval sheet.
    if os.path.exists(os.path.abspath(os.path.expanduser(out_path))) and not overwrite:
        return (f"Refusing to overwrite existing file {out_path} — pass "
                f"overwrite=True if you really mean to replace it.")
    device_id = await mesh.get_device_id()
    if not device_id:
        return mesh.no_device_error()

    arm = await state.socket.request("route_msg", {
        "target_id": device_id,
        "type": "provision-device",
        "payload": protocol.build_provision_request(channel, role, reuse_room_key),
    }, timeout=5.0)
    if arm is None:
        return ("Device did not respond to 'provision-device' (timeout) — the "
                "installed app likely predates the verb. Run check_sources.")
    if msg := protocol.provision_error_message(arm):
        return msg
    request_id = protocol.extract_provision_pending(arm)
    if not request_id:
        return f"Unexpected provision-device reply: {arm}"

    # The relay caps a routed reply at ~5s, so the approval can't ride the arming
    # request — poll the result verb until the owner decides (same pull pattern as
    # customize_on_phone / get-pending-edit).
    loop = asyncio.get_event_loop()
    deadline = loop.time() + PROVISION_WAIT_SECONDS
    result = None
    while loop.time() < deadline:
        reply = await state.socket.request("route_msg", {
            "target_id": device_id,
            "type": "get-provision-result",
            "payload": protocol.build_provision_result_request(request_id),
        }, timeout=5.0)
        if isinstance(reply, dict) and reply.get("status") == "pending":
            await asyncio.sleep(1.0)
            continue
        result = reply
        break
    if result is None:
        return (f"No decision within {PROVISION_WAIT_SECONDS}s — the approval sheet "
                f"should have timed out device-side; nothing was minted as far as "
                f"this editor knows.")
    if msg := protocol.provision_error_message(result):
        return msg
    credential = result.get("credential") if isinstance(result, dict) else None
    if not isinstance(credential, dict):
        return f"Unexpected provision result shape (no credential object)."

    warning, err = write_credential_file(out_path, credential, overwrite=overwrite)
    if err:
        return err + " (The credential was minted but NOT saved — the device shows it nowhere else, so re-provision.)"

    summary = protocol.format_provision_summary(
        credential, os.path.abspath(os.path.expanduser(out_path)),
        result.get("key_reuse", ""), expires_at=result.get("expires_at"),
        warning=warning)
    # Belt-and-braces: no secret value may ever leak into the result text.
    for field in protocol.PROVISION_SECRET_FIELDS:
        value = credential.get(field)
        if isinstance(value, str) and value and value in summary:
            return ("Internal redaction error: refusing to return a result that "
                    "contains a credential secret. The file was written correctly.")
    return summary


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
