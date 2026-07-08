"""Incremental-editing tools — the server-held working buffer.

A server-held working draft so the LLM edits surgically (add one control, tweak
one field) instead of re-emitting the whole layout. Each op mutates
`state.work_buffer`; push_buffer (tools/device.py) sends the full result to the
device (which already renders full layouts).
"""

from __future__ import annotations

import copy
import json
from typing import Optional

from carterkit import catalog, grid, validate
from carterkit.buffer import BufferError, LayoutBuffer

from carter_mcp import content, mesh, state
from carter_mcp.app import mcp
from carter_mcp.paths import SAMPLE_LAYOUTS_DIR


@mcp.tool()
async def begin_edit(name: str = "Untitled", columns: int = 4, rows: int = 8,
                     accent: str = "#667eea", from_sample: str = "",
                     from_device: bool = False, from_device_file: str = "",
                     mode: Optional[str] = None, row_height: Optional[int] = None) -> str:
    """Start (or restart) the working layout buffer for incremental editing.

    Then use add_control / insert_example / update_control / move_control / etc. to
    edit surgically, preview_buffer to see it, and push_buffer to send it live —
    without ever re-emitting the whole layout.

    The app-authored wiring flow: the user arranges controls in the phone's Layout
    Editor and saves; `begin_edit(from_device_file="my-layout.json")` pulls that
    file here (list_device_layouts shows the filenames), then probe_service +
    autowire_buffer bind its values, you wire the remaining actions/triggers it
    reports, lint_against_traffic checks reality, and save_buffer puts the wired
    layout back on the phone.

    Args:
        name: Layout name (blank-buffer mode).
        columns, rows: Grid of the first tab (blank-buffer mode).
        accent: Accent color hex (blank-buffer mode).
        mode, row_height: First tab's grid mode ("grid" default 2-D / "flow") and 2-D
            row-unit height in points (blank-buffer mode).
        from_sample: Seed from a sample layout filename (e.g. 'demo-offline.json').
        from_device: Seed from the layout currently live on the paired device (read-modify-write).
        from_device_file: Seed from a SAVED layout on the device by filename or
            display name (the phone-editor handoff), whether or not it's active.
    """
    if from_device_file:
        if mesh.connection_error():
            return "Not connected — can't read the device. Call connect first."
        layout = await mesh.fetch_saved_layout_obj(file=from_device_file, name=from_device_file)
        if not layout:
            return (f"Couldn't read '{from_device_file}' off the device — "
                    "check list_device_layouts for the exact filename.")
        try:
            state.work_buffer = LayoutBuffer.from_layout(layout)
        except BufferError as e:
            return f"Device layout couldn't be loaded into the buffer: {e}"
        return (f"Buffer seeded from the device's saved '{from_device_file}'.\n\n"
                + state.work_buffer.summary())
    if from_device:
        if mesh.connection_error():
            return "Not connected — can't read the device. Call connect first (or omit from_device)."
        layout = await mesh.fetch_device_layout_obj()
        if not layout:
            return "Couldn't read a layout off the device (none loaded, or it didn't respond)."
        try:
            state.work_buffer = LayoutBuffer.from_layout(layout)
        except BufferError as e:
            return f"Device layout couldn't be loaded into the buffer: {e}"
        return "Buffer seeded from the live device layout.\n\n" + state.work_buffer.summary()
    if from_sample:
        fname = from_sample if from_sample.endswith(".json") else from_sample + ".json"
        path = SAMPLE_LAYOUTS_DIR / fname
        if not path.exists():
            avail = ", ".join(f.name for f in SAMPLE_LAYOUTS_DIR.glob("*.json"))
            return f"Sample '{fname}' not found. Available: {avail}"
        try:
            state.work_buffer = LayoutBuffer.from_layout(json.loads(path.read_text()))
        except (json.JSONDecodeError, BufferError) as e:
            return f"Couldn't load sample: {e}"
        return f"Buffer seeded from {fname}.\n\n" + state.work_buffer.summary()
    state.work_buffer = LayoutBuffer.blank(name=name, columns=columns, rows=rows, accent=accent)
    if mode is not None:
        state.work_buffer.tabs[0]["grid"]["mode"] = mode
    if row_height is not None:
        state.work_buffer.tabs[0]["grid"]["rowHeight"] = row_height
    return (f"New blank buffer '{name}' ({rows}x{columns}, mode={mode or 'grid'}).\n\n"
            + state.work_buffer.summary())


