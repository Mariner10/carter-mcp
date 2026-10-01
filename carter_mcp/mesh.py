"""Connection runtime: local relay, QR pairing, token mint, routed RPC plumbing.

Everything that touches the live MeshSocket session lives here — starting and
tearing down the editor socket, the zero-config in-process relay, resolving the
paired device, and the shared request/push paths the device tools are thin
wrappers over. The pure protocol shapes live in `protocol`; the mutable session
handles live in `state`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from typing import Optional

import qrcode

from carter_mcp import protocol, state

# ─── Configuration (env-overridable, all optional) ────────────────────────────

RELAY_URL = os.environ.get("CARTER_RELAY_URL", "")
RELAY_TOKEN = os.environ.get("CARTER_MESH_TOKEN", "")
LOCAL_RELAY_PORT = int(os.environ.get("CARTER_LOCAL_RELAY_PORT", "8765"))
# Optional dev-validator base URL, used only by the gateway path to auto-mint a
# token so authoring never needs a hand-pasted one.
VALIDATOR_URL = os.environ.get("CARTER_VALIDATOR_URL", "")

QR_IMAGE_PATH = Path("/tmp/carter-pairing-qr.png")


# ─── QR + network helpers ─────────────────────────────────────────────────────

def make_qr_image(data: str) -> Path:
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(QR_IMAGE_PATH))
    return QR_IMAGE_PATH


def lan_ip() -> str:
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


async def ensure_local_relay() -> int:
    """Start an in-process, auth-free MeshSocket relay once; return its port.
    The zero-config authoring transport — no gateway, no token, no AWS."""
    if state.local_relay is not None and state.local_relay_task and not state.local_relay_task.done():
        return LOCAL_RELAY_PORT
    from socket_server import MeshServer
    ready = asyncio.Event()
    state.local_relay = MeshServer(
        host="0.0.0.0",
        port=LOCAL_RELAY_PORT,
        auth_handler=lambda token, ip: True,  # authoring relay accepts any pairing
        on_startup=ready.set,
    )
    state.local_relay_task = asyncio.create_task(state.local_relay.start())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(0.3)  # on_startup fires just before serve() binds the port
    return LOCAL_RELAY_PORT


async def mint_token(validator_url: str, account: str, product: str) -> str:
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


# ─── Socket lifecycle ─────────────────────────────────────────────────────────

async def run_socket() -> None:
    """Task body that owns the editor socket's lifetime."""
    try:
        await state.socket.start()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[carter-mcp] socket error: {e}", file=sys.stderr)


async def cleanup_socket() -> None:
    if state.socket:
        try:
            await state.socket.stop()
        except Exception:
            pass
    if state.socket_task:
        state.socket_task.cancel()
        try:
            await state.socket_task
        except (asyncio.CancelledError, Exception):
            pass
    state.socket = None
    state.socket_task = None
    state.current_channel = None
    state.peer_list = []
    state.device_connected_event = None
    clear_acked(support=True)


def clear_acked(support: bool = False) -> None:
    """Forget the device-acknowledged patch base (and, with `support`, the cached
    patch-layout capability) — the next push_buffer is a full apply."""
    state.acked_layout = None
    state.acked_hash = None
    state.acked_device_id = None
    if support:
        state.patch_support = None
        state.patch_support_device = None


def record_ack(layout: dict, device_id, reply_hash=None) -> None:
    """Remember `layout` as the device's acknowledged base. The device-reported
    hash wins; else the canonical carterkit hash of what we sent."""
    state.acked_layout = copy.deepcopy(layout)
    state.acked_device_id = device_id
    if isinstance(reply_hash, str) and reply_hash:
        state.acked_hash = reply_hash
    else:
        try:
            from carterkit import patch as _patch
            state.acked_hash = _patch.layout_hash(layout)
        except Exception:
            state.acked_hash = None


def connection_error() -> Optional[str]:
    """The shared not-connected guard: an error string, or None when connected."""
    if not state.socket or not state.socket.is_running:
        return "Not connected. Call connect first."
    return None


