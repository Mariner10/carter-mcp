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
        header_bits.append("Studio Session")
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
#
# Since the studio/layout connection split (device protocol v2), the top-level
# fields describe the LAYOUT link — the layout's own connection on the device's
# layout socket — and a `studio` object (present only during a Studio Session)
# describes the paired editor link. Older apps report a single conflated link;
# the formatter renders whatever is there.

def format_connection_status(resp: dict) -> str:
    connected = resp.get("connected", False)
    phase = resp.get("phase", "?")
    state = "Layout link: connected" if connected else "Layout link: not connected"
    lines = [f"{state} (phase: {phase})"]
    if resp.get("channel"):
        lines.append(f"  channel: {resp['channel']} · role: {resp.get('role','?')}")
    if resp.get("account"):
        lines.append(f"  account: {resp['account']}")
    listening = resp.get("listening") or []
    if listening:
        lines.append(f"  listening: {', '.join(listening)}")
    studio = resp.get("studio")
    if isinstance(studio, dict):
        s_state = "paired" if studio.get("connected") else f"not connected (phase: {studio.get('phase','?')})"
        line = f"Studio link: {s_state}"
        if studio.get("channel"):
            line += f" · channel: {studio['channel']}"
        if studio.get("watchTraffic"):
            line += " · wire tap ON"
        lines.append(line)
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


# ─── watch-traffic (studio wire tap) ──────────────────────────────────────────
#
# Routed `watch-traffic {enable, sample_ms?, filter?}` on the STUDIO socket asks
# the device to forward every layout-socket sync value it dispatches (post-decrypt,
# pre-render) back to the editor as `studio.traffic` broadcasts — live-data eyes
# through the device without the editor ever holding room credentials or E2EE keys.
# Sampled per control (>=250ms default) and truncated (1KB/value) device-side.

TRAFFIC_MSG_TYPE = "studio.traffic"


def build_watch_traffic_request(enable: bool, sample_ms: int = 250,
                                filter: str = "") -> dict:
    req: dict = {"enable": enable, "sample_ms": sample_ms}
    if filter:
        req["filter"] = filter
    return req


def extract_traffic_frame(payload) -> Optional[dict]:
    """A broadcast payload → a traffic record, or None if it isn't one."""
    if not isinstance(payload, dict) or payload.get("msg_type") != TRAFFIC_MSG_TYPE:
        return None
    if not isinstance(payload.get("control"), str):
        return None
    return payload


def aggregate_traffic(frames: list, seconds: float) -> dict:
    """Per-control digest of collected traffic frames:
    {control: {count, rate_hz, last, valuePath?, event?, frameMsgType?, truncated}}."""
    out: dict = {}
    span = max(0.001, float(seconds))
    for f in frames:
        control = f["control"]
        entry = out.setdefault(control, {"count": 0, "truncated": False})
        entry["count"] += 1
        entry["last"] = f.get("value")
        for key in ("valuePath", "event", "frameMsgType"):
            if f.get(key) is not None:
                entry[key] = f[key]
        if f.get("truncated"):
            entry["truncated"] = True
    for entry in out.values():
        entry["rate_hz"] = round(entry["count"] / span, 2)
    return out


def format_traffic_digest(digest: dict, seconds: float, frame_count: int) -> str:
    if not digest:
        return (f"No sync traffic observed in {seconds}s. Either the layout's server/"
                f"hub isn't sending, no control's sync filter matched, or the layout "
                f"has no live connection — check get_connection_status (hub presence) "
                f"and the layout's sync wiring.")
    lines = [f"Observed {frame_count} forwarded value(s) across "
             f"{len(digest)} control(s) over {seconds}s:"]
    for control in sorted(digest):
        e = digest[control]
        bits = [f"last={json.dumps(e.get('last'))}"]
        bits.append(f"{e['count']}x ({e['rate_hz']}/s)")
        if e.get("valuePath"):
            bits.append(f"path {e['valuePath']}")
        if e.get("frameMsgType"):
            bits.append(f"msg_type {e['frameMsgType']}")
        if e.get("truncated"):
            bits.append("TRUNCATED to 1KB")
        lines.append(f"- **{control}**: " + " · ".join(bits))
    lines.append("(Sampled device-side — per-control rate is capped by sample_ms, "
                 "so rates read as 'at least'.)")
    return "\n".join(lines)


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


