"""Tests for explain_control (K15): the trace-control formatter (one case per
finding code in the spec table, plus an unknown code) and the outside-in
fallback when the phone does not answer (timeout / older app / E2EE room).

Spec: docs/feature-research-2026-09-24/deep-dives/14-wire-inspector.md
(Finding codes, trace-control, Older apps).
"""

import asyncio

import pytest

from carter_mcp import protocol, state, wirecheck
from carter_mcp.tools import device

needs_wire = pytest.mark.skipif(not wirecheck.have_wire(),
                                reason="installed carterkit has no carterkit.wire")


def _reply(findings, delivered=0, cid="temp"):
    return {"ok": True, "window_s": 60, "controls": [{
        "id": cid,
        "registrations": [{"event": "broadcast", "filter": {"msg_type": "telemetery"},
                           "valuePath": "t.c", "delivered": delivered, "filterMiss": 12,
                           "nearMiss": 12, "pathMiss": 0, "typeMiss": 0}],
        "findings": findings,
        "frames": [{"at": 1, "verdict": "filter_miss", "value": {"truncated": True}}],
        "actions": [{"at": 2, "event": "broadcast_request", "cmd": "set",
                     "outcome": "ack", "rtt_ms": 84}],
    }]}


def _finding(code, **detail):
    return {"code": code, "control": "temp", "event": "broadcast",
            "detail": detail, "count": 12, "lastAt": 1790300123456}


# ─── formatter: one case per spec code ───────────────────────────────────────

# (code, detail, substring the plain-English line must contain)
CASES = [
    ("NO_FRAMES", {}, "check the event name"),
    ("FILTER_MISS", {"key": "msg_type", "expected": "telemetery",
                     "seen": ["telemetry", "heartbeat"]}, "'msg_type' must be"),
    ("PATH_MISSING", {"segment": "c", "index": 1}, "segment 'c' (#2)"),
    ("TYPE_UNRENDERABLE", {}, "object or array"),
    ("HELD_PENDING", {}, "local change"),
    ("E2EE_DROP", {"label": "badTag"}, "different keys"),
    ("NO_HUB_ON_CHANNEL", {}, "No hub is on the layout's channel"),
    ("NOT_CONNECTED", {}, "not connected to the relay"),
    ("SEND_NO_SOCKET", {}, "no socket"),
    ("ACK_TIMEOUT", {}, "ack never came back"),
    ("ACK_REJECTED", {"reason": "bad cmd"}, "rejected the command: bad cmd"),
    ("REQUEST_TIMEOUT", {}, "within 5 s"),
]


@pytest.mark.parametrize("code,detail,want", CASES, ids=[c[0] for c in CASES])
def test_format_trace_control_each_code(code, detail, want):
    out = protocol.format_trace_control(_reply([_finding(code, **detail)]))
    assert out.startswith("From the phone's own trace (last 60s):")
    assert f"temp: NOT receiving data: {code}" in out
    assert f"  - {code}: " in out
    assert want in out
    assert "(x12)" in out
    assert protocol.TRACE_FALLBACK_PREFIX not in out


def test_every_spec_code_has_english():
    for code, detail, _ in CASES:
        assert protocol._finding_text(code, detail, "broadcast") is not None


def test_unknown_code_passes_through_raw():
    f = {"code": "WEIRD_NEW_THING", "control": "temp", "detail": {"x": 1}}
    out = protocol.format_trace_control(_reply([f]))
    assert "WEIRD_NEW_THING" in out
    assert '"detail":{"x":1}' in out


def test_verdict_ok_when_delivered_without_findings():
    out = protocol.format_trace_control(_reply([], delivered=5))
    assert "temp: OK, receiving data (5 delivered)" in out
    assert "sync broadcast filter" in out and "delivered 5" in out
    assert "recent frames (1): filter_miss x1" in out
    assert "action broadcast_request set -> ack (84 ms)" in out


def test_format_trace_control_device_error():
    out = protocol.format_trace_control({"ok": False, "error": "not-foreground"})
    assert "wake the phone" in out


def test_trace_unanswered():
    assert protocol.trace_unanswered(None)
    assert protocol.trace_unanswered({"ok": False, "error": "unknown-control"})
    assert protocol.trace_unanswered({"error": "unknown verb trace-control"})
    assert not protocol.trace_unanswered(_reply([]))


# ─── fallback: fake device that never answers trace-control ──────────────────

def _layout(e2ee=False):
    lay = {"name": "T", "tabs": [{"title": "Main", "children": [
        {"type": "gauge", "id": "temp", "position": [0, 0],
         "sync": [{"type": "listen", "event": "broadcast",
                   "filter": {"msg_type": "telemetery"}, "valuePath": "t.c"}]},
        {"type": "gauge", "id": "hum", "position": [0, 1],
         "sync": [{"type": "listen", "event": "broadcast",
                   "filter": {"msg_type": "telemetry"}, "valuePath": "h"}]},
        {"type": "label", "id": "plain", "position": [1, 0]},
    ]}]}
    if e2ee:
        lay["connection"] = {"channel": "room", "e2eeKey": "k" * 43}
    return lay


TELEMETRY = [{"msg_type": "telemetry", "t": {"c": 21.5}, "h": 40}] * 3
SEALED = [{"e2ee": {"v": 1, "n": "bm9uY2U=", "ct": "Y2lwaGVydGV4dA=="}}] * 3


