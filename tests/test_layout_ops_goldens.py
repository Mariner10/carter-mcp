"""LayoutOp goldens (carter-c1n.8): the app's `CAR-TERTests/Fixtures/layout-ops/*.json`,
copied verbatim into `tests/fixtures/layout-ops/`. Each golden is
`{name, description, kit, input, batch: {ops, base?, author?}, expected | error}`.

The app's `LayoutOps` applier runs every golden. This runner applies the `kit: true`
cases through carterkit's `LayoutBuffer` — the buffer the MCP's buffer tools
(add_control / update_control / remove_control / move_control) edit — mapping each op
onto the K11 vocabulary it overlaps (update, add, remove, move, resize, setProp/unset).
A K11 differ (carter-n4x.10) should pass the same files.

Refresh after the app's goldens change:
    cp ../CAR-TER/CAR-TERTests/Fixtures/layout-ops/*.json tests/fixtures/layout-ops/
"""
from __future__ import annotations

import json
import pathlib

import pytest

from carterkit.buffer import LayoutBuffer

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "layout-ops"
GOLDENS = sorted(FIXTURES.glob("*.json"))
KIT_OPS = {"setProp", "set", "unset", "update", "add", "remove", "move", "resize"}


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_k11(buf: LayoutBuffer, op: dict) -> None:
    kind = op["op"]
    if kind in ("setProp", "set"):
        assert "config" not in op, "nested config writes are app-only"
        buf.update_control(op["id"], {op["key"]: op.get("value")})
    elif kind == "unset":
        buf.update_control(op["id"], {op["key"]: None})
    elif kind == "update":
        patch = dict(op.get("set") or {})
        patch.update({k: None for k in op.get("unset") or []})
        buf.update_control(op["id"], patch)
    elif kind == "add":
        parent = op.get("parent", op.get("tab"))
        tab = parent if isinstance(parent, int) else next(
            i for i, t in enumerate(buf.tabs) if parent in (t.get("id"), t.get("title")))
        control = dict(op.get("control") or op["child"])
        if op.get("span") is not None:
            control["span"] = op["span"]
        buf.add_control(control, tab_index=tab, position=op.get("position"))
    elif kind == "remove":
        buf.remove_control(op["id"])
    elif kind == "move":
        assert "parent" not in op, "reparenting is app-only until K11 lands"
        buf.move_control(op["id"], position=op.get("position"))
    elif kind == "resize":
        buf.move_control(op["id"], span=op["span"])
    else:  # pragma: no cover - guarded by KIT_OPS
        raise AssertionError(f"op {kind} is not in the kit vocabulary")


def test_golden_set_present():
    assert len(GOLDENS) >= 20
    names = [load(p)["name"] for p in GOLDENS]
    assert names == [p.stem for p in GOLDENS], "golden name must match its basename"


@pytest.mark.parametrize("path", [p for p in GOLDENS if load(p).get("kit")], ids=lambda p: p.stem)
def test_kit_golden(path: pathlib.Path):
    golden = load(path)
    ops = golden["batch"]["ops"]
    assert {op["op"] for op in ops} <= KIT_OPS, "a kit golden may only use the overlapping vocabulary"
    buf = LayoutBuffer.from_layout(golden["input"])
    for op in ops:
        apply_k11(buf, op)
    assert buf.layout == golden["expected"]


@pytest.mark.parametrize("path", GOLDENS, ids=lambda p: p.stem)
def test_golden_shape(path: pathlib.Path):
    golden = load(path)
    assert set(golden) >= {"name", "description", "kit", "input", "batch"}  # optional: minted
    assert ("expected" in golden) != ("error" in golden)
    for op in golden["batch"]["ops"]:
        assert isinstance(op.get("op"), str)
