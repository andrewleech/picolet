# PH08 — picolet-bridge-js

## Plan

### Goal (restated)

Build the JavaScript shim that gives user frontend code a clean,
promise-based API to the IPC layer established by PH06 and wired
through the WebKit postMessage bridge in PH07. When this phase
completes, any HTML page loaded in the picolet webview runtime can call
`window.picolet.invoke(cmd, args)` to await a Python result and
`window.picolet.on(event, handler)` to subscribe to Python-push events,
without writing any transport plumbing themselves.

The phase closes the following requirements from
[docs/v1-spec.md](../v1-spec.md):

| Spec id | Requirement |
|---|---|
| FR-WV-4 | The `picolet-bridge-js` script is injected before any user frontend JS runs. |
| FR-WV-5 | The bridge exposes `window.picolet.invoke(cmd, args) → Promise<result>` and `window.picolet.on(event, handler) → unsubscribe`. |

FR-WV-5 also covers `window.picolet.emit(topic, data)` (JS-to-Python
event push), which is specified implicitly via FR-IPC-3 and
architecture.md §"IPC wire format" §Event. The bridge must handle both
directions of the event channel: receiving Python-push events (the
`on`/handler path) and sending JS-push events (the `emit` path).

PH08 is **JS-bundle only**. It does not add new Python code to the
runtime. The Python side already calls
`webkit_web_view_evaluate_javascript(view, "window.__picolet_recv(" +
json + ")")` (PH07 `WebviewTransport.send`). PH08 replaces the
no-op `window.__picolet_recv` stub injected by PH07 with the real
receiver that resolves pending promises and dispatches events.

### Architectural decisions

#### AD1 — TypeScript with esbuild (not vanilla JS)

**Decision**: author the bridge in TypeScript, compile with esbuild to
a single IIFE bundle.

**Rationale.**

The bridge has enough internal state — a pending-promise `Map` keyed
by integer id, an event-subscriber `Map` keyed by topic string, a
monotonically increasing id counter — that type annotations on the
internal interfaces pay for themselves immediately. A bare
`pendingInvokeMap[id]` is easy to misuse; `Map<number,
PendingInvoke>` where `PendingInvoke = { resolve: (v: unknown) =>
void; reject: (e: Error) => void }` makes the intent explicit at zero
runtime cost. The bundle strips all types; the user sees only the IIFE
output.

esbuild compiles TypeScript natively (no separate `tsc` pass needed
for the build output). The source files carry `.ts` extensions and
inline JSDoc/type syntax to document the wire format shapes. A
declaration file (`.d.ts`) for user apps is explicitly **out of scope
for v1** (v1-spec §"Out of scope for v1") and is not generated.

Vanilla JS was considered. The wire format discriminator logic (does
the parsed object have `"id"+"ok"` vs `"event"` vs `"id"+"cmd"`?) is
subtle enough that accidental property collisions have caused real bugs
in similar bridges. TypeScript's structural type checking catches those
at author time. The esbuild compilation is a one-command step that
runs in CI and produces a checked-in bundle; app developers never
invoke it directly. The added build dependency (Node + esbuild) is
confined to the framework's own CI; it does not reach app developer
machines (see AD4).

#### AD2 — esbuild as the bundler

esbuild is the only build tool needed. It handles TypeScript directly,
produces IIFE output in a single pass, has no runtime JS dependencies
of its own, and its CLI is a single binary. Vite would add a dev-server
and HMR stack that PH08 has no use for. Rollup + tsc would require two
tools. The esbuild binary is installed with `npm install --save-dev
esbuild` inside `packages/picolet-bridge-js/`; the package.json
`devDependencies` entry is the only external dep.

#### AD3 — IIFE bundle shape

The bundle runs in a WebKit browsing context — no CommonJS loader, no
`import()` map, no Node globals. The correct output format is IIFE
(Immediately Invoked Function Expression): a self-contained script that
assigns `window.picolet` and exits. UMD would work too, but the UMD
boilerplate is unnecessary overhead for a browser-only target and the
`define` path would never fire. The esbuild flag is `--format=iife
--global-name=__picolet_iife__` (with the IIFE wrapper assigning
`window.picolet` internally — the global-name wrapper is discarded;
`window.picolet` is the public surface, set from inside the IIFE body).

The compiled output is `packages/picolet-bridge-js/dist/picolet-bridge.js`.
It is a single `.js` text file, suitable for injection verbatim as a
WebKit user script.

#### AD4 — Bundle delivery to the runtime: checked-in dist

**Decision**: build the bundle once (in CI / during framework
development) and check `dist/picolet-bridge.js` into the repository.
The `picolet build` CLI reads the file from its installed package tree
and copies it into every webview-variant romfs as
`picolet-bridge.js`. No Node or npm tooling is needed on app developer
machines.

The three options considered:

| Option | Description | Verdict |
|---|---|---|
| (a) Copy to `packages/picolet-runtime/python/picolet_ui/bridge.js` | Build team copies manually; CLI reads from `picolet_ui` package. | Rejected — splits the canonical artifact across two package trees; the JS source and its compiled form live in different packages. |
| (b) Build at `picolet build` time | Requires Node on app developer machine. | Rejected — violates D2 (pre-built runtime by default, no toolchain requirements beyond Python). |
| **(c) Check `dist/` into `picolet-bridge-js`** | Framework CI builds; app `picolet build` reads from the installed package. | **Selected.** Single canonical location; no Node requirement at app-build time; CI diff reveals unreviewed JS changes. |

