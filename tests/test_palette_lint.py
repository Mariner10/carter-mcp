"""carter-m7s.16: validate_layout / validate_buffer surface carterkit's palette lint —
a raw colour equal to a `theme.palette` token is a warning suggesting `$name`.
Skips until the installed carterkit ships `carterkit.palette`."""

import json

import pytest

pytest.importorskip("carterkit.palette")

from carter_mcp.tools import buffer as buffer_tools  # noqa: E402

LAYOUT = {
    "name": "Tokens", "version": 1,
    "theme": {"palette": {"brand": "#0A84FF"}, "accentColor": "$brand"},
    "tabs": [{"title": "A", "icon": "star", "grid": {"columns": 2, "rows": 2}, "children": [
        {"type": "gauge", "id": "c_raw", "position": [0, 0], "tint": "#0A84FF"},
        {"type": "gauge", "id": "c_bad", "position": [1, 0], "tint": "$nope"},
    ]}],
}


def test_validate_layout_warns_on_raw_token_colour_and_unknown_ref():
    out = buffer_tools.validate_layout(layout_json=json.dumps(LAYOUT))
    assert "palette_literal" in out and "$brand" in out
    assert "unknown_token" in out and "$nope" in out
