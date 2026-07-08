"""Live-service tools — probe traffic, simulate frames, wire and lint bindings,
run scripted UI scenarios, and speak through in-layout chat."""

from __future__ import annotations

import asyncio
import json

from carterkit import dynamic, validate
from carterkit.buffer import LayoutBuffer

from carter_mcp import autowire, chat, mesh, meshgraph, probe, qa, state
from carter_mcp import simulate as simulate_lib  # the `simulate` tool below shadows the name
from carter_mcp.app import mcp


@mcp.tool()
async def probe_service(seconds: int = 8, event: str = "broadcast") -> str:
    """Listen to the live mesh and report the service's data schema: which events and
    fields it emits, their value types, ranges, and examples. Run this, then
    autowire_buffer / lint_against_traffic / infer_layout can use what was discovered.

    Args:
        seconds: How long to listen (default 8).
        event: The mesh event to sniff (default 'broadcast', the data channel).
    """
    if err := mesh.connection_error():
        return err
    frames: list = []
    prior = state.socket.handlers.get(event)

    async def _rec(payload):
        frames.append((event, payload))

    state.socket.on(event, _rec)
    try:
        await asyncio.sleep(max(1, seconds))
    finally:
        if prior is not None:
            state.socket.handlers[event] = prior
        else:
            state.socket.handlers.pop(event, None)
    state.last_probe_events = probe.aggregate(frames)
    state.last_probe_frames = [payload for _ev, payload in frames]
    return (f"Observed {len(frames)} frame(s) on '{event}' over {seconds}s.\n\n"
            + probe.format_discovery(state.last_probe_events))


@mcp.tool()
async def simulate(payload_json: str, event: str = "broadcast") -> str:
    """Emit a data frame onto the channel as if from the service — drives any synced
    controls so gauges move and sparklines fill (live demo / no-backend testing).

    Args:
        payload_json: The frame to emit, e.g. {"battery":82,"cpu_temp":61}.
        event: Event to emit on (default 'broadcast').
    """
    if err := mesh.connection_error():
        return err
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    try:
        await state.socket.send("broadcast_request" if event == "broadcast" else event, payload)
    except Exception as e:
        return f"Failed to emit: {e}"
    return f"Emitted on '{event}': {json.dumps(payload)[:200]}"


@mcp.tool()
async def set_control_value(control_id: str, value_json: str) -> str:
    """Drive one buffer control live by emitting a frame at its bound valuePath.

    Args:
        control_id: id of a control in the working buffer (must have a listen sync).
        value_json: The value to push, e.g. 82 or "online" or true.
    """
    if err := mesh.connection_error():
        return err
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    found = state.work_buffer.find(control_id)
    if not found:
        return f"No control '{control_id}' in the buffer."
    binding = simulate_lib.control_binding(found[2])
    if not binding:
        return f"'{control_id}' has no listen sync to drive. Wire it first (autowire_buffer)."
    event, path = binding
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        value = value_json  # treat as a bare string
    frame = simulate_lib.build_frame({path: value})
    try:
        await state.socket.send("broadcast_request" if event == "broadcast" else event, frame)
    except Exception as e:
        return f"Failed: {e}"
    return f"Drove {control_id} → {path}={value!r} on '{event}'."


@mcp.tool()
def autowire_buffer(event: str = "broadcast") -> str:
    """Bind unbound display controls in the buffer to fields discovered by the last
    probe_service run (matched by name), then report what still needs wiring —
    values without a match AND input controls without an action (the triggers half,
    which needs the server's command vocabulary, i.e. you). Run probe_service first."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    paths = probe.candidate_paths(state.last_probe_events)
    if not paths:
        return "No discovered fields yet. Run probe_service first."
    wired = autowire.autowire_layout(state.work_buffer.layout, paths, event=event)
    out: list[str] = []
    if wired:
        out.append("Wired:\n" + "\n".join(f"  - {cid} → {p}" for cid, p in wired))
    else:
        out.append("Nothing to wire (no unbound display controls matched a discovered field).")
    remaining = autowire.unwired_report(state.work_buffer.layout)
    if remaining["values"]:
        out.append("Still no sync (no matching field seen): "
                   + ", ".join(remaining["values"]))
    if remaining["triggers"]:
        out.append("Inputs with no action yet (wire these with update_control): "
                   + ", ".join(remaining["triggers"]))
    return "\n".join(out)


@mcp.tool()
def lint_against_traffic() -> str:
    """Check the buffer's sync valuePaths against fields seen by the last probe; flags
    bindings that would silently show no data (wrong path / namespace)."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    paths = probe.candidate_paths(state.last_probe_events)
    if not paths:
        return "No probe data yet. Run probe_service first."
    findings = autowire.live_data_lint(state.work_buffer.layout, paths)
    if not findings:
        return "✓ Every synced valuePath was seen in observed traffic."
    return "⚠ Paths not seen in traffic:\n" + "\n".join(
        f"  - {f['id']}: {f['detail']}" for f in findings)