The `packages/picolet-bridge-js/dist/` directory is committed. A
`Makefile` (or `build.sh`) inside the package rebuilds it for framework
developers who have Node available. The `picolet build` CLI locates the
file via `importlib.resources` or a relative path from the installed
`picolet-bridge-js` package root — the exact resolver mechanism is an
implementation detail for the developer.

The `picolet build` pipeline already handles webview-variant romfs
assembly (FR-BP-4). PH08's only addition is one extra file copied in:
`picolet-bridge.js` at the romfs root (or a `/picolet/` subdirectory — see
Injection timing below).

#### AD5 — Injection timing: DOCUMENT_START

FR-WV-4 requires the bridge script to be injected "before any user
frontend JS runs". WebKit user scripts support two injection moments:
`WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START` (value `0`) and
`WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END` (value `1`). The bridge
must use `DOCUMENT_START`.

PH07 already calls `webkit_user_script_new(src, 1, 0, 0, 0)` with the
third argument (`0`) selecting `DOCUMENT_START` for the no-op stub.
PH08 **replaces that stub** with the real bridge bundle injected at
`DOCUMENT_START`. The injection site is `picolet_ui/_webview.py`
`Webview.__init__` where the stub was registered; PH08 changes the
`stub_src` string to the full bridge bundle text.

The bundle text is read once at import time (or at `Webview`
construction) from the frozen romfs at `/picolet/picolet-bridge.js` (or
from the python package data path on the developer host). The exact
path resolution is an implementation detail; the constraint is that the
file must be accessible inside the frozen runtime.

Alternatively: the runtime Python code can embed the bundle text
directly as a string literal in `_webview.py`. This avoids a romfs
file-path dependency but makes the file non-diffable in CI. The
**preferred approach** is to read from the romfs file so the bundle is
independently auditable:

```python
# In picolet_ui/_webview.py Webview.__init__:
import os
_bridge_path = "/rom/picolet/picolet-bridge.js"
try:
    with open(_bridge_path) as f:
        bridge_src = f.read()
except OSError:
    bridge_src = ""  # graceful degradation: window.picolet will be undefined
script = _gtk_ffi.webkit_user_script_new(bridge_src, 1, 0, 0, 0)
_gtk_ffi.webkit_user_content_manager_add_script(self._manager, script)
```

The old stub-injection lines in `Webview.__init__` are removed
entirely; the bridge bundle supersedes them (the bridge itself
initialises `window.__picolet_recv` as its own internal handler, so the
PH07 stub guard `if (!window.__picolet_recv)` is harmless but redundant
and should be stripped from the bundle build).

### API contract

The bridge exposes a single global object `window.picolet`. Its full
public surface:

#### `window.picolet.invoke(cmd, args) → Promise<unknown>`

| Parameter | Type | Description |
|---|---|---|
| `cmd` | `string` | Name of the registered Python `@picolet.command` handler. |
| `args` | `object \| null` | JSON-serialisable argument payload. Passed verbatim as the `"args"` field of the wire-format request. If omitted, `null` is sent. |

Returns a `Promise` that:
- Resolves with `result` when Python replies `{"id": N, "ok": true, "result": ...}`.
- Rejects with an `Error` when Python replies `{"id": N, "ok": false, "error": {"type": "...", "message": "..."}}`.

The rejected `Error` object is constructed as:
```js
const err = new Error(msg.error.message);
err.name = msg.error.type;  // e.g. "ValueError"
throw err;
```
User code inspects `err.name` to identify the Python exception type and
`err.message` for the human-readable text. The `err.stack` is the JS
call stack at the point the bridge constructed the error — it does not
contain a Python traceback (that is out of scope for v1).

Outbound wire message produced by `invoke`:
```json
{ "id": 42, "cmd": "greet", "args": { "name": "World" } }
```
The `id` is drawn from the bridge's internal monotonic counter starting
at 1. Each unresolved `invoke` holds a `{ resolve, reject }` closure
in `pendingInvokeMap`.

#### `window.picolet.on(event, handler) → () => void`

| Parameter | Type | Description |
|---|---|---|
| `event` | `string` | Topic name. Matches the `"event"` field of inbound push messages from Python `picolet.emit`. |
| `handler` | `(data: unknown) => void` | Called with `msg.data` whenever Python emits on this topic. Multiple handlers on the same topic are all called, in registration order. |

Returns an **unsubscribe function**. Calling the returned function
removes exactly the registered `handler` from the subscriber list for
`event`. Does nothing if called more than once.

```js
const unsub = window.picolet.on('progress', (data) => {
    progressBar.value = data.pct;
});
// Later:
unsub();
```

Multiple `on` calls for the same topic are additive. The subscriber
list is a `Map<string, Set<Function>>` (or `Map<string, Array<Function>>`
ordered — the developer chooses, `Set` is cleaner for O(1) removal via
`delete`).

#### `window.picolet.emit(topic, data) → void`

| Parameter | Type | Description |
|---|---|---|
| `topic` | `string` | Event topic name. Received by Python `picolet.on(topic, handler)` subscribers. |
| `data` | `unknown` | JSON-serialisable payload. |

