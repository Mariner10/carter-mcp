"""Tests for the live-mesh trio: simulate, probe, autowire (pure parts)."""

from carter_mcp import autowire
from carter_mcp import probe
from carter_mcp import simulate


# ─── simulate ────────────────────────────────────────────────────────────────

def test_build_frame_nests_and_merges():
    frame = simulate.build_frame({"a.b": 1, "a.c": 2, "x": 3})
    assert frame == {"a": {"b": 1, "c": 2}, "x": 3}


def test_control_binding():
    ctrl = {"sync": [{"type": "listen", "event": "broadcast", "valuePath": "battery"}]}
    assert simulate.control_binding(ctrl) == ("broadcast", "battery")
    assert simulate.control_binding({"sync": []}) is None
    assert simulate.control_binding({}) is None


# ─── probe ───────────────────────────────────────────────────────────────────

def test_flatten_paths_nested_and_array():
    out = probe.flatten_paths({"a": {"b": 5}, "arr": [1, 2], "s": "x"})
    assert out["a.b"] == {"kind": "scalar", "value": 5}
    assert out["arr"]["kind"] == "array" and out["arr"]["len"] == 2
    assert out["s"]["value"] == "x"


def test_aggregate_tracks_range_and_examples():
    frames = [("broadcast", {"cpu": 40}), ("broadcast", {"cpu": 90}),
              ("broadcast", {"state": "online"})]
    agg = probe.aggregate(frames)
    cpu = agg["broadcast"]["cpu"]
    assert cpu["min"] == 40 and cpu["max"] == 90 and cpu["count"] == 2
    assert "online" in agg["broadcast"]["state"]["examples"]


def test_candidate_paths_and_format():
    agg = probe.aggregate([("broadcast", {"a": 1, "b": {"c": 2}})])
    assert probe.candidate_paths(agg) == ["a", "b.c"]
    out = probe.format_discovery(agg)
    assert "event 'broadcast'" in out and "b.c" in out


def test_format_discovery_empty():
    assert "No traffic" in probe.format_discovery({})


# ─── autowire ────────────────────────────────────────────────────────────────

def test_best_path_exact_and_token():
    paths = ["sensors.cpu_temp", "battery", "sensors.fan_rpm"]
    assert autowire.best_path("battery", paths) == "battery"
    assert autowire.best_path("CPU Temp", paths) == "sensors.cpu_temp"
    assert autowire.best_path("nothing relevant", paths) is None


def _layout(children):
    return {"name": "T", "version": 1,
            "tabs": [{"title": "M", "icon": "i",
                      "grid": {"columns": 4, "rows": 4}, "children": children}]}


def test_autowire_binds_unbound_display_controls():
    layout = _layout([
        {"type": "gauge", "id": "battery", "position": [0, 0]},
        {"type": "gauge", "id": "temp", "label": "CPU Temp", "position": [0, 2]},
        {"type": "button", "id": "go", "position": [2, 0]},  # input control: skipped
    ])
    wired = autowire.autowire_layout(layout, ["battery", "sensors.cpu_temp"], event="broadcast")
    wired_ids = {w[0] for w in wired}
    assert "battery" in wired_ids and "temp" in wired_ids and "go" not in wired_ids
    gauge = layout["tabs"][0]["children"][0]
    assert gauge["sync"][0]["valuePath"] == "battery"


def test_autowire_skips_already_bound():
    layout = _layout([
        {"type": "gauge", "id": "battery", "position": [0, 0],
         "sync": [{"type": "listen", "event": "broadcast", "valuePath": "bat_pct"}]},
    ])
    wired = autowire.autowire_layout(layout, ["battery"])
    assert wired == []  # already had a sync


def test_live_data_lint_flags_unknown_paths():
    layout = _layout([
        {"type": "gauge", "id": "g", "position": [0, 0],
         "sync": [{"type": "listen", "event": "broadcast", "valuePath": "cpu"}]},
    ])
    findings = autowire.live_data_lint(layout, known_paths=["cpu_temp"])
    assert len(findings) == 1 and findings[0]["valuePath"] == "cpu"


# ─── wirecheck: app-rule lint (carterkit.wire) + ImportError fallback ────────

import importlib
import sys

import pytest

from carter_mcp import wirecheck