@mcp.tool()
def preview_buffer(show_grids: bool = True) -> str:
    """Show the current working buffer: structure, per-tab grid maps, and any
    placement issues. Never re-dumps the whole JSON (use get_buffer_json for that)."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return state.work_buffer.summary(show_grids=show_grids)


@mcp.tool()
def get_buffer_json() -> str:
    """Return the full JSON of the working buffer (for inspection or manual save)."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return json.dumps(state.work_buffer.layout, indent=2)


@mcp.tool()
def add_control(control_json: str, tab_index: int = 0,
                position: Optional[list[int]] = None) -> str:
    """Add one control to the buffer. Auto-assigns a unique id and (if no position
    is given) the next free grid slot honoring the control's default span.

    Args:
        control_json: A single control object, e.g. {"type":"gauge","label":"Battery"}.
        tab_index: Which tab to add to (default 0).
        position: Optional [row, col]; omit to auto-place.
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        control = json.loads(control_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(control, dict) or "type" not in control:
        return "control_json must be an object with a 'type'."
    try:
        added = state.work_buffer.add_control(
            control, tab_index=tab_index, position=position,
            default_span=content.default_span_for(control.get("type")))
    except BufferError as e:
        return f"Couldn't add control: {e}"
    span = f" span {added['span']}" if added.get("span") else ""
    return f"Added {added['id']} ({added['type']}) at {added['position']}{span} on tab {tab_index}."


@mcp.tool()
def insert_example(control_id: str, name: str = "", tab_index: int = 0,
                   position: Optional[list[int]] = None) -> str:
    """Drop a documented control example straight into the buffer, with a fresh id
    and a free grid slot. Browse with list_control_examples, then place one here.

    Args:
        control_id: Control type or doc node-id (e.g. 'gauge').
        name: Example name (prefix match); omit for the first example.
        tab_index: Which tab to add to.
        position: Optional [row, col]; omit to auto-place.
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    ex = catalog.find_example(content.demos_dir(), control_id, name) if name \
        else (catalog.get_examples(content.demos_dir(), control_id) or [None])[0]
    if not ex:
        return f"No matching example for '{control_id}'. Try list_control_examples."
    obj = catalog.example_as_obj(ex)
    if not obj:
        return f"Example '{ex['name']}' didn't parse as a control object."
    try:
        added = state.work_buffer.add_control(
            obj, tab_index=tab_index, position=position,
            default_span=content.default_span_for(obj.get("type")))
    except BufferError as e:
        return f"Couldn't insert example: {e}"
    return f"Inserted '{ex['name']}' as {added['id']} at {added['position']} on tab {tab_index}."


@mcp.tool()
def update_control(control_id: str, patch_json: str) -> str:
    """Merge fields into an existing control. Set a field to null to remove it.

    Args:
        control_id: id of the control to edit.
        patch_json: Object of fields to merge, e.g. {"tint":"#FF0000","style":"ghost"}.
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        patch = json.loads(patch_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(patch, dict):
        return "patch_json must be an object."
    try:
        state.work_buffer.update_control(control_id, patch)
    except BufferError as e:
        return f"Couldn't update: {e}"
    return f"Updated {control_id}: {', '.join(patch.keys())}."


@mcp.tool()
def remove_control(control_id: str) -> str:
    """Remove a control from the buffer by id."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        state.work_buffer.remove_control(control_id)
    except BufferError as e:
        return f"Couldn't remove: {e}"
    return f"Removed {control_id}."


