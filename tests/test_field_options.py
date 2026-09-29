"""carter-73q2.28: validate_layout accepts object-form local field declarations
(type + options) and names bad options. Needs a carterkit that knows field options
(> 0.13.1); skipped against an older kit (the MCP tracks the released kit)."""
import json

import pytest

try:
    from carterkit import local as kit_local
except ImportError:  # a kit without the local store at all
    kit_local = None

pytestmark = pytest.mark.skipif(not hasattr(kit_local, "FIELD_OPTION_KEYS"),
                                reason="carterkit without local field options")

from carter_mcp.tools import buffer  # noqa: E402


def _layout(fields):
    return json.dumps({
        "name": "Log",
        "sources": {"db": {"type": "local", "collections": {"log": {"fields": fields}}}},
        "tabs": [{"title": "Main", "controls": [
            {"id": "n", "type": "label", "text": "x", "position": [0, 0], "span": [1, 1],
             "sync": [{"method": "local", "collection": "log", "aggregate": "count"}]}]}],
    })


def test_metadata_form_validates_clean():
    fields = {"when": {"type": "date", "required": True, "default": "now"},
              "mood": {"type": "string", "choices": ["Good", "Rough"], "display": "choice"},
              "note": "string"}
    out = buffer.validate_layout(layout_json=_layout(fields))
    assert "unknown type" not in out and "log.when" not in out, out


def test_bad_option_named():
    out = buffer.validate_layout(layout_json=_layout({"x": {"type": "number", "range": [5, 1]}}))
    assert "collection log.x" in out and "range must be [min, max]" in out, out