def _filtered_layout():
    return _layout([
        {"type": "gauge", "id": "cpu", "position": [0, 0],
         "sync": [{"type": "listen", "event": "broadcast",
                   "filter": {"msg_type": "telemetry"}, "valuePath": "sys.cpu"}]},
        {"type": "label", "id": "mood", "position": [1, 0],
         "sync": [{"type": "listen", "event": "broadcast", "valuePath": "mood.0"}]},
        {"type": "label", "id": "blob", "position": [2, 0],
         "sync": [{"type": "listen", "event": "broadcast", "valuePath": "info"}]},
        {"type": "gauge", "id": "ok", "position": [3, 0],
         "sync": [{"type": "listen", "event": "broadcast", "valuePath": "fine"}]},
    ])


_FRAMES = [{"msg_type": "telemetery", "sys": {"cpu": 12}, "mood": {"x": 1},
            "info": {"a": 1}, "fine": 3}]


@pytest.fixture
def no_wire(monkeypatch):
    monkeypatch.setattr(wirecheck, "wire", None)


def test_wirecheck_import_falls_back_without_wire(monkeypatch):
    import carterkit
    monkeypatch.delattr(carterkit, "wire", raising=False)  # an already-imported kit.wire
    monkeypatch.setitem(sys.modules, "carterkit.wire", None)  # import -> ImportError
    try:
        mod = importlib.reload(wirecheck)
        assert mod.wire is None and not mod.have_wire()
        assert importlib.import_module("carter_mcp.tools.wiring")
    finally:
        monkeypatch.undo()
        importlib.reload(wirecheck)


def test_lint_fallback_uses_seen_paths(no_wire):
    findings = wirecheck.lint_traffic(_filtered_layout(), _FRAMES,
                                      known_paths=["sys.cpu", "fine", "info"])
    assert [(f["id"], f["code"]) for f in findings] == [("mood", "PATH_NOT_SEEN")]
    assert "Paths not seen" in wirecheck.format_findings(findings)


@pytest.mark.parametrize("use_wire", [True, False])
def test_set_control_value_frame_carries_filter_keys(use_wire, monkeypatch):
    if use_wire:
        pytest.importorskip("carterkit.wire")
    else:
        monkeypatch.setattr(wirecheck, "wire", None)
    ctrl = _filtered_layout()["tabs"][0]["children"][0]
    event, frame = wirecheck.frame_for_control(ctrl, 61)
    assert event == "broadcast"
    assert frame == {"msg_type": "telemetry", "sys": {"cpu": 61}}
    assert wirecheck.frame_for_control({"sync": []}, 1) is None


def test_lint_reports_near_miss_and_failing_segment():
    pytest.importorskip("carterkit.wire")
    findings = {f["id"]: f for f in wirecheck.lint_traffic(_filtered_layout(), _FRAMES)}
    assert set(findings) == {"cpu", "mood", "blob"}
    assert findings["cpu"]["code"] == "FILTER_MISS" and findings["cpu"]["near"]
    assert "telemetery" in findings["cpu"]["detail"]
    assert findings["mood"]["code"] == "PATH_MISSING"
    assert "segment '0'" in findings["mood"]["detail"]
    assert findings["blob"]["code"] == "TYPE_UNRENDERABLE"
    text = wirecheck.format_findings(list(findings.values()))
    assert "near miss" in text


def test_lint_unrelated_traffic_is_not_near():
    pytest.importorskip("carterkit.wire")
    findings = wirecheck.lint_traffic(_filtered_layout(), [{"msg_type": "weather"}])
    cpu = next(f for f in findings if f["id"] == "cpu")
    assert cpu["code"] == "FILTER_MISS" and not cpu["near"]


def test_lint_true_is_not_one():
    pytest.importorskip("carterkit.wire")
    layout = _layout([{"type": "label", "id": "x", "position": [0, 0],
                       "sync": [{"type": "listen", "filter": {"on": True}, "valuePath": "v"}]}])
    assert wirecheck.lint_traffic(layout, [{"on": 1, "v": "a"}])[0]["code"] == "FILTER_MISS"
    assert wirecheck.lint_traffic(layout, [{"on": True, "v": "a"}]) == []


def test_simulate_report_per_control():
    pytest.importorskip("carterkit.wire")
    lines = wirecheck.simulate_report(_filtered_layout(),
                                      {"msg_type": "telemetry", "sys": {"cpu": 5}, "fine": 1})
    joined = "\n".join(lines)
    assert "cpu: DELIVERED" in joined and "ok: DELIVERED" in joined
    assert "mood: PATH_MISSING" in joined


def test_simulate_report_empty_without_wire(no_wire):
    assert wirecheck.simulate_report(_filtered_layout(), {"fine": 1}) == []