# ─── Peer resolution ──────────────────────────────────────────────────────────

async def poll_peers() -> list[dict]:
    """Fetch the live roster via the `get_nodes` request and refresh the cache.
    Polling is the source of truth: the relay's identify-time roster push races
    client registration, so later joiners never arrive via the push."""
    if not (state.socket and state.socket.is_running):
        return []
    try:
        result = await state.socket.request("get_nodes", timeout=3.0)
    except Exception:
        return state.peer_list
    if isinstance(result, dict):
        state.peer_list = [c for c in result.get("clients", [])
                           if c.get("name") != "carter-mcp-editor"]
    return state.peer_list


async def get_device_id() -> Optional[str]:
    """Get the first paired device's server-assigned ID."""
    if state.peer_list:
        return state.peer_list[0].get("id")
    if state.socket and state.socket.is_running:
        result = await state.socket.request("get_nodes")
        if result and isinstance(result, dict):
            for c in result.get("clients", []):
                if c.get("name") != "carter-mcp-editor":
                    return c.get("id")
    return None


def no_device_error() -> str:
    """What to say when the editor is up but nothing has scanned the QR yet.
    Distinguishes the editor's own (fine) relay connection from device pairing —
    'no device' right after a successful connect otherwise reads like a failure."""
    channel = state.current_channel or "editor"
    return (f"Editor is connected to the relay (channel '{channel}'), but no device "
            f"has paired yet. Have the user scan the QR in CAR-TER (show_qr to "
            f"re-display it), or call wait_for_device to block until it joins.")


# ─── Routed RPC + push paths ──────────────────────────────────────────────────

async def routed_request(verb: str, payload, on_ok, timeout: float = 5.0) -> str:
    """Resolve the paired device, send a routed `verb` request, and format the
    reply via `protocol.format_routed_response` (handles not-connected / no-device /
    timeout / relay-error uniformly). `on_ok(result)` renders a successful reply.

    `timeout` is passed straight to the socket request. The relay caps a routed
    reply at about 5 s, so callers should never ask for more than 5.0; slow
    verbs use the arm-and-poll pattern (provision-device) instead.
    """
    if err := connection_error():
        return err
    device_id = await get_device_id()
    if not device_id:
        return no_device_error()
    result = await state.socket.request("route_msg", {
        "target_id": device_id,
        "type": verb,
        "payload": payload,
    }, timeout=timeout)
    return protocol.format_routed_response(result, on_ok=on_ok, verb=verb)


async def apply_routed(layout: dict, device_id: str):
    """Routed apply-layout to `device_id` → (tool text, raw reply). An ok reply
    records `layout` as the acknowledged patch base (device hash preferred); any
    other outcome forgets the base, since the device's layout is then unknown."""
    result = await state.socket.request("route_msg", {
        "target_id": device_id,
        "type": "apply-layout",
        "payload": protocol.build_apply_layout_request(layout),
    }, timeout=5.0)
    if isinstance(result, dict) and result.get("ok"):
        record_ack(layout, device_id, result.get("hash"))
    else:
        clear_acked()
    text = protocol.format_routed_response(
        result, on_ok=protocol.format_apply_result, verb="apply-layout")
    return text, result


async def apply_or_broadcast(layout: dict) -> str:
    """Send a layout to the device truthfully when a single device is resolvable
    (routed apply-layout with a rendered echo), else broadcast to all viewers.
    Shared by push_layout and push_buffer."""
    device_id = await get_device_id()
    if protocol.should_push_routed(device_id):
        text, _raw = await apply_routed(layout, device_id)
        return text

    # A broadcast is never acknowledged, so the device's base is unknown now.
    clear_acked()
    payload = dict(layout)
    payload["msg_type"] = "layout-update"
    try:
        await state.socket.send("broadcast_request", payload)
        tab_count = len(layout.get("tabs", []))
        control_count = sum(len(tab.get("children", [])) for tab in layout.get("tabs", []))
        return (f"Layout broadcast to all viewers. {tab_count} tabs, {control_count} "
                f"top-level controls. (No single device paired, so no render confirmation.)")
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost. Try reconnecting."
    except Exception as e:
        return f"Failed to push layout: {e}"


