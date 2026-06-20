"""Tests for the MCP read-back + truthful-push protocol logic (carter-mcp, Track 3).

These exercise the pure functions that build routed-RPC request payloads and
format device responses into tool output. The async tools are thin wrappers
over these, so the protocol-shaped bugs (payload shapes, response formatting,
the three failure surfaces) live here and are tested without a live device.

Per spec: docs/superpowers/specs/2026-06-20-mcp-readback-preview-design.md
"""

import server


# ─── get-current-layout ──────────────────────────────────────────────────────

def test_current_layout_request_summary_default():
    # Default ask is the cheap structural summary, not the full layout.
    assert server.build_get_layout_request() == {"include": "summary"}


def test_current_layout_request_full():
    assert server.build_get_layout_request(full=True) == {"include": "full"}


def test_format_current_layout_summary():
    resp = {
        "ok": True,
        "isLiveEditSession": True,
        "activeFile": "ups-monitor.json",
        "summary": {
            "name": "UPS Monitor",
            "accentColor": "#667eea",
            "tabs": [
                {"title": "Main", "icon": "house.fill", "controls": [
                    {"id": "battery", "type": "gauge", "position": [0, 0], "span": [1, 1]},
                    {"id": "load", "type": "gauge", "position": [0, 1], "span": [1, 1]},
                ]},
            ],
        },
    }
    out = server.format_current_layout(resp)
    assert "UPS Monitor" in out
    assert "ups-monitor.json" in out
    assert "battery" in out and "load" in out
    assert "gauge" in out


def test_format_current_layout_none_loaded():
    resp = {"ok": True, "isLiveEditSession": False, "activeFile": None, "summary": None}
    out = server.format_current_layout(resp)
    assert "no layout" in out.lower()


# ─── get-control-state ────────────────────────────────────────────────────────

def test_control_state_request_all():
    assert server.build_control_state_request() is None


def test_control_state_request_filtered():
    assert server.build_control_state_request(["battery", "load"]) == {"ids": ["battery", "load"]}


def test_format_control_state():
    resp = {"ok": True, "values": {"battery": 82, "load": 0.4, "status-light": "green"}}
    out = server.format_control_state(resp)
    assert "battery" in out and "82" in out
    assert "status-light" in out and "green" in out


def test_format_control_state_empty():
    resp = {"ok": True, "values": {}}
    out = server.format_control_state(resp)
    assert "no control" in out.lower()


# ─── get-connection-status ────────────────────────────────────────────────────

def test_format_connection_status_connected():
    resp = {
        "ok": True, "connected": True, "phase": "connected",
        "channel": "editor-abc", "role": "viewer", "account": "acct-1",
        "listening": ["broadcast", "list-layouts", "get-current-layout"],
    }
    out = server.format_connection_status(resp)
    assert "connected" in out.lower()
    assert "editor-abc" in out
    assert "acct-1" in out
    assert "get-current-layout" in out


def test_format_connection_status_disconnected():
    resp = {
        "ok": True, "connected": False, "phase": "failed",
        "channel": "editor-abc", "role": "viewer", "account": None, "listening": [],
    }
    out = server.format_connection_status(resp)
    assert "not connected" in out.lower() or "disconnected" in out.lower()
    assert "failed" in out


# ─── apply-layout (truthful push) ─────────────────────────────────────────────

def test_apply_layout_request_strips_broadcast_framing():
    # The routed apply-layout carries the layout itself, with NO msg_type framing
    # (that framing belongs only to the broadcast layout-update path).
    layout = {"name": "X", "version": 1, "tabs": []}
    req = server.build_apply_layout_request(layout)
    assert "msg_type" not in req
    assert req["name"] == "X"


def test_format_apply_result_success_echoes_rendered():
    resp = {"ok": True, "rendered": {
        "name": "UPS Monitor",
        "tabs": [{"title": "Main", "icon": "house.fill", "controls": [
            {"id": "battery", "type": "gauge", "position": [0, 0], "span": [1, 1]},
        ]}],
    }}
    out = server.format_apply_result(resp)
    assert "UPS Monitor" in out
    assert "battery" in out
    # truthful: reports what the DEVICE rendered
    assert "render" in out.lower()


def test_format_apply_result_device_rejected():
    # JSON-valid but the device failed to decode/apply it — must NOT report success.
    resp = {"ok": False, "error": "missing required field: tabs"}
    out = server.format_apply_result(resp)
    assert "fail" in out.lower() or "error" in out.lower() or "reject" in out.lower()
    assert "missing required field: tabs" in out
    assert "success" not in out.lower()


# ─── shared routed-RPC failure surfaces ───────────────────────────────────────

def test_format_routed_response_timeout():
    # Device did not answer (relay returned None).
    out = server.format_routed_response(None, on_ok=lambda r: "OK", verb="get-current-layout")
    assert "did not respond" in out.lower() or "timeout" in out.lower()


def test_format_routed_response_relay_error():
    out = server.format_routed_response(
        {"error": "no route to target"}, on_ok=lambda r: "OK", verb="get-current-layout"
    )
    assert "no route to target" in out


def test_format_routed_response_ok_delegates():
    out = server.format_routed_response(
        {"ok": True, "x": 1}, on_ok=lambda r: f"got {r['x']}", verb="get-current-layout"
    )
    assert out == "got 1"


# ─── push_layout transport selection ──────────────────────────────────────────

def test_push_uses_routed_when_single_device():
    # One resolved device → routed apply-layout (truthful, gets an echo back).
    assert server.should_push_routed(device_id="dev-1") is True


def test_push_uses_broadcast_when_no_device():
    # No resolvable target → fall back to broadcast layout-update (multi-viewer / demo path).
    assert server.should_push_routed(device_id=None) is False
