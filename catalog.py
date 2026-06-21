"""Control catalog + example extraction — pure parsing of the ControlDocs markdown.

Mirrors the app's `ControlDocLoader` frontmatter parser (Swift) so the MCP and the
device agree on the field schema for every control. Deliberately does NOT use a real
YAML parser: the docs carry unquoted `#hex` defaults (e.g. `default: #667eea`) which
YAML would treat as comments, and some values omit quotes inconsistently. The
hand-rolled parser tolerates both.

Public surface (all pure; `docs_dir` is injected for testability):
    parse_doc(content, node_id)      -> dict | None   one doc's frontmatter + body + examples
    parse_all(docs_dir)              -> {node_id: doc}
    build_catalog(docs_dir, types)   -> {type: compact}   placeable-control schema
    get_examples(docs_dir, control)  -> [{"name", "json"}]
    find_example(docs_dir, control, name) -> {"name", "json"} | None
    resolve_doc(docs_dir, control)   -> doc | None   accepts node_id OR control type
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


# ─── Frontmatter parsing (mirrors ControlDocLoader.swift) ────────────────────


def _parse_str_array(value: str) -> list[str]:
    inner = value.strip().strip("[]")
    out = []
    for part in inner.split(","):
        s = part.strip().strip("\"'")
        if s:
            out.append(s)
    return out


def _parse_int_array(value: str) -> Optional[list[int]]:
    inner = value.strip().strip("[]")
    parts = []
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            parts.append(int(part))
        except ValueError:
            return None
    return parts or None


def _make_field(raw: dict[str, str]) -> dict:
    field: dict = {"name": raw.get("name", ""), "type": raw.get("type", "string")}
    if "values" in raw:
        field["values"] = _parse_str_array(raw["values"])
    if "default" in raw:
        field["default"] = raw["default"]
    if raw.get("description"):
        field["description"] = raw["description"]
    return field


def parse_doc(content: str, node_id: str) -> Optional[dict]:
    """Parse one control doc into {node_id,type,label,icon,category,defaultSpan,
    fields,themeFields,body,examples}. Returns None if it lacks a type+label
    (matches the Swift loader, which skips non-control prose-only docs)."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None

    front = lines[1:end]
    body = "\n".join(lines[end + 1:]).strip()

    meta: dict = {
        "node_id": node_id,
        "type": "",
        "label": "",
        "icon": "",
        "category": "",
        "defaultSpan": None,
        "fields": [],
        "themeFields": [],
    }

    in_fields = False
    current_list: Optional[list] = None
    current: dict[str, str] = {}

    def flush():
        nonlocal current
        if in_fields and current and current_list is not None:
            current_list.append(_make_field(current))
        current = {}

    for line in front:
        if not line.strip():
            continue
        top_level = not line.startswith((" ", "\t")) and ":" in line
        if top_level:
            flush()
            in_fields = False
            current_list = None
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "type":
                meta["type"] = value
            elif key == "label":
                meta["label"] = value
            elif key == "icon":
                meta["icon"] = value
            elif key == "category":
                meta["category"] = value
            elif key == "defaultSpan":
                meta["defaultSpan"] = _parse_int_array(value)
            elif key == "fields":
                in_fields = True
                current_list = meta["fields"]
            elif key == "themeFields":
                in_fields = True
                current_list = meta["themeFields"]
        elif in_fields:
            trimmed = line.strip()
            if trimmed.startswith("- name:"):
                flush()
                current = {"name": trimmed[len("- name:"):].strip().strip("\"'")}
            elif ":" in trimmed:
                k, _, v = trimmed.partition(":")
                current[k.strip()] = v.strip().strip('"')
    flush()

    if not meta["type"] or not meta["label"]:
        return None
    meta["body"] = body
    meta["examples"] = extract_examples(body)
    return meta


# ─── Example extraction (the `## Examples` section) ──────────────────────────


