"""The MCP's validate_layout learns the local `span` aggregate (carter-73q2.31)
from carterkit's local-store lint: first..last over one or two date fields."""

import json

import pytest

local = pytest.importorskip("carterkit.local")
if "span" not in getattr(local, "AGGREGATE_OPS", ()):
    pytest.skip("installed carterkit predates the span aggregate", allow_module_level=True)

from carter_mcp.tools.buffer import validate_layout  # noqa: E402


def _layout(aggregate):
    return {"name": "Reads", "version": 1,
            "sources": {"db": {"type": "local", "namespace": "reads",
                               "collections": {"books": {"fields": {
                                   "title": "string", "started": "date", "finished": "date"}}}}},
            "tabs": [{"title": "Main", "icon": "book.fill", "grid": {"columns": 4, "rows": 4},
                      "children": [{"type": "label", "id": "since", "label": "Reading",
                                    "position": [0, 0], "span": [1, 4],
                                    "sync": [{"method": "local", "collection": "books",
                                              "aggregate": aggregate}]}]}]}


def test_span_validates_clean():
    out = validate_layout(json.dumps(_layout({"op": "span", "field": "started", "to": "finished"})))
    assert "span" not in out, out


def test_bad_span_is_reported():
    out = validate_layout(json.dumps(_layout({"op": "span", "field": "title"})))
    assert "span needs a date field" in out, out
