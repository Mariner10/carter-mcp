"""carter-mcp — an MCP server for authoring CAR-TER layouts conversationally.

A thin, LOCAL tool layer that gives an LLM access to CAR-TER's live control
documentation and the ability to push layout updates to a paired iPhone/iPad over
MeshSocket. It does not vendor the control vocabulary — it pulls it from the
current sources at call time (see `sources`).

Package map — one module per concern:

  app         The FastMCP instance, its session instructions, and main().
  state       All mutable per-session state (socket, working buffer, snapshots…).
  mesh        Connection runtime: local relay, QR, token mint, routed RPC.
  protocol    Pure wire-protocol builders/formatters (testable without a device).
  content     Resolved docs/catalog content + 2-D default spans.
  sources     Where definitions (website) and demos (carterkit) come from; drift.
  paths       Workspace-relative paths (sample layouts, repo ControlDocs).
  tools/      The MCP tools, grouped by surface:
                docs        browse the control catalog, docs, examples, samples
                buffer      the incremental-editing working buffer
                session     connect / QR pairing / disconnect
                device      push, save, read-back, on-device files, drift check
                wiring      probe / simulate / autowire / lint / scenario / chat
                generators  infer layouts, tune gauges, themes, backend codegen

  autowire, chat, meshgraph, probe, qa, simulate
              Pure helper libraries backing the wiring/QA tools.

Entry points: `python server.py` (repo root, what MCP registrations run) or
`python -m carter_mcp` — both serve MCP over stdio.
"""

__version__ = "0.6.0"