@mcp.tool()
def move_control(control_id: str, position: Optional[list[int]] = None,
                 span: Optional[list[int]] = None,
                 tab_index: Optional[int] = None) -> str:
    """Reposition / resize a control, or move it to another tab.

    Args:
        control_id: id of the control.
        position: New [row, col].
        span: New [rowSpan, colSpan].
        tab_index: Move to this tab index.
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        ch = state.work_buffer.move_control(control_id, position=position, span=span,
                                            tab_index=tab_index)
    except BufferError as e:
        return f"Couldn't move: {e}"
    return f"Moved {control_id} → position {ch.get('position')} span {ch.get('span', [1, 1])}."


@mcp.tool()
def add_tab(title: str, icon: str = "square.grid.2x2",
            columns: int = 4, rows: int = 8,
            mode: Optional[str] = None, row_height: Optional[int] = None) -> str:
    """Add a tab to the buffer. Returns its index.

    mode: "grid" (default 2-D — controls span row x col) or "flow" (legacy
    row-banded; use for a full-page map/chat/cardList or a plain form).
    row_height: points per row-unit in 2-D mode (default 56)."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    # Only forward mode/row_height when set, so the default path still works against
    # a pre-0.5.0 carterkit (its add_tab doesn't accept those kwargs).
    extra = {}
    if mode is not None:
        extra["mode"] = mode
    if row_height is not None:
        extra["row_height"] = row_height
    idx = state.work_buffer.add_tab(title, icon=icon, columns=columns, rows=rows, **extra)
    return f"Added tab {idx}: '{title}' ({rows}x{columns}, mode={mode or 'grid'})."


@mcp.tool()
def add_group(group_json: str, tab_index: int = 0,
              position: Optional[list[int]] = None) -> str:
    """Add a group container to the buffer (auto-placed unless position given).
    Nested children are normalized like add_control: each gets an id and an
    auto-placed position in the group's own grid.

    Args:
        group_json: A group object, e.g. {"label":"Lights","grid":{"columns":2,"rows":2},"children":[...]}.
        tab_index: Which tab to add to.
        position: Optional [row, col].
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        group = json.loads(group_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(group, dict):
        return "group_json must be an object."
    try:
        added = state.work_buffer.add_group(group, tab_index=tab_index, position=position)
    except BufferError as e:
        return f"Couldn't add group: {e}"
    return f"Added group {added['id']} at {added['position']} on tab {tab_index}."


@mcp.tool()
def show_grid(tab_index: int = 0) -> str:
    """ASCII occupancy map of a tab's grid — which cells are filled (by which
    control) and which are free. 'See' the layout spatially before editing."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        tab = state.work_buffer._tab(tab_index)
    except BufferError as e:
        return str(e)
    cols, rows = LayoutBuffer._grid_dims(tab)
    return (f"Tab {tab_index}: {tab.get('title', '?')} ({rows}x{cols})\n"
            + grid.render_grid(tab.get("children", []), cols, rows))


@mcp.tool()
def discard_buffer() -> str:
    """Discard the working buffer without sending anything."""
    if state.work_buffer is None:
        return "No active buffer."
    state.work_buffer = None
    return "Buffer discarded."


@mcp.tool()
def validate_layout(layout_json: str) -> str:
    """Lint a layout against the control schema WITHOUT pushing it: duplicate ids,
    unknown control types, unknown/bad-enum fields, and grid overlaps/out-of-bounds.

    Args:
        layout_json: Complete layout JSON string.
    """
    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    return validate.format_findings(
        validate.validate_layout(layout, content.build_catalog(include_theme=True)))


@mcp.tool()
def validate_buffer() -> str:
    """Lint the working buffer against the control schema (same checks as
    validate_layout) before you push or save it."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    return validate.format_findings(
        validate.validate_layout(state.work_buffer.layout,
                                 content.build_catalog(include_theme=True)))


@mcp.tool()
def snapshot_buffer(label: str = "") -> str:
    """Save a snapshot of the working buffer so you can revert later."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    name = label or f"snapshot {len(state.buffer_snapshots) + 1}"
    state.buffer_snapshots.append((name, copy.deepcopy(state.work_buffer.layout)))
    return f"Saved '{name}' ({len(state.buffer_snapshots)} snapshot(s) total)."


@mcp.tool()
def list_snapshots() -> str:
    """List saved buffer snapshots."""
    if not state.buffer_snapshots:
        return "No snapshots yet. Use snapshot_buffer."
    return "\n".join(f"  {i}: {label}" for i, (label, _) in enumerate(state.buffer_snapshots))


@mcp.tool()
def revert_buffer(index: int = -1) -> str:
    """Restore the working buffer from a snapshot (default the most recent).

    Args:
        index: Snapshot index (see list_snapshots); -1 = latest.
    """
    if not state.buffer_snapshots:
        return "No snapshots to revert to."
    try:
        label, layout = state.buffer_snapshots[index]
    except IndexError:
        return f"No snapshot at index {index}."
    state.work_buffer = LayoutBuffer.from_layout(layout)
    return f"Reverted buffer to '{label}'.\n\n" + state.work_buffer.summary()
