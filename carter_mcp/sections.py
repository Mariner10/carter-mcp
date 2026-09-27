"""Sectioned documents (schemaVersion 2, carter-m7s.37) for the MCP.

A layout may keep facets in top-level ``placements`` / ``styles`` / ``connectivity``
sections keyed by control id (controldocs/document-contract.md). The device decodes
both forms, so ``push_layout`` / ``save_layout`` send the document as-is. The working
buffer and the lint work on the inline form: ``to_inline`` folds the sections onto
their children.

carterkit owns the transform (``carterkit.sections``). The MCP tracks the RELEASED
kit, so until a kit with sections ships this module carries a minimal fold with the
same rules (sections win, unknown ids dropped, non-facet keys kept) — delete the
fallback once the pinned carterkit has ``carterkit.sections``.
"""
from __future__ import annotations

import copy
from typing import Optional

from carterkit.buffer import LayoutBuffer

try:                                                  # carterkit >= the m7s.37 release
    from carterkit.sections import SECTIONS, is_sectioned, to_inline, to_sectioned
    KIT_KNOWS_SECTIONS = True
except ImportError:                                   # pragma: no cover - depends on the kit
    KIT_KNOWS_SECTIONS = False
    SECTIONS = {
        "placements": ("position", "span", "landscape", "regular"),
        "styles": ("theme", "tint", "icon", "hideLabel", "hideValue", "hideBackground", "animation"),
        "connectivity": ("sync", "action", "longPressAction"),
    }

    def is_sectioned(layout) -> bool:
        return isinstance(layout, dict) and any(k in layout for k in SECTIONS)

    def to_inline(layout: dict) -> dict:
        doc = copy.deepcopy(layout)
        if not is_sectioned(doc):
            return doc
        holders: dict = {}

        def walk(kids, depth=0):
            for ch in kids or []:
                if not isinstance(ch, dict):
                    continue
                cid = ch.get("id")
                if isinstance(cid, str) and cid and cid not in holders:
                    holders[cid] = ch
                if depth < 64 and ch.get("type") == "group":
                    walk(ch.get("children"), depth + 1)

        for tab in doc.get("tabs") or []:
            if isinstance(tab, dict):
                walk(tab.get("children"))
        for name, facets in SECTIONS.items():
            raw = doc.pop(name, None)
            if not isinstance(raw, dict):
                continue
            kept = {}
            for cid, entry in raw.items():
                if cid not in holders or not isinstance(entry, dict):
                    continue
                entry = dict(entry)
                alias = entry.get("default")
                if name == "placements" and isinstance(alias, dict):
                    alias = dict(alias)
                    for key in ("position", "span"):
                        if key in alias:
                            entry.setdefault(key, alias.pop(key))
                    entry.pop("default")
                    if alias:
                        entry["default"] = alias
                for facet in facets:
                    if facet in entry:
                        holders[cid][facet] = entry.pop(facet)
                if entry:
                    kept[cid] = entry
            if kept:
                doc[name] = kept
        if not is_sectioned(doc) and doc.get("schemaVersion") == 2:
            del doc["schemaVersion"]
        return doc

    def to_sectioned(layout: dict) -> dict:
        """Inverse of ``to_inline``: move each first holder's inline facets into its
        section entry (an existing entry wins) and declare ``schemaVersion: 2``."""
        doc = copy.deepcopy(layout)
        found = {name: {cid: dict(e) for cid, e in doc[name].items() if isinstance(e, dict)}
                 for name in SECTIONS if isinstance(doc.get(name), dict)}
        seen: set = set()

        def walk(kids, depth=0):
            for ch in kids or []:
                if not isinstance(ch, dict):
                    continue
                cid = ch.get("id")
                if isinstance(cid, str) and cid and cid not in seen:
                    seen.add(cid)
                    for name, facets in SECTIONS.items():
                        for facet in facets:
                            if facet in ch:
                                found.setdefault(name, {}).setdefault(cid, {}).setdefault(facet, ch.pop(facet))
                if depth < 64 and ch.get("type") == "group":
                    walk(ch.get("children"), depth + 1)

        for tab in doc.get("tabs") or []:
            if isinstance(tab, dict):
                walk(tab.get("children"))
        for name, entries in found.items():
            kept = {cid: e for cid, e in entries.items() if e}
            if kept:
                doc[name] = kept
            else:
                doc.pop(name, None)
        if is_sectioned(doc):
            doc["schemaVersion"] = 2
        return doc


def lintable(layout: dict) -> dict:
    """What to hand carterkit's validator: the document itself when the kit folds
    sections on its own, else the inline form (an older kit would report every
    sectioned control as missing its position)."""
    if KIT_KNOWS_SECTIONS or not is_sectioned(layout):
        return layout
    return to_inline(layout)


def for_buffer(layout: dict) -> dict:
    """The working buffer edits inline keys, so a sectioned seed is folded first
    (a buffer push then sends the inline form, which every app reads)."""
    return to_inline(layout) if is_sectioned(layout) else layout


def seed_buffer(layout: dict, sectioned: Optional[bool] = None) -> LayoutBuffer:
    """A working buffer on the folded view of ``layout`` that remembers whether the
    source was sectioned (carter-m7s.39), so ``saved_form`` writes it back that way."""
    buf = LayoutBuffer.from_layout(for_buffer(layout))
    buf.source_sectioned = is_sectioned(layout) if sectioned is None else sectioned
    return buf


def saved_form(buf: LayoutBuffer) -> dict:
    """What save writes: the buffer in its source's form (sectioned stays sectioned)."""
    return to_sectioned(buf.layout) if getattr(buf, "source_sectioned", False) else buf.layout