async def device_patch_support(device_id: str) -> Optional[bool]:
    """Does `device_id` answer patch-layout? Reads the RAW get-device-info reply
    (so a `features` list counts even before extract_device_info keeps it) and
    caches a definite answer per pairing. None = unknown (no/odd reply)."""
    if state.patch_support_device == device_id and state.patch_support is not None:
        return state.patch_support
    try:
        result = await state.socket.request("route_msg", {
            "target_id": device_id, "type": "get-device-info", "payload": None,
        }, timeout=4.0)
    except Exception:
        result = None
    info = result if isinstance(result, dict) and "error" not in result else None
    supported = protocol.device_supports(info, "patch-layout")
    if supported is not None:
        state.patch_support = supported
        state.patch_support_device = device_id
    return supported


async def _try_patch(layout: dict, device_id: str):
    """One patch-layout attempt → (tool text, None) on success, or (None, reason)
    when the caller must full-push instead. Never sends more than one request."""
    if state.acked_device_id != device_id:
        clear_acked()   # a different device paired since the last ack
    if state.acked_layout is None or not state.acked_hash:
        return None, "no acknowledged base yet"
    supported = await device_patch_support(device_id)
    if supported is None:
        return None, "device did not report its capabilities"
    if not supported:
        return None, f"older app — {protocol.too_old_message('patch-layout')}"
    try:
        from carterkit import patch as _patch
    except ImportError:
        return None, "installed carterkit has no carterkit.patch (pip install -U carterkit)"
    ops = _patch.diff(state.acked_layout, layout)
    if ops is None:
        return None, "change not expressible as a patch (tab structure / ids)"
    try:
        payload = protocol.build_patch_layout_request(state.acked_hash, ops, allow_empty=True)
    except ValueError as e:
        return None, f"patch op rejected locally: {e}"
    try:
        result = await state.socket.request("route_msg", {
            "target_id": device_id, "type": "patch-layout", "payload": payload,
        }, timeout=5.0)
    except Exception as e:
        return None, f"relay error on patch-layout: {e}"
    if result is None:
        return None, "device did not answer patch-layout"
    if protocol.is_stale_base(result):
        return None, "stale-base (the device's layout changed since the last push)"
    if isinstance(result, dict) and result.get("ok"):
        record_ack(layout, device_id, result.get("hash"))
        n = result.get("applied", len(ops))
        reload = result.get("reload")
        how = {"hot": "hot reload (values kept)",
               "full": "full reload (wiring changed; values reset)"}.get(
                   reload, f"reload {reload or '?'}")
        head = f"Patched {n} op{'s' if n != 1 else ''} · {how}"
        rest = protocol.format_patch_result(result).split("\n")[1:]
        return "\n".join([head] + rest), None
    if isinstance(result, dict) and "error" in result:
        detail = protocol.studio_error_message(result, "patch-layout") or result["error"]
        return None, f"patch-layout error: {detail}"
    return None, f"unexpected patch-layout reply: {result!r}"


async def patch_push(layout: dict, full: bool = False) -> str:
    """push_buffer's transport (K12): send a small `patch-layout` against the last
    device-acknowledged layout (hot reload keeps live values; dodges the 256 KB
    frame cap), else exactly one full apply-layout, prefixed with why.

    Patches only when a single device is paired, it advertises patch-layout, a
    base was acknowledged on this pairing and `carterkit.patch.diff` can express
    the change (an empty diff is still sent, to verify the base)."""
    if full:
        reason = "full=True"
    else:
        device_id = await get_device_id()
        if not protocol.should_push_routed(device_id):
            reason = "no single device paired"
        else:
            text, reason = await _try_patch(layout, device_id)
            if text is not None:
                return text
    return f"Full push · full reload ({reason})\n" + await apply_or_broadcast(layout)


