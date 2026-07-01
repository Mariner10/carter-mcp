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

## Device prerequisite (Track-1 / Swift)

> The routed responders below are installed by `registerRequestHandlers()`, which today runs
> **only** in the connected-layout load path — never during a QR live-edit session. The Swift
> task MUST call `registerRequestHandlers()` during a live-edit session (and re-arm it on each
> `applyLiveEditLayout`), tracking each verb in `activeListenerEvents` for clean teardown.
> Until that lands, every verb below times out in the MCP flow. This is the gating fix and is
> out of scope for the carter-mcp repo.

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
tool + a `control-edit-response` listener (server.py). Both legs are broadcast frames on
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

## Out of scope (this contract)

- Visual preview (screenshot / PNG / perceptual hash) — structural echo only.
- A device→editor `layout-ack` broadcast (the routed reply carries the ack).
