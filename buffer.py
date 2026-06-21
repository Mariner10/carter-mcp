"""Working-layout buffer + incremental patch ops.

The headline authoring model: instead of re-emitting an 800-line layout on every
change, the LLM issues surgical operations against a server-held draft. The buffer
keeps the full document; each op mutates it and the result is pushed to the device as
a full layout (which it already knows how to render). Pure (no I/O, no socket) so it
is fully unit-testable; the async push/save live in server.py.

Top-level children (controls + groups) of a tab are addressable by id. Editing inside
a group is not yet supported (ids inside groups still count for uniqueness).
"""

from __future__ import annotations

import copy
from typing import Optional

import grid as gridmod

DEFAULT_COLUMNS = 4
DEFAULT_ROWS = 8


class BufferError(Exception):
    """Raised on an invalid buffer operation (rendered to the user as a tool error)."""


class LayoutBuffer:
    def __init__(self, layout: dict):
        self.layout = layout

    # ─── construction ────────────────────────────────────────────────────────

    @classmethod
    def blank(cls, name: str = "Untitled", columns: int = DEFAULT_COLUMNS,
              rows: int = DEFAULT_ROWS, accent: str = "#667eea",
              tab_title: str = "Tab 1", tab_icon: str = "house.fill") -> "LayoutBuffer":
        return cls({
            "name": name,
            "version": 1,
            "accentColor": accent,
            "tabs": [{
                "title": tab_title,
                "icon": tab_icon,
                "grid": {"columns": columns, "rows": rows},
                "children": [],
            }],
        })

    @classmethod
    def from_layout(cls, layout: dict) -> "LayoutBuffer":
        if not isinstance(layout, dict) or "tabs" not in layout:
            raise BufferError("source layout must be an object with a 'tabs' array")
        return cls(copy.deepcopy(layout))

    # ─── access ──────────────────────────────────────────────────────────────

    @property
    def tabs(self) -> list:
        return self.layout.setdefault("tabs", [])

    def _tab(self, i: int) -> dict:
        tabs = self.tabs
        if i < 0 or i >= len(tabs):
            raise BufferError(f"tab index {i} out of range (have {len(tabs)} tab(s))")
        return tabs[i]

    @staticmethod
    def _grid_dims(tab: dict) -> tuple[int, int]:
        g = tab.get("grid") or {}
        return int(g.get("columns", DEFAULT_COLUMNS)), int(g.get("rows", DEFAULT_ROWS))

    def all_ids(self) -> set[str]:
        ids: set[str] = set()

        def walk(children):
            for ch in children or []:
                if isinstance(ch, dict):
                    if "id" in ch:
                        ids.add(ch["id"])
                    if ch.get("type") == "group":
                        walk(ch.get("children"))

        for tab in self.tabs:
            walk(tab.get("children"))
        return ids

    def unique_id(self, base: str) -> str:
        base = base or "control"
        ids = self.all_ids()
        if base not in ids:
            return base
        i = 2
        while f"{base}-{i}" in ids:
            i += 1
        return f"{base}-{i}"

    def find(self, control_id: str) -> Optional[tuple[int, int, dict]]:
        """Locate a top-level child by id -> (tab_index, child_index, child)."""
        for ti, tab in enumerate(self.tabs):
            for ci, ch in enumerate(tab.get("children", [])):
                if isinstance(ch, dict) and ch.get("id") == control_id:
                    return ti, ci, ch
        return None

    # ─── mutations ───────────────────────────────────────────────────────────

    def add_control(self, control: dict, tab_index: int = 0,
                    position: Optional[list[int]] = None,
                    default_span: Optional[list[int]] = None) -> dict:
        if not isinstance(control, dict) or "type" not in control:
            raise BufferError("control must be an object with a 'type'")
        control = copy.deepcopy(control)
        control["id"] = self.unique_id(control.get("id") or control["type"])

        tab = self._tab(tab_index)
        children = tab.setdefault("children", [])
        cols, rows = self._grid_dims(tab)

        if control.get("span") is None and default_span and default_span != [1, 1]:
            control["span"] = default_span
        span = control.get("span") or [1, 1]

        if position is None:
            slot = gridmod.find_slot(children, cols, rows, span)
            if slot is None:
                raise BufferError(
                    f"no free {span} slot in tab {tab_index} ({rows}x{cols} grid). "
                    f"Grow the grid (add_tab/edit grid) or pass an explicit position.")
            position = slot
        control["position"] = position
        children.append(control)
        return control

    def update_control(self, control_id: str, patch: dict) -> dict:
        found = self.find(control_id)
        if not found:
            raise BufferError(f"no control '{control_id}' in the buffer")
        ch = found[2]
        for k, v in patch.items():
            if v is None:
                ch.pop(k, None)
            else:
                ch[k] = v
        return ch

    def remove_control(self, control_id: str) -> dict:
        found = self.find(control_id)
        if not found:
            raise BufferError(f"no control '{control_id}' in the buffer")
        ti, ci, _ = found
        return self.tabs[ti]["children"].pop(ci)

    def move_control(self, control_id: str, position: Optional[list[int]] = None,
                     span: Optional[list[int]] = None,
                     tab_index: Optional[int] = None) -> dict:
        found = self.find(control_id)
        if not found:
            raise BufferError(f"no control '{control_id}' in the buffer")
        ti, ci, ch = found
        if tab_index is not None and tab_index != ti:
            ch = self.tabs[ti]["children"].pop(ci)
            self._tab(tab_index).setdefault("children", []).append(ch)
        if position is not None:
            ch["position"] = position
        if span is not None:
            ch["span"] = span
        return ch

    def add_tab(self, title: str, icon: str = "square.grid.2x2",
                columns: int = DEFAULT_COLUMNS, rows: int = DEFAULT_ROWS) -> int:
        self.tabs.append({
            "title": title, "icon": icon,
            "grid": {"columns": columns, "rows": rows}, "children": [],
        })
        return len(self.tabs) - 1

    def add_group(self, group: dict, tab_index: int = 0,
                  position: Optional[list[int]] = None) -> dict:
        group = copy.deepcopy(group)
        group["type"] = "group"
        group["id"] = self.unique_id(group.get("id") or "group")
        tab = self._tab(tab_index)
        children = tab.setdefault("children", [])
        cols, rows = self._grid_dims(tab)
        span = group.get("span") or [1, 1]
        if position is None:
            slot = gridmod.find_slot(children, cols, rows, span)
            if slot is None:
                raise BufferError(f"no free {span} slot in tab {tab_index} for the group")
            position = slot
        group["position"] = position
        children.append(group)
        return group

    # ─── views ───────────────────────────────────────────────────────────────

    def issues(self) -> list[dict]:
        """Placement issues across all tabs, each tagged with its tab index."""
        out: list[dict] = []
        for ti, tab in enumerate(self.tabs):
            cols, rows = self._grid_dims(tab)
            for issue in gridmod.validate_placement(tab.get("children", []), cols, rows):
                out.append({"tab": ti, **issue})
        return out

    def summary(self, show_grids: bool = True) -> str:
        name = self.layout.get("name", "Untitled")
        accent = self.layout.get("accentColor")
        head = f"**{name}**" + (f" · accent {accent}" if accent else "")
        lines = [head]
        for ti, tab in enumerate(self.tabs):
            cols, rows = self._grid_dims(tab)
            children = tab.get("children", [])
            lines.append(f"\nTab {ti}: {tab.get('title','?')} ({tab.get('icon','')}) "
                         f"— {rows}x{cols}, {len(children)} item(s)")
            for ch in children:
                pos = ch.get("position")
                span = ch.get("span")
                extra = f" span {span}" if span and span != [1, 1] else ""
                lines.append(f"  - {ch.get('id','?')} ({ch.get('type','?')}) @ {pos}{extra}")
            if show_grids and children:
                lines.append(gridmod.render_grid(children, cols, rows))
        problems = self.issues()
        if problems:
            lines.append("\n⚠ placement issues:")
            for p in problems:
                ids = p.get("ids") or [p.get("id")]
                lines.append(f"  - [tab {p['tab']}] {p['kind']}: {', '.join(ids)} — {p['detail']}")
        return "\n".join(lines)
