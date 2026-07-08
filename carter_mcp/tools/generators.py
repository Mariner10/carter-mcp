"""Generator tools — infer layouts from data, tune gauges, themes, backend codegen."""

from __future__ import annotations

import json

from carterkit import codegen, infer, theming, tune
from carterkit.buffer import LayoutBuffer

from carter_mcp import content, state
from carter_mcp.app import mcp


@mcp.tool()
def infer_layout(payload_json: str, name: str = "Inferred", event: str = "telemetry",
                 columns: int = 4, rows: int = 8, into_buffer: bool = True) -> str:
    """Infer a wired first-draft layout from a real JSON payload (paste a sample from
    your service, or use probe_service to capture one). Numbers→gauges, 0..1→progress
    rings, bools→toggles, lat/lng→map, arrays→sparklines/cardlists, strings→labels —
    each bound to the right nested valuePath.

    Args:
        payload_json: A sample JSON object emitted by your service.
        name: Layout name.
        event: The mesh event the controls should listen on (default 'telemetry').
        columns, rows: Grid of the inferred tab.
        into_buffer: Load the result into the working buffer for further editing (default).
    """
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(payload, dict):
        return "payload_json must be a JSON object."
    layout = infer.build_layout(payload, name=name, event=event, columns=columns,
                                rows=rows, default_span_fn=content.default_span_for)
    if into_buffer:
        state.work_buffer = LayoutBuffer.from_layout(layout)
        return ("Inferred layout loaded into the buffer (edit, then push_buffer):\n\n"
                + state.work_buffer.summary())
    return json.dumps(layout, indent=2)


@mcp.tool()
def autotune_gauge(control_id: str, samples_json: str, field_name: str = "") -> str:
    """Tune a buffer gauge from observed samples: sets min/max and color zones at the
    data's percentiles, oriented by whether higher is better (battery) or worse (temp),
    inferred from the field name.

    Args:
        control_id: id of a gauge in the working buffer.
        samples_json: JSON array of numeric samples, e.g. [40,52,61,73,95].
        field_name: Metric name for unit/direction hints (defaults to the id).
    """
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    try:
        samples = json.loads(samples_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(samples, list) or not samples:
        return "samples_json must be a non-empty array of numbers."
    found = state.work_buffer.find(control_id)
    if not found:
        return f"No control '{control_id}' in the buffer."
    ch = found[2]
    if ch.get("type") != "gauge":
        return f"'{control_id}' is a {ch.get('type')}, not a gauge."
    patch = tune.tune_gauge(ch, samples, field_name=field_name or control_id)
    state.work_buffer.update_control(control_id, patch)
    unit = tune.infer_unit(field_name or control_id)
    return (f"Tuned {control_id}: min {patch['min']}, max {patch['max']}, "
            f"{len(patch['segments'])} color zone(s)" + (f", unit {unit}" if unit else "") + ".")


@mcp.tool()
def list_theme_vibes() -> str:
    """List the built-in theme vibes you can generate with generate_theme."""
    return ("Vibes: " + ", ".join(sorted(theming.VIBES.keys()))
            + ". Or pass an accent hex color for a custom brand theme.")


@mcp.tool()
def generate_theme(description: str = "", accent: str = "",
                   apply_to_buffer: bool = True) -> str:
    """Generate a layout theme from a vibe description or a brand color, and (by
    default) apply it to the working buffer.

    Args:
        description: Free text, e.g. 'fighter jet HUD', 'synthwave neon', 'clean apple'.
        accent: A brand hex color to build the theme around (overrides description).
        apply_to_buffer: Set the buffer layout's theme + accentColor (default true).
    """
    if accent:
        try:
            theme = theming.brand_theme(accent)
        except ValueError as e:
            return f"Bad accent color: {e}"
    elif description:
        theme = theming.theme_for(description)
    else:
        return "Provide a description (e.g. 'synthwave') or an accent hex color."
    pretty = json.dumps(theme, indent=2)
    if apply_to_buffer:
        if state.work_buffer is None:
            return f"No active buffer to apply to. Theme:\n```json\n{pretty}\n```"
        state.work_buffer.layout["theme"] = theme
        if theme.get("accentColor"):
            state.work_buffer.layout["accentColor"] = theme["accentColor"]
        return (f"Applied theme (accent {theme.get('accentColor')}). push_buffer to see it.\n"
                f"```json\n{pretty}\n```")
    return pretty


def _layout_or_buffer(layout_json: str):
    """Resolve a layout from JSON arg, else the working buffer. Returns (layout, error)."""
    if layout_json:
        try:
            return json.loads(layout_json), None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"
    if state.work_buffer is not None:
        return state.work_buffer.layout, None
    return None, "No layout given and no active buffer. Pass layout_json or begin_edit."


@mcp.tool()
def generate_service(layout_json: str = "") -> str:
    """Generate a runnable Python MeshSocket service that speaks to a layout — handles
    the events its controls fire and emits the telemetry they listen for. Uses the
    working buffer if no layout_json is given.

    Args:
        layout_json: Optional layout JSON; defaults to the working buffer.
    """
    layout, err = _layout_or_buffer(layout_json)
    if err:
        return err
    return "```python\n" + codegen.generate_service_stub(layout) + "\n```"


@mcp.tool()
def generate_adapter(base_url: str = "https://api.example.com",
                     layout_json: str = "") -> str:
    """Generate a REST-poll → MeshSocket adapter that maps an API's fields to a
    layout's synced valuePaths. Uses the working buffer if no layout_json is given.

    Args:
        base_url: The REST endpoint to poll.
        layout_json: Optional layout JSON; defaults to the working buffer.
    """
    layout, err = _layout_or_buffer(layout_json)
    if err:
        return err
    return "```python\n" + codegen.generate_rest_adapter(layout, base_url=base_url) + "\n```"
