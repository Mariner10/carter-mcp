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
        # App-side studio error codes ({ok:false, error:"not-foreground"}) are
        # device answers, not relay failures: explain them before the generic branch.
        if result.get("error") in _STUDIO_ERRORS:
            return studio_error_message(result, verb)
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


# ─── Studio verbs: render-snapshot / patch-layout / trace-control (K9, DRAFT) ──
#
# Routed, studio-socket-only verbs (PROTOCOL.md "render-snapshot" et seq.). The
# app side is not implemented yet; these are the pure builders/formatters the
# K10/K12/K15 tools wrap. Replies reuse the `{ok:false, error, reason}` envelope.
#
# Capability gate (decision carter-ek9, 2026-09-28): a device advertises a verb
# as `verb.<name>` in get-device-info `features`; deviceProtocolVersion stays 2.
# `protocolVersion >= 3` is also honoured so a future bump needs no MCP change.

STUDIO_V3_VERBS = ("render-snapshot", "patch-layout", "trace-control")

_TOO_OLD_WHAT = {
    "render-snapshot": "snapshots",
    "patch-layout": "patch pushes",
    "trace-control": "control tracing",
}


def _verb_name(verb: str) -> str:
    return verb[len("verb."):] if verb.startswith("verb.") else verb


def device_supports(info, verb: str) -> Optional[bool]:
    """Does the paired device answer `verb`?

    True  — `verb.<name>` (optionally `@N`) is in `info["features"]`, or
            `protocolVersion >= 3`.
    False — device info is known and neither applies (an older app: it would
            silently not answer, so the caller should say "update" instead of
            waiting out the relay timeout).
    None  — no device info (not paired / get-device-info unanswered).
    """
    if not isinstance(info, dict) or not info:
        return None
    # A relay/app error reply ({ok:false, error:"target not found"}) says
    # nothing about the app's version: unknown, not "too old".
    if (info.get("ok") is False or "error" in info) and \
            "features" not in info and "protocolVersion" not in info:
        return None
    name = _verb_name(verb)
    feats = info.get("features")
    if isinstance(feats, list):
        for f in feats:
            if isinstance(f, str) and f.strip().split("@", 1)[0] == f"verb.{name}":
                return True
    proto = info.get("protocolVersion")
    try:
        if proto is not None and int(proto) >= 3:
            return True
    except (TypeError, ValueError):
        pass
    return False


def unsupported_verbs(info, verbs=STUDIO_V3_VERBS) -> list[str]:
    """The verbs `device_supports` says False for (unknown info → [])."""
    return [v for v in verbs if device_supports(info, v) is False]


def too_old_message(verb: str) -> str:
    """The line a tool returns instead of timing out on an older app."""
    what = _TOO_OLD_WHAT.get(_verb_name(verb), f"'{_verb_name(verb)}'")
    return f"update CAR-TER to 1.3 for {what}"


_STUDIO_ERRORS = {
    "not-foreground": "The phone is locked or CAR-TER is in the background — wake the phone and open CAR-TER, then retry.",
    "too-large": "The reply would exceed the relay frame budget even after lowering quality/scale — lower `scale`/`quality` or snapshot a smaller tab.",
    "studio-not-sealed": "The loaded layout is E2EE but the studio session is not sealed (no e2ee key in the pairing) — the device refuses to send its contents in plaintext. Re-pair with an E2EE studio session.",
    "stale-base": "The device's layout changed since the patch base — fall back to a full apply-layout.",
    "rate-limited": "The device is rate-limiting this verb — wait a moment and retry.",
    "unknown-tab": "The device has no tab with that title.",
    "unknown-control": "The device has no control with that id.",
    "no-layout": "No layout is loaded on the device.",
    "bad-request": "The device rejected the request shape.",
}


def studio_error_message(resp, verb: str = "") -> Optional[str]:
    """A human line for a `{ok:false, error, reason}` studio-verb reply, or None
    when the reply isn't an error (same style as `provision_error_message`)."""
    if not isinstance(resp, dict) or resp.get("ok") is True or "error" not in resp:
        return None
    error = resp.get("error")
    reason = resp.get("reason", "")
    explain = _STUDIO_ERRORS.get(error)
    if explain is None:
        where = f" to '{verb}'" if verb else ""
        explain = f"Device returned error '{error}'{where}."
    return explain + (f" ({reason})" if reason and reason != error else "")


