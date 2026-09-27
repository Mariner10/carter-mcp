"""carter-m7s.37 — the MCP accepts sectioned (schemaVersion 2) documents: the lint and
the working buffer see them folded inline; push/save send them as-is."""
import json

from carter_mcp import sections
from carter_mcp.tools import buffer as buffer_tools

INLINE = {
    "name": "S", "version": 1,
    "tabs": [{"title": "A", "icon": "a", "grid": {"columns": 2, "rows": 4}, "children": [
        {"type": "label", "id": "c1", "text": "hi", "position": [0, 0], "span": [1, 2],
         "tint": "#34C759"}]}],
}
SECTIONED = {
    "schemaVersion": 2, "name": "S", "version": 1,
    "tabs": [{"title": "A", "icon": "a", "grid": {"columns": 2, "rows": 4}, "children": [
        {"type": "label", "id": "c1", "text": "hi"}]}],
    "placements": {"c1": {"default": {"position": [0, 0]}, "span": [1, 2]}},
    "styles": {"c1": {"tint": "#34C759"}, "ghost": {"tint": "#000000"}},
}


def test_to_inline_matches_the_inline_form():
    assert sections.to_inline(SECTIONED) == INLINE
    assert sections.for_buffer(INLINE) is INLINE


def test_validate_layout_accepts_both_forms(tmp_path):
    inline = buffer_tools.validate_layout(layout_json=json.dumps(INLINE))
    path = tmp_path / "s.json"
    path.write_text(json.dumps(SECTIONED))
    sectioned = buffer_tools.validate_layout(layout_path=str(path))
    assert "No issues" in inline
    if sections.KIT_KNOWS_SECTIONS:
        assert "ghost" in sectioned                  # carterkit's bad_section error
    else:
        assert sectioned == inline                   # fallback fold drops it silently


def test_buffer_seeds_inline_from_a_sectioned_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(SECTIONED))
    import asyncio
    asyncio.run(buffer_tools.begin_edit(from_file=str(path)))
    layout = buffer_tools.state.work_buffer.layout
    assert "placements" not in layout
    assert layout["tabs"][0]["children"][0]["position"] == [0, 0]


def test_to_sectioned_inverts_to_inline():
    assert sections.to_sectioned(INLINE)["placements"] == {"c1": {"position": [0, 0], "span": [1, 2]}}
    assert sections.to_inline(sections.to_sectioned(INLINE)) == INLINE


def _save_through_buffer(monkeypatch, source, edit):
    """begin_edit(from_file) -> edit -> save_buffer; returns what reached the device."""
    import asyncio
    from carter_mcp.tools import device as device_tools
    sent = {}

    async def persist(layout, filename=""):
        sent["layout"] = layout
        return "saved"

    monkeypatch.setattr(device_tools.mesh, "connection_error", lambda: None)
    monkeypatch.setattr(device_tools.mesh, "persist_layout_obj", persist)
    asyncio.run(buffer_tools.begin_edit(from_file=str(source)))
    edit()
    assert asyncio.run(device_tools.save_buffer()) == "saved"
    return sent["layout"]


def test_get_edit_save_keeps_a_sectioned_file_sectioned(tmp_path, monkeypatch):
    """carter-m7s.39: the buffer edits the folded view; save writes the source's form."""
    source = dict(SECTIONED, placements={"c1": {"position": [0, 0], "span": [1, 2], "note": "keep"}})
    path = tmp_path / "s.json"
    path.write_text(json.dumps(source))
    saved = _save_through_buffer(
        monkeypatch, path, lambda: buffer_tools.move_control("c1", position=[2, 0]))
    assert saved["schemaVersion"] == 2
    assert saved["placements"]["c1"] == {"position": [2, 0], "span": [1, 2], "note": "keep"}
    assert saved["styles"] == {"c1": {"tint": "#34C759"}}
    assert "position" not in saved["tabs"][0]["children"][0]
    assert "tint" not in saved["tabs"][0]["children"][0]


def test_get_edit_save_keeps_an_inline_file_inline(tmp_path, monkeypatch):
    path = tmp_path / "i.json"
    path.write_text(json.dumps(INLINE))
    saved = _save_through_buffer(
        monkeypatch, path, lambda: buffer_tools.move_control("c1", position=[2, 0]))
    assert not sections.is_sectioned(saved)
    assert saved["tabs"][0]["children"][0]["position"] == [2, 0]


def test_revert_keeps_the_source_form(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(SECTIONED))

    def edit():
        buffer_tools.snapshot_buffer("start")
        buffer_tools.move_control("c1", position=[3, 0])
        buffer_tools.revert_buffer()

    saved = _save_through_buffer(monkeypatch, path, edit)
    assert saved["placements"]["c1"]["position"] == [0, 0]
