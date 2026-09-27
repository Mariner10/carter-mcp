"""get_layout_schema (carter-m7s.6, interim until the generated JSON Schema lands):
serves the document contract + layout/group/control references from ControlDocs
instead of a hand-kept string that only listed 8 top-level keys."""

import shutil
from pathlib import Path

import pytest

from carter_mcp import content
from carter_mcp.tools import docs

ROOT = Path(__file__).resolve().parents[1]
_CANDIDATES = [
    ROOT.parent / "bead-carter-m7s.6" / "CAR-TER" / "ControlDocs",   # bead worktree
    ROOT.parent / "CAR-TER" / "CAR-TER" / "ControlDocs",              # workspace checkout
]
CONTROLDOCS = next((p for p in _CANDIDATES if (p / "document-contract.md").exists()), None)
pytestmark = pytest.mark.skipif(CONTROLDOCS is None, reason="no ControlDocs with document-contract.md")


def _point_at(monkeypatch, definitions: Path, demos: Path):
    monkeypatch.setattr(content, "definitions_dir", lambda: definitions)
    monkeypatch.setattr(content, "demos_dir", lambda: demos)


def test_schema_is_the_contract_plus_layout_references(monkeypatch):
    _point_at(monkeypatch, CONTROLDOCS, CONTROLDOCS)
    out = docs.get_layout_schema()
    for doc_id in docs.LAYOUT_SCHEMA_DOCS:
        assert f"<!-- {doc_id}.md -->" in out
    # The keys the old hand string never listed.
    for key in ("schemaVersion", "extensions", "provenance", "keepAwake", "glance",
                "sources", "appearance", "theme", "maxNestingDepth", "renameId"):
        assert key in out, key
    assert "Not in the current definitions" not in out


def test_contract_falls_back_to_the_installed_kit(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    for doc_id in ("layout-config", "group-def", "control-def"):
        shutil.copy(CONTROLDOCS / f"{doc_id}.md", site)
    _point_at(monkeypatch, site, CONTROLDOCS)          # site predates the contract doc
    assert "## Extensions" in docs.get_layout_schema()


def test_missing_docs_are_named(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path, tmp_path)
    out = docs.get_layout_schema()
    assert "Not in the current definitions: document-contract" in out
