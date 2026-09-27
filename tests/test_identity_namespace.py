"""carter-1o0: the MCP's validators agree with the device about the id namespace.
validate_layout / validate_buffer flag a container panel child or long-press popup
child that reuses a grid id; lint_dynamic_traffic flags a deck id that reuses a layout
id (the app refuses such a deck). The checks live in carterkit, so with an installed
kit that predates them these cases are expected failures naming the kit version."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("CARTER_OFFLINE", "1")   # catalog from cache/installed kit, never HTTP

from carterkit import validate as kit_validate           # noqa: E402
from carterkit.buffer import LayoutBuffer                # noqa: E402

from carter_mcp import state                             # noqa: E402
from carter_mcp.tools import buffer as buffer_tools      # noqa: E402
from carter_mcp.tools import wiring as wiring_tools      # noqa: E402

try:
    from importlib.metadata import version as _version
    KIT_VERSION = _version("carterkit")
except Exception:                       # pragma: no cover
    KIT_VERSION = "?"

stale_kit = pytest.mark.xfail(
    not hasattr(kit_validate, "identity_ids"), strict=True,
    reason=f"installed carterkit {KIT_VERSION} predates the carter-1o0 id namespace")


def _label(cid):
    return {"id": cid, "type": "label", "text": "x", "position": [0, 0]}


def _host(gid, *kids):
    return {"id": gid, "position": [0, 0], "grid": {"columns": 1, "rows": 1},
            "children": list(kids)}


def _doc(*children):
    return {"name": "t", "version": 1, "tabs": [
        {"title": "A", "icon": "star", "grid": {"columns": 4, "rows": 8},
         "children": list(children)}]}


def _both(layout) -> str:
    import json
    report = buffer_tools.validate_layout(layout_json=json.dumps(layout))
    state.work_buffer = LayoutBuffer.from_layout(layout)
    try:
        assert buffer_tools.validate_buffer() == report
    finally:
        state.work_buffer = None
    return report


@stale_kit
def test_panel_child_duplicate_id_is_reported():
    carousel = {"type": "carousel", "id": "c", "position": [0, 1],
                "panels": [_host("p", _label("x"))]}
    assert "[duplicate_id]" in _both(_doc(_label("x"), carousel))


@stale_kit
def test_long_press_child_duplicate_id_is_reported():
    button = {"type": "button", "id": "b", "position": [0, 2],
              "longPressGroup": _host("l", _label("x"))}
    assert "[duplicate_id]" in _both(_doc(_label("x"), button))


@stale_kit
def test_deck_reusing_a_layout_id_is_reported():
    slot = {"type": "group", "id": "deck", "dynamic": "deck", "position": [1, 0],
            "grid": {"columns": 4, "rows": 4}, "children": []}
    state.work_buffer = LayoutBuffer.from_layout(_doc(_label("x"), slot))
    state.last_probe_frames = [{"msg_type": "deck", "children": [_label("x")]}]
    try:
        report = wiring_tools.lint_dynamic_traffic()
    finally:
        state.work_buffer = None
        state.last_probe_frames = []
    assert "[duplicate_id]" in report and "layout" in report


def test_panel_and_popup_group_ids_stay_local():
    carousel = {"type": "carousel", "id": "c", "position": [0, 1],
                "panels": [_host("p", _label("a"))]}
    button = {"type": "button", "id": "b", "position": [0, 2],
              "longPressGroup": _host("p", _label("z"))}
    assert "[duplicate_id]" not in _both(_doc(_label("p"), carousel, button))
