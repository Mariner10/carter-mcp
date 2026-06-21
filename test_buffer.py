"""Tests for buffer.py — incremental layout editing operations."""

import pytest

import buffer
from buffer import LayoutBuffer, BufferError


def test_blank_buffer_shape():
    b = LayoutBuffer.blank(name="Demo", columns=3, rows=5)
    assert b.layout["name"] == "Demo"
    assert b.layout["version"] == 1
    assert len(b.tabs) == 1
    assert b.tabs[0]["grid"] == {"columns": 3, "rows": 5}


def test_add_control_auto_places_and_dedupes_id():
    b = LayoutBuffer.blank(columns=4, rows=4)
    a = b.add_control({"type": "button", "id": "go"})
    c = b.add_control({"type": "button", "id": "go"})  # duplicate base id
    assert a["position"] == [0, 0]
    assert a["id"] == "go" and c["id"] == "go-2"
    assert c["position"] == [0, 1]  # next free, row-major


def test_add_control_uses_default_span_and_finds_slot():
    b = LayoutBuffer.blank(columns=4, rows=4)
    g = b.add_control({"type": "gauge", "id": "bat"}, default_span=[2, 2])
    assert g["span"] == [2, 2]
    assert g["position"] == [0, 0]
    # Next 2x2 can't share rows 0-1 cols 0-1; must move right.
    g2 = b.add_control({"type": "gauge", "id": "load"}, default_span=[2, 2])
    assert g2["position"] == [0, 2]


def test_add_control_explicit_position():
    b = LayoutBuffer.blank(columns=4, rows=4)
    c = b.add_control({"type": "toggle", "id": "t"}, position=[2, 1])
    assert c["position"] == [2, 1]


def test_add_control_no_room_raises():
    b = LayoutBuffer.blank(columns=2, rows=1)
    b.add_control({"type": "button", "id": "a"})
    b.add_control({"type": "button", "id": "b"})
    with pytest.raises(BufferError):
        b.add_control({"type": "button", "id": "c"})


def test_update_control_merge_and_delete():
    b = LayoutBuffer.blank()
    b.add_control({"type": "button", "id": "go", "tint": "#fff", "label": "Go"})
    b.update_control("go", {"tint": "#FF0000", "label": None, "style": "ghost"})
    _, _, ch = b.find("go")
    assert ch["tint"] == "#FF0000"
    assert "label" not in ch          # null patch deletes
    assert ch["style"] == "ghost"     # new key added


def test_update_missing_raises():
    b = LayoutBuffer.blank()
    with pytest.raises(BufferError):
        b.update_control("nope", {"x": 1})


def test_remove_control():
    b = LayoutBuffer.blank()
    b.add_control({"type": "button", "id": "go"})
    b.remove_control("go")
    assert b.find("go") is None


def test_move_control_across_tabs():
    b = LayoutBuffer.blank()
    b.add_tab("Two")
    b.add_control({"type": "button", "id": "go"}, tab_index=0)
    b.move_control("go", position=[1, 1], tab_index=1)
    assert b.find("go")[0] == 1  # now on tab index 1
    _, _, ch = b.find("go")
    assert ch["position"] == [1, 1]


def test_add_group_is_typed_and_placed():
    b = LayoutBuffer.blank(columns=4, rows=4)
    g = b.add_group({"id": "grp", "span": [2, 2],
                     "grid": {"columns": 2, "rows": 2}, "children": []})
    assert g["type"] == "group"
    assert g["position"] == [0, 0]


def test_unique_id_walks_into_groups():
    b = LayoutBuffer.blank()
    b.add_group({"id": "grp", "children": [
        {"type": "toggle", "id": "inner"}]})
    # 'inner' is nested in a group but still reserved.
    assert b.unique_id("inner") == "inner-2"


def test_from_layout_deepcopies():
    src = {"name": "X", "version": 1, "tabs": [
        {"title": "T", "icon": "i", "grid": {"columns": 2, "rows": 2}, "children": []}]}
    b = LayoutBuffer.from_layout(src)
    b.add_control({"type": "button", "id": "go"})
    assert src["tabs"][0]["children"] == []  # original untouched


def test_from_layout_rejects_garbage():
    with pytest.raises(BufferError):
        LayoutBuffer.from_layout({"not": "a layout"})


def test_issues_surface_overlap():
    b = LayoutBuffer.blank(columns=4, rows=4)
    b.add_control({"type": "button", "id": "a"}, position=[0, 0])
    b.add_control({"type": "button", "id": "b"}, position=[0, 0])
    problems = b.issues()
    assert any(p["kind"] == "overlap" for p in problems)


def test_summary_renders():
    b = LayoutBuffer.blank(name="Dash", columns=4, rows=4)
    b.add_control({"type": "gauge", "id": "bat"}, default_span=[2, 2])
    out = b.summary()
    assert "Dash" in out
    assert "bat" in out
    assert "Tab 0" in out
