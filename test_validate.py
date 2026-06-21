"""Tests for validate.py — schema-driven layout linting against the real catalog."""

from pathlib import Path

import catalog
import validate

DOCS = Path(__file__).parent.parent / "CAR-TER" / "CAR-TER" / "ControlDocs"
CAT = catalog.build_catalog(DOCS, include_theme=True)


def _layout(children, columns=4, rows=4):
    return {"name": "T", "version": 1,
            "tabs": [{"title": "Main", "icon": "house.fill",
                      "grid": {"columns": columns, "rows": rows},
                      "children": children}]}


def _kinds(findings):
    return {f["kind"] for f in findings}


def test_clean_layout_no_errors():
    layout = _layout([
        {"type": "gauge", "id": "bat", "position": [0, 0], "span": [2, 2],
         "min": 0, "max": 100, "label": "Battery", "tint": "#34C759",
         "sync": [{"method": "meshsocket", "type": "listen", "event": "broadcast",
                   "valuePath": "battery"}]},
        {"type": "button", "id": "go", "position": [0, 2], "label": "Go", "style": "ghost"},
    ])
    findings = validate.validate_layout(layout, CAT)
    errors = [f for f in findings if f["severity"] == "error"]
    assert errors == [], errors


def test_missing_top_level_fields():
    findings = validate.validate_layout({"tabs": []}, CAT)
    details = " ".join(f["detail"] for f in findings)
    assert "name" in details and "version" in details


def test_duplicate_id():
    layout = _layout([
        {"type": "button", "id": "x", "position": [0, 0]},
        {"type": "button", "id": "x", "position": [0, 1]},
    ])
    assert "duplicate_id" in _kinds(validate.validate_layout(layout, CAT))


def test_unknown_type():
    layout = _layout([{"type": "frobnicator", "id": "f", "position": [0, 0]}])
    assert "unknown_type" in _kinds(validate.validate_layout(layout, CAT))


def test_bad_enum_value():
    layout = _layout([{"type": "button", "id": "b", "position": [0, 0],
                       "style": "sparkly"}])
    findings = validate.validate_layout(layout, CAT)
    assert "bad_enum" in _kinds(findings)
    assert any(f["severity"] == "error" for f in findings if f["kind"] == "bad_enum")


def test_unknown_field_is_warning():
    layout = _layout([{"type": "button", "id": "b", "position": [0, 0],
                       "flooberity": 7}])
    findings = validate.validate_layout(layout, CAT)
    uf = [f for f in findings if f["kind"] == "unknown_field"]
    assert uf and uf[0]["severity"] == "warn"


def test_grid_overlap_detected():
    layout = _layout([
        {"type": "button", "id": "a", "position": [0, 0], "span": [1, 2]},
        {"type": "button", "id": "b", "position": [0, 1]},
    ])
    assert "overlap" in _kinds(validate.validate_layout(layout, CAT))


def test_group_recursion_and_nested_ids():
    layout = _layout([
        {"type": "group", "id": "g", "position": [0, 0], "span": [2, 2],
         "grid": {"columns": 2, "rows": 2}, "children": [
             {"type": "toggle", "id": "dup", "position": [0, 0]},
         ]},
        {"type": "toggle", "id": "dup", "position": [0, 2]},
    ])
    # nested + top-level share id 'dup' -> duplicate
    assert "duplicate_id" in _kinds(validate.validate_layout(layout, CAT))


def test_format_findings_clean():
    assert "No issues" in validate.format_findings([])
