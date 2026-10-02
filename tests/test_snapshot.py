"""K10 (carter-n4x.9): the snapshot_device MCP tool + its pure digest/advice logic.

The app's render-snapshot verb does not exist yet, so the tool is exercised
against a monkeypatched `mesh.routed_call` (fake device). Spec:
docs/feature-research-2026-09-24/deep-dives/17-mcp-upgrades.md §2.
"""

import asyncio
import base64

import pytest
from mcp.server.fastmcp import Image

from carter_mcp import mesh, protocol
from carter_mcp.tools import device

# Smallest valid-looking JPEG (SOI ... EOI); the tool never decodes pixels.
TINY_JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")

SPEC_REPLY = {
    "ok": True, "image": base64.b64encode(TINY_JPEG).decode(), "px": [402, 874],
    "scale": 1,
    "window": {"w": 402, "h": 874, "sizeClass": "compact", "idiom": "phone"},
    "tab": "Power", "layoutName": "Nordlys Station",
    "controls": {
        "battery-ring": {"frame": [16, 212, 178, 56], "cell": 56, "content": 91,
                         "scale": 0.75, "cuts": True},
        "pv-gauge": {"frame": [202, 212, 178, 112], "cell": 112, "content": 112,
                     "scale": 1, "cuts": False},
    },
    "offscreen": ["log-tail"], "unverified": ["route-map"], "locked": ["pro-chart"],
}

V3_INFO = {"ok": True, "appVersion": "1.3", "protocolVersion": 2,
           "features": ["verb.render-snapshot"]}
V2_INFO = {"ok": True, "appVersion": "1.2.6", "protocolVersion": 2, "features": []}


# ─── request shape ────────────────────────────────────────────────────────────

def test_request_shape_defaults():
    assert protocol.build_render_snapshot_request("Power") == {
        "mode": "window", "scale": 1, "format": "jpeg", "quality": 0.7,
        "report": True, "tab": "Power"}


def test_request_omits_empty_tab():
    assert "tab" not in protocol.build_render_snapshot_request(None)


# ─── digest ───────────────────────────────────────────────────────────────────

def test_digest_of_spec_reply():
    out = protocol.format_snapshot_digest(SPEC_REPLY)
    lines = out.splitlines()
    assert "Nordlys Station" in lines[0] and "tab Power" in lines[0]
    assert "window 402×874pt" in lines[0]
    assert "sizeClass compact" in lines[0] and "idiom phone" in lines[0]
    assert "battery-ring: cell 56pt, content 91pt, scaled 0.75, CUT" in lines
    assert not any(l.startswith("pv-gauge") for l in lines)  # clean → counted only
    assert "1 cut, 1 clean of 2 visible controls." in lines
    assert any(l.startswith("Offscreen") and "log-tail" in l for l in lines)
    unverified = next(l for l in lines if l.startswith("Unverified"))
    assert "route-map" in unverified and "do not 'fix' blanks" in unverified
    locked = next(l for l in lines if l.startswith("Locked"))
    assert "pro-chart" in locked and "not a bug" in locked


def test_digest_without_report():
    out = protocol.format_snapshot_digest({"ok": True, "image": "x", "layoutName": "L"})
    assert "No fit report" in out


# ─── error advice ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,needle", [
    ("not-foreground", "wake the phone and open CAR-TER"),
    ("too-large", "lower scale/quality"),
    ("studio-not-sealed", "E2EE; re-pair with a sealed studio session"),
])
def test_error_codes_map_to_advice(code, needle):
    advice = protocol.snapshot_error_advice({"ok": False, "error": code})
    assert needle.lower() in advice.lower()
    assert "\n" not in advice


def test_old_device_gets_update_line():
    assert protocol.snapshot_error_advice(None, V2_INFO) == "update CAR-TER to 1.3 for snapshots"


def test_new_device_or_unknown_info_is_not_too_old():
    assert protocol.snapshot_error_advice(SPEC_REPLY, V3_INFO) is None
    assert protocol.snapshot_error_advice(SPEC_REPLY, {"protocolVersion": 3}) is None
    assert protocol.snapshot_error_advice(SPEC_REPLY, None) is None


def test_none_reply_is_timeout_text():
    assert "timeout" in protocol.snapshot_error_advice(None)


def test_unknown_error_code_still_explained():
    advice = protocol.snapshot_error_advice({"ok": False, "error": "weird"})
    assert "weird" in advice and "Relay error" not in advice


def test_app_error_never_reads_as_relay_error():
    out = protocol.format_routed_response({"ok": False, "error": "not-foreground"},
                                          on_ok=lambda r: "ok", verb="render-snapshot")
    assert "Relay error" not in out and "wake the phone" in out
    # A genuine relay failure keeps the generic text.
    relay = protocol.format_routed_response({"error": "target not found"},
                                            on_ok=lambda r: "ok", verb="x")
    assert relay.startswith("Relay error")


