# carter-mcp — "Insane AI authoring" ideas & capability map

Running brainstorm for beefing up the MCP server into a deeply interactive,
human-in-the-loop layout authoring tool. Living doc — append freely.

Status legend: 🟢 feasible today (no device changes) · 🟡 small device verb needed
· 🔴 larger device work · ⭐ headline / spine.

## SHIPPED (2026-06-20)

All editor-side ideas below are built, wired as MCP tools, and unit-tested
(120 tests). New modules: `catalog.py`, `grid.py`, `buffer.py`, `validate.py`,
`infer.py`, `tune.py`, `theming.py`, `probe.py`, `simulate.py`, `autowire.py`,
`codegen.py`, `meshgraph.py`, `qa.py`, `chat.py`. Idea → tool map:

- Catalog & examples → `get_control_catalog`, `list_control_examples`, `get_control_example`
- Incremental buffer → `begin_edit`, `add_control`, `update_control`, `remove_control`,
  `move_control`, `add_tab`, `add_group`, `insert_example`, `preview_buffer`,
  `push_buffer`, `save_buffer`, `show_grid`, `discard_buffer`
- Validation → `validate_layout`, `validate_buffer`
- Snapshot/revert → `snapshot_buffer`, `list_snapshots`, `revert_buffer`
- Handoff round-trip → `customize_on_phone` (+ control-edit-response listener)
- Probe / auto-wire / live-data lint → `probe_service`, `autowire_buffer`, `lint_against_traffic`
- Puppeteer / simulate → `simulate`, `set_control_value`
- Schema-to-UI inference → `infer_layout`
- Auto-tune → `autotune_gauge`
- Theming → `generate_theme`, `list_theme_vibes`
- Service stub / adapter → `generate_service`, `generate_adapter`
- Live mesh graph → `show_mesh_graph`
- QA over the mesh → `run_scenario`
- Chat agent → `say_in_chat`, `read_chat`

Still open (need device-side work): 🟡 spotlight/highlight-control verb, 🔴 full
on-device drag layout editor, 🟡 serve docs to phone, photo→layout (needs vision input).

---

## Capability substrate (what the app already exposes)

What the MCP has to work with, with evidence:

- **~29 control types**, each with **machine-readable doc frontmatter** (`fields`,
  `themeFields`, `defaultSpan`, enum `values`, defaults) + `## Examples` JSON snippets.
  Both the MCP (`server.py`) and the app (`ControlDocLoader.swift`) parse the same `.md`.
- **Routed RPC verbs** (point-to-point, returns a reply) — `AppState+ReadbackResponders.swift`,
  `AppState+Actions.swift`:
  - `get-current-layout` (summary | full), `get-control-state` (live values),
    `get-connection-status`, `apply-layout` (truthful push w/ rendered echo),
    `list-layouts`, `save-layout`.
- **Broadcast verbs** (fan-out emit) — `AppState+Broadcast.swift`:
  - MCP→phone: `layout-update`, `layout-save`, **`control-edit-request`**.
  - phone→MCP: **`control-edit-response`** (← the return path we never wired).
- **On-device authoring surfaces**: `ControlConfiguratorView` (live preview + field form +
  "Send to Editor"), `FieldEditorView` (doc-driven), `LiveControlPreview`, `SFSymbolPicker`.
- **Live-edit session model**: QR pair → blank canvas, re-arms responders on every push
  (`AppState+LayoutLifecycle.swift:195`, `applyLiveEditLayout`).
- **The MCP is a full MeshSocket peer** (`can_monitor=True`) — it can join the user's real
  service channel and listen to live traffic (`socketCore.py`: `on/send/emit/request`).
- Live data plumbing on device: `controlValues`, sync registrations, sparkline/list/log
  buffers, dynamic groups/tabs, poll groups, group pulses, theme system, alerts/APNs.

### Wire contracts discovered (for the handoff round-trip)

`control-edit-request` (MCP → phone, broadcast; only consumed during a live-edit session):
```json
{ "msg_type": "control-edit-request",
  "control": { "type": "gauge", "id": "battery", "...seed props...": "..." } }
```
→ phone opens the configurator, form pre-filled from props (minus type/id).

`control-edit-response` (phone → MCP, emit on event `control-edit-response`, on "Send to Editor"):
```json
{ "msg_type": "control-edit-response",
  "control": { "...all edited props...": "...", "type": "gauge", "id": "battery" } }
```
MCP captures this via `@socket.on("control-edit-response")`.

---

## Tier 1 — the spine ("shape it on glass, wire it on the model")

- ⭐🟢 **On-phone configurator round-trip.** `customize_on_phone(control_json, timeout)`:
  send `control-edit-request`, await `control-edit-response`, return the user-shaped control.
  Device side already done; MCP needs the send tool + a response listener that resolves a
  future. *This is the user's core ask and is nearly free.*
