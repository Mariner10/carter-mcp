# carter-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (e.g.
Claude) **author and push [CAR-TER](https://carterbeaudoin.net/CAR-TER) layouts
to a paired iPhone/iPad in real time.**

CAR-TER turns a phone or tablet into any control surface you can describe — tabs,
grids, and controls (gauges, sliders, joysticks, maps, graphs, live logs, chat,
web views…) rendered as native UI and wired to your server over a WebSocket mesh
([MeshSocket](https://pypi.org/project/meshsocket/)). This server gives a model
the tools to build those layouts *conversationally* and see them appear on the
device as it works.

## What it does

- **Reads CAR-TER's live control documentation** so the model builds against the
  real, current catalog instead of guessing — every control type, its fields,
  and worked examples.
- **Edits incrementally** — a server-held draft buffer (`begin_edit`,
  `add_control`, `move_control`, `add_group`, `preview_buffer`, `push_buffer`…)
  with validation, snapshots/revert, and grid layout.
- **Pushes live to a device** — pair by QR over a zero-config local relay (no
  account, same Wi-Fi) or a gateway, then push/preview layouts and read control
  values back.
- **Wires to your data** — probe a running service's traffic, auto-wire controls
  to it, infer a layout from a schema, generate service/adapter stubs, and lint a
  draft against real frames.
- 58 tools in total, exposed over stdio via `FastMCP` under the name `carter`.

## Install

Requires Python 3.11+.

```bash
pip install -r requirements.txt
```

This pulls [`carterkit`](https://pypi.org/project/carterkit/) (the layout-authoring
engine — catalog, buffer, grid, validation, codegen, theming), which brings
`meshsocket` transitively, plus the `mcp` SDK and `qrcode`.

## Run it from an MCP client

The server speaks MCP over **stdio**. Point your client at `server.py`:

```jsonc
// e.g. Claude Desktop's claude_desktop_config.json, or a Claude Code MCP entry
{
  "mcpServers": {
    "carter": {
      "command": "python",
      "args": ["/absolute/path/to/carter-mcp/server.py"]
    }
  }
}
```

Then ask the assistant to build you a panel — it will read the control docs, draft
a layout, show you a QR to pair your device, and push the layout live.

To run it directly for a smoke test:

```bash
python server.py   # serves MCP on stdio
```

## Where the knowledge comes from

The MCP is a thin tool layer over the *current* truth, not a vendored copy of it
(see [`sources.py`](sources.py)):

- **Control definitions** (the catalog + doc prose) are fetched from the website
  — `carterbeaudoin.net/CAR-TER/catalog.json` — cached on disk with a TTL.
- **Authoring demos** (example snippets + the builder engine) come from the
  installed `carterkit`; it also checks PyPI so it can tell you to upgrade a stale
  kit.

The `check_sources` tool reports drift between the website, the installed
carterkit, and a paired device so mismatches are visible rather than silent.

## Configuration

All optional — the defaults target the zero-config local relay:

| Env var | Purpose |
|---|---|
| `CARTER_RELAY_URL` | Gateway ws/wss URL for `target='relay'` connects. |
| `CARTER_MESH_TOKEN` | Gateway auth token (else auto-minted via a validator). |
| `CARTER_VALIDATOR_URL` | Dev validator base URL used to auto-mint a token. |
| `CARTER_LOCAL_RELAY_PORT` | Port for the in-process local relay (default 8765). |

## Tests

```bash
python -m pytest -q
```

## Related

- [`carterkit`](https://pypi.org/project/carterkit/) — the Python layout library + client this depends on.
- [`meshsocket`](https://pypi.org/project/meshsocket/) — the underlying WebSocket mesh transport.
- [CAR-TER docs](https://carterbeaudoin.net/CAR-TER) — the control reference and integration guide.
- [`PROTOCOL.md`](PROTOCOL.md) — the read-back / truthful-push wire contract with the device.

## Notes

- The sample-layout tools read layouts bundled beside the CAR-TER app repo in the
  original workspace; in a standalone checkout that set is simply empty — build
  layouts from the catalog and examples instead.
