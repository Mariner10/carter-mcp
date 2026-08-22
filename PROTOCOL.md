# CAR-TER Authoring Protocol — Read-back + Truthful Push

The wire contract between the **carter-mcp editor** (this repo, Python) and the
**CAR-TER device** (the Swift app). It defines the read-back and truthful-push
responders the device implements so the editor can preview, verify, and safely
push layouts.

## Transports

Two patterns, both already in production for the existing `list-layouts` / `save-layout` verbs:

- **Broadcast** — `socket.send("broadcast_request", {..., "msg_type": <type>})`. Fire-and-forget,
  no reply, reaches every paired viewer. Used by `layout-update`, `layout-save`. The `msg_type`
  field is the discriminator the device matches on.
- **Routed RPC** — `socket.request("route_msg", {"target_id": <id>, "type": <verb>, "payload": <p>},
  timeout=5.0)`. Point-to-point, returns the device's reply. The device registers a responder via
  `handle(event: <verb>)` returning a `JSONValue?`. **All read-back verbs and the truthful push
  use this pattern.** The routed `type` string IS the MeshSocket event the device handles.

## Device prerequisite (Track-1 / Swift) — ✅ landed

> The routed responders below are installed by `registerRequestHandlers()`, which runs in
> **both** the connected-layout load path **and** the QR live-edit session
> (`startLiveEditSession` → `registerRequestHandlers()`), with each verb tracked in
> `activeListenerEvents` for clean teardown. All verbs — including `get-device-info` — are
> armed and answer during live edit (verified end-to-end; see `e2e-walkthrough/`). An app
> build predating this still times out on these verbs, which the MCP surfaces as a drift hint.

## Routed verbs (MCP → device, with reply)

Each reply SHOULD include `"ok": true`. The MCP treats `null` as a timeout and any object with
`"error"` (and not `ok`) as a relay/routing error.

### `get-current-layout`
Read the layout currently live on the device.

Request payload:
```json
{ "include": "summary" | "full" }   // default "summary"
```
Reply:
```json
{
  "ok": true,
  "isLiveEditSession": true,
  "activeFile": "ups-monitor.json" | null,
  "summary": {                              // always present when a layout is loaded
    "name": "UPS Monitor",
    "accentColor": "#667eea" | null,
    "tabs": [
      { "title": "Main", "icon": "house.fill",
        "controls": [
          { "id": "battery", "type": "gauge", "position": [0,0], "span": [1,1] }
        ] }
    ]
  } | null,                                 // null when no layout is loaded
  "layout": { ...full LayoutConfig... }     // present ONLY when include == "full"
}
```
The `summary` is the **structural echo** — built from the live `LayoutConfig` AFTER it was applied,
so it reflects what actually rendered (a control dropped on decode does not appear).

### `get-layout`  (saved-layout readback)
Read the full JSON of any layout **saved on the device**, active or not — the pull half of the
phone-editor handoff (`save-layout` is the push half). Match by `file` (from `list-layouts`)
or display `name`; passing both matches either.

Request payload:
```json
{ "file": "my-layout.json" }   // or { "name": "My Layout" }
```
Reply:
```json
{ "ok": true, "file": "my-layout.json", "layout": { ...raw layout JSON... } }
```
`layout` is the **raw on-disk dict** (not a re-encoded `LayoutConfig`), so fields the app
doesn't model survive a pull→wire→push round trip. Errors: `{ "ok": false, "error": "no such
layout" | "pass file or name" | "layout file unreadable" }`. Additive verb — an older app
simply never responds (the MCP reports the timeout).

### `get-control-state`
Read current control values.

Request payload:
```json
null                          // all controls
{ "ids": ["battery", "load"] } // filtered
```
Reply:
```json
{ "ok": true, "values": { "battery": 82, "load": 0.4, "status-light": "green" } }
```
`values` is the device's `controlValues` map serialized to JSON.

### `set-control-state`  (drive any control by id)
The dual of `get-control-state`, and the **only** wire path to a control that has no
`sync` binding — which is what makes a demo-style layout (no `connection`, no `sync`)
drivable at all. Triggers/feeds describe the wire contract; this addresses the device.