- ⭐🟢 **Service probe / traffic sniff.** `probe_service(seconds)`: MCP joins the user's real
  service channel and listens, then reports discovered events, payload schemas, and candidate
  `valuePath`s. Turns "what do I bind to?" from a guess into a pick-list. No device work.
- ⭐🟢 **Auto-wire from observed traffic.** After the human shapes a control's shell, fill in
  `action`/`sync` by matching control intent to fields seen in the probe, then push it back
  live. Closes the loop: human draws the knob, MCP wires it to the actual data.
- ⭐🟢 **Incremental working buffer.** Server-held draft + surgical ops (`add_control`,
  `update_control(id, patch)`, `remove_control`, `move_control`, `add_tab`, `add_group`,
  `preview_buffer`, `push_buffer`/`save_buffer`). Pushes a *full* layout under the hood
  (device already accepts it) so the LLM never re-emits 800 lines. `begin_edit` can seed from
  blank, a sample, or the **live device** (read-modify-write).

## Tier 2 — high-value adds

- 🟢 **Control capability catalog.** One `get_control_catalog()` returning a compact JSON
  manifest of every control's fields/enums/defaults/spans parsed from frontmatter — so the
  LLM authors from structured data instead of reading 44 markdown files.
- 🟢 **Example library + insert.** `list_control_examples(id)` / `get_control_example(id,name)`
  (parse the `## Examples` `### Title` + ```json``` blocks) and `insert_example(...)` to drop a
  snippet into the buffer with a fresh id + free grid slot.
- 🟢 **Seed the configurator from an example.** Fuse the two: open a doc example *on the phone*
  for the user to tweak ("pull up the battery-gauge example for you to customize").
- 🟢 **Simulate telemetry (MCP-as-service).** `simulate(event, payload)` / scripted
  `play_demo_data`: MCP emits fake data on the channel so gauges move, sparklines fill, maps
  animate — the user sees the layout *alive* before their backend exists. Device sync already
  listens on `broadcast`/events.
- 🟢 **Validation / lint from frontmatter.** `validate_buffer`: duplicate ids, unknown control
  types, unknown/missing fields per type, bad enum values, grid overlaps/out-of-bounds —
  before pushing.
- 🟢 **Live-data lint (the "no data" killer).** Cross-check a layout's sync `valuePath`s against
  probe-observed traffic; warn `gauge listens for "cpu" but server only emits "cpu_temp"`.
  Targets the #1 real-world failure (wrong path/namespace → silent blank control).
- 🟢 **Grid intelligence.** `show_grid(tab)` ASCII occupancy map + auto-placement honoring
  `defaultSpan` + collision detection.

## Tier 3 — stretch / "insane"

- 🟢 **Variant shoot-out on glass.** Push 2–3 candidate controls/layouts; user taps the one
  they like; MCP reads the choice and adopts it. Conversational A/B, but tactile.
- 🟢 **Theme studio on glass.** The field form already includes `themeFields`; hand the whole
  theme to the phone, let the user tune accent/corner radius/etc, read it back, apply
  layout-wide.
- 🟢 **Verify wiring.** After wiring, drive the control programmatically and confirm via
  `get-control-state` that values actually moved — "wired" means *proven*.
- 🟢 **Live value mirror / narrate.** Poll `get-control-state` + `get-current-layout` so the LLM
  can describe, in words, exactly what's on the user's screen right now.
- 🟢 **Snapshot / diff / revert.** Server-held layout history so the human can experiment
  fearlessly and the MCP can roll back.
- 🟢 **Photo/screenshot → starter layout.** User sends an image of a physical remote / app;
  LLM proposes a CAR-TER shell, pushes it to the phone as the *seed* for the handoff flow.
- 🟢 **Accessibility audit.** Read back a layout and audit against `ControlAccessibility.swift`
  (labels present, contrast, hit targets).
- 🟡 **Spotlight / follow cursor.** Highlight a control on the phone as it's discussed (needs a
  small `highlight-control` device verb).
- 🔴 **Full on-device layout co-editing.** Drag/resize/add controls by hand on the phone, MCP
  reads the changed layout back and continues. Needs an on-device layout (not just per-control)
  drag editor — only the per-control configurator exists today.

## Tier 4 — further ideas (batch 2)

- 🟢 **Puppeteer the live UI (demo-by-doing).** MCP emits values on the channel so the user's
  own controls move — slider slides, gauge sweeps, map animates. Authoring as live demo.
- 🟢 **Generate the matching MeshSocket service stub.** From a layout's `action`/`sync` events,
  emit a runnable Python/Node server skeleton that speaks to *that* layout; keep them in
  lockstep as the layout changes. Closes the backend half of the system.
