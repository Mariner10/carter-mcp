"""The MCP's validate_layout learns the local range groupBy, zero-fill and distinct
over a date bucket (carter-73q2.32) from carterkit's local-store lint."""

import json

import pytest

local = pytest.importorskip("carterkit.local")
if "_lint_range" not in dir(local):
    pytest.skip("installed carterkit predates range buckets", allow_module_level=True)

from carter_mcp.tools.buffer import validate_layout  # noqa: E402


def _layout(sync):
    return {"name": "Reads", "version": 1,
            "sources": {"db": {"type": "local", "namespace": "reads",
                               "collections": {"books": {"fields": {
                                   "title": "string", "started": "date", "finished": "date",
                                   "pages": "integer"}}}}},
            "tabs": [{"title": "Main", "icon": "book.fill", "grid": {"columns": 4, "rows": 4},
                      "children": [{"type": "heatmap", "id": "days", "position": [0, 0], "span": [2, 4],
                                    "sync": [dict({"method": "local", "collection": "books"}, **sync)]}]}]}


@pytest.mark.parametrize("sync", [
    {"groupBy": {"range": ["started", "finished"], "bucket": "day",
                 "fill": {"from": "{{startOfYear}}", "to": "{{today}}"}}},
    {"groupBy": {"field": "finished", "bucket": "month", "fill": True},
     "aggregate": {"op": "sum", "field": "pages"}},
    {"aggregate": {"op": "distinct", "field": "finished", "bucket": "day"}},
])
def test_new_forms_validate_clean(sync):
    out = validate_layout(json.dumps(_layout(sync)))
    for needle in ("groupBy", "fill", "bucket", "range"):
        assert needle not in out, out


@pytest.mark.parametrize("sync,needle", [
    ({"groupBy": {"range": ["title", "finished"], "bucket": "day"}}, "range needs date fields"),
    ({"groupBy": {"field": "pages", "width": 10, "fill": True}}, "fill needs a date bucket"),
    ({"aggregate": {"op": "distinct", "field": "pages", "bucket": "day"}}, "distinct bucket needs a date field"),
])
def test_bad_forms_are_reported(sync, needle):
    out = validate_layout(json.dumps(_layout(sync)))
    assert needle in out, out
