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
