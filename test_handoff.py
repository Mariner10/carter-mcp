"""Tests for the control-edit handoff (customize_on_phone) pure helpers."""

import server


def test_build_control_edit_request_shape():
    req = server.build_control_edit_request({"type": "gauge", "id": "bat"})
    assert req["msg_type"] == "control-edit-request"
    assert req["control"] == {"type": "gauge", "id": "bat"}


def test_extract_from_wrapper():
    payload = {"msg_type": "control-edit-response",
               "control": {"type": "gauge", "id": "bat", "tint": "#34C759"}}
    out = server.extract_edited_control(payload)
    assert out == {"type": "gauge", "id": "bat", "tint": "#34C759"}


def test_extract_from_bare_control():
    payload = {"type": "button", "id": "go", "msg_type": "control-edit-response"}
    out = server.extract_edited_control(payload)
    assert out == {"type": "button", "id": "go"}  # msg_type stripped


def test_extract_garbage_returns_none():
    assert server.extract_edited_control("nope") is None
    assert server.extract_edited_control({"nothing": 1}) is None