class _FakeDevice:
    """Answers get_nodes and get-current-layout; never answers trace-control
    (an older app). `probe_frames` are fed to any listener registered via on()."""

    def __init__(self, layout, trace_reply=None, probe_frames=()):
        self.layout = layout
        self.trace_reply = trace_reply
        self.probe_frames = list(probe_frames)
        self.is_running = True
        self.handlers = {}
        self.calls = []

    def on(self, event, fn):
        self.handlers[event] = fn
        for p in self.probe_frames:
            asyncio.ensure_future(fn(p))

    async def request(self, event, payload=None, timeout=None):
        verb = payload.get("type") if isinstance(payload, dict) else None
        self.calls.append((event, verb, timeout))
        if event == "get_nodes":
            return {"clients": [{"id": "dev-1", "name": "iPhone"}]}
        if verb == "trace-control":
            return self.trace_reply
        if verb == "get-current-layout":
            return {"ok": True, "layout": self.layout}
        return None


@pytest.fixture
def clean_state(monkeypatch):
    monkeypatch.setattr(state, "peer_list", [{"id": "dev-1"}])
    monkeypatch.setattr(state, "work_buffer", None)
    monkeypatch.setattr(state, "last_probe_events", {})
    monkeypatch.setattr(state, "last_probe_frames", [])
    monkeypatch.setattr(state, "last_probe_event", "broadcast")
    monkeypatch.setattr(device, "FALLBACK_PROBE_S", 1)


def _use(monkeypatch, dev):
    monkeypatch.setattr(state, "socket", dev)
    return dev


def test_answered_trace_is_formatted_not_fallback(monkeypatch, clean_state):
    dev = _use(monkeypatch, _FakeDevice(_layout(), trace_reply=_reply(
        [_finding("FILTER_MISS", key="msg_type", expected="telemetery", seen=["telemetry"])])))
    out = asyncio.run(device.explain_control("temp"))
    assert out.startswith("From the phone's own trace")
    assert protocol.TRACE_FALLBACK_PREFIX not in out
    assert ("route_msg", "trace-control", device.TRACE_TIMEOUT_S) in dev.calls
    assert device.TRACE_TIMEOUT_S < 5.0


@needs_wire
def test_timeout_fallback_uses_probe_frames(monkeypatch, clean_state):
    from carter_mcp import probe
    monkeypatch.setattr(state, "last_probe_frames", list(TELEMETRY))
    monkeypatch.setattr(state, "last_probe_events",
                        probe.aggregate([("broadcast", f) for f in TELEMETRY]))
    _use(monkeypatch, _FakeDevice(_layout(), trace_reply=None))
    out = asyncio.run(device.explain_control("temp"))
    lines = out.splitlines()
    assert lines[0] == protocol.TRACE_FALLBACK_PREFIX
    assert "no reply within 3 s" in lines[1]
    assert "temp: NOT receiving data (outside-in): FILTER_MISS" in out
    assert "near miss" in out
    assert "ciphertext" not in out


@needs_wire
def test_fallback_runs_short_probe_when_none(monkeypatch, clean_state):
    _use(monkeypatch, _FakeDevice(_layout(), trace_reply={"error": "unknown verb"},
                                  probe_frames=TELEMETRY))
    out = asyncio.run(device.explain_control("hum"))
    assert out.splitlines()[0] == protocol.TRACE_FALLBACK_PREFIX
    assert "unknown verb" in out.splitlines()[1]
    assert "hum: OK, observed traffic would reach it" in out
    assert len(state.last_probe_frames) == 3


def test_fallback_no_frames_says_check_event_name(monkeypatch, clean_state):
    _use(monkeypatch, _FakeDevice(_layout(), trace_reply={"ok": False, "error": "bad-request"}))
    out = asyncio.run(device.explain_control("temp"))
    assert out.splitlines()[0] == protocol.TRACE_FALLBACK_PREFIX
    assert "NO_FRAMES" in out and "check the event name" in out


def test_e2ee_layout_fallback_calls_out_ciphertext(monkeypatch, clean_state):
    monkeypatch.setattr(state, "last_probe_frames", list(SEALED))
    _use(monkeypatch, _FakeDevice(_layout(e2ee=True), trace_reply=None))
    out = asyncio.run(device.explain_control("temp"))
    assert out.splitlines()[0] == protocol.TRACE_FALLBACK_PREFIX
    assert "observed traffic is ciphertext" in out
    assert "connection.e2eeKey" in out


def test_fallback_uses_work_buffer_when_device_layout_missing(monkeypatch, clean_state):
    from carterkit.buffer import LayoutBuffer
    monkeypatch.setattr(state, "work_buffer", LayoutBuffer.from_layout(_layout()))
    _use(monkeypatch, _FakeDevice(None, trace_reply=None))
    out = asyncio.run(device.explain_control("plain"))
    assert "plain: has no listen sync" in out


def test_fallback_unknown_control_and_no_layout():
    out = wirecheck.explain_fallback(_layout(), "nope", [])
    assert "No control 'nope'" in out
    out = wirecheck.explain_fallback(None, "temp", [])
    assert out.startswith(protocol.TRACE_FALLBACK_PREFIX) and "No layout to check" in out


def test_not_connected(monkeypatch):
    monkeypatch.setattr(state, "socket", None)
    assert "Not connected" in asyncio.run(device.explain_control("temp"))
