"""Resolve a layout argument that may arrive inline or as a file path.

Tools historically took only `layout_json` — the complete document as a string —
which forces an LLM caller to stream the entire layout through its own output
(slow, token-expensive, and error-prone for big layouts). `layout_path` lets the
caller point at a JSON file on disk instead; the file never transits the model.
"""

from __future__ import annotations

import json
from pathlib import Path

# Sanity cap for file reads. Far above any real layout (the biggest bundled
# sample is ~60 KB) but low enough to catch a wrong path (a log, a sqlite db).
MAX_LAYOUT_BYTES = 1_000_000


def load_layout_arg(layout_json: str = "", layout_path: str = "",
                    require: tuple[str, ...] = ()) -> tuple[dict | None, str | None]:
    """Return (layout, None) from exactly one of the two sources, or (None, error).

    Args:
        layout_json: The layout document inline.
        layout_path: Path to a .json layout file on disk.
        require: Top-level fields that must be present (e.g. ("name", "version", "tabs")).
    """
    if layout_json and layout_path:
        return None, "Pass either layout_json or layout_path, not both."
    if layout_path:
        p = Path(layout_path).expanduser()
        if not p.is_file():
            return None, f"No file at {p}"
        if p.stat().st_size > MAX_LAYOUT_BYTES:
            return None, (f"{p.name} is {p.stat().st_size:,} bytes — over the "
                          f"{MAX_LAYOUT_BYTES:,}-byte layout cap. Is that really a layout?")
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            return None, f"Couldn't read {p}: {e}"
        src = f" in {p.name}"
    elif layout_json:
        text, src = layout_json, ""
    else:
        return None, "Pass layout_json (inline) or layout_path (file on disk)."

    try:
        layout = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON{src}: {e}"
    if not isinstance(layout, dict):
        return None, f"Layout{src} must be a JSON object, got {type(layout).__name__}."

    missing = [f for f in require if f not in layout]
    if missing:
        return None, f"Layout{src} missing required fields: {', '.join(missing)}"
    return layout, None


# Cap for local-store seed files (spec 2026-09-24-local-readback-design §6.2).
MAX_RECORDS_BYTES = 1_000_000


def load_records_arg(records_json: str = "", records_path: str = "",
                     allow_inline: bool = True) -> tuple[list | None, str | None]:
    """Return (records, None) from exactly one source, or (None, error).

    Records are a JSON array of objects (`{"id"?, "fields": {...}}`). A path is
    read from disk so a seed never transits the model; `allow_inline=False`
    (local_seed) refuses inline JSON outright.
    """
    if records_json and records_path:
        return None, "Pass either records_json or records_path, not both."
    if records_json and not allow_inline:
        return None, "Seeds are file-only: pass records_path (a JSON array file)."
    if records_path:
        p = Path(records_path).expanduser()
        if not p.is_file():
            return None, f"No file at {p}"
        size = p.stat().st_size
        if size > MAX_RECORDS_BYTES:
            return None, (f"{p.name} is {size:,} bytes — over the "
                          f"{MAX_RECORDS_BYTES:,}-byte records cap.")
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return None, f"Couldn't read {p}: {e}"
        src = f" in {p.name}"
    elif records_json:
        text, src = records_json, ""
    else:
        return None, "Pass records_json (inline) or records_path (JSON array file)."

    try:
        records = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON{src}: {e}"
    if not isinstance(records, list):
        return None, f"Records{src} must be a JSON array, got {type(records).__name__}."
    bad = next((i for i, r in enumerate(records) if not isinstance(r, dict)), None)
    if bad is not None:
        return None, f"Record {bad}{src} must be an object ({{\"id\"?, \"fields\": {{…}}}})."
    return records, None