def _num(v) -> str:
    """56.0 → "56", 0.75 → "0.75"."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ─── render-snapshot ──────────────────────────────────────────────────────────

SNAPSHOT_MODES = ("window", "full")


def build_render_snapshot_request(tab: Optional[str] = None, mode: str = "window",
                                  scale: float = 1, quality: float = 0.7,
                                  report: bool = True) -> dict:
    """`render-snapshot` payload. `mode: "full"` is phase 3 on the device."""
    if mode not in SNAPSHOT_MODES:
        raise ValueError(f"mode must be one of {SNAPSHOT_MODES}, got {mode!r}")
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not 0 < scale <= 3:
        raise ValueError(f"scale must be in (0, 3], got {scale!r}")
    if not isinstance(quality, (int, float)) or isinstance(quality, bool) or not 0 < quality <= 1:
        raise ValueError(f"quality must be in (0, 1], got {quality!r}")
    payload = {"mode": mode, "scale": scale, "format": "jpeg",
               "quality": quality, "report": bool(report)}
    if tab:
        payload["tab"] = tab
    return payload


def extract_snapshot_image(resp) -> Optional[bytes]:
    """Decoded JPEG bytes from an ok reply, or None."""
    import base64
    import binascii
    if not isinstance(resp, dict) or not resp.get("ok"):
        return None
    data = resp.get("image")
    if not isinstance(data, str) or not data:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def format_fit_line(cid: str, fit: dict) -> str:
    """`battery-ring: cell 56pt, content 91pt, scaled 0.75, CUT`."""
    parts = []
    if "cell" in fit:
        parts.append(f"cell {_num(fit['cell'])}pt")
    if "content" in fit:
        parts.append(f"content {_num(fit['content'])}pt")
    scale = fit.get("scale")
    if scale is not None and scale != 1:
        parts.append(f"scaled {_num(scale)}")
    if fit.get("cuts"):
        parts.append("CUT")
    return f"{cid}: " + (", ".join(parts) if parts else "no fit data")


def format_snapshot_report(resp) -> str:
    """Text digest of a render-snapshot reply: capture header, per-control fit
    lines (cut controls first), and the offscreen/unverified/locked lists."""
    if (err := studio_error_message(resp, "render-snapshot")):
        return err
    if not isinstance(resp, dict):
        return f"Unexpected render-snapshot reply: {resp!r}"
    head = f"Snapshot of **{resp.get('layoutName', '?')}**"
    if resp.get("tab"):
        head += f" · tab {resp['tab']}"
    px = resp.get("px")
    if isinstance(px, list) and len(px) == 2:
        head += f" · {_num(px[0])}×{_num(px[1])}px"
    if resp.get("scale") is not None:
        head += f" @{_num(resp['scale'])}x"
    win = resp.get("window") or {}
    if win:
        bits = [b for b in (win.get("idiom"), win.get("sizeClass")) if b]
        if "w" in win and "h" in win:
            bits.insert(0, f"{_num(win['w'])}×{_num(win['h'])}pt")
        if bits:
            head += f" · window {' '.join(bits)}"
    lines = [head]
    if resp.get("sheetPresented"):
        lines.append("Note: a sheet/alert was presented; it is not in the capture.")
    controls = resp.get("controls") or {}
    if controls:
        cut = sorted(c for c, f in controls.items() if isinstance(f, dict) and f.get("cuts"))
        ok = sorted(c for c, f in controls.items() if isinstance(f, dict) and not f.get("cuts"))
        lines.append(f"Fit ({len(cut)} cut of {len(cut) + len(ok)}):")
        for cid in cut + ok:
            lines.append("  " + format_fit_line(cid, controls[cid]))
    elif resp.get("ok"):
        lines.append("No fit report (report=false or no visible controls).")
    for key, label in (("offscreen", "Offscreen (below the fold)"),
                       ("unverified", "Unverified (map/camera/web pixels may be blank)"),
                       ("locked", "Locked (Pro placeholder, not a bug)")):
        ids = resp.get(key) or []
        if ids:
            lines.append(f"{label}: {', '.join(ids)}")
    return "\n".join(lines)


SNAPSHOT_TIMEOUT_S = 10.0
# get-device-info is answered by every app version, and the relay caps routed
# replies at about 5 s, so asking for longer than this buys nothing.
DEVICE_INFO_TIMEOUT_S = 5.0
DEVICE_NO_ANSWER_ADVICE = (
    "The device did not answer get-device-info, so render-snapshot was not "
    "sent. Wake the phone, open CAR-TER, check the Studio Session is still "
    "paired, then try again.")

_SNAPSHOT_ADVICE = {
    "not-foreground": "Wake the phone and open CAR-TER, then retry the snapshot.",
    "too-large": "The snapshot is too large for the relay frame — lower scale/quality (e.g. scale=0.5) and retry.",
    "studio-not-sealed": "This layout is E2EE; re-pair with a sealed studio session (E2EE studio pairing) to snapshot it.",
}

SNAPSHOT_TIMEOUT_ADVICE = ("The device did not answer 'render-snapshot' (timeout, retried once). "
                           "Make sure the phone is awake with CAR-TER open and still paired.")


def snapshot_error_advice(resp, device_info=None) -> Optional[str]:
    """One plain line of advice for a failed snapshot, or None when `resp` is ok.

    `device_info` is the RAW get-device-info reply (with `features`); a device
    known not to support render-snapshot (protocol < 3, no `verb.render-snapshot`)
    gets the update line instead of a timeout. Checked first, so the caller
    can pass `resp=None` to ask "is this device too old?" before sending."""
    if device_supports(device_info, "render-snapshot") is False:
        return too_old_message("render-snapshot")
    if resp is None:
        return SNAPSHOT_TIMEOUT_ADVICE
    if not isinstance(resp, dict):
        return f"Unexpected render-snapshot reply: {resp!r}"
    if resp.get("ok") is True or "error" not in resp:
        if resp.get("ok") and extract_snapshot_image(resp) is None:
            return "The device answered render-snapshot without a decodable JPEG image."
        return None
    error = resp.get("error")
    if error in _SNAPSHOT_ADVICE:
        return _SNAPSHOT_ADVICE[error]
    return studio_error_message(resp, "render-snapshot")


def format_snapshot_digest(resp) -> str:
    """Compact fit digest of an ok render-snapshot reply: one line per CUT
    control, a count of the clean ones, the offscreen/unverified/locked lists,
    and the capture context (window size, sizeClass, idiom, tab)."""
    if not isinstance(resp, dict):
        return f"Unexpected render-snapshot reply: {resp!r}"
    win = resp.get("window") or {}
    ctx = [f"Snapshot of **{resp.get('layoutName', '?')}**"]
    if resp.get("tab"):
        ctx.append(f"tab {resp['tab']}")
    if "w" in win and "h" in win:
        ctx.append(f"window {_num(win['w'])}×{_num(win['h'])}pt")
    if win.get("sizeClass"):
        ctx.append(f"sizeClass {win['sizeClass']}")
    if win.get("idiom"):
        ctx.append(f"idiom {win['idiom']}")
    lines = [" · ".join(ctx)]
    if resp.get("sheetPresented"):
        lines.append("Note: a sheet/alert was presented; it is not in the capture.")
    controls = resp.get("controls") or {}
    fits = {c: f for c, f in controls.items() if isinstance(f, dict)}
    cut = sorted(c for c, f in fits.items() if f.get("cuts"))
    clean = len(fits) - len(cut)
    if fits:
        for cid in cut:
            lines.append(format_fit_line(cid, fits[cid]))
        lines.append(f"{len(cut)} cut, {clean} clean of {len(fits)} visible controls.")
    else:
        lines.append("No fit report (report=false or no visible controls).")
    for key, label in (
            ("offscreen", "Offscreen (below the fold, not checked)"),
            ("unverified", "Unverified (map/camera/web pixels may be blank in the capture — do not 'fix' blanks)"),
            ("locked", "Locked (ProFeatures upsell placeholder, not a bug)")):
        ids = resp.get(key) or []
        if ids:
            lines.append(f"{label}: {', '.join(map(str, ids))}")
    return "\n".join(lines)


# ─── patch-layout ─────────────────────────────────────────────────────────────

PATCH_OP_KINDS = ("update", "add", "remove", "move", "layout", "renameTab")


def _validate_patch_op(i: int, op) -> None:
    if not isinstance(op, dict):
        raise ValueError(f"ops[{i}] must be an object")
    kind = op.get("op")
    if kind not in PATCH_OP_KINDS:
        raise ValueError(f"ops[{i}]: unknown op kind {kind!r} "
                         f"(expected one of {', '.join(PATCH_OP_KINDS)})")
    if kind in ("update", "remove", "move") and not (isinstance(op.get("id"), str) and op["id"]):
        raise ValueError(f"ops[{i}] ({kind}) needs a control `id`")
    if kind in ("update", "layout"):
        if not isinstance(op.get("set", {}), dict) or not isinstance(op.get("unset", []), list):
            raise ValueError(f"ops[{i}] ({kind}): `set` must be an object, `unset` a list")
        if not op.get("set") and not op.get("unset"):
            raise ValueError(f"ops[{i}] ({kind}) needs `set` and/or `unset`")
    if kind == "add":
        ctl = op.get("control")
        if not isinstance(ctl, dict) or not ctl.get("id"):
            raise ValueError(f"ops[{i}] (add) needs a `control` object with an `id`")
        if not (isinstance(op.get("tab"), str) and op["tab"]):
            raise ValueError(f"ops[{i}] (add) needs a target `tab` title")
    if kind == "move" and not any(k in op for k in ("position", "span", "tab")):
        raise ValueError(f"ops[{i}] (move) needs `position`, `span` and/or `tab`")
    if kind == "renameTab":
        if not all(isinstance(op.get(k), str) and op[k] for k in ("from", "to")):
            raise ValueError(f"ops[{i}] (renameTab) needs `from` and `to` tab titles")


def build_patch_layout_request(base: str, ops: list) -> dict:
    """`patch-layout` payload: id-addressed ops against the layout hashed `base`.
    Raises ValueError on an unknown op kind or a malformed op."""
    if not isinstance(base, str) or not base:
        raise ValueError("base must be the last-acknowledged layout hash (e.g. 'sha256:…')")
    if not isinstance(ops, list) or not ops:
        raise ValueError("ops must be a non-empty list")
    for i, op in enumerate(ops):
        _validate_patch_op(i, op)
    return {"base": base, "ops": ops}


def is_stale_base(resp) -> bool:
    """True when the device refused the patch because its layout moved on — the
    caller should fall back to a full apply-layout."""
    return isinstance(resp, dict) and not resp.get("ok") and resp.get("error") == "stale-base"


def format_patch_result(resp) -> str:
    if is_stale_base(resp):
        dev = resp.get("hash")
        return ("Patch refused: stale-base — the device's layout changed since the "
                "patch base" + (f" (device is at {dev})" if dev else "")
                + ". Fall back to a full apply-layout (push_layout).")
    if (err := studio_error_message(resp, "patch-layout")):
        return f"Patch failed: {err}"
    if not isinstance(resp, dict) or not resp.get("ok"):
        return f"Unexpected patch-layout reply: {resp!r}"
    reload = resp.get("reload")
    how = {"hot": "hot reload (live values, socket and history kept)",
           "full": "full reload (wiring changed; values reset)"}.get(reload, f"reload {reload or '?'}")
    lines = [f"Patched: {resp.get('applied', '?')} op(s) applied · {how}"]
    if resp.get("hash"):
        lines.append(f"New base: {resp['hash']}")
    rendered = resp.get("rendered")
    if isinstance(rendered, dict) and rendered.get("tabs") is not None:
        lines.append(format_summary(rendered))
    return "\n".join(lines)


# ─── trace-control ────────────────────────────────────────────────────────────

def build_trace_control_request(ids, frames: int = 20) -> dict:
    """`trace-control` payload. `ids` may be one id or a list."""
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise ValueError("ids must be a control id or a non-empty list of ids")
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 0:
        raise ValueError(f"frames must be a non-negative int, got {frames!r}")
    return {"ids": ids, "frames": frames}


_TRACE_COUNTERS = ("delivered", "filterMiss", "nearMiss", "pathMiss", "typeMiss")


def format_trace(resp) -> str:
    """Per-control digest: registrations + miss counters, findings, recent frame
    verdicts, and action outcomes."""
    if (err := studio_error_message(resp, "trace-control")):
        return err
    if not isinstance(resp, dict):
        return f"Unexpected trace-control reply: {resp!r}"
    window = resp.get("window_s")
    lines = ["Control trace" + (f" (last {_num(window)}s)" if window is not None else "")]
    controls = resp.get("controls") or []
    if not controls:
        lines.append("No controls traced.")
    for c in controls:
        lines.append(f"- {c.get('id', '?')}")
        regs = c.get("registrations") or []
        if not regs:
            lines.append("    no sync registrations")
        for r in regs:
            desc = r.get("event", "?")
            if r.get("filter"):
                desc += f" filter {json.dumps(r['filter'], separators=(',', ':'))}"
            if r.get("valuePath"):
                desc += f" path {r['valuePath']}"
            counts = ", ".join(f"{k} {r[k]}" for k in _TRACE_COUNTERS if k in r)
            lines.append(f"    reg {desc}" + (f": {counts}" if counts else ""))
        for f in c.get("findings") or []:
            text = f if isinstance(f, str) else (f.get("message") or f.get("code") or json.dumps(f))
            lines.append(f"    finding: {text}")
        frames = c.get("frames") or []
        if frames:
            verdicts: dict[str, int] = {}
            for fr in frames:
                v = fr.get("verdict", "?") if isinstance(fr, dict) else "?"
                verdicts[v] = verdicts.get(v, 0) + 1
            lines.append(f"    frames ({len(frames)}): "
                         + ", ".join(f"{v} ×{n}" for v, n in sorted(verdicts.items())))
        for a in c.get("actions") or []:
            bit = f"    action {a.get('event', '?')}"
            if a.get("cmd"):
                bit += f" {a['cmd']}"
            bit += f" → {a.get('outcome', '?')}"
            if a.get("rtt_ms") is not None:
                bit += f" ({_num(a['rtt_ms'])} ms)"
            lines.append(bit)
    return "\n".join(lines)