Sends the event message immediately:
```json
{ "event": "user-action", "data": { "button": "submit" } }
```

Returns `void` synchronously. There is no reply; `emit` is fire-and-forget
from the JS side.

#### `window.__picolet_recv(jsonString) → void`

**Internal function, not part of the public API.** Called by the Python
runtime (`WebviewTransport.send`) when dispatching a reply or a push
event. The bridge registers this on `window` so the Python side can
reach it via `webkit_web_view_evaluate_javascript`. The function:

1. Parses `jsonString` via `JSON.parse`.
2. If the parsed object has `"id"` and `"ok"`:
   - Look up `pendingInvokeMap.get(msg.id)`.
   - If `ok === true`: call `pending.resolve(msg.result)`.
   - If `ok === false`: construct an `Error` as specified above and call `pending.reject(err)`.
   - Remove the entry from `pendingInvokeMap`.
3. If the parsed object has `"event"`:
   - Look up subscribers in `eventHandlerMap.get(msg.event)`.
   - Call each handler with `msg.data`.
4. Anything else: log a warning to `console.warn` and discard.

### Wire format

The bridge speaks the IPC wire format from
[docs/architecture.md §"IPC wire format"](../architecture.md#ipc-wire-format)
verbatim. No extensions. No envelope fields beyond those specified.

```
Outbound (JS → Python), request:
  { "id": <int>, "cmd": <string>, "args": <any-json> }

Outbound (JS → Python), event push:
  { "event": <string>, "data": <any-json> }

Inbound (Python → JS), reply success:
  { "id": <int>, "ok": true, "result": <any-json> }

Inbound (Python → JS), reply error:
  { "id": <int>, "ok": false, "error": { "type": <string>, "message": <string> } }

Inbound (Python → JS), event push:
  { "event": <string>, "data": <any-json> }
```

The discriminator logic in `__picolet_recv`:

```
has "id" AND has "ok"  →  reply (resolve or reject pending invoke)
has "event"            →  push event (dispatch to on() handlers)
anything else          →  console.warn + discard
```

`args` and `data` are optional by the spec; the bridge sends `null`
when the caller omits them. The bridge does NOT send `"cmd"` in a
reply, and does NOT send `"ok"` in a request — it produces only the
two outbound shapes listed above.

The bridge does **not** validate inbound message shapes beyond reading
`msg.id`, `msg.ok`, `msg.result`, `msg.error`, `msg.event`, `msg.data`
with normal property access. Missing fields yield `undefined`, which is
handled gracefully (a resolve with `undefined` is valid per the spec).

### Injection timing

The bridge script is injected at `WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START`
(value `0` in the WebKitGTK enum), meaning it runs before any
`<script>` tag in the user's HTML is parsed or executed. This satisfies
FR-WV-4.

The injection site is `packages/picolet-runtime/python/picolet_ui/_webview.py`,
`Webview.__init__`, replacing the existing stub injection (the four
lines starting with `stub_src = ...` and ending with
`webkit_user_content_manager_add_script(...)`).

The bridge bundle is read from `/rom/picolet/picolet-bridge.js` inside the
frozen runtime. The `picolet build` CLI copies the file into the romfs
staging directory at `picolet/picolet-bridge.js` for every webview-variant
build. The directory `picolet/` inside the romfs is owned by the
framework (not the user's `[romfs] include` list) and acts as the
framework's reserved namespace inside the user's rom image.

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | esbuild produces a single `dist/picolet-bridge.js` file with no errors. | `cd packages/picolet-bridge-js && node_modules/.bin/esbuild src/index.ts --bundle --format=iife --outfile=dist/picolet-bridge.js` → exit 0, file exists, size > 0. |
| 2 | The bundle is valid JS (no syntax errors). | `node --input-type=module < dist/picolet-bridge.js` or `node -e "require('./dist/picolet-bridge.js')"` → exit 0 (no uncaught exceptions on load). Alternatively, `npx --yes acorn --ecma2019 dist/picolet-bridge.js > /dev/null` → parses clean. |
| 3 | `window.picolet.invoke`, `window.picolet.on`, `window.picolet.emit` are present after the bundle executes. | JS unit test (Node, no DOM): set up a `window` mock, source the bundle, assert all three are functions. `node tests/phase-08/test_api_surface.js` → exit 0. |
| 4 | `invoke` round-trips a request/reply: outbound request has correct wire shape, inbound reply resolves the promise. **FR-WV-5.** | `node tests/phase-08/test_invoke_roundtrip.js` — posts a mock reply `{id:1, ok:true, result:"ok"}` via the mock `__picolet_recv` call; asserts the resolved value is `"ok"` and the pending map is empty after resolution. Exit 0. |
| 5 | `invoke` error path: Python error reply constructs `Error` with correct `name` and `message`. | `node tests/phase-08/test_invoke_error.js` — calls mock `__picolet_recv({id:1, ok:false, error:{type:"ValueError",message:"bad input"}})`, asserts `err.name === "ValueError"` and `err.message === "bad input"`. Exit 0. |
| 6 | `on` / event dispatch: inbound `{event:"progress", data:{pct:42}}` calls registered handler with `{pct:42}`. | `node tests/phase-08/test_event_dispatch.js` — registers a handler, triggers `__picolet_recv` with event message, asserts handler was called with correct data. Exit 0. |
| 7 | `on` returns a working unsubscribe function: after calling it, further events are not delivered to the removed handler. | `node tests/phase-08/test_unsubscribe.js` → exit 0. |
| 8 | `emit` sends the correct wire shape outbound. | `node tests/phase-08/test_emit.js` — intercepts the `window.webkit.messageHandlers.picolet.postMessage` mock, calls `window.picolet.emit("click", {x:10})`, asserts intercepted JSON parses to `{event:"click", data:{x:10}}`. Exit 0. |
| 9 | The bundle is included in the webview-variant romfs produced by `picolet build`. **FR-BP-4.** | `picolet build` against the `tests/phase-07/fixtures/hello-webview-min/` fixture (webview renderer); inspect the romfs image: `picolet-bridge.js` is present at path `picolet/picolet-bridge.js` inside the image. Verification: unpack the romfs and `ls picolet/picolet-bridge.js`. |
| 10 | **FR-WV-4**: the bridge is injected at `DOCUMENT_START` — `window.picolet` is defined when the first user `<script>` tag runs. | Build a fixture `tests/phase-08/fixtures/bridge-inject-order/ui/index.html` whose `<script>` immediately asserts `typeof window.picolet === "object"` and posts a `{event:"ready"}` message if true, `{event:"missing"}` if false. Run the runtime with `xvfb-run -a timeout 5 ...`; assert `PICOLET_WV_BRIDGE_INJECT_OK` appears in stdout. |
| 11 | **FR-WV-5** end-to-end: JS `invoke("greet", {name:"World"})` reaches the Python handler, which returns `"Hello, World"`; JS receives the resolved value and posts it back as a `{event:"result", data:{value:"Hello, World"}}` message. | Same `xvfb-run` driver with a fixture that registers `@picolet.command async def greet(args)` returning `"Hello, " + args["name"]`, and an `index.html` that awaits `invoke` and sends the result back via `window.webkit.messageHandlers.picolet.postMessage`. Python side awaits the event, asserts the payload, prints `PICOLET_WV_INVOKE_OK` and exits 0. |
| 12 | **FR-WV-5** event path: Python `picolet.emit("tick", {"n": 1})` is received by JS `on("tick", handler)` which posts a confirmation postMessage back. | Python side emits, then waits for the echo, asserts it arrived, prints `PICOLET_WV_EVENT_OK` and exits 0. Integration test via `xvfb-run`. |
| 13 | Error propagation end-to-end: Python handler raises `ValueError("bad input")`; JS `invoke().catch(err)` receives `err.name === "ValueError"`, `err.message === "bad input"`. | `xvfb-run` fixture — Python command raises, JS catch block posts `{event:"err", data:{name:err.name, message:err.message}}` back; Python asserts and prints `PICOLET_WV_ERROR_OK`. |
| 14 | Pending-promise map is empty after resolution (no leak on normal round-trip). | `node tests/phase-08/test_invoke_roundtrip.js` asserts `pendingMap.size === 0` after reply received (requires the test to access the map via an exported test hook). |
| 15 | Regression: PH07 gates 2–8 still pass (no regression from `_webview.py` stub removal). | `bash packages/picolet-runtime/tests/phase-07/run.sh` → all gates green. |
| 16 | NFR-2 still holds after adding the bridge bundle to the romfs. | `wc -c target/linux-x64/<app>` ≤ 2 097 152 bytes (excluding the system webview). Print actual size. |

Gates 10–13 close FR-WV-{4,5}. Gate 9 closes FR-BP-4's webview-bundle
path. Gates 1–8 are unit-level gates that verify JS correctness
independently of the runtime. Gates 14–16 are safety/regression gates.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-WV-{4,5}, FR-BP-4, NFR-2. |
| `/home/anl/picolet/docs/v1-plan.md` §PH08 | Phase scope, deliverables, model tiers. |
| `/home/anl/picolet/docs/architecture.md` §"IPC wire format" | Wire format shapes used verbatim by the bridge. |
| `/home/anl/picolet/CLAUDE.md` | Branch / commit / signing / dev-log conventions. |
| `/home/anl/picolet/docs/phases/PHASE_06_picolet-ipc-dispatcher.md` | Dispatcher wire format (request/reply/event), `@picolet.command` registration pattern, error serialisation (`{"type":..., "message":...}`). |
| `/home/anl/picolet/docs/phases/PHASE_07_webview-renderer-linux.md` | Injection site (stub lines in `Webview.__init__`), `__picolet_recv` outbound call shape, `webkit_user_script_new` argument order, `DOCUMENT_START` constant value (`0`), `postMessage` handler name `"picolet"`. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet_ui/_webview.py` | Exact stub injection code to be replaced; `eval_js` call shape; `WebviewTransport.send`'s JS expression `"window.__picolet_recv(" + encoded + ")"`. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet/_transport.py` | Transport duck-type contract (no change in PH08, for context). |
| `/home/anl/picolet/packages/picolet-bridge-js/README.md` | Existing placeholder; confirms the `window.picolet.invoke / .on` API shape and that the package is not yet implemented. |

### Deliverables

1. `packages/picolet-bridge-js/package.json` — Node package manifest declaring esbuild as a `devDependency` and the build script.
2. `packages/picolet-bridge-js/src/index.ts` — TypeScript source of the bridge IIFE.
3. `packages/picolet-bridge-js/build.sh` — one-line build helper: `node_modules/.bin/esbuild src/index.ts --bundle --format=iife --outfile=dist/picolet-bridge.js --minify`.
4. `packages/picolet-bridge-js/dist/picolet-bridge.js` — compiled IIFE bundle, committed to the repo.
5. `packages/picolet-bridge-js/tsconfig.json` — TypeScript configuration (editor tooling only; esbuild does not use it for compilation).
6. `packages/picolet-cli/picolet/build_cmd.py` — modified to copy `dist/picolet-bridge.js` from the installed `picolet-bridge-js` package into the romfs staging directory at `picolet/picolet-bridge.js` for every webview-variant build.
7. `packages/picolet-runtime/python/picolet_ui/_webview.py` — modified: replace stub injection with bridge-bundle injection from `/rom/picolet/picolet-bridge.js`.
8. `tests/phase-08/test_api_surface.js` — JS unit test for gate 3.
9. `tests/phase-08/test_invoke_roundtrip.js` — JS unit test for gates 4 and 14.
10. `tests/phase-08/test_invoke_error.js` — JS unit test for gate 5.
11. `tests/phase-08/test_event_dispatch.js` — JS unit test for gate 6.
12. `tests/phase-08/test_unsubscribe.js` — JS unit test for gate 7.
13. `tests/phase-08/test_emit.js` — JS unit test for gate 8.
14. `tests/phase-08/fixtures/bridge-inject-order/picolet.toml` + `src/main.py` + `ui/index.html` — gate 10 fixture.
15. `tests/phase-08/fixtures/invoke-roundtrip/picolet.toml` + `src/main.py` + `ui/index.html` — gates 11 and 13 fixture.
16. `tests/phase-08/fixtures/event-push/picolet.toml` + `src/main.py` + `ui/index.html` — gate 12 fixture.
17. `tests/phase-08/run.sh` — tester harness: runs JS unit tests (gates 1–8, 14), runs integration tests (gates 9–13, 15–16).

### Sequence the developer follows

All from `/home/anl/picolet` on `dev`.

**1. Log the architectural decisions.**
```
git commit --allow-empty -s \
  -m "[PH08] Decision: TypeScript + esbuild IIFE; dist/ checked in." \
  -m "TypeScript chosen for structural typing of the pending-invoke Map ..."
```
One empty commit per decision (AD1, AD2, AD3, AD4, AD5 can be one commit
if the body covers all five).

**2. Scaffold the npm package.**
```
mkdir -p packages/picolet-bridge-js/src packages/picolet-bridge-js/dist
cd packages/picolet-bridge-js
npm init -y
npm install --save-dev esbuild
```
Edit `package.json` to set `"private": true`, add `"build": "esbuild
src/index.ts --bundle --format=iife --outfile=dist/picolet-bridge.js
--minify"` to `scripts`. Add `node_modules/` to the repo's root
`.gitignore`. Commit `package.json` and `package-lock.json`.

**3. Write `src/index.ts`.**

Internal types:
```ts
type PendingInvoke = {
  resolve: (value: unknown) => void;
  reject:  (error: Error)   => void;
};

type PicoletWireRequest = { id: number; cmd: string; args: unknown };
type PicoletWireEvent   = { event: string; data: unknown };
type PicoletWireReply   = { id: number; ok: boolean; result?: unknown;
                          error?: { type: string; message: string } };
```

Module-level state (inside the IIFE, not exported):
```ts
let _nextId = 1;
const _pending  = new Map<number, PendingInvoke>();
const _handlers = new Map<string, Set<(data: unknown) => void>>();
```

`postMessage` outbound helper:
```ts
function _send(msg: PicoletWireRequest | PicoletWireEvent): void {
  const json = JSON.stringify(msg);
  window.webkit.messageHandlers.picolet.postMessage(json);
}
```

`__picolet_recv` (replace PH07 stub):
```ts
window.__picolet_recv = function(jsonString: string): void {
  let msg: Record<string, unknown>;
  try {
    msg = JSON.parse(jsonString) as Record<string, unknown>;
  } catch (e) {
    console.warn('[picolet] malformed inbound JSON:', e);
    return;
  }
  if (typeof msg.id === 'number' && 'ok' in msg) {
    const pending = _pending.get(msg.id as number);
    if (!pending) {
      console.warn('[picolet] no pending invoke for id', msg.id);
      return;
    }
    _pending.delete(msg.id as number);
    if (msg.ok) {
      pending.resolve(msg.result);
    } else {
      const errInfo = msg.error as { type: string; message: string };
      const err = new Error(errInfo?.message ?? String(msg.error));
      err.name = errInfo?.type ?? 'Error';
      pending.reject(err);
    }
  } else if (typeof msg.event === 'string') {
    const subs = _handlers.get(msg.event);
    if (subs) {
      for (const handler of subs) {
        try { handler(msg.data); }
        catch (e) { console.error('[picolet] on() handler threw:', e); }
      }
    }
  } else {
    console.warn('[picolet] unrecognised inbound message:', msg);
  }
};
```

Public API:
```ts
window.picolet = {
  invoke(cmd: string, args: unknown = null): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = _nextId++;
      _pending.set(id, { resolve, reject });
      _send({ id, cmd, args });
    });
  },

  on(event: string, handler: (data: unknown) => void): () => void {
    if (!_handlers.has(event)) _handlers.set(event, new Set());
    _handlers.get(event)!.add(handler);
    return function unsubscribe() {
      _handlers.get(event)?.delete(handler);
    };
  },

  emit(topic: string, data: unknown = null): void {
    _send({ event: topic, data } as PicoletWireEvent);
  },
};
```

**4. Build the bundle.**
```
cd packages/picolet-bridge-js && npm run build
```
Verify `dist/picolet-bridge.js` is produced with no errors. Check file size
(expect < 5 KB minified for this logic). Commit both `src/index.ts` and
`dist/picolet-bridge.js`.

**5. Write JS unit tests (gates 1–8, 14).**

Each test file under `tests/phase-08/` is a self-contained Node script
(no test framework required; use plain `assert` from the stdlib).
The tests mock `window.webkit.messageHandlers.picolet.postMessage` and
drive `window.__picolet_recv` directly. Example setup:

```js
// tests/phase-08/test_invoke_roundtrip.js
const assert = require('assert');
global.window = { webkit: { messageHandlers: { picolet: { postMessage: () => {} } } } };
require('../../packages/picolet-bridge-js/dist/picolet-bridge.js');

let capturedJson;
window.webkit.messageHandlers.picolet.postMessage = (json) => { capturedJson = json; };

const p = window.picolet.invoke('greet', { name: 'World' });
const req = JSON.parse(capturedJson);
assert.strictEqual(req.cmd, 'greet');
assert.deepStrictEqual(req.args, { name: 'World' });
assert.strictEqual(typeof req.id, 'number');

// Simulate reply from Python.
window.__picolet_recv(JSON.stringify({ id: req.id, ok: true, result: 'Hello, World' }));
p.then((val) => {
  assert.strictEqual(val, 'Hello, World');
  console.log('PASS');
}).catch((e) => { console.error('FAIL', e); process.exit(1); });
```

All JS tests must run with `node <testfile>` on a host that has Node ≥ 18.

**6. Modify `picolet build` to include the bridge bundle.**

In `packages/picolet-cli/picolet/build_cmd.py`, add a step in the
webview-variant romfs assembly path:

```python
# After staging user files, before building the romfs image:
if renderer == "webview":
    import importlib.resources
    bridge_pkg = importlib.resources.files("picolet_bridge_js")
    bridge_js  = bridge_pkg / "dist" / "picolet-bridge.js"
    dest = staging_dir / "picolet" / "picolet-bridge.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bridge_js.read_bytes())
```

The `picolet-bridge-js` package must be on `sys.path` when `picolet build`
runs. Add it to `packages/picolet-cli/pyproject.toml` as a dependency
(path dependency for local development; tagged release for distribution).

The module name used in `importlib.resources` must match the Python
package name declared in `packages/picolet-bridge-js/pyproject.toml` (or
equivalent). If the JS package does not have a `pyproject.toml` yet,
create a minimal one so pip/uv can install it as a Python package whose
`package_data` includes `dist/picolet-bridge.js`.

**7. Modify `_webview.py` to inject the bridge bundle.**

Replace the four stub-injection lines in `Webview.__init__` with the
file-read + inject sequence from the Injection timing section above.
Remove the `if (!window.__picolet_recv)` guard comment (the bridge sets it
unconditionally). Keep the `WEBKIT_USER_CONTENT_INJECT_TOP_FRAME=1,
WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START=0` argument order unchanged.

**8. Write integration test fixtures (gates 10–13).**

Each fixture is a minimal `picolet.toml` + `src/main.py` + `ui/index.html`
under `tests/phase-08/fixtures/<name>/`. The `main.py` registers the
command or emits the event needed for the gate and waits for the
confirmation back from JS (via `WebviewTransport.recv`). Each fixture
has a matching entry in `tests/phase-08/run.sh`.

**9. Run the full gate suite.**
```
bash tests/phase-08/run.sh
```
Expect all gates green. Fix any failures before committing the tester
verification section.

**10. Regression check.**
```
bash packages/picolet-runtime/tests/phase-07/run.sh
```
Confirm PH07's gates still pass (especially gate 8, the callback probe,
and gate 5, the sanity test, since `_webview.py` was modified).

**11. Document.**
Update `packages/picolet-bridge-js/README.md` to replace the "Not yet
implemented" note with the actual API reference, wire format, and build
instructions for framework contributors.

### Foreseeable risks

**Risk 1: race between DOCUMENT_START injection and user JS that runs at
parse time.**

DOCUMENT_START fires after the document's DOM is created but before any
`<script>` tags in the user's HTML are executed. WebKit's specification
for `UserScriptInjectionTime` guarantees this ordering. However, inline
scripts as attribute values (e.g. `<img onerror="picolet.invoke(...)">`)
could theoretically fire before `DOCUMENT_START` on edge-case parser
implementations.

Mitigation: the bridge's injection is a user-content-manager script,
which WebKit's documentation states runs at `DOCUMENT_START` reliably.
Gate 10 explicitly verifies the ordering with a page whose first
`<script>` block asserts `window.picolet` is defined. If it fails, the
contingency is to also inject a `<meta>` redirect hack or switch to
injecting the bridge via `evaluateJavaScript` on the `load-started`
signal — but this should not be necessary given the DOCUMENT_START
guarantee.

**Risk 2: JSON edge cases in user-supplied `args` or `data`.**

`JSON.stringify` silently drops `undefined` values in object properties,
converts `NaN`/`Infinity` to `null`, and throws on circular references.
These are JavaScript's standard `JSON.stringify` semantics, not a bridge
defect, but they can surprise users.

Mitigation: document the behaviour in the bridge's README. Do not add
special-case handling in v1; the wire format (architecture.md) already
specifies "JSON" without carve-outs. Circular-reference detection is an
antipattern (it hides bugs); `JSON.stringify` throwing is the correct
behaviour — the error propagates to the caller.

**Risk 3: pending-promise leak if the page navigates or closes mid-invoke.**

If the user navigates the webview to a new page while `invoke` is
outstanding, the new page will not have the pending `Map` entries from
the old page's bridge instance. The Python dispatcher will attempt to
send the reply, `evaluate_javascript("window.__picolet_recv(...)")` will
call whatever `__picolet_recv` the new page has (possibly none, possibly
the new page's bridge). The old promise is GC'd with the old page's JS
heap — no user-visible leak at the Python level, but the Python reply
message is silently discarded.

Mitigation: for v1, this is acceptable. The picolet model is a single
long-lived page (not a multi-page navigation). Document the limitation.
A future phase can add a `"cancel"` wire message type so the Python
dispatcher can abandon the outstanding handler. Out of scope for PH08.

**Risk 4: bundle size impact on NFR-2.**

The bridge bundle adds bytes to the romfs, which is appended to the
runtime binary. Even minified, `picolet-bridge.js` contributes to the
webview-variant binary size checked against the 2 MB NFR-2 budget.
Empirically, the bridge's logic — a `Map`, a counter, four functions —
minifies to under 1 KB. This is negligible against the 2 MB budget.
Gate 16 measures the final binary size to confirm.

**Risk 5: `importlib.resources` path resolution for the bridge bundle.**

`importlib.resources.files()` requires the package to be installed as a
proper Python package with correct `package_data` declarations. A bare
directory on `sys.path` without a `pyproject.toml` will fail at
runtime. The developer must ensure `packages/picolet-bridge-js/` has a
`pyproject.toml` (or `setup.cfg`) that declares `dist/picolet-bridge.js`
in `package_data`. If this is fiddly, the fallback is to resolve the
path relative to `build_cmd.py`'s `__file__` at development time and
use a fixed install path at distribution time — pragmatic and less
elegant, but safe.

**Risk 6: `window.webkit.messageHandlers.picolet.postMessage` is undefined in
unit test environments.**

The unit tests run in Node.js, not in WebKit. `window.webkit` does not
exist. The tests must mock this before sourcing the bundle.

Mitigation: the test setup in step 5 above sets `global.window` with the
mock before requiring the bundle. The bundle's IIFE reads
`window.webkit.messageHandlers.picolet.postMessage` at call time (inside
`_send`), not at load time, so the mock can be established after the
bundle is sourced and before any API call. Gate 3's `test_api_surface.js`
confirms this pattern works.

### Out of scope

- TypeScript `.d.ts` declaration file for user app authors (v1-spec §"Out of
  scope for v1": "TypeScript code generation from registered commands").
- Per-frame `<iframe>` IPC isolation. The bridge registers one
  `window.__picolet_recv` on the top-level frame only.
- PH09 (end-to-end `hello-webview` template app). PH08 produces the
  bridge and its unit/integration tests; PH09 builds the full template
  on top.
- Windows WebView2 injection (PH10). PH08 is Linux only for exit-gate
  verification, matching PH07's scope.
- Hot-reload of the bridge bundle during `picolet dev` (FR-CLI-7 scope).

### Spec traceability

| Spec id | Requirement | Covered by |
|---|---|---|
| FR-WV-4 | Bridge script injected before user JS. | AD5 (DOCUMENT_START injection); `_webview.py` modification (deliverable 7); gate 10. |
| FR-WV-5 | `window.picolet.invoke(cmd, args) → Promise<result>` and `window.picolet.on(event, handler) → unsubscribe`. | API contract §invoke, §on, §emit; `src/index.ts` (deliverable 2); gates 4–8, 11–13. |
| FR-BP-4 | Romfs includes the bridge-js bundle for webview variants. | AD4 (checked-in dist); `build_cmd.py` modification (deliverable 6); gate 9. |
| FR-IPC-2 | Error type and message preserved across the wire. | API contract §invoke error path; `src/index.ts` error construction; gate 5, 13. |
| FR-IPC-3 | `picolet.emit` from Python reachable by `picolet.on` in JS. | API contract §on, §emit; `window.__picolet_recv` event dispatch; gates 6, 12. |
| NFR-2 | Webview runtime ≤ 2 MB. | Gate 16 (size check after adding bundle to romfs). |

## Verification

**Tester:** scrum-tester (sonnet-4.6) | **Date:** 2026-05-15 | **Verdict: PASS**

### Build

Bundle rebuilt cleanly: `cd packages/picolet-bridge-js && node build.mjs` exits 0. `dist/picolet-bridge.js` is a single-line IIFE, 1083 bytes. `node -e` load with window mock exits 0.

### Gate results — run.sh (22 gates, 0 failed, 0 skipped)

| Gate | Label | Result | Evidence |
|---|---|---|---|
| A1 | bundle-exists (gate 1) | PASS | 1083 bytes |
| A2 | bundle-valid-js (gate 2) | PASS | node load ok |
| B1 | gate3 / api-surface | PASS | test_api_surface.js |
| B2 | gate4+14 / invoke-roundtrip + no-leak | PASS | test_invoke_roundtrip.js |
| B3 | gate5 / invoke-error | PASS | test_invoke_error.js |
| B4 | gate6 / event-dispatch | PASS | test_event_dispatch.js |
| B5 | gate7 / unsubscribe | PASS | test_unsubscribe.js |
| B6 | gate8 / emit | PASS | test_emit.js |
| B7 | sqe-concurrent-invokes | PASS | test_concurrent_invokes.js |
| B8 | sqe-multi-subscriber | PASS | test_multi_subscriber.js |
| B9 | sqe-error-empty-message | PASS | test_error_empty_message.js |
| B10 | sqe-args-edge-cases | PASS | test_args_edge_cases.js |
| B11 | sqe-bundle-size | PASS | test_bundle_size.js |
| B12 | sqe-malformed-inbound | PASS | test_malformed_inbound.js |
| C1 | bridge-in-romfs (gate 9 / FR-BP-4) | PASS | `picolet-bridge.js` present in `/rom/picolet` |
| D1 | bridge-inject-order (gate 10 / FR-WV-4) | PASS | `PICOLET_WV_BRIDGE_INJECT_OK` in stdout |
| D2a | invoke-roundtrip (gate 11 / FR-WV-5) | PASS | `PICOLET_WV_INVOKE_OK` in stdout |
| D2b | error-propagation (gate 13 / FR-IPC-2) | PASS | `PICOLET_WV_ERROR_OK` in stdout |
| D3 | event-push (gate 12 / FR-IPC-3) | PASS | `PICOLET_WV_EVENT_OK` in stdout |
| D4 | js-emit-fire-and-forget (FR-WV-5 emit) | PASS | `PICOLET_WV_EMIT_OK` in stdout |
| E1 | nfr-2-webview-le-2mib (gate 16) | PASS | 665 904 bytes (31% of 2 MiB) |
| F1 | ph07-gates-still-pass (gate 15) | PASS | PH07 run.sh: 20 passed, 0 failed |

### Independent integration test

Fresh webview app built from scratch (`picolet init` + hand-written `src/main.py` + `ui/index.html`) exercising all three bridge directions simultaneously:

- `invoke("ping", {from:"tester"})` → resolves `"pong:tester"` ✓
- `invoke("explode")` → rejects with `KeyError / "oops"` ✓
- Python `picolet.emit("server-push", …)` → JS `on("server-push")` handler echoes back ✓
- JS `window.picolet.emit("js-emit", {button:"click"})` → Python `picolet.on("js-emit")` receives payload ✓

Output: `TESTER_BRIDGE_ALL_OK`. Built with `picolet build --target linux-x64`; run under `xvfb-run -a`.

### FR-WV-4 verification (injection timing)

`_webview.py:147` calls `webkit_user_script_new(bridge_src, 1, 0, 0, 0)` with the third argument `0` selecting `WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START`. Bridge text is read from `/rom/picolet/picolet-bridge.js` at `Webview.__init__` time. Gate D1 (bridge-inject-order) confirms that the first user `<script>` tag already sees `window.picolet` as an object.

### FR-WV-5 verification

`dist/picolet-bridge.js` exports `window.picolet.invoke` (Promise-based), `window.picolet.on` (returns unsubscribe), `window.picolet.emit` (fire-and-forget). `window.__picolet_recv` handles both reply and push-event discriminator correctly. All verified by unit tests B1–B6 and integration tests D2a, D2b, D3, D4.

### NFR-2

Webview runtime binary: **665 904 bytes** (31% of 2 MiB ceiling). Adding the 1 083-byte bridge bundle to the romfs has negligible impact.

### PH03–07 regression

| Phase | Result |
|---|---|
| PH03 | 21 passed, 0 failed |
| PH04 | 31 passed, 0 failed |
| PH05 | 19 passed, 0 failed, 2 skipped |
| PH06 | 21 passed, 0 failed |
| PH07 | 20 passed, 0 failed, 3 skipped |

### Observations

- `WebviewTransport.send` correctly double-encodes: `json.dumps(json.dumps(msg))` so the JS call is `window.__picolet_recv("…string…")` not `window.__picolet_recv({…object…})`. This was identified and fixed by the developer and is confirmed correct.
- No TODO/FIXME/HACK markers found in any new or modified source files.
- The "not implemented" string at `build_cmd.py:156` is a pre-existing error message for unsupported targets, not an incomplete implementation.
- The independent integration test exposed a subtlety: JS must subscribe `on("server-push")` before emitting `page-ready` to avoid a race with Python's immediate emit. This is a user-code ordering concern, not a bridge defect — the bridge delivers events correctly whenever the handler is registered.
