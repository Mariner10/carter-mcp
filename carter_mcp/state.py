"""Mutable per-session state, shared across the tool modules.

Everything that persists between tool calls within one MCP session lives here,
as plain module attributes — tools read `state.work_buffer` and assign
`state.socket = …` directly. One home for all of it keeps the session's moving
parts visible at a glance (and avoids `global` scattered through the tools).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from carterkit.buffer import LayoutBuffer
from meshsocket import MeshSocket

# ─── Connection ───────────────────────────────────────────────────────────────

socket: Optional[MeshSocket] = None
socket_task: Optional[asyncio.Task] = None
current_channel: Optional[str] = None
peer_list: list[dict] = []
device_connected_event: Optional[asyncio.Event] = None

# Active session connection target, set by connect(); lets the relay URL/token be
# overridden per call (the relay URL can change) instead of being hardcoded.
# show_qr reads these so the device pairs to the exact relay the editor used.
active_url: Optional[str] = None
active_token: Optional[str] = None
# What the *device* scans. For the local relay this is the Mac's LAN IP (the
# editor itself dials loopback), so the two can differ.
active_qr_url: Optional[str] = None

# Zero-config authoring transport: an in-process, auth-free MeshSocket relay.
local_relay = None
local_relay_task: Optional[asyncio.Task] = None

# ─── Authoring ────────────────────────────────────────────────────────────────

# The server-held draft for incremental editing (begin_edit / add_control / …).
# Persists across tool calls within a session; pushed to the device as a full layout.
work_buffer: Optional[LayoutBuffer] = None

# Saved buffer states for snapshot / revert (experiment fearlessly).
buffer_snapshots: list[tuple[str, dict]] = []

# ─── Live-service data ────────────────────────────────────────────────────────

# Aggregated traffic schema from the last probe_service run (event -> path -> stats),
# consumed by autowire_buffer / lint_against_traffic.
last_probe_events: dict = {}
# Raw decoded payloads from the last probe_service run, consumed by
# lint_dynamic_traffic (which needs the actual `children` arrays, not the aggregate).
last_probe_frames: list = []

# Resolved by the `control-edit-response` listener when the user taps "Send to
# Editor" on the phone's configurator (the customize_on_phone round-trip).
control_edit_future: Optional[asyncio.Future] = None
