"""Tests for the K9 studio-verb plumbing (carter-n4x.8): render-snapshot,
patch-layout, trace-control builders/formatters, the `device_supports` gate,
check_sources' newer-device wording, and routed_request's timeout pass-through.

Spec: docs/feature-research-2026-09-24/deep-dives/17-mcp-upgrades.md §2-3 and
deep-dives/14-wire-inspector.md (trace-control shape).
"""

import asyncio
import base64

import pytest

from carter_mcp import mesh, protocol, sources, state


# ─── device_supports / too_old_message ───────────────────────────────────────

@pytest.mark.parametrize("info, expected", [
    (None, None),
    ({}, None),
    ("junk", None),
    ({"protocolVersion": 2}, False),
    ({"appVersion": "1.2.5"}, False),
    ({"protocolVersion": 3}, True),
    ({"protocolVersion": 4}, True),
    ({"protocolVersion": "3"}, True),
    ({"protocolVersion": "x"}, False),
    ({"protocolVersion": 2, "features": ["verb.render-snapshot"]}, True),
    ({"protocolVersion": 2, "features": ["verb.render-snapshot@2"]}, True),
    ({"protocolVersion": 2, "features": ["control.gauge", "verb.patch-layout"]}, False),
    ({"protocolVersion": 2, "features": []}, False),
    ({"protocolVersion": 2, "features": [7, None]}, False),
])
def test_device_supports_matrix(info, expected):
    assert protocol.device_supports(info, "render-snapshot") is expected


def test_device_supports_accepts_prefixed_verb_name():
    info = {"features": ["verb.trace-control"]}
    assert protocol.device_supports(info, "verb.trace-control") is True
    assert protocol.device_supports(info, "trace-control") is True


def test_unsupported_verbs_lists_only_false():
    info = {"protocolVersion": 2, "features": ["verb.patch-layout"]}
    assert protocol.unsupported_verbs(info) == ["render-snapshot", "trace-control"]
    assert protocol.unsupported_verbs(None) == []
    assert protocol.unsupported_verbs({"protocolVersion": 3}) == []


def test_too_old_message_text():
    assert protocol.too_old_message("render-snapshot") == "update CAR-TER to 1.3 for snapshots"
    assert protocol.too_old_message("patch-layout") == "update CAR-TER to 1.3 for patch pushes"
    assert protocol.too_old_message("trace-control") == "update CAR-TER to 1.3 for control tracing"
    assert "1.3" in protocol.too_old_message("some-verb")


# ─── error envelope ──────────────────────────────────────────────────────────

def test_studio_error_message_known_and_unknown_codes():
    assert "wake the phone" in protocol.studio_error_message(
        {"ok": False, "error": "not-foreground"})
    msg = protocol.studio_error_message(
        {"ok": False, "error": "weird", "reason": "because"}, "render-snapshot")
    assert "'weird'" in msg and "render-snapshot" in msg and "(because)" in msg
    assert protocol.studio_error_message({"ok": True}) is None
    assert protocol.studio_error_message(None) is None


def test_routed_relay_error_still_handled_before_formatter():
    # A relay-level {error} without ok is caught by format_routed_response.
    out = protocol.format_routed_response({"error": "no such target"},
                                          on_ok=protocol.format_snapshot_report,
                                          verb="render-snapshot")
    assert out.startswith("Relay error on 'render-snapshot'")
    assert "timeout" in protocol.format_routed_response(
        None, on_ok=protocol.format_trace, verb="trace-control")


# ─── render-snapshot ─────────────────────────────────────────────────────────

def test_snapshot_request_defaults_and_tab():
    assert protocol.build_render_snapshot_request() == {
        "mode": "window", "scale": 1, "format": "jpeg", "quality": 0.7, "report": True}
    req = protocol.build_render_snapshot_request(tab="Power", report=False)
    assert req["tab"] == "Power" and req["report"] is False