# ─── the tool (fake device) ───────────────────────────────────────────────────

@pytest.fixture
def fake_device(monkeypatch):
    calls = []
    replies = {"get-device-info": [V3_INFO], "render-snapshot": [SPEC_REPLY]}

    async def routed_call(verb, payload, timeout=5.0, device_id=None):
        calls.append((verb, payload, timeout, device_id))
        queue = replies[verb]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def get_device_id():
        return "dev-1"

    monkeypatch.setattr(mesh, "connection_error", lambda: None)
    monkeypatch.setattr(mesh, "get_device_id", get_device_id)
    monkeypatch.setattr(mesh, "routed_call", routed_call)
    return calls, replies


def run(coro):
    return asyncio.run(coro)


def test_tool_returns_image_and_digest(fake_device):
    calls, _ = fake_device
    out = run(device.snapshot_device(tab="Power"))
    assert isinstance(out, list) and len(out) == 2
    img, digest = out
    assert isinstance(img, Image) and img.data == TINY_JPEG
    assert img.to_image_content().mimeType == "image/jpeg"
    assert "battery-ring: cell 56pt, content 91pt, scaled 0.75, CUT" in digest
    # Capability check precedes the verb; the verb gets the ~10 s timeout.
    assert [c[0] for c in calls] == ["get-device-info", "render-snapshot"]
    assert calls[1][1]["tab"] == "Power" and calls[1][2] == protocol.SNAPSHOT_TIMEOUT_S


def test_tool_retries_once_on_no_reply(fake_device):
    calls, replies = fake_device
    replies["render-snapshot"] = [None, SPEC_REPLY]
    out = run(device.snapshot_device())
    assert isinstance(out, list)
    assert [c[0] for c in calls].count("render-snapshot") == 2


def test_tool_timeout_after_retry(fake_device):
    calls, replies = fake_device
    replies["render-snapshot"] = [None]
    out = run(device.snapshot_device())
    assert out == protocol.SNAPSHOT_TIMEOUT_ADVICE
    assert [c[0] for c in calls].count("render-snapshot") == 2


def test_tool_old_device_never_sends_verb(fake_device):
    calls, replies = fake_device
    replies["get-device-info"] = [V2_INFO]
    out = run(device.snapshot_device())
    assert out == "update CAR-TER to 1.3 for snapshots"
    assert "render-snapshot" not in [c[0] for c in calls]


def test_tool_app_error_gives_advice(fake_device):
    _, replies = fake_device
    replies["render-snapshot"] = [{"ok": False, "error": "not-foreground"}]
    out = run(device.snapshot_device())
    assert "wake the phone" in out.lower() and "Relay error" not in out


def test_tool_rejects_bad_mode(fake_device):
    out = run(device.snapshot_device(mode="sideways"))
    assert out.startswith("Invalid snapshot request")


def test_tool_not_connected(monkeypatch):
    monkeypatch.setattr(mesh, "connection_error", lambda: "Not connected. Call connect first.")
    assert run(device.snapshot_device()) == "Not connected. Call connect first."


# ─── review fixes: error-dict info is unknown, unanswered probe stops early ───

@pytest.mark.parametrize("info", [
    {"ok": False, "error": "target not found"},
    {"error": "target not found"},
])
def test_error_reply_is_unknown_not_too_old(info):
    assert protocol.device_supports(info, "render-snapshot") is None
    assert protocol.snapshot_error_advice(SPEC_REPLY, info) is None


def test_tool_error_info_still_sends_verb(fake_device):
    calls, replies = fake_device
    replies["get-device-info"] = [{"ok": False, "error": "target not found"}]
    out = run(device.snapshot_device())
    assert isinstance(out, list)
    assert "render-snapshot" in [c[0] for c in calls]


def test_tool_unanswered_probe_skips_verb(fake_device):
    calls, replies = fake_device
    replies["get-device-info"] = [None]
    out = run(device.snapshot_device())
    assert out == protocol.DEVICE_NO_ANSWER_ADVICE
    assert [c[0] for c in calls] == ["get-device-info"]
    assert calls[0][2] == protocol.DEVICE_INFO_TIMEOUT_S


def test_tool_probe_exception_is_reported(monkeypatch, fake_device):
    calls, _ = fake_device

    async def boom(verb, payload, timeout=5.0, device_id=None):
        calls.append((verb, payload, timeout, device_id))
        raise ConnectionError("socket closed")

    monkeypatch.setattr(mesh, "routed_call", boom)
    out = run(device.snapshot_device())
    assert out.startswith(protocol.DEVICE_NO_ANSWER_ADVICE) and "socket closed" in out
    assert [c[0] for c in calls] == ["get-device-info"]
