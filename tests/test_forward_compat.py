"""Forward-compat preview + feature-set drift (carter-5sn.6). Pure functions only.

Fixtures stand in for a carter-5sn.4 app's get-device-info reply (`features`).
"""

import pytest

from carter_mcp import forward_compat as fc
from carter_mcp import protocol, sources

FEATURES = [
    "control.button", "control.gauge", "control.label", "control.slider",
    "control.carousel", "control.canvas",
    "sync.meshsocket", "sync.mqtt", "action.meshsocket",
    "layout.requires", "layout.fallback@2",
]
DEVICE = {"appVersion": "1.3.0", "build": "60", "protocolVersion": 2,
          "features": FEATURES}
OLD_DEVICE = {"appVersion": "1.2.4", "build": "55", "protocolVersion": 2}


def _layout(*children, requires=None):
    out = {"name": "T", "tabs": [{"name": "A", "children": list(children)}]}
    if requires is not None:
        out["requires"] = requires
    return out


# ─── device info ─────────────────────────────────────────────────────────────

def test_extract_device_info_keeps_features():
    info = protocol.extract_device_info(dict(DEVICE, ok=True))
    assert info["features"] == FEATURES
    assert "ok" not in info


def test_format_device_info_prints_feature_count():
    out = protocol.format_device_info(DEVICE)
    assert f"features: {len(FEATURES)} reported" in out
    assert "features" not in protocol.format_device_info(OLD_DEVICE)


def test_feature_version_grammar():
    have = fc.feature_map(["layout.fallback@2", "control.gauge", 7, ""])
    assert have == {"layout.fallback": 2, "control.gauge": 1}
    assert fc.supports("layout.fallback@2", have)
    assert not fc.supports("layout.fallback@3", have)
    assert fc.supports("control.gauge", have)
    assert not fc.supports("control.gauge@2", have)


# ─── layout_forward_compat ───────────────────────────────────────────────────

def test_no_features_is_unknown():
    lay = _layout({"id": "x", "type": "hologram"}, requires={"app": "9.0"})
    assert fc.layout_forward_compat(lay, OLD_DEVICE) == []
    assert fc.layout_forward_compat(lay, None) == []
    assert fc.layout_forward_compat(lay, dict(OLD_DEVICE, features=[])) == []
    assert protocol.layout_forward_compat is fc.layout_forward_compat


def test_clean_layout_has_no_lines():
    lay = _layout({"id": "g", "type": "gauge",
                   "sync": [{"method": "meshsocket", "valuePath": "cpu"}]},
                  requires={"app": "1.3", "features": ["layout.requires"]})
    assert fc.layout_forward_compat(lay, DEVICE) == []
    assert fc.format_forward_compat([]) == ""


def test_missing_control_renders_placeholder():
    lines = fc.layout_forward_compat(_layout({"id": "h", "type": "hologram"}), DEVICE)
    assert len(lines) == 1
    assert "'h' (hologram)" in lines[0] and "placeholder" in lines[0]


def test_fallback_is_named():
    ctrl = {"id": "h", "type": "hologram", "fallback": {"type": "gauge"}}
    lines = fc.layout_forward_compat(_layout(ctrl), DEVICE)
    assert lines == ["'h' (hologram) at tabs[0]/h: phone doesn't know 'hologram', "
                     "falls back to gauge"]


def test_fallback_chain_and_unknown_fallback():
    chain = {"id": "a", "type": "hologram",
             "fallback": {"type": "radar", "fallback": {"type": "label"}}}
    dead = {"id": "b", "type": "hologram", "fallback": {"type": "radar"}}
    lines = fc.layout_forward_compat(_layout(chain, dead), DEVICE)
    assert "falls back to label" in lines[0]
    assert "placeholder" in lines[1]


def test_group_fallback_not_usable_in_canvas():
    inner = {"id": "c1", "type": "hologram", "fallback": {"type": "group"}}
    canvas = {"id": "cv", "type": "canvas",
              "canvasConfig": {"items": [{"control": inner}]}}
    top = dict(inner, id="c2")
    lines = fc.layout_forward_compat(_layout(canvas, top), DEVICE)
    assert any("'c1'" in ln and "placeholder" in ln for ln in lines)
    assert any("'c2'" in ln and "falls back to group" in ln for ln in lines)


def test_unmet_requires_app():
    lines = fc.layout_forward_compat(_layout(requires={"app": "1.4"}), DEVICE)
    assert lines == ["layout requires app 1.4 (device has 1.3.0): "
                     "layout shows an update banner"]
    assert fc.layout_forward_compat(_layout(requires={"app": "1.3"}), DEVICE) == []


def test_unmet_requires_features():
    req = {"features": ["control.hologram", "layout.fallback@3", "sync.mqtt"]}
    lines = fc.layout_forward_compat(_layout(requires=req), DEVICE)
    assert len(lines) == 1
    assert "control.hologram, layout.fallback@3" in lines[0]
    assert "sync.mqtt" not in lines[0] and "update banner" in lines[0]


def test_missing_sync_and_action_methods():
    ctrl = {"id": "s", "type": "slider",
            "sync": [{"method": "meshsocket"}, {"method": "http", "path": "/x"}],
            "action": {"method": "mqtt", "topic": "t"}}
    lines = fc.layout_forward_compat(_layout(ctrl), DEVICE)
    assert any("sync method 'http'" in ln and "binding ignored" in ln for ln in lines)
    assert any("action method 'mqtt'" in ln for ln in lines)
    assert not any("meshsocket" in ln for ln in lines)