@pytest.mark.parametrize("kwargs", [
    {"mode": "screen"}, {"scale": 0}, {"scale": 5}, {"quality": 0}, {"quality": 1.5},
    {"scale": True},
])
def test_snapshot_request_rejects_bad_args(kwargs):
    with pytest.raises(ValueError):
        protocol.build_render_snapshot_request(**kwargs)


SNAP_OK = {
    "ok": True, "image": base64.b64encode(b"\xff\xd8jpeg").decode(), "px": [402, 874],
    "scale": 1, "window": {"w": 402, "h": 874, "sizeClass": "compact", "idiom": "phone"},
    "tab": "Power", "layoutName": "Nordlys Station",
    "controls": {
        "pv-gauge": {"frame": [202, 212, 178, 112], "cell": 112, "content": 112,
                     "scale": 1, "cuts": False},
        "battery-ring": {"frame": [16, 212, 178, 56], "cell": 56.0, "content": 91,
                         "scale": 0.75, "cuts": True},
    },
    "offscreen": ["log-tail"], "unverified": ["route-map"], "locked": [],
}


def test_format_snapshot_report_ok():
    out = protocol.format_snapshot_report(SNAP_OK)
    lines = out.splitlines()
    assert "Nordlys Station" in lines[0] and "tab Power" in lines[0]
    assert "402×874px" in lines[0] and "compact" in lines[0]
    assert "Fit (1 cut of 2):" in out
    # Cut control first, in the spec's exact fit-line shape.
    assert lines[2] == "  battery-ring: cell 56pt, content 91pt, scaled 0.75, CUT"
    assert lines[3] == "  pv-gauge: cell 112pt, content 112pt"
    assert "Offscreen (below the fold): log-tail" in out
    assert "Unverified (map/camera/web pixels may be blank): route-map" in out
    assert "Locked" not in out  # empty list omitted


def test_format_snapshot_report_locked_and_sheet():
    out = protocol.format_snapshot_report({"ok": True, "layoutName": "X", "controls": {},
                                           "locked": ["pro-chart"], "sheetPresented": True})
    assert "Locked (Pro placeholder, not a bug): pro-chart" in out
    assert "sheet" in out and "No fit report" in out


@pytest.mark.parametrize("code, needle", [
    ("not-foreground", "wake the phone"),
    ("too-large", "lower `scale`/`quality`"),
    ("studio-not-sealed", "E2EE"),
])
def test_format_snapshot_report_errors(code, needle):
    assert needle in protocol.format_snapshot_report({"ok": False, "error": code})


def test_extract_snapshot_image():
    assert protocol.extract_snapshot_image(SNAP_OK) == b"\xff\xd8jpeg"
    assert protocol.extract_snapshot_image({"ok": True, "image": "!!notb64"}) is None
    assert protocol.extract_snapshot_image({"ok": False, "error": "too-large"}) is None


# ─── patch-layout ────────────────────────────────────────────────────────────

ALL_OPS = [
    {"op": "update", "id": "battery-ring", "set": {"span": [2, 2]}, "unset": ["style"]},
    {"op": "add", "tab": "Power", "control": {"id": "soc-label", "type": "label"}},
    {"op": "remove", "id": "old-spark"},
    {"op": "move", "id": "pv-gauge", "position": [2, 0]},
    {"op": "layout", "set": {"accentColor": "#7CFFB2"}},
    {"op": "renameTab", "from": "Power", "to": "Energy"},
]


def test_patch_request_accepts_every_op_kind():
    req = protocol.build_patch_layout_request("sha256:9f1c", ALL_OPS)
    assert req == {"base": "sha256:9f1c", "ops": ALL_OPS}
    assert {o["op"] for o in ALL_OPS} == set(protocol.PATCH_OP_KINDS)


def test_patch_request_rejects_unknown_op_kind():
    with pytest.raises(ValueError, match="unknown op kind 'replace'"):
        protocol.build_patch_layout_request("sha256:a", [{"op": "replace", "id": "x"}])


