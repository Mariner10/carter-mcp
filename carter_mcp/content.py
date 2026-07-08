"""Resolved documentation content: control docs, the parsed catalog, default spans.

Wraps `sources` (which decides WHERE definitions/demos come from) with the small
amount of reading/parsing the tools share: loading a doc file, listing docs with
their frontmatter, the memoized machine-readable catalog, and the 2-D-tuned
default span for each control type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from carterkit import catalog

from carter_mcp import sources


def definitions_dir() -> Path:
    """Docs dir for the control catalog + doc prose (website-sourced, cached)."""
    return sources.definitions_docs_dir()


def demos_dir() -> Path:
    """Docs dir for example snippets / authoring demos (latest installed carterkit)."""
    return sources.demos_docs_dir()


def load_doc(doc_id: str) -> Optional[str]:
    path = definitions_dir() / f"{doc_id}.md"
    if path.exists():
        return path.read_text()
    return None


def list_doc_files() -> list[dict]:
    results = []
    for f in sorted(definitions_dir().glob("*.md")):
        node_id = f.stem
        content = f.read_text()
        label = node_id
        category = "unknown"
        ctype = None
        # Read only the TOP-LEVEL frontmatter keys (column 0, between the --- fences).
        # A field's own indented `type:` (e.g. `    type: number`) must not be mistaken
        # for the control's `type` token — hence the indentation guard.
        lines = content.split("\n")
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if not line or line[0] in (" ", "\t") or ":" not in line:
                    continue  # nested/indented key or not a key line
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if key == "label":
                    label = val
                elif key == "category":
                    category = val
                elif key == "type":  # camelCase token for {"type": …}; differs from node_id
                    ctype = val
        results.append({"id": node_id, "label": label, "category": category, "type": ctype})
    return results


_catalog_cache: dict[bool, dict] = {}


def build_catalog(include_theme: bool = False) -> dict:
    """The machine-readable control catalog, memoized per include_theme flavor."""
    if include_theme not in _catalog_cache:
        _catalog_cache[include_theme] = catalog.build_catalog(
            definitions_dir(), include_theme=include_theme)
    return _catalog_cache[include_theme]


# 2-D grid default spans [rowSpan, colSpan], tuned so each control reads at its
# natural aspect in the default ~4-column grid (rowHeight ~56pt): square visuals get
# ~3 rows, wide visuals span columns, inputs sit in 1 row, content panels are tall.
# infer auto-grows rows, so tall spans are safe; colSpan stays <= 4 to fit the
# default grid width (a narrower grid will report "grow the grid").
SPAN_2D: dict[str, list[int]] = {
    # square visuals
    "progressRing": [3, 2], "gauge": [2, 3], "joystick": [3, 3], "qrCode": [3, 3],
    # wide visual
    "sparkline": [2, 4],
    # tall content panels (infer grows rows to fit)
    "map": [4, 4], "graph": [4, 4], "chat": [5, 4], "list": [4, 4],
    "cardList": [4, 4], "logConsole": [3, 4], "webView": [4, 4], "image": [3, 3],
    # container controls (hold groups)
    "carousel": [4, 4], "flipCard": [4, 4], "accordion": [4, 4],
    # row inputs + text (1 row tall)
    "slider": [1, 2], "stepper": [1, 2], "segmentedControl": [1, 2],
    "picker": [1, 2], "datePicker": [1, 2], "colorPicker": [1, 2],
    "textInput": [1, 2], "toggle": [1, 2], "button": [1, 2],
    "label": [1, 2], "statusLight": [1, 2],
    # structural
    "divider": [1, 4], "spacer": [1, 1],
}


def default_span_for(control_type: str) -> Optional[list[int]]:
    """2-D-tuned default span for a control type (see `SPAN_2D`) — squares get a
    near-square footprint, inputs a single row, content a tall panel. Falls back to
    the control doc's catalog `defaultSpan` for anything not mapped."""
    span = SPAN_2D.get(control_type)
    if span is not None:
        return list(span)
    entry = build_catalog().get(control_type)
    return entry.get("defaultSpan") if entry else None
