"""QA over the mesh — assert control states and diff layout snapshots.

Pure helpers for the scripted-scenario runner (emit → read control state → assert)
and for snapshot/diff of layout summaries. The socket I/O lives in server.
"""

from __future__ import annotations


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def assert_values(expected: dict, actual: dict, tol: float = 1e-6) -> list[dict]:
    """Compare expected control values against actual; numbers compared with tolerance.
    Returns a list of failures (empty == all passed)."""
    failures: list[dict] = []
    for k, exp in expected.items():
        if k not in actual:
            failures.append({"id": k, "detail": f"missing (expected {exp!r})"})
            continue
        act = actual[k]
        if _is_num(exp) and _is_num(act):
            if abs(exp - act) > tol:
                failures.append({"id": k, "detail": f"expected {exp}, got {act}"})
        elif exp != act:
            failures.append({"id": k, "detail": f"expected {exp!r}, got {act!r}"})
    return failures


def _index_summary(summary: dict) -> dict:
    out: dict = {}
    for tab in (summary or {}).get("tabs", []):
        for c in tab.get("controls", []):
            out[c.get("id")] = c
    return out


def diff_summaries(before: dict, after: dict) -> dict:
    """Diff two layout summaries (the structural echo shape). Returns added/removed/
    moved/retyped control-id lists."""
    a, b = _index_summary(before), _index_summary(after)
    common = set(a) & set(b)
    moved = [i for i in common
             if a[i].get("position") != b[i].get("position")
             or a[i].get("span") != b[i].get("span")]
    retyped = [i for i in common if a[i].get("type") != b[i].get("type")]
    return {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "moved": sorted(moved),
        "retyped": sorted(retyped),
    }


def format_diff(diff: dict) -> str:
    if not any(diff.values()):
        return "No structural changes."
    parts = []
    for kind in ("added", "removed", "moved", "retyped"):
        if diff.get(kind):
            parts.append(f"{kind}: {', '.join(diff[kind])}")
    return "; ".join(parts)
