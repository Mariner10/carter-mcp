"""Shared layout conformance fixtures (carter-c1n.11), run through the MCP's own
`validate_layout` (layout_path) and `validate_buffer` tools, so the MCP agrees with the
device's `LayoutDecoder` and with carterkit.

The fixtures have ONE home, the app repo:
    CAR-TER/CAR-TERTests/Fixtures/layout-conformance/{accept,repair,reject}/<name>.json
with `<name>.expect.json` sidecars (schema: that folder's README.md). Point
CARTER_CONFORMANCE_DIR at it, or keep the workspace layout (this repo beside CAR-TER/);
otherwise this module skips. It reads the sidecar's `mcp` block if present, else `kit`
(the MCP's validator IS the installed carterkit's). `pending.mcp` cases run as
xfail(strict=True) and fail the day they pass, so the owning bead flips them.

The MCP tracks the RELEASED carterkit. While the installed kit predates the carter-c1n.11
validator fixes (no `validate.MAX_TABS`), a mismatch is an expected failure that names
the installed version; it turns green once a kit with those fixes is released and
installed in this venv, with no edit here. What the MCP itself owns is always strict:
the tools never raise, and validate_buffer agrees with validate_layout.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("CARTER_OFFLINE", "1")   # catalog from cache/installed kit, never HTTP

from carterkit import validate as kit_validate           # noqa: E402
from carterkit.buffer import BufferError, LayoutBuffer   # noqa: E402

from carter_mcp import state                             # noqa: E402
from carter_mcp.tools import buffer as buffer_tools      # noqa: E402

BUCKETS = ("accept", "repair", "reject")
_REL = Path("CAR-TER") / "CAR-TERTests" / "Fixtures" / "layout-conformance"
KIT_HAS_C1N11 = hasattr(kit_validate, "MAX_TABS")
try:
    from importlib.metadata import version as _version
    KIT_VERSION = _version("carterkit")
except Exception:                       # pragma: no cover
    KIT_VERSION = "?"


def conformance_dir() -> Path | None:
    env = os.environ.get("CARTER_CONFORMANCE_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if (parent / _REL).is_dir():
            return parent / _REL
    return None


ROOT = conformance_dir()
CASES = [] if ROOT is None or not ROOT.is_dir() else [
    p for b in BUCKETS for p in sorted((ROOT / b).glob("*.json"))
    if not p.name.endswith(".expect.json")]

_LINE = re.compile(r"^\s*([✗⚠])\s*\[([a-z_]+)\]")


def parse_report(text: str) -> list[dict]:
    """The tools' formatted report back into findings (severity + kind)."""
    if text.startswith("Invalid JSON"):
        return [{"severity": "error", "kind": "not_json"}]
    if "must be a JSON object" in text:
        return [{"severity": "error", "kind": "structure"}]
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.append({"severity": "error" if m.group(1) == "✗" else "warn", "kind": m.group(2)})
    assert out or text.startswith("✓"), f"unrecognised validator output: {text[:200]}"
    return out


def judge(findings: list[dict], expect: dict) -> list[str]:
    errors = {f["kind"] for f in findings if f["severity"] == "error"}
    warns = {f["kind"] for f in findings if f["severity"] == "warn"}
    problems = []
    verdict = "error" if errors else "ok"
    if verdict != expect["verdict"]:
        problems.append(f"verdict {verdict} (errors {sorted(errors)}), expected {expect['verdict']}")
    if set(expect.get("errors", [])) - errors:
        problems.append(f"missing error kinds {sorted(set(expect['errors']) - errors)}")
    if set(expect.get("warns", [])) - warns:
        problems.append(f"missing warn kinds {sorted(set(expect['warns']) - warns)}")
    return problems


def test_fixture_folder_found():
    if ROOT is None:
        pytest.skip("conformance fixtures not found: set CARTER_CONFORMANCE_DIR")
    assert len(CASES) >= 40, f"fixture set thinned or missing under {ROOT}"


@pytest.mark.parametrize("path", CASES, ids=[f"{p.parent.name}/{p.stem}" for p in CASES])
def test_mcp_matches_sidecar(path: Path):
    expect = json.loads(path.with_name(path.stem + ".expect.json").read_text())
    want = expect.get("mcp") or expect["kit"]

    try:
        report = buffer_tools.validate_layout(layout_path=str(path))
    except Exception as e:              # the tool must report, never raise
        report = None
        problems = [f"validate_layout raised {type(e).__name__}: {e}"]
    if report is None:
        if not KIT_HAS_C1N11:
            pytest.xfail(f"installed carterkit {KIT_VERSION} is stale: {problems}")
        pytest.fail(problems[0])
    problems = judge(parse_report(report), want)

    # validate_buffer must say the same thing about any document it can hold.
    try:
        layout = json.loads(path.read_text())
        state.work_buffer = LayoutBuffer.from_layout(layout)
    except (ValueError, BufferError):
        pass
    else:
        try:
            buffered = buffer_tools.validate_buffer()
        finally:
            state.work_buffer = None
        assert buffered == report, "validate_buffer and validate_layout disagree"

    pending = expect.get("pending", {}).get("mcp")
    if pending:
        if problems:
            pytest.xfail(f"pending {pending}: {problems}")
        pytest.fail(f"pending {pending} now passes: delete its pending.mcp line")
    if problems and not KIT_HAS_C1N11:
        pytest.xfail(f"installed carterkit {KIT_VERSION} predates the carter-c1n.11 "
                     f"validator fixes: {problems}")
    assert not problems, f"{path.parent.name}/{path.stem}: {problems}\n{report}"
