"""Backend codegen — generate a MeshSocket service that speaks to a given layout.

A layout declares the events its controls fire (actions) and the events/valuePaths
its display controls listen for (sync). From that contract we can emit a runnable
Python service skeleton (handles the actions, emits the telemetry) and a REST-poll
adapter template. Pure string generation.
"""

from __future__ import annotations

import re
from typing import Any


def _walk_controls(layout: dict) -> list[dict]:
    out: list[dict] = []

    def walk(children):
        for ch in children or []:
            if not isinstance(ch, dict):
                continue
            out.append(ch)
            if ch.get("type") == "group":
                walk(ch.get("children"))

    for tab in layout.get("tabs", []):
        walk(tab.get("children"))
    return out


def analyze_layout(layout: dict) -> dict:
    """Extract the wire contract: {actions: {event: mode}, emits: {event: [valuePaths]}}."""
    actions: dict[str, str] = {}
    emits: dict[str, set] = {}
    for ch in _walk_controls(layout):
        a = ch.get("action")
        if isinstance(a, dict) and a.get("event"):
            actions.setdefault(a["event"], a.get("mode", "send"))
        for s in ch.get("sync") or []:
            if isinstance(s, dict) and s.get("valuePath"):
                emits.setdefault(s.get("event") or "broadcast", set()).add(s["valuePath"])
    return {"actions": actions, "emits": {k: sorted(v) for k, v in emits.items()}}


def _ident(event: str) -> str:
    s = re.sub(r"\W+", "_", event).strip("_")
    return s or "evt"


def _connection(layout: dict) -> tuple[str, str]:
    c = layout.get("connection") or {}
    return c.get("url") or "ws://localhost:8765", c.get("token") or ""


def generate_service_stub(layout: dict, meshsocket_path: str =
                          "/Users/carter/Desktop/Programming/MeshSocket/Python") -> str:
    """A runnable Python MeshSocket service skeleton for this layout."""
    spec = analyze_layout(layout)
    url, token = _connection(layout)
    name = layout.get("name", "service")

    handlers = []
    for event, mode in sorted(spec["actions"].items()):
        fn = f"on_{_ident(event)}"
        reply = "    return {\"ok\": True}" if mode == "request" else "    return None"
        handlers.append(
            f'@socket.on("{event}")\n'
            f'async def {fn}(payload):\n'
            f'    # action from a control (mode={mode})\n'
            f'    print("[action] {event}:", payload)\n'
            f'{reply}\n')
    handlers_block = "\n".join(handlers) if handlers else "# (layout fires no actions)\n"

    emit_lines = []
    for event, paths in sorted(spec["emits"].items()):
        emit_lines.append(f'        frame = {{}}')
        for p in paths:
            emit_lines.append(f'        _set(frame, "{p}", round(random.uniform(0, 100), 2))')
        send = ('await socket.send("broadcast_request", frame)' if event == "broadcast"
                else f'await socket.send("{event}", frame)')
        emit_lines.append(f'        {send}')
    emit_block = "\n".join(emit_lines) if emit_lines else "        pass  # no sync controls"

    return f'''#!/usr/bin/env python3
"""Auto-generated MeshSocket service for layout "{name}".

Handles the actions its controls fire and emits the telemetry they listen for.
Fill in the TODOs with your real device/data logic.
"""
import asyncio
import random
import sys

sys.path.insert(0, "{meshsocket_path}")
from socketCore import MeshSocket  # noqa: E402

URL = "{url}"
TOKEN = "{token}"


def _set(d, dotted, value):
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {{}})
    cur[parts[-1]] = value


socket = MeshSocket(url=URL, name="{_ident(name)}-service", auth_token=TOKEN,
                    channel="home", role="server", can_broadcast=True)


{handlers_block}

async def telemetry_loop():
    while True:
        # TODO: replace random values with real readings
{emit_block}
        await asyncio.sleep(1.0)


async def main():
    asyncio.create_task(socket.start())
    await socket.wait_until_ready()
    await telemetry_loop()


if __name__ == "__main__":
    asyncio.run(main())
'''


def generate_rest_adapter(layout: dict, base_url: str = "https://api.example.com",
                          meshsocket_path: str =
                          "/Users/carter/Desktop/Programming/MeshSocket/Python") -> str:
    """A REST-poll → MeshSocket adapter template mapping API fields to the layout's
    sync valuePaths."""
    spec = analyze_layout(layout)
    paths = sorted({p for ps in spec["emits"].values() for p in ps})
    mapping = "\n".join(f'        _set(frame, "{p}", data.get("{p.split(".")[-1]}"))'
                        for p in paths) or "        pass  # map API fields here"
    url, token = _connection(layout)
    return f'''#!/usr/bin/env python3
"""Auto-generated REST -> MeshSocket adapter for "{layout.get('name','layout')}".

Polls a REST API and pushes the fields the layout's controls listen for.
"""
import asyncio
import sys
import urllib.request
import json

sys.path.insert(0, "{meshsocket_path}")
from socketCore import MeshSocket  # noqa: E402

API = "{base_url}"
URL = "{url}"
TOKEN = "{token}"


def _set(d, dotted, value):
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {{}})
    cur[parts[-1]] = value


socket = MeshSocket(url=URL, name="rest-adapter", auth_token=TOKEN,
                    channel="home", role="server", can_broadcast=True)


def fetch():
    with urllib.request.urlopen(API, timeout=10) as r:
        return json.loads(r.read())


async def loop():
    while True:
        data = await asyncio.to_thread(fetch)
        frame = {{}}
{mapping}
        await socket.send("broadcast_request", frame)
        await asyncio.sleep(2.0)


async def main():
    asyncio.create_task(socket.start())
    await socket.wait_until_ready()
    await loop()


if __name__ == "__main__":
    asyncio.run(main())
'''
