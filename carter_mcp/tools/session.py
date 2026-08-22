"""Session tools — connect to a relay, pair a device by QR, disconnect."""

from __future__ import annotations

import asyncio
import json
import os

from meshsocket import MeshSocket

from carter_mcp import mesh, state
from carter_mcp.app import mcp


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
    if state.socket and state.socket.is_running:
        return (f"Already connected on channel '{state.current_channel}'. "
                f"Call disconnect first to reconnect.")

    if target == "local":
        port = await mesh.ensure_local_relay()
        lan = mesh.lan_ip()
        state.active_url = f"ws://127.0.0.1:{port}"      # editor dials loopback
        state.active_qr_url = f"ws://{lan}:{port}"        # phone dials the Mac over Wi-Fi
        state.active_token = ""
        transport_note = (f"local relay at {state.active_qr_url} — no gateway, no token "
                          f"(phone must share this Wi-Fi)")
    else:
        state.active_url = url or mesh.RELAY_URL
        if not state.active_url:
            return ("Gateway target needs a relay URL: pass url=… or set "
                    "CARTER_RELAY_URL.")
        state.active_qr_url = state.active_url
        state.active_token = token or mesh.RELAY_TOKEN
        if not state.active_token:
            vurl = validator_url or mesh.VALIDATOR_URL
            if not vurl:
                return ("Gateway target needs a token: pass token=… or set "
                        "CARTER_VALIDATOR_URL (validator_url=…) so I can auto-mint one.")
            try:
                state.active_token = await mesh.mint_token(
                    vurl, f"mcp-{os.urandom(4).hex()}", "CARTER.connectplus.duo")
            except Exception as e:
                return f"Failed to auto-mint a token from {vurl}: {e}"
            transport_note = f"gateway {state.active_url} (auto-minted token)"
        else:
            transport_note = f"gateway {state.active_url}"
    state.current_channel = channel
    state.device_connected_event = asyncio.Event()

    state.socket = MeshSocket(
        url=state.active_url,
        name="carter-mcp-editor",
        auth_token=state.active_token,
        channel=channel,
        role=role,
        can_broadcast=True,
        can_route=True,
        can_monitor=True,
    )

    @state.socket.on("server_client_list")
    async def _on_client_list(payload):
        clients = payload.get("clients", [])
        state.peer_list = [c for c in clients if c.get("name") != "carter-mcp-editor"]
        if state.peer_list:
            state.device_connected_event.set()

    @state.socket.on("control-edit-response")
    async def _on_control_edit_response(payload):
        # The phone tapped "Send to Editor" in the configurator (customize_on_phone).
        if state.control_edit_future and not state.control_edit_future.done():
            state.control_edit_future.set_result(payload)

    state.socket_task = asyncio.create_task(mesh.run_socket())

    try:
        await asyncio.wait_for(state.socket.wait_until_ready(), timeout=10)
    except asyncio.TimeoutError:
        await mesh.cleanup_socket()
        return "Failed to connect to MeshSocket relay within 10 seconds."

    qr_payload = json.dumps({
        "url": state.active_qr_url,
        "token": state.active_token,
        "channel": channel,
        "role": "viewer",
    })

    qr_path = mesh.make_qr_image(qr_payload)

    return f"""Connected — {transport_note}.
Channel '{channel}'. QR saved to: {qr_path}
In CAR-TER, start a Studio Session (Settings → Studio Session → Open Scanner, or
the layout picker's "Start a Studio Session") and scan this QR to pair (the app
pairs by camera scan; there is no paste-the-code field).

QR payload contents (for reference / to regenerate the QR — not something you type
into the app):

```json
{qr_payload}
```

Once paired, use push_layout to send layouts to the device."""


@mcp.tool()
async def show_qr() -> str:
    """Show the QR code for pairing a device to the current editing session.

    Must be connected first via the connect tool.
    """
    if err := mesh.connection_error():
        return err

    qr_payload = json.dumps({
        "url": state.active_qr_url or mesh.RELAY_URL,
        "token": state.active_token or "",
        "channel": state.current_channel,
        "role": "viewer",
    })

    qr_path = mesh.make_qr_image(qr_payload)

    return f"""QR code saved to: {qr_path}
Open that image and scan it in CAR-TER to pair.

Channel: {state.current_channel}"""


@mcp.tool()
async def wait_for_device(timeout: int = 30) -> str:
    """Wait for a device to pair after scanning the QR code.

    Blocks until a device joins the editing channel or the timeout expires.

    Args:
        timeout: Max seconds to wait (default: 30)
    """
    if err := mesh.connection_error():
        return err

    # Poll the roster rather than wait on the identify push: the relay registers a
    # client into self.clients only AFTER broadcasting the roster, so a later joiner
    # (the device — the editor always connects first) is never pushed. `get_nodes`
    # reads the live roster at request time, by which point the device is registered.
    deadline = asyncio.get_event_loop().time() + max(1, timeout)
    while True:
        peers = await mesh.poll_peers()
        if peers:
            names = ", ".join(p.get("name", "unknown") for p in peers)
            return f"Device paired: {names}"
        if asyncio.get_event_loop().time() >= deadline:
            return f"No device connected within {timeout}s. Make sure to scan the QR code in CAR-TER."
        await asyncio.sleep(1.0)


@mcp.tool()
async def disconnect() -> str:
    """Disconnect from the MeshSocket relay and end the editing session."""
    if mesh.connection_error():
        return "Not currently connected."

    channel = state.current_channel
    await mesh.cleanup_socket()
    return f"Disconnected from channel '{channel}'."
