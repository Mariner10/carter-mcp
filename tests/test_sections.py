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