Request payload:
```json
{ "values": { "battery": 82, "status-light": "green", "trend": [1, 2, 3] } }
```
Reply:
```json
{ "ok": true, "applied": ["battery", "status-light"], "skipped": ["trend"] }
```
Each value is routed by the target control's **declared type**, exactly the way a real
server frame is routed: scalars into `controlValues`; an array of numbers appends to a
`sparkline`; an array of objects fills a `list`; a value for a `logConsole` appends a
line; an object/array for a scalar control (graph, chart, board) is delivered as the JSON
string that control parses.

An id the layout doesn't contain — or a value shape the control can't use — is
**skipped, not an error**: a partially-unknown push still replies `ok: true`. The
`applied`/`skipped` split is truthful, decided by the same routing the write itself
uses, so an id the device is about to ignore is never reported as applied.

These writes are **wire-originated**: they do not emit a Studio Mirror `value` event
(see below), because echoing them would send a value straight back to whoever pushed it.

### `get-connection-status`
Read the device's relay connection state.

Request payload: `null`

Reply:
```json
{
  "ok": true,
  "connected": true,
  "phase": "connected" | "connecting" | "failed" | "idle",
  "channel": "editor-abc",
  "role": "viewer",
  "account": "acct-1" | null,     // from the relay token's acct claim
  "listening": ["broadcast", "list-layouts", "get-current-layout", ...]  // activeListenerEvents
}
```

### `get-device-info`  (version / drift detection)
Report which CAR-TER app build the device is running so the editor can detect when
the phone's installed app has drifted from the **published control definitions**
(the website catalog) or the **authoring kit** (carterkit). The MCP `check_sources`
tool reconciles all three; this verb provides the device leg.

Request payload: `null`

Reply:
```json
{
  "ok": true,
  "appVersion": "1.3.0",            // CFBundleShortVersionString
  "build": "57",                    // CFBundleVersion
  "protocolVersion": 1,             // layout/wire protocol the app speaks
  "catalogFingerprint": "sha256:…", // OPTIONAL — hash of the ControlDocs the app bundles
  "model": "iPhone15,2",            // OPTIONAL
  "osVersion": "18.5"               // OPTIONAL
}
```
All fields except `ok` are optional and the MCP tolerates their absence — an older
app that doesn't implement this verb simply times out, which the MCP reports as
"update the app to enable drift detection." If the app already carries the
`catalogFingerprint` of its bundled ControlDocs, the MCP compares it to the
website catalog's `manifest.fingerprint` to tell whether the installed app matches
the published definitions exactly. For convenience, newer apps MAY also fold these
same fields into the `get-connection-status` reply; the MCP reads either source.

### `apply-layout`  (truthful push)
Apply a layout and report exactly what rendered. Replaces blind broadcast push when a single
device is resolvable.

Request payload: the full layout JSON **without** any `msg_type` framing (routed, not broadcast).

Reply — success:
```json
{ "ok": true, "rendered": { ...same shape as `summary` above... } }
```
Reply — device rejected (decode/apply failed):
```json
{ "ok": false, "error": "missing required field: tabs" }
```
The device MUST reply `ok:false` with an `error` when it cannot decode/apply the layout, rather
than rendering nothing and letting the editor assume success.

## Broadcast verbs (unchanged, MCP → all viewers)

Kept as-is for the multi-viewer / demo path. Used by `push_layout` only when **no** single device
is resolvable.

- `layout-update` — `broadcast_request` with `"msg_type": "layout-update"` + the layout fields.
- `layout-save`   — `broadcast_request` with `"msg_type": "layout-save"`.

## MCP push selection rule

`push_layout` resolves the paired device id, then:
- **single device resolved** → routed `apply-layout` (truthful, returns the rendered echo);
- **no device resolved** → broadcast `layout-update` (no render confirmation).

## Control-edit handoff ("shape it on glass")

Lets the USER customize a control by hand on the phone, then the MCP reads it back.
The device side already exists (`AppState+Broadcast.swift`, `ControlConfiguratorView`,
`AppState+Actions.swift sendControlEditResponse`); the MCP side is the `customize_on_phone`
tool + a `control-edit-response` listener (`carter_mcp/tools/device.py` / `session.py`). Both legs are broadcast frames on
the channel; only consumed while the device is in a live-edit session.

`control-edit-request` (MCP → phone, `broadcast_request`):
```json
{ "msg_type": "control-edit-request",
  "control": { "type": "gauge", "id": "battery", "...seed props...": "..." } }
```
The phone opens the configurator (live preview + doc-driven field form) seeded from the
control's props. The user edits and taps **"Send to Editor"**.

