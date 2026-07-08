"""Read-back / truthful-push wire protocol — pure logic, no I/O.

Routed RPC over `route_msg` — the device answers these via handle(event:).
Everything here is pure (build a request payload, format a response) so the
protocol shapes and response formatting are testable without a live device;
the async tools in `tools/` are thin wrappers. Wire contract: PROTOCOL.md.
"""

from __future__ import annotations

import json
from typing import Optional


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


def format_summary(summary: dict) -> str:
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


# ─── get-current-layout ───────────────────────────────────────────────────────

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
    body = format_summary(summary)
    full = resp.get("layout")
    if full is not None:
        body += "\n\n```json\n" + json.dumps(full, indent=2) + "\n```"
    return header + body


# ─── get-control-state ────────────────────────────────────────────────────────

def build_control_state_request(ids=None):
    if ids:
        return {"ids": ids}
    return None


def format_control_state(resp: dict) -> str:
    values = resp.get("values", {})
    if not values:
        return "Device reports no control values."
    return "\n".join(f"- {k}: {json.dumps(v)}" for k, v in values.items())


# ─── get-connection-status ────────────────────────────────────────────────────

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


# ─── get-device-info ──────────────────────────────────────────────────────────
#
# App version / protocol / catalog fingerprint — for drift checks. The device
# echoes which CAR-TER app build the user is running so the model can confirm
# the phone understands the control definitions it's authoring against. An older
# app may simply drop controls or fields it doesn't know; comparing the device's
# reported protocol/fingerprint to the website catalog catches that early. The
# Swift responder is a separate device-side track (see PROTOCOL.md); the MCP
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


# ─── apply-layout (truthful push) ─────────────────────────────────────────────

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
    return "Device rendered the layout:\n" + format_summary(rendered)


def should_push_routed(device_id) -> bool:
    """Push truthfully (routed apply-layout, gets a rendered echo) when a single
    device is resolvable; else broadcast layout-update to all viewers."""
    return device_id is not None


# ─── Control-edit handoff ─────────────────────────────────────────────────────
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