@pytest.mark.parametrize("base, ops", [
    ("", ALL_OPS[:1]),
    (None, ALL_OPS[:1]),
    ("sha256:a", []),
    ("sha256:a", "nope"),
    ("sha256:a", ["not-a-dict"]),
    ("sha256:a", [{"op": "update", "id": "x"}]),                 # nothing to set
    ("sha256:a", [{"op": "update", "set": {"a": 1}}]),           # no id
    ("sha256:a", [{"op": "add", "control": {"id": "c"}}]),       # no tab
    ("sha256:a", [{"op": "add", "tab": "T", "control": {}}]),    # no control id
    ("sha256:a", [{"op": "move", "id": "x"}]),                   # nowhere to move
    ("sha256:a", [{"op": "renameTab", "from": "A"}]),            # no `to`
    ("sha256:a", [{"op": "layout", "set": []}]),                 # set not an object
])
def test_patch_request_rejects_malformed(base, ops):
    with pytest.raises(ValueError):
        protocol.build_patch_layout_request(base, ops)


def test_format_patch_result_hot_and_full():
    hot = protocol.format_patch_result({
        "ok": True, "hash": "sha256:41ab", "applied": 4, "reload": "hot",
        "rendered": {"name": "Nordlys", "tabs": [{"title": "Power", "controls": [
            {"id": "battery-ring", "type": "ring", "span": [2, 2]}]}]}})
    assert "4 op(s) applied" in hot and "hot reload" in hot
    assert "New base: sha256:41ab" in hot and "battery-ring (ring)" in hot
    full = protocol.format_patch_result({"ok": True, "applied": 1, "reload": "full"})
    assert "full reload" in full


def test_format_patch_result_stale_base():
    resp = {"ok": False, "error": "stale-base", "hash": "sha256:beef"}
    assert protocol.is_stale_base(resp)
    out = protocol.format_patch_result(resp)
    assert "stale-base" in out and "sha256:beef" in out and "full apply-layout" in out
    assert not protocol.is_stale_base({"ok": True, "hash": "x"})


def test_format_patch_result_other_error():
    out = protocol.format_patch_result({"ok": False, "error": "not-foreground"})
    assert out.startswith("Patch failed:") and "wake the phone" in out


# ─── trace-control ───────────────────────────────────────────────────────────

def test_trace_request_shapes():
    assert protocol.build_trace_control_request("temp") == {"ids": ["temp"], "frames": 20}
    assert protocol.build_trace_control_request(["a", "b"], frames=0) == {
        "ids": ["a", "b"], "frames": 0}
    for bad in ([], "", ["ok", ""], None):
        with pytest.raises(ValueError):
            protocol.build_trace_control_request(bad)
    for bad_frames in (-1, 2.5, True):
        with pytest.raises(ValueError):
            protocol.build_trace_control_request("a", frames=bad_frames)


def test_format_trace_ok():
    out = protocol.format_trace({
        "ok": True, "window_s": 60, "controls": [{
            "id": "temp",
            "registrations": [{"event": "broadcast", "filter": {"msg_type": "telemetery"},
                               "valuePath": "t.c", "delivered": 0, "filterMiss": 12,
                               "nearMiss": 12, "pathMiss": 0, "typeMiss": 0}],
            "findings": ["msg_type 'telemetery' never seen; did you mean 'telemetry'?",
                         {"code": "NO_HUB_ON_CHANNEL"}],
            "frames": [{"at": 1, "verdict": "filter_miss"}, {"at": 2, "verdict": "filter_miss"},
                       {"at": 3, "verdict": "delivered"}],
            "actions": [{"at": 4, "event": "broadcast_request", "cmd": "set",
                         "outcome": "ack", "rtt_ms": 84}],
        }, {"id": "idle"}]})
    assert out.splitlines()[0] == "Control trace (last 60s)"
    assert ('reg broadcast filter {"msg_type":"telemetery"} path t.c: '
            "delivered 0, filterMiss 12, nearMiss 12, pathMiss 0, typeMiss 0") in out
    assert "finding: msg_type 'telemetery' never seen" in out
    assert "finding: NO_HUB_ON_CHANNEL" in out
    assert "frames (3): delivered ×1, filter_miss ×2" in out
    assert "action broadcast_request set → ack (84 ms)" in out
    assert "- idle\n    no sync registrations" in out