- 🟢 **Paste your API → layout (schema-to-UI inference).** From a real JSON payload (pasted or
  probe-captured), infer controls (number→gauge, bool→toggle, lat/lng→map, array→sparkline,
  string→label) + correct nested `valuePath`s. Instant first draft from real data.
- 🟢 **Record & replay real traffic into the offline demo.** Capture a window of real telemetry
  via the probe, save as a `DemoDataProvider` dataset, replay it — perfect offline demos.
- 🟡 **Alert-rule authoring + test-fire.** Use the alerts/APNs stack (`AlertRegistrar`,
  `alertTester`, layout `alerts`) to author rules ("battery < 20%"), wire them, and fire a test.
- 🟢 **Entitlement-aware authoring.** Read unlocked tier (`EntitlementStore`/`ProFeatures`) and
  author within it, or flag "needs Pro".
- 🟡 **Serve docs to the phone.** Answer the app's `requestMarkdownContent` graph-node fetches
  as the on-channel service — "pull up the gauge doc on your phone" while explaining in chat.
- 🟢 **Configurator-confirmed NL patches.** "Make it red and bigger" → compute prop diff → open
  the configurator pre-filled with the change → user confirms/tweaks on glass → read back.
- 🟢 **Auto-pack / aesthetic placement.** Bin-pack a set of controls into a balanced grid with
  span awareness, instead of hand-assigning every `position`.
- 🟢 **Multi-viewer authoring.** Push to several paired devices at once (author on iPad, preview
  on iPhone) via the existing broadcast path.

## Tier 5 — new avenues (batch 3, different dimensions)

### A. The LLM lives inside the app (chat as an agent surface)
Grounded: `ChatManager` (`chat_message`/`chat_typing`/`chat_reaction`, listens on `broadcast`),
routed companion chat via `route_msg_noreply` to a peer by name (`ChatManager.swift:84`).
- 🟢 MCP joins a layout's chat control as a peer and answers live, in-app.
- 🟢 **Conversational control:** chat command → MCP interprets → emits values that move controls.
  The LLM as the *backend brain*, not just the editor.
- 🟢 Post AI status as chat system messages (`systemMessageEvents`).

### B. Connect CAR-TER to the real world (adapter generation)
Grounded: actions/sync event model + MeshSocket Python lib.
- 🟢 Generate a bridge from a real system → MeshSocket (Home Assistant, Hue, MQTT, Shelly, OBS,
  REST) so a layout actually controls hardware.
- 🟢 "Describe your device" → adapter + matched layout + wiring, as one set. Useful day one.

### C. Auto-tune from live data
Grounded: probe + gauge `segments`/`min`/`max` + `valuePath` + sparkline buffers.
- 🟢 Set gauge ranges + segment color zones from observed percentiles.
- 🟢 Infer units/format (°C, %, $, ms) and the right control type per field shape.
- 🟢 Anomaly detection → suggested alert rule.

### D. Generative / brand theming
Grounded: `ThemeConfig`/`ResolvedTheme`/`GlassTheme`, per-control theme overrides, configurator
theme fields, `BrandAssets/` (icons).
- 🟢 "Vibe" → full theme (HUD / synthwave / Apple-clean).
- 🟢 Logo / brand colors → matched accent + glass tint.
- 🟢 Theme shoot-out on glass.

### E. Live mesh graph & node-graph authoring
Grounded: `CARGraph` + `GraphConfig` (physics, particles, glow, node-tap→action), MCP `get_nodes`.
- 🟢 Push a **live graph of the user's actual MeshSocket network** — "show me my mesh."
- 🟢 Author the data flow as a graph (nodes = events/services, edges = bindings; tap → doc/wiring).

### F. QA / testing over the mesh
Grounded: emit values + read `controlValues` + `apply-layout` rendered echo.
- 🟢 Scripted scenarios: emit a sequence, assert controls reach expected states (no XCUITest).
- 🟢 Soak/fuzz: blast layouts / malformed frames to surface decode failures.
- 🟢 Regression: snapshot rendered echoes and diff across changes.

### G. Misc avenues (noted, unexpanded)
- 🟢 **Self-improving docs:** harvest good real layouts → write new `## Examples` into `ControlDocs/`.
- 🟢 **Animation/haptic choreography:** pulse + haptic + color-flash sequences tied to events
  (`CARPulseEffect`, group pulses, animations, haptics).
- 🟡 **Bridge the three authoring paths:** web editor (`CAR-TER/editor`) ↔ MCP buffer ↔ hand JSON.

---

## Open questions / notes

- `control-edit-response` is sent via plain `networkService.send` (channel emit), not a routed
  reply. Confirm the relay fans a peer emit out to the editor; if not, route it instead.
- `control-edit-request` is only handled while `isLiveEditSession == true` — fine, since the
  MCP `connect()` flow puts the phone into a live-edit session via QR.
</content>
