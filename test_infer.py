"""Tests for infer.py — payload → wired layout inference."""

import infer


def test_humanize_and_sanitize():
    assert infer.humanize("cpu_temp") == "Cpu Temp"
    assert infer.humanize("cpuTemp") == "Cpu Temp"
    assert infer.sanitize_id("sensors.cpu.temp") == "sensors-cpu-temp"


def test_nice_max():
    assert infer.nice_max(0.4) == 1
    assert infer.nice_max(73) == 100
    assert infer.nice_max(420) == 500
    assert infer.nice_max(1500) == 2000


def test_scalar_type_mapping():
    payload = {"on": True, "ratio": 0.5, "temp": 73, "name": "hello", "state": "online"}
    by_id = {c["id"]: c for c in infer.infer_controls(payload)}
    assert by_id["on"]["type"] == "toggle"
    assert by_id["ratio"]["type"] == "progressRing"
    assert by_id["temp"]["type"] == "gauge" and by_id["temp"]["max"] == 100
    assert by_id["name"]["type"] == "label"
    assert by_id["state"]["type"] == "statusLight"


def test_nested_paths_and_sync():
    payload = {"sensors": {"cpu": {"temp": 50}}}
    ctrls = infer.infer_controls(payload, event="telemetry")
    assert len(ctrls) == 1
    c = ctrls[0]
    assert c["id"] == "sensors-cpu-temp"
    assert c["sync"][0]["valuePath"] == "sensors.cpu.temp"
    assert c["sync"][0]["event"] == "telemetry"


def test_geo_object_becomes_map():
    payload = {"position": {"lat": 42.3, "lng": -71.0}}
    ctrls = infer.infer_controls(payload)
    assert len(ctrls) == 1 and ctrls[0]["type"] == "map"


def test_number_array_is_sparkline():
    payload = {"history": [1, 2, 3, 4]}
    ctrls = infer.infer_controls(payload)
    assert ctrls[0]["type"] == "sparkline"


def test_object_array_is_cardlist():
    payload = {"events": [{"a": 1}, {"a": 2}]}
    ctrls = infer.infer_controls(payload)
    assert ctrls[0]["type"] == "cardList"


def test_build_layout_places_all_controls():
    payload = {"a": 1, "b": True, "c": "online", "d": 0.5}
    layout = infer.build_layout(payload, name="Live", columns=4, rows=8)
    assert layout["name"] == "Live"
    children = layout["tabs"][0]["children"]
    assert len(children) == 4
    assert all("position" in c for c in children)
    # ids unique
    ids = [c["id"] for c in children]
    assert len(ids) == len(set(ids))


def test_build_layout_grows_when_full():
    # 12 fields into a 4x2 grid (8 cells) must not drop any — rows grow.
    payload = {f"f{i}": i + 2 for i in range(12)}
    layout = infer.build_layout(payload, columns=4, rows=2)
    assert len(layout["tabs"][0]["children"]) == 12