def test_nested_groups_panels_canvas_longpress_walked():
    deep = {"id": "d", "type": "hologram"}
    group = {"type": "group", "children": [
        {"type": "group", "children": [{"id": "g2", "type": "hologram"}]}]}
    carousel = {"id": "car", "type": "carousel",
                "panels": [{"children": [{"id": "p1", "type": "hologram"}]}]}
    canvas = {"id": "cv", "type": "canvas",
              "canvasConfig": {"items": [{"control": {"id": "ci", "type": "hologram"}}]}}
    button = {"id": "b", "type": "button",
              "longPressGroup": {"children": [{"id": "lp", "type": "hologram"}]}}
    lines = fc.layout_forward_compat(_layout(deep, group, carousel, canvas, button), DEVICE)
    for cid in ("'d'", "'g2'", "'p1'", "'ci'", "'lp'"):
        assert any(cid in ln for ln in lines), cid
    assert len(lines) == 5
    assert any("tabs[0]/car/panels[0]/p1" in ln for ln in lines)


def test_format_block_truncates():
    out = fc.format_forward_compat([f"line {i}" for i in range(25)], limit=20)
    assert out.startswith("Forward-compat")
    assert "  - line 19" in out and "line 20" not in out and "5 more" in out


# ─── check_sources: feature-set drift ────────────────────────────────────────

@pytest.fixture
def fake_sources(monkeypatch):
    bundle = {"manifest": {"protocolVersion": 1, "fingerprint": None,
                           "features": ["layout.requires"]},
              "catalog": {"gauge": {}, "button": {}, "hologram": {}, "radar": {}}}
    monkeypatch.setattr(sources, "fetch_catalog_bundle",
                        lambda force=False: (bundle, "cache"))
    monkeypatch.setattr(sources, "installed_carterkit_version", lambda: "0.13.1")
    monkeypatch.setattr(sources, "latest_carterkit_version", lambda force=False: "0.13.1")
    monkeypatch.setattr(sources, "carterkit_is_current", lambda: True)
    monkeypatch.setattr(sources, "carterkit_controldocs_dir", lambda: None)
    return bundle


def test_status_lists_missing_catalog_controls_and_skips_protocol(fake_sources):
    dev = dict(DEVICE, protocolVersion=0)          # would trip the int compare
    st = sources.sources_status(device_info=dev)
    assert st["device"]["missingFeatures"] == ["control.hologram", "control.radar"]
    assert len(st["issues"]) == 1
    assert "control.hologram, control.radar" in st["issues"][0]
    assert "protocol" not in st["issues"][0]
    assert "missingFeatures" not in dev              # caller's dict untouched
    text = sources.format_status(st)
    assert "2 catalog feature(s) missing" in text
    assert "- missing: control.hologram, control.radar" in text


def test_status_aligned_when_device_has_every_feature(fake_sources):
    dev = dict(DEVICE, features=FEATURES + ["control.hologram", "control.radar"])
    st = sources.sources_status(device_info=dev)
    assert st["aligned"] and st["device"]["missingFeatures"] == []


def test_featureless_newer_protocol_is_not_drift(fake_sources):
    st = sources.sources_status(device_info=OLD_DEVICE)   # app v2, catalog v1
    assert st["issues"] == [] and st["device"] == OLD_DEVICE
    assert "features" not in sources.format_status(st)


def test_featureless_older_protocol_is_drift(fake_sources):
    fake_sources["manifest"]["protocolVersion"] = 3
    st = sources.sources_status(device_info=OLD_DEVICE)
    assert len(st["issues"]) == 1 and "protocol v2" in st["issues"][0]


# ─── push: routed apply-layout appends the block and still applies ──────────

class _FakeSocket:
    is_running = True

    def __init__(self, info):
        self.info, self.calls = info, []

    async def request(self, event, payload=None, timeout=None):
        verb = (payload or {}).get("type")
        self.calls.append(verb)
        if verb == "apply-layout":
            return {"ok": True, "rendered": {"name": "T", "tabs": []}}
        if verb == "get-device-info":
            return self.info
        return None


def _push(monkeypatch, info, layout):
    import asyncio
    from carter_mcp import mesh, state
    sock = _FakeSocket(info)
    monkeypatch.setattr(state, "socket", sock)
    monkeypatch.setattr(state, "peer_list", [{"id": "dev1"}])
    monkeypatch.setattr(state, "device_info_cache", {})
    return asyncio.run(mesh.apply_or_broadcast(layout)), sock


def test_push_appends_forward_compat_and_applies(monkeypatch):
    lay = _layout({"id": "h", "type": "hologram", "fallback": {"type": "gauge"}},
                  requires={"app": "2.0"})
    text, sock = _push(monkeypatch, dict(DEVICE, ok=True), lay)
    assert text.startswith("Device rendered the layout")
    assert "Forward-compat" in text and "falls back to gauge" in text
    assert "update banner" in text
    assert sock.calls == ["apply-layout", "get-device-info"]


def test_push_to_featureless_app_is_unchanged_and_asks_once(monkeypatch):
    from carter_mcp import mesh
    import asyncio
    lay = _layout({"id": "h", "type": "hologram"})
    text, sock = _push(monkeypatch, dict(OLD_DEVICE, ok=True), lay)
    assert "Forward-compat" not in text
    asyncio.run(mesh.apply_or_broadcast(lay))
    assert sock.calls.count("get-device-info") == 1