def extract_examples(body: str) -> list[dict]:
    """Pull named JSON snippets from a doc's `## Examples` section: each `### Title`
    followed by a ```json fenced block becomes {"name", "json"}. Only the Examples
    section is scanned, so unrelated json blocks (e.g. a "## Segments" sample) are
    ignored."""
    lines = body.split("\n")
    start = None
    for i, l in enumerate(lines):
        if l.strip().lower() == "## examples":
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    section = lines[start:end]

    examples: list[dict] = []
    title: Optional[str] = None
    seen: dict[str, int] = {}
    i = 0
    while i < len(section):
        l = section[i]
        if l.startswith("### "):
            title = l[4:].strip().strip("`")
            i += 1
            continue
        if l.strip().startswith("```json"):
            j = i + 1
            buf = []
            while j < len(section) and not section[j].strip().startswith("```"):
                buf.append(section[j])
                j += 1
            name = title or f"Example {len(examples) + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name} ({seen[name]})"
            else:
                seen[name] = 1
            examples.append({"name": name, "json": "\n".join(buf).strip()})
            i = j + 1
            continue
        i += 1
    return examples


# ─── Catalog assembly ────────────────────────────────────────────────────────

# Categories whose docs describe controls that can be placed in a layout grid.
PLACEABLE_CATEGORIES = {"controls", "display"}


def parse_all(docs_dir) -> dict[str, dict]:
    """Parse every `*.md` doc in the directory. Keyed by node_id (filename stem)."""
    out: dict[str, dict] = {}
    for f in sorted(Path(docs_dir).glob("*.md")):
        doc = parse_doc(f.read_text(), f.stem)
        if doc:
            out[doc["node_id"]] = doc
    return out


def _compact(doc: dict, include_theme: bool = False) -> dict:
    out: dict = {
        "type": doc["type"],
        "node_id": doc["node_id"],
        "label": doc["label"],
        "category": doc["category"],
    }
    if doc.get("defaultSpan"):
        out["defaultSpan"] = doc["defaultSpan"]
    if doc.get("fields"):
        out["fields"] = doc["fields"]
    if include_theme and doc.get("themeFields"):
        out["themeFields"] = doc["themeFields"]
    if doc.get("examples"):
        out["examples"] = [e["name"] for e in doc["examples"]]
    return out


def build_catalog(docs_dir, types: Optional[list[str]] = None,
                  include_theme: bool = False) -> dict[str, dict]:
    """Compact, machine-readable schema for placeable controls, keyed by control
    `type` (the value used in a layout). `types` filters to specific control types;
    `include_theme` adds the per-control theme override fields."""
    docs = parse_all(docs_dir)
    catalog: dict[str, dict] = {}
    wanted = set(types) if types else None
    for doc in docs.values():
        t = doc["type"]
        if wanted is not None:
            if t not in wanted and doc["node_id"] not in wanted:
                continue
        elif doc["category"] not in PLACEABLE_CATEGORIES:
            continue
        catalog[t] = _compact(doc, include_theme=include_theme)
    return catalog


def resolve_doc(docs_dir, control: str) -> Optional[dict]:
    """Find a doc by node_id (e.g. 'color-picker') or control type (e.g. 'colorPicker')."""
    docs = parse_all(docs_dir)
    if control in docs:
        return docs[control]
    for doc in docs.values():
        if doc["type"] == control:
            return doc
    return None


def get_examples(docs_dir, control: str) -> list[dict]:
    doc = resolve_doc(docs_dir, control)
    return doc["examples"] if doc else []


def find_example(docs_dir, control: str, name: str) -> Optional[dict]:
    """Match an example by exact or case-insensitive-prefix name."""
    examples = get_examples(docs_dir, control)
    for ex in examples:
        if ex["name"] == name:
            return ex
    low = name.lower()
    for ex in examples:
        if ex["name"].lower().startswith(low):
            return ex
    return None


def example_as_obj(example: dict) -> Optional[dict]:
    """Parse an example's JSON snippet into a dict (None if it doesn't parse)."""
    try:
        obj = json.loads(example["json"])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None
