"""Tests for codegen.py — service-stub + REST-adapter generation."""

import codegen

LAYOUT = {
    "name": "Rover", "version": 1,
    "connection": {"url": "ws://10.0.0.5:8765", "token": "tok"},
    "tabs": [{"title": "Main", "icon": "house.fill",
              "grid": {"columns": 4, "rows": 4}, "children": [
                  {"type": "gauge", "id": "battery", "position": [0, 0],
                   "sync": [{"method": "meshsocket", "type": "listen",
                             "event": "broadcast", "valuePath": "battery"}]},
                  {"type": "gauge", "id": "temp", "position": [0, 2],
                   "sync": [{"method": "meshsocket", "type": "listen",
                             "event": "broadcast", "valuePath": "sensors.cpu_temp"}]},
                  {"type": "button", "id": "stop", "position": [2, 0],
                   "action": {"method": "meshsocket", "mode": "request",
                              "event": "emergency_stop", "payload": {}}},
              ]}]}


def test_analyze_layout_contract():
    spec = codegen.analyze_layout(LAYOUT)
    assert spec["actions"] == {"emergency_stop": "request"}
    assert spec["emits"]["broadcast"] == ["battery", "sensors.cpu_temp"]


def test_service_stub_compiles_and_mentions_contract():
    code = codegen.generate_service_stub(LAYOUT)
    compile(code, "<stub>", "exec")  # must be valid Python
    assert "emergency_stop" in code
    assert "battery" in code and "sensors.cpu_temp" in code
    assert "ws://10.0.0.5:8765" in code  # connection carried through


def test_rest_adapter_compiles_and_maps_fields():
    code = codegen.generate_rest_adapter(LAYOUT, base_url="https://rover.local/api")
    compile(code, "<adapter>", "exec")
    assert "https://rover.local/api" in code
    assert "battery" in code and "cpu_temp" in code


def test_empty_layout_still_compiles():
    code = codegen.generate_service_stub({"name": "Empty", "version": 1, "tabs": []})
    compile(code, "<stub>", "exec")