def test_format_trace_empty_and_error():
    assert "No controls traced." in protocol.format_trace({"ok": True, "controls": []})
    assert "rate-limiting" in protocol.format_trace({"ok": False, "error": "rate-limited"})


# ─── routed_request timeout pass-through ─────────────────────────────────────

class _FakeSocket:
    is_running = True

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def request(self, event, payload=None, timeout=None):
        self.calls.append((event, payload, timeout))
        return self.reply


@pytest.fixture
def fake_socket(monkeypatch):
    sock = _FakeSocket({"ok": True, "controls": []})
    monkeypatch.setattr(state, "socket", sock)
    monkeypatch.setattr(state, "peer_list", [{"id": "dev-1"}])
    return sock


def test_routed_request_default_timeout_unchanged(fake_socket):
    out = asyncio.run(mesh.routed_request("trace-control", {"ids": ["a"]},
                                          on_ok=protocol.format_trace))
    assert "No controls traced." in out
    event, payload, timeout = fake_socket.calls[-1]
    assert event == "route_msg" and timeout == 5.0
    assert payload == {"target_id": "dev-1", "type": "trace-control",
                       "payload": {"ids": ["a"]}}


def test_routed_request_passes_timeout(fake_socket):
    asyncio.run(mesh.routed_request("render-snapshot", {}, on_ok=str, timeout=2.5))
    assert fake_socket.calls[-1][2] == 2.5


# ─── check_sources: newer device + missing verbs ─────────────────────────────

@pytest.fixture
def offline_sources(monkeypatch):
    manifest = {"protocolVersion": 2, "fingerprint": None}
    monkeypatch.setattr(sources, "fetch_catalog_bundle",
                        lambda force=False: ({"manifest": manifest}, "network"))
    monkeypatch.setattr(sources, "installed_carterkit_version", lambda: "0.15.0")
    monkeypatch.setattr(sources, "latest_carterkit_version", lambda force=False: "0.15.0")
    monkeypatch.setattr(sources, "carterkit_is_current", lambda: True)
    monkeypatch.setattr(sources, "carterkit_controldocs_dir", lambda: None)
    return manifest


def test_check_sources_newer_device_not_told_to_update(offline_sources):
    st = sources.sources_status({"appVersion": "1.3.0", "protocolVersion": 3})
    joined = " ".join(st["issues"])
    assert "update the app" not in joined
    assert "catalog is behind" in joined
    assert st["missingVerbs"] == []


def test_check_sources_older_device_still_told_to_update(offline_sources):
    offline_sources["protocolVersion"] = 3
    st = sources.sources_status({"appVersion": "1.2.5", "protocolVersion": 2})
    assert any("update the app" in i for i in st["issues"])


def test_check_sources_names_missing_verbs(offline_sources):
    st = sources.sources_status({"appVersion": "1.3.0", "protocolVersion": 2,
                                 "features": ["verb.patch-layout"]})
    assert st["missingVerbs"] == ["render-snapshot", "trace-control"]
    assert st["aligned"] is True  # informational, not drift
    out = sources.format_status(st)
    assert "studio verbs not supported: render-snapshot, trace-control" in out
    assert "1.3" in out


def test_check_sources_no_device_no_missing_verbs(offline_sources):
    st = sources.sources_status(None)
    assert st["missingVerbs"] == []
    assert "studio verbs" not in sources.format_status(st)