@mcp.tool()
def lint_dynamic_traffic() -> str:
    """Check the buffer's `dynamic=` groups against the broadcasts seen by the last
    probe: events that never arrive, payloads missing a `children` array, and injected
    children that won't render (unknown type, bad enum, id clash, off-grid placement).
    Run probe_service first. The dynamic-content counterpart to lint_against_traffic."""
    if state.work_buffer is None:
        return "No active buffer. Call begin_edit first."
    groups = dynamic.dynamic_groups(state.work_buffer.layout)
    if not groups:
        return "No dynamic groups in the buffer (no group has a `dynamic` event)."
    if not state.last_probe_frames:
        return "No probe data yet. Run probe_service first."
    findings = dynamic.lint_dynamic_traffic(state.work_buffer.layout, state.last_probe_frames)
    if not findings:
        return f"✓ All {len(groups)} dynamic group(s) get well-formed children from observed traffic."
    return validate.format_findings(findings)


@mcp.tool()
async def show_mesh_graph(name: str = "Mesh") -> str:
    """Visualize the live MeshSocket network on the device — builds a graph layout and
    pushes the current roster as animated nodes/edges. "Show me my mesh."

    Replaces the working buffer with the mesh layout.

    Args:
        name: Layout name.
    """
    if err := mesh.connection_error():
        return err
    try:
        result = await state.socket.request("get_nodes", timeout=3.0)
    except Exception as e:
        return f"Couldn't read the roster: {e}"
    clients = result.get("clients", []) if isinstance(result, dict) else []
    graphdata = meshgraph.roster_to_graph(clients)
    state.work_buffer = LayoutBuffer.from_layout(meshgraph.build_mesh_layout(name))
    push_result = await mesh.apply_or_broadcast(state.work_buffer.layout)
    frame = simulate_lib.build_frame({meshgraph.MESH_VALUE_PATH: json.dumps(graphdata)})
    try:
        await state.socket.send("broadcast_request", frame)
    except Exception:
        pass
    return f"Pushed a mesh graph with {len(graphdata['nodes'])} node(s). {push_result}"


@mcp.tool()
async def run_scenario(steps_json: str) -> str:
    """Drive the device through a scripted scenario and assert control states — UI
    testing over the mesh, no XCUITest.

    Each step: {"emit": {...frame...}, "event": "broadcast", "wait": 0.5,
                "expect": {"control_id": expected_value, ...}}.
    `emit` pushes a data frame; after `wait` seconds the named controls are read back
    and compared to `expect`.

    Args:
        steps_json: JSON array of step objects.
    """
    if err := mesh.connection_error():
        return err
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    if not isinstance(steps, list):
        return "steps_json must be an array of step objects."

    lines, passed, failed = [], 0, 0
    for i, step in enumerate(steps):
        emit = step.get("emit")
        event = step.get("event", "broadcast")
        if emit is not None:
            await state.socket.send("broadcast_request" if event == "broadcast" else event, emit)
        await asyncio.sleep(step.get("wait", 0.5))
        expect = step.get("expect")
        if not expect:
            lines.append(f"step {i}: emitted")
            continue
        actual = await mesh.fetch_control_state(list(expect.keys())) or {}
        fails = qa.assert_values(expect, actual)
        if fails:
            failed += 1
            lines.append(f"step {i}: FAIL — " + "; ".join(f"{f['id']}: {f['detail']}" for f in fails))
        else:
            passed += 1
            lines.append(f"step {i}: ok ({', '.join(expect.keys())})")
    return f"Scenario: {passed} passed, {failed} failed.\n" + "\n".join(lines)


@mcp.tool()
async def say_in_chat(text: str, sender_name: str = "Claude") -> str:
    """Post a message into a layout's channel chat control as the LLM — the in-app
    agent surface. Requires a paired device showing a chat control.

    Args:
        text: The message to send.
        sender_name: Display name to send as (default 'Claude').
    """
    if err := mesh.connection_error():
        return err
    msg = chat.build_chat_message(text, sender_name=sender_name,
                                  channel=state.current_channel or "")
    try:
        await state.socket.send("chat_message", msg)
    except Exception as e:
        return f"Failed to send: {e}"
    return f"Sent to chat as {sender_name}: {text!r}"


@mcp.tool()
async def read_chat(seconds: int = 15) -> str:
    """Listen for incoming chat messages for a while and return them — so the LLM can
    answer what users type in an in-app chat control.

    Args:
        seconds: How long to listen (default 15).
    """
    if err := mesh.connection_error():
        return err
    msgs: list = []
    prior = state.socket.handlers.get("chat_message")

    async def _rec(payload):
        m = chat.parse_incoming(payload)
        if m:
            msgs.append(m)

    state.socket.on("chat_message", _rec)
    try:
        await asyncio.sleep(max(1, seconds))
    finally:
        if prior is not None:
            state.socket.handlers["chat_message"] = prior
        else:
            state.socket.handlers.pop("chat_message", None)
    if not msgs:
        return f"No chat messages in {seconds}s."
    return "\n".join(f"{m['sender']}: {m['text']}" for m in msgs)