# ─── provision-device (hub credential mint, human-gated) ──────────────────────
#
# Routed `provision-device {channel, role?, reuse_room_key?}` on the STUDIO socket
# arms an approval sheet on the phone and answers `{ok, status:"pending",
# request_id}` immediately (the relay caps a routed reply at ~5s — a 60s human
# approval can never ride the arming request). The editor then polls the routed
# `get-provision-result {request_id}` until the outcome lands. The credential is
# written straight to disk by the tool; NONE of its secrets (token / refresh / k)
# may ever appear in a tool result. Wire contract: PROTOCOL.md.

#: The credential's secret fields — never allowed into a tool result.
PROVISION_SECRET_FIELDS = ("token", "refresh", "k")


def build_provision_request(channel: str, role: str = "hub",
                            reuse_room_key: bool = True) -> dict:
    return {"channel": channel, "role": role, "reuse_room_key": reuse_room_key}


def build_provision_result_request(request_id: str) -> dict:
    return {"request_id": request_id}


def extract_provision_pending(resp) -> Optional[str]:
    """The request_id from a `{ok, status:"pending", request_id}` arming reply."""
    if (isinstance(resp, dict) and resp.get("ok")
            and resp.get("status") == "pending"
            and isinstance(resp.get("request_id"), str)):
        return resp["request_id"]
    return None


def provision_error_message(resp) -> Optional[str]:
    """A human-readable line for a structured `{error, reason}` provision reply,
    or None when the reply isn't an error."""
    if not isinstance(resp, dict) or "error" not in resp:
        return None
    error = resp.get("error")
    reason = resp.get("reason", "")
    explain = {
        "denied": "The owner denied the request on the device (or it timed out unanswered).",
        "busy": "The device already has an approval sheet up — retry after it resolves.",
        "no-connect-session": "The device has no active Connect+ session — the owner must sign in / restore Connect+ first.",
        "mint-failed": "The device's mint call failed.",
        "bad-request": "The device rejected the request shape.",
        "unknown-request": "The device no longer knows this request (session ended, or the result was already drained).",
    }.get(error, f"Device returned error '{error}'.")
    return f"{explain}" + (f" ({reason})" if reason and reason != error else "")


def provision_key_change_warning(old_text: str, new_credential: dict) -> Optional[str]:
    """Loud warning when overwriting a credential whose `k` (same channel) differs —
    a changed room key breaks every layout pinning the old one."""
    try:
        old = json.loads(old_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(old, dict):
        return None
    if old.get("channel") != new_credential.get("channel"):
        return None
    old_k, new_k = old.get("k"), new_credential.get("k")
    if not old_k or old_k == new_k:
        return None
    return ("⚠️ ROOM KEY CHANGED: the overwritten file held a different E2EE key for "
            f"channel '{old.get('channel')}'. Every layout pinning the old key can no "
            "longer read this hub's frames — update those layouts, or re-provision "
            "with reuse_room_key=True from a device that still knows the old key.")


def format_provision_summary(credential: dict, out_path: str, key_verdict: str,
                             expires_at=None, warning: Optional[str] = None) -> str:
    """The redacted tool result: did, channel, role, token expiry, url host,
    out_path, and the key-reuse verdict — and NOTHING from the secret fields."""
    from urllib.parse import urlparse
    host = urlparse(credential.get("url", "")).netloc or credential.get("url", "?")
    verdict = {
        "reused-existing": "reused the channel's existing room key",
        "fresh-channel-had-none": "generated a fresh room key (channel had none)",
        "fresh-forced": "generated a fresh room key (reuse_room_key=False)",
    }.get(key_verdict, key_verdict or "no key verdict reported")
    lines = [
        "Hub credential provisioned and written to disk (secrets stay in the file — "
        "never in this result).",
        f"- device id: {credential.get('did', '?')}",
        f"- channel: {credential.get('channel', '?')} · role: {credential.get('role', '?')}",
        f"- relay host: {host}",
        f"- room key: {verdict}",
        f"- written to: {out_path} (mode 0600)",
    ]
    if expires_at:
        lines.insert(4, f"- token expires at: {expires_at} (unix; the hub self-refreshes)")
    if warning:
        lines.append(warning)
    return "\n".join(lines)