async def save_layout_obj(layout: dict) -> str:
    """Broadcast a layout-save so the device persists the layout to disk."""
    payload = dict(layout)
    payload["msg_type"] = "layout-save"
    try:
        await state.socket.send("broadcast_request", payload)
        return f"Save request sent. Device will persist '{layout.get('name', 'Untitled')}' to disk."
    except ConnectionError:
        return "Failed to send — MeshSocket connection lost."
    except Exception as e:
        return f"Failed to save layout: {e}"


async def persist_layout_obj(layout: dict, filename: str = "") -> str:
    """Persist a layout to the device's disk — routed save-layout (with a file name
    and an ok/file reply) when a device is resolvable, else a broadcast layout-save."""
    device_id = await get_device_id()
    if device_id:
        payload: dict = {"layout": layout}
        if filename:
            payload["file"] = filename
        result = await state.socket.request("route_msg", {
            "target_id": device_id, "type": "save-layout", "payload": payload,
        }, timeout=5.0)
        if result is None:
            return "Device did not respond to save (timeout)."
        if isinstance(result, dict) and result.get("ok"):
            return f"Saved on device as `{result.get('file', '?')}`."
        if isinstance(result, dict) and "error" in result:
            return f"Save error: {result['error']}"
        return f"Unexpected save response: {result}"
    return await save_layout_obj(layout)


async def fetch_device_layout_obj() -> Optional[dict]:
    """Pull the device's live layout (full) as a raw dict, for begin_edit(from_device)."""
    device_id = await get_device_id()
    if not device_id:
        return None
    result = await state.socket.request("route_msg", {
        "target_id": device_id,
        "type": "get-current-layout",
        "payload": {"include": "full"},
    }, timeout=5.0)
    if isinstance(result, dict):
        return result.get("layout")
    return None


async def fetch_saved_layout_obj(file: str = "", name: str = "") -> Optional[dict]:
    """Pull a SAVED layout off the device by filename (from list-layouts) or display
    name, as a raw dict — for begin_edit(from_device_file). Unlike the live-layout
    fetch this reads the file the phone's Layout Editor saved, active or not."""
    device_id = await get_device_id()
    if not device_id:
        return None
    payload: dict = {}
    if file:
        payload["file"] = file
    if name:
        payload["name"] = name
    result = await state.socket.request("route_msg", {
        "target_id": device_id,
        "type": "get-layout",
        "payload": payload,
    }, timeout=5.0)
    if isinstance(result, dict) and result.get("ok"):
        return result.get("layout")
    return None


async def fetch_control_state(ids: Optional[list[str]] = None) -> Optional[dict]:
    """Raw control values from the device (routed get-control-state)."""
    device_id = await get_device_id()
    if not device_id:
        return None
    payload = {"ids": ids} if ids else None
    result = await state.socket.request("route_msg", {
        "target_id": device_id, "type": "get-control-state", "payload": payload,
    }, timeout=5.0)
    if isinstance(result, dict):
        return result.get("values", {})
    return None


async def device_info_if_connected() -> Optional[dict]:
    """Best-effort device version readback for check_sources. Returns None (never
    raises) when no device is paired or the app is too old to answer."""
    if not state.socket or not state.socket.is_running:
        return None
    try:
        device_id = await get_device_id()
    except Exception:
        return None
    if not device_id:
        return None
    # get-device-info is the dedicated verb; get-connection-status may also carry
    # the version fields on newer apps, so try it as a fallback.
    for verb in ("get-device-info", "get-connection-status"):
        try:
            result = await state.socket.request("route_msg", {
                "target_id": device_id, "type": verb, "payload": None,
            }, timeout=4.0)
        except Exception:
            result = None
        info = protocol.extract_device_info(result)
        if info:
            return info
    return None
