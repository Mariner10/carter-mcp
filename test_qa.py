"""Tests for qa.py — value assertions and summary diffs."""

import qa


def test_assert_values_pass():
    assert qa.assert_values({"a": 1, "b": "x"}, {"a": 1, "b": "x", "c": 9}) == []


def test_assert_values_numeric_tolerance():
    assert qa.assert_values({"a": 1.0}, {"a": 1.0000001}) == []
    fails = qa.assert_values({"a": 1.0}, {"a": 2.0})
    assert len(fails) == 1 and fails[0]["id"] == "a"


def test_assert_values_missing_and_mismatch():
    fails = qa.assert_values({"a": 1, "b": "on"}, {"b": "off"})
    ids = {f["id"] for f in fails}
    assert ids == {"a", "b"}


def _summary(controls):
    return {"name": "T", "tabs": [{"title": "M", "controls": controls}]}


def test_diff_added_removed_moved_retyped():
    before = _summary([
        {"id": "a", "type": "gauge", "position": [0, 0], "span": [1, 1]},
        {"id": "b", "type": "toggle", "position": [0, 1], "span": [1, 1]},
    ])
    after = _summary([
        {"id": "a", "type": "gauge", "position": [1, 0], "span": [1, 1]},   # moved
        {"id": "b", "type": "button", "position": [0, 1], "span": [1, 1]},  # retyped
        {"id": "c", "type": "label", "position": [0, 2], "span": [1, 1]},   # added
    ])
    diff = qa.diff_summaries(before, after)
    assert diff["added"] == ["c"]
    assert diff["removed"] == []
    assert diff["moved"] == ["a"]
    assert diff["retyped"] == ["b"]


def test_format_diff():
    assert qa.format_diff({"added": [], "removed": [], "moved": [], "retyped": []}) \
        == "No structural changes."
    assert "added: c" in qa.format_diff(
        {"added": ["c"], "removed": [], "moved": [], "retyped": []})