`control-edit-response` (phone → MCP, emit on event `control-edit-response`):
```json
{ "msg_type": "control-edit-response",
  "control": { "...all edited props...": "...", "type": "gauge", "id": "battery" } }
```
The MCP receives it via `@socket.on("control-edit-response")` (the device emits it with
plain `send`, which the relay fans out to channel peers — the same pattern chat uses).

## Studio Mirror (device → editor broadcasts)

The other direction of the live-edit channel: while a Studio Session is active the phone
**narrates what the user is doing** so an editor can mirror it. Consumed today by
`carterkit explore` (the Device Mirror panel); any channel peer may listen.

The premise is the session's connection override: during a session the **studio socket is
authoritative and overrides every layout-level connection**. Layouts the user opens register
their sync/action/read-back wiring on the studio socket and their own `connection` block is
never dialed — so the user can navigate the app normally and stay attached to the editor.
These events are how the editor finds out where they went. (Device side:
`CAR-TER/App/AppState+StudioMirror.swift`.)

Every frame is a broadcast — `broadcast_request` with `"msg_type": "studio.event"` — carrying
a **flat** `event` discriminator alongside its fields:

```json
{ "msg_type": "studio.event", "event": "tab", "tab": "Power", "title": "Power", "index": 0 }
```

| `event` | Fields | Emitted when |
|---|---|---|
| `hello` | `device` (string), `appVersion` (string), `layout` (string \| null — layout name) | the studio socket connects, and on every broadcast-listener re-arm |
| `layout` | `layout` (string — name), `layoutId` (string \| null — file name), `tabs` (array of `{id, title}`), `controls` (int — control count), `tab` (string — initially-selected tab id) | a layout finishes loading during the session |
| `layout-closed` | — | the active layout was torn down **without a replacement** (back to the blank studio screen) |
| `tab` | `tab` (string — tab id), `title` (string), `index` (int) | the user switches tabs |
| `action` | `control` (string — control id), `controlType` (string), `payload` (object — the substituted action payload as sent) | any control action fires |
| `value` | `control` (string), `value` (scalar/JSON) | the user edits a control's value |
| `bye` | — | the session ends (`stopLiveEditSession`) |

No timestamps ride the wire — a consumer stamps arrival itself (the explorer's SSE layer
adds `ts`). A tab id is the tab's **title** (the app's `TabDefinition.id == title`).

### `hello` + `layout` are a repeating announcement, not a handshake

They are emitted together, and emitted **again** on every broadcast-listener arm/re-arm *and*
every confirmed socket connect — including MeshSocket's own transport auto-reconnects.
Consumers **MUST** be idempotent about them and **MUST NOT** treat the first `hello` as a
once-per-session event.

This is deliberate. A frame emitted while the socket is still dialing is dropped (the device
only broadcasts on a connected socket), and `connect()` returning does not mean the socket has
identified — so an announcement made in that window is silently lost. Re-announcing on the
*confirmed* connect is what guarantees the editor's mirror fills in, rather than staying blank
until the user happens to navigate.

A consumer that attached late (or restarted) and has heard nothing yet should not wait: the
same facts are available by request/response — `get-current-layout` for the layout, its tabs
and control count, `get-device-info` for `appVersion` — and the roster for the device name.
Anything derived that way is an inference and **MUST** yield to a real `studio.event` when one
arrives. (`carterkit`'s explorer does exactly this in `Explorer.prime_mirror`.)

### `value` is user-originated only

A `value` event means **a person touched that control** — a slider drag ending, a toggle, a
text commit. Values arriving from the wire (a server's sync push) are never echoed: doing so
would feed a value straight back to the sender that pushed it. Consecutive edits to one control
are coalesced to ≥100 ms, so a drag reports a first and a last sample rather than every frame.

`action` is likewise purely additive narration — the control's authored action is dispatched
unchanged and separately, so an editor watching both sees the mirror frame *and* the real
action frame. Deduplicate on `control` + `payload` if you flash a UI on each.

## Out of scope (this contract)

- Visual preview (screenshot / PNG / perceptual hash) — structural echo only.
- A device→editor `layout-ack` broadcast (the routed reply carries the ack).
