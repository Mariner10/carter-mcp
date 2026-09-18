"""load_layout_arg — the inline-vs-file layout argument resolver."""

import json

import pytest

from carter_mcp.layoutio import MAX_LAYOUT_BYTES, load_layout_arg

GOOD = {"name": "T", "version": 1, "tabs": []}


def test_inline_happy():
    layout, err = load_layout_arg(layout_json=json.dumps(GOOD))
    assert err is None
    assert layout["name"] == "T"


def test_path_happy(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps(GOOD))
    layout, err = load_layout_arg(layout_path=str(p))
    assert err is None
    assert layout == GOOD


def test_neither_errors():
    layout, err = load_layout_arg()
    assert layout is None
    assert "layout_json" in err and "layout_path" in err


def test_both_errors(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("{}")
    layout, err = load_layout_arg(layout_json="{}", layout_path=str(p))
    assert layout is None
    assert "not both" in err


def test_missing_file():
    layout, err = load_layout_arg(layout_path="/nope/definitely-missing.json")
    assert layout is None
    assert "No file" in err


def test_size_cap(tmp_path):
    p = tmp_path / "big.json"
    p.write_text("x" * (MAX_LAYOUT_BYTES + 1))
    layout, err = load_layout_arg(layout_path=str(p))
    assert layout is None
    assert "cap" in err


def test_invalid_json_names_file(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{nope")
    layout, err = load_layout_arg(layout_path=str(p))
    assert layout is None
    assert "broken.json" in err


def test_non_object_rejected():
    layout, err = load_layout_arg(layout_json="[1,2]")
    assert layout is None
    assert "object" in err


def test_require_fields(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"name": "T"}))
    layout, err = load_layout_arg(layout_path=str(p), require=("name", "version", "tabs"))
    assert layout is None
    assert "version" in err and "tabs" in err


def test_expanduser(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "h.json").write_text(json.dumps(GOOD))
    layout, err = load_layout_arg(layout_path="~/h.json")
    assert err is None
    assert layout["name"] == "T"
