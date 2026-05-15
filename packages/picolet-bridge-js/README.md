# picolet-bridge-js

The JavaScript shim injected into the webview frontend that exposes the IPC
bridge as `window.picolet`.

## Public API

### `window.picolet.invoke(cmd, args) → Promise<unknown>`

Call a Python `@picolet.command` handler and await its result.

```js
const result = await window.picolet.invoke('greet', { name: 'World' });
```

- `cmd` — name of the registered Python command.
- `args` — JSON-serialisable payload (default: `null`).
- Resolves with the Python return value on success.
- Rejects with an `Error` on failure. `err.name` is the Python exception
  type (e.g. `"ValueError"`); `err.message` is the human-readable text.

### `window.picolet.on(event, handler) → () => void`

Subscribe to push events emitted by Python (`picolet.emit` on the Python side).

```js
const unsub = window.picolet.on('progress', (data) => {
    progressBar.value = data.pct;
});
// Later, to remove the handler:
unsub();
```

- `event` — topic name, matching the Python `picolet.emit()` topic.
- `handler` — called with `msg.data` whenever the event arrives.
- Returns an unsubscribe function. Multiple handlers on the same topic are
  all called in registration order.

### `window.picolet.emit(topic, data) → void`

Send a JS-push event to Python (`picolet.on()` subscribers on the Python side).

```js
window.picolet.emit('user-action', { button: 'submit' });
```

- `topic` — event topic name.
- `data` — JSON-serialisable payload (default: `null`).
- Fire-and-forget; no reply.

## Wire format

See [docs/architecture.md §"IPC wire format"](../../docs/architecture.md#ipc-wire-format).

Outbound (JS → Python), request:
```json
{ "id": 1, "cmd": "greet", "args": { "name": "World" } }
```

Outbound (JS → Python), event push:
```json
{ "event": "user-action", "data": { "button": "submit" } }
```

Inbound (Python → JS), reply success:
```json
{ "id": 1, "ok": true, "result": "Hello, World" }
```

Inbound (Python → JS), reply error:
```json
{ "id": 1, "ok": false, "error": { "type": "ValueError", "message": "bad input" } }
```

Inbound (Python → JS), event push:
```json
{ "event": "progress", "data": { "pct": 42 } }
```

## Injection

The bundle is injected by the picolet runtime at
`WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START` (before any `<script>` tags in
the user's HTML execute), satisfying FR-WV-4.

The bundle text is read from `/rom/picolet/picolet-bridge.js` inside the frozen
runtime. `picolet build` copies `dist/picolet-bridge.js` into the romfs staging
directory at `picolet/picolet-bridge.js` for every webview-variant build.

## Build (framework developers)

Node 18+ and npm are required.

```sh
cd packages/picolet-bridge-js
npm install
node build.mjs            # minified production bundle → dist/picolet-bridge.js
node build.mjs --no-minify  # readable output for debugging
```

The compiled `dist/picolet-bridge.js` is committed to the repository. App
developers do not need Node or npm.

## Known limitations (v1)

- No `.d.ts` declaration file for TypeScript app authors. Out of scope for v1.
- `err.stack` in rejected promises is the JS call stack at bridge construction
  time; it does not include a Python traceback.
- If the page navigates while an `invoke()` is outstanding, the old promise is
  silently abandoned (the Python reply is discarded by the new page's bridge).
  The picolet model is a single long-lived page; multi-page navigation is not
  supported.
- `JSON.stringify` semantics apply to `args` and `data`: `undefined` values in
  objects are dropped, `NaN`/`Infinity` become `null`, circular references
  throw. This matches the wire format spec (architecture.md) which specifies
  plain JSON with no carve-outs.
