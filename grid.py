"""Grid intelligence — pure occupancy math for a tab/group's child grid.

A CAR-TER grid is `columns` x `rows`; each child has `position: [row, col]` and
`span: [rowSpan, colSpan]` (default [1, 1]). These helpers let the editor place
controls without the LLM computing collisions by hand, and "see" the grid spatially.

Children are plain dicts in layout JSON form: {"id","type","position","span",...}.
"""

from __future__ import annotations

from typing import Optional


def child_span(child: dict) -> list[int]:
    span = child.get("span") or [1, 1]
    # tolerate a scalar or malformed span
    if not isinstance(span, list) or len(span) != 2:
        return [1, 1]
    return [max(1, int(span[0])), max(1, int(span[1]))]


def child_cells(child: dict) -> list[tuple[int, int]]:
    """The (row, col) cells a child occupies, given its position + span."""
    pos = child.get("position")
    if not isinstance(pos, list) or len(pos) != 2:
        return []
    r0, c0 = int(pos[0]), int(pos[1])
    rs, cs = child_span(child)
    return [(r, c) for r in range(r0, r0 + rs) for c in range(c0, c0 + cs)]


def occupancy(children: list[dict]) -> dict[tuple[int, int], str]:
    """Map of occupied cell -> the id of the (last) child occupying it."""
    cells: dict[tuple[int, int], str] = {}
    for child in children:
        cid = child.get("id", "?")
        for cell in child_cells(child):
            cells[cell] = cid
    return cells


def find_slot(children: list[dict], columns: int, rows: int,
              span: Optional[list[int]] = None) -> Optional[list[int]]:
    """First free [row, col] (row-major) where a `span`-sized block fits without
    overlap and within bounds. None if the grid has no room."""
    rs, cs = (span or [1, 1])
    rs, cs = max(1, int(rs)), max(1, int(cs))
    taken = set(occupancy(children).keys())
    for r in range(0, rows - rs + 1):
        for c in range(0, columns - cs + 1):
            block = {(r + dr, c + dc) for dr in range(rs) for dc in range(cs)}
            if block.isdisjoint(taken):
                return [r, c]
    return None


def validate_placement(children: list[dict], columns: int, rows: int) -> list[dict]:
    """Structural placement issues: out-of-bounds and overlaps. Each issue is
    {"kind","ids"/"id","detail"}."""
    issues: list[dict] = []
    # Out of bounds
    for child in children:
        pos = child.get("position")
        if not isinstance(pos, list) or len(pos) != 2:
            issues.append({"kind": "bad_position", "id": child.get("id", "?"),
                           "detail": f"position must be [row, col], got {pos!r}"})
            continue
        r0, c0 = int(pos[0]), int(pos[1])
        rs, cs = child_span(child)
        if r0 < 0 or c0 < 0 or r0 + rs > rows or c0 + cs > columns:
            issues.append({"kind": "out_of_bounds", "id": child.get("id", "?"),
                           "detail": f"occupies rows {r0}-{r0+rs-1}, cols {c0}-{c0+cs-1} "
                                     f"but grid is {rows}x{columns}"})
    # Overlaps
    seen: dict[tuple[int, int], str] = {}
    reported: set[tuple[str, str]] = set()
    for child in children:
        cid = child.get("id", "?")
        for cell in child_cells(child):
            other = seen.get(cell)
            if other and other != cid:
                key = tuple(sorted((other, cid)))
                if key not in reported:
                    reported.add(key)
                    issues.append({"kind": "overlap", "ids": list(key),
                                   "detail": f"both occupy cell {list(cell)}"})
            else:
                seen[cell] = cid
    return issues


def _token(cid: str, width: int) -> str:
    cid = cid or "?"
    return cid[:width].ljust(width)


def render_grid(children: list[dict], columns: int, rows: int,
                cell_width: int = 6) -> str:
    """ASCII occupancy map. Each cell shows the id of its occupant (truncated),
    empty cells show dots. A child spanning multiple cells repeats its token."""
    grid_ids: dict[tuple[int, int], str] = occupancy(children)
    lines: list[str] = []
    header = "      " + " ".join(f"c{c}".ljust(cell_width) for c in range(columns))
    lines.append(header)
    for r in range(rows):
        row_cells = []
        for c in range(columns):
            cid = grid_ids.get((r, c))
            row_cells.append(_token(cid, cell_width) if cid else ("·" * cell_width))
        lines.append(f"r{r}".ljust(5) + " " + " ".join(row_cells))
    free = rows * columns - len(grid_ids)
    lines.append(f"\n({free} free cell(s) of {rows*columns})")
    return "\n".join(lines)
