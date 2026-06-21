"""Tests for catalog.py — frontmatter parsing, catalog assembly, example extraction.

Runs against the real ControlDocs so the parser stays honest about the actual
(slightly irregular) doc format, including unquoted #hex defaults.
"""

from pathlib import Path

import catalog

DOCS = Path(__file__).parent.parent / "CAR-TER" / "CAR-TER" / "ControlDocs"


def _field(doc, name):
    for f in doc["fields"] + doc["themeFields"]:
        if f["name"] == name:
            return f
    return None


def test_parse_all_finds_controls():
    docs = catalog.parse_all(DOCS)
    assert "button" in docs and "gauge" in docs
    assert docs["button"]["type"] == "button"
    assert docs["button"]["label"] == "Button"


def test_enum_field_values_and_default():
    doc = catalog.parse_all(DOCS)["button"]
    style = _field(doc, "style")
    assert style is not None
    assert style["type"] == "enum"
    assert "filled" in style["values"] and "ghost" in style["values"]
    assert style["default"] == "filled"


def test_unquoted_hex_default_survives():
    # `default: #FFFFFF0F` would be a comment in real YAML; the tolerant parser keeps it.
    doc = catalog.parse_all(DOCS)["gauge"]
    surface = _field(doc, "surfacePrimary")
    assert surface is not None
    assert surface["default"] == "#FFFFFF0F"


def test_default_span_parsed():
    docs = catalog.parse_all(DOCS)
    assert docs["gauge"]["defaultSpan"] == [2, 2]
    assert docs["button"]["defaultSpan"] == [1, 1]


def test_theme_fields_separated():
    doc = catalog.parse_all(DOCS)["button"]
    names = {f["name"] for f in doc["fields"]}
    theme_names = {f["name"] for f in doc["themeFields"]}
    assert "label" in names
    assert "cornerRadius" in theme_names
    assert "cornerRadius" not in names  # theme fields are not mixed into control fields


def test_build_catalog_keyed_by_type():
    cat = catalog.build_catalog(DOCS)
    assert "button" in cat and "gauge" in cat
    # node_id 'color-picker' but layout type 'colorPicker' — keyed by type.
    assert "colorPicker" in cat
    assert cat["colorPicker"]["node_id"] == "color-picker"
    # System/prose docs are excluded from the placeable-control catalog.
    assert "index" not in cat


def test_build_catalog_type_filter_and_theme():
    cat = catalog.build_catalog(DOCS, types=["gauge"], include_theme=True)
    assert set(cat.keys()) == {"gauge"}
    assert "themeFields" in cat["gauge"]


def test_resolve_doc_by_nodeid_or_type():
    by_node = catalog.resolve_doc(DOCS, "color-picker")
    by_type = catalog.resolve_doc(DOCS, "colorPicker")
    assert by_node is not None and by_type is not None
    assert by_node["node_id"] == by_type["node_id"] == "color-picker"


def test_examples_extracted_and_parse():
    examples = catalog.get_examples(DOCS, "button")
    assert len(examples) >= 3
    # Every extracted example must be valid JSON describing a control.
    for ex in examples:
        obj = catalog.example_as_obj(ex)
        assert obj is not None, f"example {ex['name']!r} did not parse"
        assert obj.get("type") == "button"


def test_find_example_by_prefix():
    ex = catalog.find_example(DOCS, "gauge", "Temperature")
    assert ex is not None
    assert "Temperature".lower() in ex["name"].lower()
    obj = catalog.example_as_obj(ex)
    assert obj and obj["type"] == "gauge"


def test_examples_exclude_non_example_json():
    # gauge.md has a ```json block under "## Gauge Segments" (before Examples);
    # it must NOT be captured as an example.
    names = [e["name"] for e in catalog.get_examples(DOCS, "gauge")]
    assert all("segment" not in n.lower() for n in names)
