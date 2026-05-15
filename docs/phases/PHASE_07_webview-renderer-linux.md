# PH07 — Webview renderer on Linux (WebKitGTK 4.1)

## Plan

### Goal (restated)

Stand up the first GUI variant of the picolet runtime: a `picolet-webview`
unix-port variant that opens a native window holding a WebKitGTK 4.1
webview, loads its root document from `/rom/<ui.root>/index.html`, and
plumbs the webview's `postMessage` channel into a new `WebviewTransport`
class that the PH06 dispatcher consumes unchanged.

The phase closes the following requirements from
[docs/v1-spec.md](../v1-spec.md):

| Spec id | Requirement |
|---|---|
| FR-WV-1 | On Linux the webview is WebKitGTK 4.1. |
| FR-WV-2 | The webview loads its root document from `/rom/<ui.root>/<index>`; default index is `index.html`. |
| FR-WV-3 | The window title and size come from `[window]` in `picolet.toml`. |
| FR-RT-2 | Three runtime variants per target: webview is one of them. PH07 lands the Linux build of the webview variant. |
| NFR-2 | `picolet-runtime-linux-x64-webview` ≤ 2 MB (excluding system webview). |
| NFR-8 | Linux artifacts run on Ubuntu 22.04 with no extra packages beyond `webkit2gtk-4.1` (webview variant only). |

PH07 is **Linux-only**. Windows webview (FR-WV-1 Windows half + NFR-9)
is PH10's scope. The bridge-JS shim (FR-WV-{4,5}) is PH08's scope. The
end-to-end round-trip from a browser button through `picolet.invoke` to a
Python handler is PH09's scope. PH07 closes the rendering and inbound
postMessage path; an outbound postMessage handshake will be exercised
in PH07 only via a stubbed receive-callback test.

### Major design decisions

#### D1 — WebKitGTK 4.1 access: pure libffi from Python (no native overlay C module)

The v1-plan text calls for `overlay/modules/picolet_window/` and
`overlay/modules/picolet_webview/` C source. After surveying both the
MicroPython FFI surface and the WebKitGTK 4.1 ABI surface, **PH07 ships
the webview integration as pure Python via `ffi.open` / `ffi.func` /
`ffi.callback`**. No `overlay/modules/picolet_*` directories are added in
this phase. The implementation lives in two frozen Python modules under
`packages/picolet-runtime/python/picolet_ui/`:

- `picolet_ui/_window.py` — opens the `GtkWindow`, sets title and size.
- `picolet_ui/_webview.py` — creates the `WebKitWebView`, loads the URI,
  registers the script-message handler, owns the `WebviewTransport`
  class.

These are wrapped by a public façade `picolet_ui/__init__.py` exporting
`Window`, `Webview`, `WebviewTransport`, and a `run(transport=...)`
helper that the user's `main.py` typically delegates to via
`picolet.run(transport=WebviewTransport(window))`.

**Rationale.**

1. **Precedent is solid.** pydfu-win's pyusb (which seeds the entire
   picolet binary-size story) is a non-trivial ~700-line libffi binding
   over libusb-1.0 living entirely in user-frozen Python under
   `tools/pydfu_app/lib/usb/core.py`. WebKitGTK is a larger API
   surface but picolet only touches ~15 functions (`gtk_init`,
   `gtk_window_new`, `gtk_window_set_title`, `gtk_window_set_default_size`,
   `gtk_widget_show_all`, `gtk_main`, `gtk_main_iteration`, `gtk_events_pending`,
   `webkit_web_view_new`, `webkit_web_view_load_uri`,
   `webkit_web_view_get_user_content_manager`,
   `webkit_user_content_manager_register_script_message_handler`,
   `webkit_user_content_manager_add_script`,
   `webkit_user_script_new`, `g_signal_connect_data`,
   `jsc_value_to_string`). Every one of these is a plain C function
   exported from `libwebkit2gtk-4.1.so.0` or `libgtk-3.so.0` /
   `libgobject-2.0.so.0` — no C++, no inline functions, no macros.
2. **`ffi.callback()` is in the modffi.c we already ship.** The unix
   port's `modffi.c` (lines 322–369) exposes `ffi.callback(rettype, fn,
   paramtypes, lock=True)` which wraps a Python callable into a
   libffi closure — exactly what we need for the
   `webkit_user_content_manager` "script-message-received" signal
   handler and for `g_signal_connect_data` callbacks. The `lock=True`
   kwarg makes the closure take the MicroPython scheduler lock + GC
   lock before re-entering Python — necessary for callbacks fired from
   the GTK main loop. This was a real concern raised in the
   pre-planning brief; it is no longer one.
3. **NFR-8 is satisfied by dynamic linking only.** The webview variant
   does not statically link any GTK or WebKit code. `ffi.open("libwebkit2gtk-4.1.so.0")`
   at runtime is the only contact point. The runtime artifact has no
   build-time dependency on webkit2gtk-4.1-dev; only the **runtime
   user** needs `apt install libwebkit2gtk-4.1-0` (NFR-8). This sharply
   reduces the source-build complexity and avoids dragging a GPL/LGPL
   header dependency into a static link path (NFR-5 implication: WebKit
   is LGPL; dynamic linkage is permitted, static linkage is not).
4. **NFR-2 size headroom.** The CLI variant is 641 KB. Adding pure-Python
   wrapper code costs ~15 KB frozen. The webview variant therefore stays
   well under the 2 MB budget. A native C overlay module would add
   ~20–40 KB of compiled code, no expressive power, and a build-time
   webkit2gtk-4.1-dev dependency.
5. **The "C module" wording in v1-plan §PH07 is descriptive, not
   normative.** No FR mandates a C module. FR-WV-{1,2,3} are about
   behaviour, not implementation language. The same precedent PH06 set
   ("defer C work to where it is actually load-bearing") applies.

**Known limitation discovered.** `ffi.callback()` in the unix port
(`call_py_func` at modffi.c:274 and `call_py_func_with_lock` at
modffi.c:289) marshals *all* callback arguments as `mp_int_t` —
i.e. as Python integers — regardless of the declared param type. The
return is also coerced to a truncated int. **This is acceptable for
PH07** because the script-message handler signature in WebKitGTK is
`(WebKitUserContentManager *, JSCValue *, gpointer)` — all pointer
arguments arrive as integers, which is exactly what
`call_py_func_with_lock` produces, and we never need to return a
non-trivial value from a callback (GObject signal callbacks for the
events we hook are void). The Python side then does
`uctypes.struct(addr, …)` / `ffi.string(…)` to dereference the
pointers manually. Recorded as a caveat for future renderer work
(e.g. if PH10 needs structured callback args from WebView2's COM
glue, that *might* push it to native C; PH07 does not).

**Decision log entry.** The developer logs this deviation as the
first commit in PH07:

```
[PH07] Decision: pure-libffi WebKitGTK 4.1 binding; defer C overlay to PH10 if needed.

The v1-plan text for PH07 calls for overlay/modules/picolet_window/ and
overlay/modules/picolet_webview/ C source.  In practice WebKitGTK 4.1's
C ABI is plain enough that ffi.open + ffi.func + ffi.callback (already
shipped in the unix port modffi.c, lines 322-369) covers the entire
surface PH07 needs (~15 functions).  ffi.callback's lock=True mode
serialises Python re-entry from a non-Python thread, which is the
exact integration shape required for GTK signal callbacks.  No static
link of WebKit (LGPL) into the runtime; NFR-5 honoured by construction.
NFR-2 stays well under budget.  A native C module in PH07 would add
build-time webkit2gtk-4.1-dev dependency, ~30 KB of code, and zero
expressive power.  PH10 (Windows WebView2) will re-evaluate; nothing in
PH07 prejudges that.
```

#### D2 — asyncio + GMainLoop integration: GTK on the asyncio thread, drive iterations from an asyncio task

WebKitGTK is GTK 3; GTK 3 is **single-threaded** — every GTK call must
happen on the same thread that called `gtk_init`. MicroPython's
asyncio is also single-threaded (cooperative). There are three plausible
ways to compose them, examined and rejected/selected below:

| Option | Description | Verdict |
|---|---|---|
| **A: asyncio in GMainLoop** | Run `gtk_main()` as the outer loop; install asyncio's task scheduler as a GMainLoop idle handler. | **Rejected.** Asyncio's `loop.step()` is not exposed; we'd reimplement the scheduler. Coupling is the wrong direction (we'd own GLib's loop more than it owns us). |
| **B: GMainLoop on a worker thread** | `gtk_main()` on a pthread; asyncio on the MicroPython main thread; marshal across with `ThreadSafeFlag`. | **Rejected for PH07.** Requires `MICROPY_PY_THREAD=1` in the variant config (a regression risk: the cli variant runs with threading defaults). GTK's "all calls on its own thread" rule means every Python-side webview call would need to be queued via `g_idle_add`. Adds two synchronisation layers (GTK queue + asyncio queue) for marginal benefit. |
| **C: GTK pumped from asyncio (chosen)** | `gtk_init` called on the asyncio thread. Instead of `gtk_main()` (blocks forever), we install an asyncio task that periodically calls `gtk_main_iteration_do(blocking=False)` — drains the GTK event queue when there is work, yields to asyncio when there isn't. | **Selected.** Same thread, no GIL story, no marshalling. The pump interval is tuned by the inbound-event load. |

**Concrete shape of D2.**

```python
# picolet_ui/_loop.py — pseudo-code, see Implementation guidance below

async def _gtk_pump():
    """Drain pending GTK events; yield to asyncio when idle.

    Runs as a task alongside the picolet dispatcher.  When script-message
    callbacks fire from inside gtk_main_iteration_do, they call
    asyncio.Event.set() on the receive flag, which the WebviewTransport
    is awaiting.
    """
    while True:
        # Drain everything queued so we don't fall behind on UI events.
        while gtk_events_pending():
            gtk_main_iteration_do(0)   # blocking=False
        # Sleep a short tick — short enough that user clicks feel
        # instant, long enough not to burn CPU on an idle window.
        await asyncio.sleep(0.005)     # 5 ms — see Risk 2 mitigation
```

The receive-side primitive is `asyncio.Event` (not `ThreadSafeFlag`,
which is only needed across threads — we are single-thread). When a
postMessage arrives, the libffi callback runs **synchronously inside
`gtk_main_iteration_do`** (i.e. inside our own task, with the GIL-free
scheduler-locked frame `call_py_func_with_lock` provides). The
callback appends the decoded JSON string into a Python list and
`Event.set()`s the WebviewTransport's `_recv_event`. The next
iteration of the asyncio scheduler resumes `transport.recv()` which
pops the list head and returns the parsed dict.

**Why 5 ms.** Tuning lever, not a hard contract. 5 ms gives 200 GTK
iterations per second when idle — invisible to a user, ~1% CPU on a
modern laptop. Apps that need lower idle CPU can ship a longer pump
interval; apps that need lower input latency can ship a shorter one.
The default is recorded in `picolet_ui/_loop.py` and documented in the
class docstring.

**Risk discharge.** This is genuinely the most-likely-to-be-wrong
piece of PH07. Mitigations:

- A failing alternative — Option B (GMainLoop on a worker thread) —
  is sketched in `picolet_ui/_loop.py` as a `_gtk_thread_pump` function
  behind a `PICOLET_WV_THREADED=1` env var. If gate 6 (page renders)
  fails because of pump starvation under load, the developer can flip
  to Option B without re-architecting the rest of the phase. This is
  carried as a contingency, not a primary plan.
- Unit-testable: the pump-and-event pattern is exercised by a
  CPython-side test that drives a `MockGtkLoop` instead of real GTK.
  See gate 8 below.

#### D3 — postMessage handler shape

WebKitGTK 4.1 exposes the postMessage channel via
`WebKitUserContentManager`. The host (Python) registers a named
handler. JS calls `window.webkit.messageHandlers.<name>.postMessage(payload)`.
The host receives a `JSCValue *` in a "script-message-received::<name>"
signal callback.

**Decisions for PH07.**

- Handler name: `"picolet"`. Stable, namespaced; PH08 builds the JS
  shim on top of it (`window.webkit.messageHandlers.picolet.postMessage(json)`).
- Payload shape: **a single JSON string** (the wire-format JSON from
  architecture.md §IPC, verbatim). Not a structured JSCValue —
  marshalling structured JSC values through libffi is far more
  involved than reading a single string. PH08's `window.picolet.invoke`
  internally does `JSON.stringify({id,cmd,args})` and posts that.
- Outbound (Python → JS): use
  `webkit_web_view_evaluate_javascript(view, script, …)` to run
  `window.__picolet_recv(jsonString)` where `__picolet_recv` is the
  bridge-JS callback registered by PH08. PH07 stubs this with a
  no-op `__picolet_recv` injected at user-content-manager startup so
  the round-trip is testable without PH08.
- JSC unmarshal: a single `jsc_value_to_string(value)` → returns
  `char *` (caller owns; `g_free` after `ffi.string`). One libffi call
  per inbound message; no recursive JSC traversal.

**Wire shape.** Unchanged from PH06 — the `WebviewTransport` consumes
the same `{"id":..., "cmd":..., "args":...}` request shape that
`StdioTransport` does. PH07 does not extend the wire format.

#### D4 — Window vs Webview split

The v1-plan text calls for two modules `picolet_window` and `picolet_webview`.
**Keep the split, as two sibling Python modules.** Rationale: PH11's
LVGL variant will reuse `picolet_window`-equivalent code (an SDL2 window,
window title from `[window]`) without webview. Sharing a window
abstraction now even within a single variant means PH11's `picolet_lvgl`
module can mirror the surface, with `Window` as the renderer-agnostic
type and `Webview` (PH07) / `LvglDisplay` (PH11) as the content layer.

Concretely:

- `picolet_ui/_window.py` exposes `class Window(title, size, resizable)`
  using GTK directly. Reads `[window]` from `/rom/picolet.toml` via the
  same `tomllib` substitute the runtime already has (`json`-based —
  `[window]` is small enough that we accept a tiny `_toml.py` mini-parser,
  see Implementation guidance).
- `picolet_ui/_webview.py` exposes `class Webview(window, root_uri)` —
  takes an existing `Window`, embeds a `WebKitWebView`, loads
  `file:///rom/<ui.root>/<ui.index>`.
- `picolet_ui/__init__.py` re-exports both plus a `Application` factory
  that wires the common case together for `hello-webview`.

Naming. The v1-plan uses `picolet_window` / `picolet_webview` as
**module overlays**, which would suggest top-level package names.
Since PH07 lands these as pure Python, they are *submodules of
`picolet_ui`* — a single package. This is a tiny deviation from the
plan text (one package vs. two) but cleaner: it avoids polluting the
top-level Python namespace with two near-empty packages, and groups
the renderer code under one umbrella that PH11's LVGL work will not
collide with (PH11's umbrella is `picolet_ui` too, with `_lvgl.py` and
`_window_lvgl.py`).

If reviewers prefer two top-level packages, the rename is
mechanical — the design isn't affected.

#### D5 — Headless test strategy: `xvfb-run` everywhere, no host display required

PH07's "sanity test: window opens, document loads, page renders" must
work on a developer host with a display **and** on the CI host with no
display. **Decision: every webview integration test wraps the runtime
binary in `xvfb-run -a`.** Rationale:

- `xvfb-run` is in the standard Ubuntu repos (`apt install xvfb`); the
  build container's Dockerfile already runs apt during image build, so
  adding `xvfb` is a one-line change.
- Same command works in CI (PH15) and locally — no test path divergence.
- Tests can still assert visually-meaningful state via WebKit's
  `notify::title` signal (we set the page title via JS once rendering
  is complete) and via WebKit's `web_process_terminated` signal (page
  load failure surface).

**Concrete gate-6/7 test path.**

```bash
# tests/phase-07/run_window_opens.sh
xvfb-run -a -s "-screen 0 800x600x24" \
    timeout 5 ./build/picolet-runtime-linux-x64-webview \
        -c "import picolet_ui; picolet_ui.run_sanity_test()"
```

`run_sanity_test()` lives in `picolet_ui/_test.py` and:
1. Builds a tiny `index.html` containing `<script>document.title='LOADED-'+Date.now();window.webkit.messageHandlers.picolet.postMessage('{"event":"loaded","data":{}}');</script>`.
2. Mounts it as a temp file (not /rom — the sanity test runs without
   the romfs trailer fixture).
3. Opens window + webview pointing at the file.
4. Waits for the postMessage callback to fire (asyncio.Event) with a
   1-second timeout.
5. Prints `PICOLET_WV_SANITY_OK` and exits 0 on success; otherwise
   prints the failure mode and exits 1.

The driver script asserts the magic string `PICOLET_WV_SANITY_OK`
appears in stdout. The page rendering is verified indirectly via the
JS-side `postMessage` arriving back at Python — i.e. the page loaded
AND its `<script>` ran AND the postMessage bridge works. Three things
proven in one shot.

A second test fixture under `tests/phase-07/fixtures/` is a normal
`picolet build`-produced binary that loads `/rom/ui/index.html` via the
real romfs path; gate 5 below.

### Architecture

```
                    ┌──────────────────────────────────────────┐
                    │   picolet-runtime-linux-x64-webview        │
                    │   (single ELF, ≤ 2 MB, frozen MicroPy)   │
                    └──────────────────────────────────────────┘
                              │                       │
                              │ asyncio task          │ asyncio task
                              │ (picolet dispatcher)    │ (picolet_ui._loop._gtk_pump)
                              ▼                       ▼
                  ┌───────────────────────┐  ┌────────────────────────────┐
                  │  picolet._dispatcher    │  │  gtk_main_iteration_do(0)  │
                  │  ._run_dispatcher     │  │  via libffi → libgtk-3     │
                  │  awaiting transport   │  │  drains GTK event queue    │
                  │  .recv()              │  │  fires signal callbacks    │
                  └───────────────────────┘  └────────────────────────────┘
                              ▲                       │
                              │                       │ signal "script-message-received::picolet"
                              │ Event.set()           │ → libffi closure (lock=True)
                              │                       ▼
                  ┌───────────────────────┐  ┌────────────────────────────┐
                  │  WebviewTransport     │◀─│  on_script_message(mgr,    │
                  │  recv_event,          │  │   jsc_value, user_data):   │
                  │  _inbox = [json,...]  │  │   s = jsc_value_to_string()│
                  └───────────────────────┘  │   _inbox.append(s)         │
                                             │   _recv_event.set()        │
                                             └────────────────────────────┘
                                                            ▲
                                                            │ window.webkit.messageHandlers
                                                            │ .picolet.postMessage(json)
                                             ┌────────────────────────────┐
                                             │  WebKitWebView (renderer   │
                                             │  process, GPL-isolated)    │
                                             │  loaded file:///rom/ui/    │
                                             │  index.html                │
                                             └────────────────────────────┘
                                                            ▲
                                             ┌──────────────┴─────────────┐
                                             │  index.html + bridge-js    │
                                             │  (bridge-js is stubbed in  │
                                             │  PH07; landed in PH08)     │
                                             └────────────────────────────┘
```

Outbound (Python → JS) flows in the reverse direction:
`WebviewTransport.send(msg)` → `json.dumps(msg)` → schedule a GTK idle
function that calls `webkit_web_view_evaluate_javascript(view,
"window.__picolet_recv(" + JSON.stringify(s) + ")")` → JS receives the
string.

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` exits 0 (no regression of PH00–PH06). | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0. |
| 2 | `build-runtime.sh --target linux-x64 --variant webview` exits 0. **FR-RT-2.** | Build succeeds inside `picolet-linux-x64-build:22.04` container. Artifact at `packages/picolet-runtime/build/picolet-runtime-linux-x64-webview`. |
| 3 | `import picolet_ui` succeeds in the webview runtime. | `./build/picolet-runtime-linux-x64-webview -c 'import picolet_ui; print("picolet_ui-ok")'` → `picolet_ui-ok`. Does NOT need a display (the import does not open a window). |
| 4 | NFR-2 size gate. | `wc -c build/picolet-runtime-linux-x64-webview` → ≤ 2 097 152 bytes (2 MiB). Print actual size + percentage. |
| 5 | **FR-WV-2**: webview loads `/rom/<ui.root>/index.html` from a romfs-embedded fixture. | `picolet build` against `tests/phase-07/fixtures/hello-webview-min/picolet.toml` (with `[ui] renderer="webview", root="ui"` and a `ui/index.html` that sets `document.title = 'LOADED'`). `xvfb-run -a timeout 5 ./hello-webview-min` — runtime exits 0 and stdout shows `PICOLET_WV_SANITY_OK title=LOADED`. |
| 6 | **FR-WV-3**: window title and size come from `[window]` in `picolet.toml`. | Same fixture as gate 5, with `[window] title="PH07 Sanity" size=[640,480] resizable=false`. The runtime emits `window: title=PH07 Sanity size=640x480 resizable=False` on stderr at startup; assertion script greps for this exact line. |
| 7 | **FR-WV-1**: the linked library is `libwebkit2gtk-4.1.so.0`. | `ldd build/picolet-runtime-linux-x64-webview` — assert `libwebkit2gtk-4.1.so.0` is **not** present (it isn't dynamically linked at build time; we dlopen at runtime). Then `strings build/picolet-runtime-linux-x64-webview \| grep libwebkit2gtk-4.1` finds the literal string the Python source `ffi.open`s. Confirms FR-WV-1 by source rather than by linker manifest (the string is the only contact point). |
| 8 | The script-message handler fires the Python callback. | `xvfb-run -a timeout 5 ./build/picolet-runtime-linux-x64-webview -m picolet_ui._test.run_callback_probe` — registers `"picolet"` handler, injects user script that calls `postMessage('{"id":1,"cmd":"ping","args":null}')`, waits on asyncio.Event (timeout 2s), asserts the received string round-trips through `json.loads` to the expected dict. Output: `PICOLET_WV_CALLBACK_OK`. |
| 9 | `WebviewTransport` satisfies the PH06 transport duck-type contract. | CPython unit test `tests/phase-07/test_transport_contract.py` — instantiates `WebviewTransport` with a mock GTK loop, drives `recv()` / `send()` / `close()`, asserts each is awaitable and behaves per `Transport.__doc__`. (Pure-Python test; the GTK calls are stubbed via a `MockGtkBackend` injected via constructor.) |
| 10 | The dispatcher run loop accepts `WebviewTransport` and runs to EOF cleanly. | `tests/phase-07/test_dispatcher_with_webview_transport.py` (CPython) — wires `picolet._dispatcher.run(transport=WebviewTransport(mock_backend))`; `main=` returns after the mock injects 3 events; assert dispatcher cleanly exits and outbox shows the expected request/reply shapes. |
| 11 | `xvfb-run` smoke from a clean checkout works (no host-dev-host coupling). | After `--clean`, the gate-5 test still passes. Confirms libwebkit2gtk-4.1-0 is installed at the build/test layer and that no host display is needed. |
| 12 | Webview variant builds idempotently (warm cache). | Second invocation of gate-2's `build-runtime.sh` completes in < 5 s with no new compile units. |
| 13 | The cli variant still builds and gates PH01/PH06 pass unchanged. | `build-runtime.sh --target linux-x64 --variant cli` exits 0; `tests/phase-06/run.sh` still green. Regression test. |
| 14 | NFR-8 — no extra apt packages beyond `webkit2gtk-4.1` are required to run. | Inside a fresh `ubuntu:22.04` container, run `apt install -y libwebkit2gtk-4.1-0 xvfb` (the test-only xvfb is excluded from NFR-8 counting). Drop in the gate-5 artefact, run it. Exits 0 with `PICOLET_WV_SANITY_OK`. Audit `apt list --installed` shows no other gui packages were pulled in beyond webkit's own libgtk-3-0 dependency chain (which is itself part of the webkit2gtk-4.1 package's depends). |
| 15 | Trace-level diagnostic: page render is real (not just script execution). | Same `xvfb-run` driver as gate 5, with the screen captured to a PNG via `xwd | xwdtopnm | pnmtopng`; the PNG is ≥ 1 KB (non-trivial pixel content) and contains the colour `#336699` set as `<body style="background:#336699">`. Verified by reading the pixel at (1,1) — `convert in.png -format "%[pixel:p{1,1}]" info:` returns the matching srgb triplet. This gate is in the harness as the visual confirmation; gate 5's `PICOLET_WV_SANITY_OK` is the cheap fast gate, gate 15 is the slow definitive one. Run only in CI (slow). |
| 16 | The pump+event design does not starve the dispatcher. | `tests/phase-07/test_pump_responsiveness.py` (CPython) drives the asyncio scheduler with 50 inbound mock-postMessages back-to-back; asserts each is delivered to the dispatcher within 25 ms (5 ms pump × 5 retries margin). |
| 17 | postMessage with non-UTF8 payload produces a clean error, no crash. | Inject a script that posts a JSC string with an invalid UTF8 byte sequence (using `\uD800` — unpaired surrogate). `jsc_value_to_string` returns the bytes; `json.loads` raises ValueError; the transport logs and drops, dispatcher keeps running. Assertion: stderr contains `picolet_ui: malformed JSON from postMessage`, no exit. |
| 18 | Windows webview build is **not** attempted. | `build-runtime.sh --target windows-x64 --variant webview` still exits with the PH10 stub error message ("see PH10"). PH07 must not break this. |
| 19 | Frozen manifest line for the webview variant is unique to it. | `cat manifests/manifest_webview.py` — exists, includes `picolet` (from PH06) AND `picolet_ui` (new). `manifest_cli.py` is unchanged. |
| 20 | Documentation: the runtime README has a webview-variant section that names `webkit2gtk-4.1` as the required runtime package (NFR-8 user-facing note). | Grep `packages/picolet-runtime/README.md` for `webkit2gtk-4.1` and find a section header `## Webview variant`. |

Gates 2, 5, 6, 7, 14 close FR-WV-{1,2,3}. Gate 2 closes FR-RT-2's
Linux webview half. Gate 4 closes NFR-2. Gate 14 closes NFR-8. Gates
8–10, 16–17 cover operational correctness implied by but not literally
in the FRs (callback wiring, transport contract, pump responsiveness,
malformed-payload handling). Gates 11–13, 18–19 protect the build
pipeline and the cli/PH06 baseline.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-WV-{1,2,3,4,5}, FR-RT-2, NFR-2, NFR-8 normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH07 + §PH08 + §PH09 | Phase scope, deliverables, exit gate, model tiers. PH08/PH09 read for downstream-impact awareness. |
| `/home/anl/picolet/docs/architecture.md` §"IPC wire format" | Confirms PH07 carries the same `{id,cmd,args}` shape through postMessage; no new wire shape. |
| `/home/anl/picolet/CLAUDE.md` | Branch / commit / dev-log policy. |
| `/home/anl/picolet/docs/phases/PHASE_06_picolet-ipc-dispatcher.md` | Verification section, esp. (a) hollow tests + (b) generator slip-through residual risk + Risk 3 stdin/poll story (informs PH07's pump+event design); the carryover items affect how PH07 designs the WebviewTransport. The transport duck-type contract is the load-bearing inheritance. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet/_transport.py` | The duck-typed Transport class (recv/send/close). PH07's WebviewTransport subclasses nothing; it just exposes the three async methods. The MockTransport.pair pattern (lines 231-237) is reused for unit tests. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet/_dispatcher.py` | Confirms `picolet.run(transport=..., main=...)` accepts any duck-typed transport; PH07 plugs in `WebviewTransport` with zero dispatcher changes. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Baseline lean variant config to fork. The webview variant inherits most defines; the only deltas are leaving threading at its default (no change) and removing the explicit MICROPY_PY_MACHINE / MICROPY_PY_WEBSOCKET turn-offs only if the GUI-variant size budget needs them (it doesn't — keep the same lean shape). |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.mk` | Same — fork to `picolet-webview/mpconfigvariant.mk` with `FROZEN_MANIFEST = manifests/manifest_webview.py` and otherwise identical. |
| `/home/anl/picolet/packages/picolet-runtime/manifests/manifest_cli.py` | Pattern for `freeze("../python", "picolet")`. PH07's `manifest_webview.py` does the same + a second `freeze("../python", "picolet_ui")` line. |
| `/home/anl/picolet/packages/picolet-runtime/scripts/build-runtime.sh` | Lines 86–96 handle the `linux-x64/webview` case stub today (exits with "see PH07"). PH07 replaces that stub with a real branch that calls `build_linux_x64` with `VARIANT=webview`. The build container needs `apt install xvfb` (test-only) plus `libwebkit2gtk-4.1-0` (runtime-only); the **build** image does NOT need `libwebkit2gtk-4.1-dev` because we dlopen at runtime. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/modffi.c` | Confirmed: `ffi.callback(rettype, fn, paramtypes, lock=True)` is supported (lines 322-369). Callback args marshalled as mp_int_t (lines 274-287) — see D1 limitation. Closure allocation via `ffi_closure_alloc` (line 343). This is the core "is libffi enough?" question and the answer is yes for PH07. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/event.py` | `Event` and `ThreadSafeFlag` semantics. PH07 uses plain `Event` (single-thread design); ThreadSafeFlag is the fallback if Option B (worker-thread GTK) is needed. |
| `/home/anl/pydfu-win/micropython/tools/pydfu_app/lib/usb/core.py` | Reference implementation of the pure-libffi pattern. Lines 99-118 show the ffi.open + ffi.func table. PH07's `picolet_ui/_gtk_ffi.py` mirrors this exact shape. |
| `/home/anl/picolet/packages/picolet-cli/picolet/validator.py` | `[window]` schema (lines 41-42, 64): `title: str`, `size: list`. The validator already accepts the schema; PH07 needs to *consume* it inside the runtime, which means reading `/rom/picolet.toml`. |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | Confirmed: `picolet build` does NOT inject `picolet.toml` into the romfs automatically. PH07 must add `picolet.toml` to the romfs for the webview path (or document that users add it to `[romfs] include`). Decision: PH07's `picolet build` change copies `picolet.toml` into the romfs root when `[ui] renderer = "webview"` — automatic and invisible to the user. |
| `/usr/include/webkitgtk-4.1/webkit/*.h` (host introspection) | API surface confirmation — every WebKit function PH07 calls exists and has the expected signature in webkit2gtk-4.1 (Ubuntu 22.04 ships 2.36, Ubuntu 24.04 ships 2.52). Stable ABI within the 4.1 series; minor differences are documented per the gtk-doc deprecation notes. |
| `/usr/lib/x86_64-linux-gnu/libwebkit2gtk-4.1.so.0` | Confirmed present on the developer host; symlinks to `libwebkit2gtk-4.1.so.0.21.7`. The runtime `ffi.open("libwebkit2gtk-4.1.so.0")` resolves this. |
| `apt-cache show libwebkit2gtk-4.1-0` | Confirmed: the runtime user installs *exactly* `libwebkit2gtk-4.1-0` (binary package) — the `-dev` headers are NOT required at runtime. NFR-8 holds. |
| `which xvfb-run` | Confirmed present on the developer host; the test harness depends on it. The build container's Dockerfile is updated to `apt install xvfb`. |

### Files to create

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview/mpconfigvariant.h` | Forked from `picolet-cli/mpconfigvariant.h`. Identical macro set; only the comment header changes. The webview variant does NOT need new ROM-level flags. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview/mpconfigvariant.mk` | Forked from `picolet-cli/mpconfigvariant.mk`. Only delta: `FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview.py`. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview/romfs_trailer.c` | Copy of `picolet-cli/romfs_trailer.c` (the trailer mechanic is variant-independent). |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview/romfs_trailer.h` | Copy of `picolet-cli/romfs_trailer.h`. |
| `packages/picolet-runtime/manifests/manifest_webview.py` | New frozen manifest. Same as `manifest_cli.py` plus `freeze("../python", "picolet_ui")`. |
| `packages/picolet-runtime/python/picolet_ui/__init__.py` | Public façade: `from ._window import Window; from ._webview import Webview, WebviewTransport; from ._app import run, Application`. |
| `packages/picolet-runtime/python/picolet_ui/_gtk_ffi.py` | libffi bindings: opens `libgtk-3.so.0`, `libgobject-2.0.so.0`, `libwebkit2gtk-4.1.so.0`, `libjavascriptcoregtk-4.1.so.0`, declares the ~15 `ffi.func` signatures listed in D1. Mirrors `pydfu-win/.../usb/core.py` shape. |
| `packages/picolet-runtime/python/picolet_ui/_window.py` | `class Window` — `gtk_init`, `gtk_window_new`, `gtk_window_set_title`, `gtk_window_set_default_size`, `gtk_window_set_resizable`, `gtk_widget_show_all`. Reads `[window]` from `/rom/picolet.toml` via `_toml.py` mini-parser. |
| `packages/picolet-runtime/python/picolet_ui/_webview.py` | `class Webview` — constructs `WebKitWebView`, gets its user content manager, registers the `"picolet"` script-message handler, hooks the signal callback (libffi closure with lock=True), embeds the view in the Window's container, loads the URI. Also `class WebviewTransport` per the contract. |
| `packages/picolet-runtime/python/picolet_ui/_loop.py` | `async def _gtk_pump()`, `async def run(transport, main=None)`. Wraps `picolet._dispatcher.run` with the pump task alongside the dispatcher task. |
| `packages/picolet-runtime/python/picolet_ui/_toml.py` | Tiny `[window]` parser: a single-section TOML reader sufficient for `title="..."` and `size=[w,h]` and `resizable=true/false`. Pure Python; ~50 lines. Does NOT pull in micropython-lib's full toml. |
| `packages/picolet-runtime/python/picolet_ui/_app.py` | `Application` helper that mass-wires the common `picolet.run(transport=WebviewTransport(Webview(Window(...), root_uri)))` case for `hello-webview`. Optional convenience; not strictly required by any FR. |
| `packages/picolet-runtime/python/picolet_ui/_test.py` | `run_sanity_test()` (gate 5/6), `run_callback_probe()` (gate 8). Lives next to the source so `python -m picolet_ui._test.run_*` from the frozen runtime works. |
| `packages/picolet-runtime/scripts/dockerfiles/linux-x64-build/Dockerfile` | Modified — add `RUN apt install -y --no-install-recommends libwebkit2gtk-4.1-0 xvfb` so the build container can run the webview tests. The webview runtime is dlopen-only so `-dev` is NOT installed. |
| `packages/picolet-runtime/tests/phase-07/run.sh` | Tester harness. Mirrors `tests/phase-06/run.sh` (per-gate driver, PASS/FAIL summary). |
| `packages/picolet-runtime/tests/phase-07/run_window_opens.sh` | Gate 5 driver: `xvfb-run -a -s "-screen 0 800x600x24" timeout 5 ./build/picolet-runtime-linux-x64-webview -c "import picolet_ui._test as t; t.run_sanity_test()"`. |
| `packages/picolet-runtime/tests/phase-07/run_window_title_size.sh` | Gate 6 driver. |
| `packages/picolet-runtime/tests/phase-07/run_callback_probe.sh` | Gate 8 driver. |
| `packages/picolet-runtime/tests/phase-07/test_transport_contract.py` | Gate 9 — CPython unit test against `WebviewTransport` with `MockGtkBackend`. |
| `packages/picolet-runtime/tests/phase-07/test_dispatcher_with_webview_transport.py` | Gate 10. |
| `packages/picolet-runtime/tests/phase-07/test_pump_responsiveness.py` | Gate 16. |
| `packages/picolet-runtime/tests/phase-07/test_malformed_postmessage.py` | Gate 17 — CPython unit test. |
| `packages/picolet-runtime/tests/phase-07/fixtures/hello-webview-min/picolet.toml` | Gate 5 fixture: `[app]`, `[ui] renderer="webview", root="ui"`, `[window] title="PH07 Sanity" size=[640,480]`, `[romfs] include=["ui"]`. |
| `packages/picolet-runtime/tests/phase-07/fixtures/hello-webview-min/src/main.py` | Two lines: `import picolet_ui; picolet_ui.run()`. The runtime auto-discovers `picolet.toml` from `/rom` and runs the standard webview flow. |
| `packages/picolet-runtime/tests/phase-07/fixtures/hello-webview-min/ui/index.html` | `<html><body style="background:#336699"><script>document.title='LOADED'; window.webkit.messageHandlers.picolet.postMessage('{"event":"loaded","data":{}}');</script></body></html>`. |
| `packages/picolet-runtime/tests/phase-07/run_visual_render.sh` | Gate 15 (visual confirmation via xwd + convert pixel sampling). Marked SLOW; not in the default `run.sh` set. |

### Files to modify

| Path | Change |
|---|---|
| `packages/picolet-runtime/scripts/build-runtime.sh` | Lines 86–96 — replace the `linux-x64/webview` stub error with a real branch that calls `build_linux_x64` (the existing function, parameterised on `VARIANT_NAME=picolet-webview`). The `manifest_webview.py` resolution is automatic via `mpconfigvariant.mk`'s `FROZEN_MANIFEST`. The Windows webview branch (line ~90) stays as the PH10 stub. |
| `packages/picolet-runtime/scripts/dockerfiles/linux-x64-build/Dockerfile` | Add `libwebkit2gtk-4.1-0 xvfb` to the apt install line. Bump the image tag (e.g. `picolet-linux-x64-build:22.04-webview` or just rebuild against the existing tag — the developer chooses, with a note in the build script). |
| `packages/picolet-runtime/README.md` | Add `## Webview variant` section: variant name, `apt install libwebkit2gtk-4.1-0` requirement, how to run inside xvfb on a headless host. Gate 20. |
| `packages/picolet-cli/picolet/build_cmd.py` | Add: when `[ui] renderer == "webview"`, copy the source `picolet.toml` (a clean subset, just `[window]` and `[ui]`) into the romfs root so the runtime can read it at startup. Single-function addition. The `[romfs] include` user list is preserved. This is the minimum mechanism to satisfy FR-WV-3 without requiring users to add `picolet.toml` to `[romfs] include` manually. |
| `packages/picolet-runtime/manifests/manifest_cli.py` | None expected. The cli variant must not pick up `picolet_ui` (it would be size + import surface for nothing). The split-manifest pattern guarantees this. |
| `packages/picolet-runtime/overlay/ports/unix/main.c` | None expected. The webview variant's `main.py` (the user's, frozen via romfs) calls `import picolet_ui; picolet_ui.run()`; this is the same auto-run path the cli variant uses. The main.c overlay does not need to know about variants — variant-specific behaviour is entirely in the frozen Python. **Confirmed: no overlay/ports/unix/main.c changes needed.** |

### Sequence the developer follows

All from `/home/anl/picolet` on `dev`.

**1. Log the design decision.**
```
git commit --allow-empty -s -m "[PH07] Decision: pure-libffi WebKitGTK 4.1 binding; defer C overlay to PH10 if needed" -m "..."
```
Body covers D1's full rationale.

**2. Log the loop-integration decision.**
```
git commit --allow-empty -s -m "[PH07] Decision: GTK pumped from asyncio task at 5ms tick (Option C)" -m "..."
```
Body explains why Option C beats Options A and B, including the
fallback path to Option B if the pump tick proves too coarse.

**3. Fork the variant config.**
```
mkdir -p packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview
cp overlay/ports/unix/variants/picolet-cli/{mpconfigvariant.h,mpconfigvariant.mk,romfs_trailer.c,romfs_trailer.h} \
   overlay/ports/unix/variants/picolet-webview/
```
Edit the .mk to point at `manifest_webview.py`. Update headers.

**4. Create the new manifest.**
Copy `manifest_cli.py` to `manifest_webview.py`; append
`freeze("../python", "picolet_ui")`.

**5. Lay down the `picolet_ui` package skeleton.**
Create the files under `python/picolet_ui/` with stubs.

**6. Wire `build-runtime.sh` for the webview variant.**
Replace the linux-x64/webview stub with a real branch. Add
`libwebkit2gtk-4.1-0` and `xvfb` to the build container's Dockerfile.

**7. Confirm gate 3 (`import picolet_ui` works).**
```
./scripts/build-runtime.sh --target linux-x64 --variant webview
./build/picolet-runtime-linux-x64-webview -c 'import picolet_ui; print("ok")'
```
Should succeed even with no display (the import does no GTK init).

**8. Implement `_gtk_ffi.py`.**
Open the four shared libraries, declare the function signatures, expose
them as module attributes. Add a runtime check on import: if any
`ffi.open` fails, raise a clean `ImportError` with the missing library
name.

**9. Implement `_window.py`.**
`gtk_init(0, None)`, `gtk_window_new`, set title + size from
`[window]` (parsed by `_toml.py`), `gtk_widget_show_all`.

**10. Implement `_webview.py`.**
- Create `WebKitWebView` via `webkit_web_view_new`.
- Get its `WebKitUserContentManager`.
- Register the `"picolet"` script-message handler.
- Create an `ffi.callback("v", on_script_message, "ppp", lock=True)`
  closure; pass to `g_signal_connect_data` for
  `"script-message-received::picolet"`.
- Inside `on_script_message`, dereference the JSCValue via
  `jsc_value_to_string`, append to `WebviewTransport._inbox`, set
  `_recv_event`.
- `WebviewTransport.recv()` awaits `_recv_event`, pops inbox head,
  `json.loads`, returns dict.
- `WebviewTransport.send(msg)` schedules `g_idle_add` to run
  `webkit_web_view_evaluate_javascript(view, 'window.__picolet_recv('+
  JSON.stringify(jsonStr)+')')`.
- Inject a no-op `window.__picolet_recv = function(s){};` user script at
  webview creation so PH07 outbound sends don't error before PH08
  lands the real receiver.

**11. Implement `_loop.py`'s pump.**
`async def _gtk_pump():` per D2's snippet. `async def run(transport,
main=None)` wraps `picolet._dispatcher.run` and adds the pump as a
sibling task; race them.

**12. Land the gate-5 fixture and run the visual sanity test.**
```
xvfb-run -a -s "-screen 0 800x600x24" timeout 5 \
    ./build/picolet-runtime-linux-x64-webview \
        -c 'import picolet_ui._test as t; t.run_sanity_test()'
```
Expect `PICOLET_WV_SANITY_OK title=LOADED` on stdout.

**13. Land the gate-8 callback probe.**
Same shape as gate 5 but exercises the inbound postMessage path
explicitly (without bridge-js — directly via a user script that calls
`window.webkit.messageHandlers.picolet.postMessage(...)` itself).

**14. CPython unit tests for gates 9, 10, 16, 17.**
These run without GTK via the `MockGtkBackend` injection point. Mirror
the PH06 unit test structure under `tests/phase-07/`.

**15. Wire `picolet build` to copy `picolet.toml` into the webview romfs.**
Smallest possible change in `build_cmd.py`: an `if renderer ==
"webview"` branch that adds a sanitised `picolet.toml` (just `[ui]` and
`[window]`) to the romfs staging dir before image build.

**16. Run the full gate suite.**
```
bash packages/picolet-runtime/tests/phase-07/run.sh
```
All gates green.

**17. Confirm non-regression of PH06 gates and the Windows build.**
```
bash packages/picolet-runtime/tests/phase-06/run.sh
./scripts/build-runtime.sh --target windows-x64 --variant cli   # should still pass
```

**18. Document.**
Append the `## Webview variant` section to `packages/picolet-runtime/README.md`
(gate 20).

### Foreseeable risks

**Risk 1: `ffi.callback`'s integer-only marshalling is insufficient
for a non-trivial WebKit signal.**

The `script-message-received::picolet` callback signature is
`void (*)(WebKitUserContentManager *, JSCValue *, gpointer)`. All three
parameters are pointer-shaped on x86_64 and arrive as 64-bit integers
in the libffi `args[]` array. `call_py_func_with_lock` (modffi.c:289)
casts each arg via `mp_obj_new_int(*(mp_int_t *)args[i])`. As long as
the host is little-endian 64-bit (which v1 targets are by spec) this
correctly recovers the pointer value as a Python int. The Python side
then treats those ints as opaque handles and calls back into libffi
functions that take `"p"` args — `jsc_value_to_string(value_int)` —
to dereference.

Mitigation: a CPython-side unit test (gate 9) drives the same marshalling
contract with `MockGtkBackend` that passes Python ints in. A
runtime-side integration test (gate 8) verifies the real WebKit path
end to end on the host. If the integer marshalling somehow doesn't
round-trip a 64-bit pointer through libffi (e.g. on a 32-bit picolet
build, which v1 doesn't target), the failure mode is a segfault on the
first `jsc_value_to_string` call — loud, not silent.

**Risk 2: the 5 ms GTK pump is too coarse for input responsiveness, or
too fine and burns CPU.**

5 ms is a starting point. Symptom of "too coarse": a button click
takes > 50 ms to register. Symptom of "too fine": the runtime burns
> 5% CPU on an idle window.

Mitigation: the pump interval is a module attribute
`picolet_ui._loop.PUMP_INTERVAL_S` (default 0.005). Apps can override it
before calling `run()`. The contingency Option B (GTK on a worker
thread) is sketched in `_loop.py` under `_gtk_thread_pump` and can be
enabled by the `PICOLET_WV_THREADED=1` env var; this turns the dance
into pure event-driven (no polling) at the cost of enabling
`MICROPY_PY_THREAD=1` in the variant config. If gate 16 (pump
responsiveness) fails, the developer flips the variant default.

**Risk 3: WebKitGTK 4.1's renderer-process sandbox interferes with
file:// loads.**

WebKitGTK 4.1 by default isolates the web content process in a
seccomp/bubblewrap sandbox. The sandbox is fine for `file://` URLs
*usually*, but some distros (esp. those that ship WebKit built without
the sandbox-helper) refuse to load them.

Mitigation: call
`webkit_web_context_set_sandbox_enabled(default_context, FALSE)`
once at app startup — explicitly disable the sandbox. The picolet
runtime is loading content it bundled itself (i.e. trusted), so the
sandbox costs us correctness without buying security. Documented as a
deliberate choice in the `_webview.py` comments. The alternative —
shipping bubblewrap — is a hard NFR-8 violation (extra apt package).

A second mitigation: if sandbox-disable fails on some Ubuntu builds,
fall back to a `data:` URI containing the inlined HTML. Acceptable for
tiny `index.html`; not for an app with assets. Recorded as a
contingency only.

**Risk 4: WebKit's GObject signal callbacks may be fired before our
asyncio loop reaches the pump tick.**

If the user clicks an in-page button at startup, the postMessage
callback fires the moment GTK processes that event. Our `_recv_event.set()`
must be safe to call from inside `gtk_main_iteration_do`. Since we
are single-threaded (D2), this is fine — `Event.set()` just marks the
flag, and the next asyncio scheduler tick wakes the waiter. The
callback returns to GTK in microseconds.

Mitigation: gate 16 exercises 50 back-to-back postMessages within one
pump tick; it asserts every one is delivered before the next tick.
This proves the buffered-inbox design is robust to bursts.

**Risk 5: `webkit2gtk-4.1` shipping with different soversions across
Ubuntu releases.**

Ubuntu 22.04 ships `libwebkit2gtk-4.1.so.0.4.x`; 24.04 ships
`.0.21.x`. The soname `libwebkit2gtk-4.1.so.0` is stable across; we
`ffi.open` the soname, not the full filename.

Mitigation: confirmed via `apt-cache show libwebkit2gtk-4.1-0` and via
the SONAME in the .so on the host. Documented in
`_gtk_ffi.py` as the contract; if a future Ubuntu bumps the soversion
to `.so.1`, the runtime is rebuilt with the new soname (it's literally
one string in `_gtk_ffi.py`).

**Risk 6: NFR-2 size budget overrun.**

641 KB cli + ~15 KB of `picolet_ui` Python + 0 KB of native GTK code
(dlopen at runtime) = ~660 KB. NFR-2 ceiling is 2 MiB = 2 097 152
bytes. Headroom > 1.4 MB. No risk in practice; called out so the gate-4
check is explicit.

Mitigation: build-runtime.sh's finish_artifact already runs a size
check; the NFR-2 budget is set per variant in the mpconfigvariant.mk
file (`PICOLET_NFR_CEILING ?= 2097152` for the webview variant). The
size gate fails the build if exceeded.

**Risk 7: The runtime needs to read `[window]` from `/rom/picolet.toml`,
but PH02's `picolet.toml` validator doesn't currently dictate that the
file is in the romfs.**

PH07 takes the small change in `picolet-cli/build_cmd.py` to copy a
sanitised `picolet.toml` (just `[ui]` and `[window]`) into the romfs
root when the renderer is webview. Users do NOT need to add
`picolet.toml` to `[romfs] include` themselves. The full `picolet.toml`
is not embedded — only the user-facing window-config subset, sanitised
of any host-only sections like `[build]`.

Mitigation: `_toml.py` is a minimal parser, so even if the user adds
the full picolet.toml to `[romfs] include` for some reason, the runtime
only reads the keys it cares about and tolerates the rest.

**Risk 8: `xvfb-run` flakiness in CI.**

`xvfb-run` occasionally fails to find a free display number on busy
CI hosts, producing `Cannot establish any listening sockets`.

Mitigation: pass `-a` (auto-pick display) and a wide port range. If
still flaky in PH15 CI, switch to explicit `Xvfb :99 &` setup with a
known-free display number. PH07's tests use `-a` by default.

**Risk 9: PH06 carryover — the generator slip-through defect could
make a webview-side handler hang the entire app.**

PH06's verification flagged that decorating a non-async function with
`@picolet.command` can corrupt the IPC channel by dumping a Python
traceback to stdout. In PH07's webview transport the "channel" is
postMessage, not stdout — a traceback going to stdout is harmless
(it's stderr-bound logs, not the IPC wire). However, the underlying
defect (the dispatcher's per-request wrapper not catching all
exception classes cleanly) could still hang the asyncio loop or wedge
the GTK pump task.

Mitigation: NOT fixing the PH06 defect inside PH07. The PH06
verification recommended the fix for scrum-po follow-up before
user-facing release; PH07 inherits the carryover. If gate 8's callback
probe surfaces the defect (it shouldn't — the test uses a correct
async handler), record it as a re-raised carryover and continue.

**Risk 10: PH06 carryover — hollow tests precedent.**

PH06's verification flagged two hollow unit tests that pass without
assertions. PH07 must NOT replicate this pattern. Every test under
`tests/phase-07/` must end with at least one `assert` and ideally a
clear "the thing we are testing failed if you see this" stderr
message before the assertion fires.

Mitigation: explicit in each test file's docstring; the SQE role will
also audit. The unit tests' shape is "drive the mock backend, then
assert specific observable state".

**Risk 11: PH06 carryover — Windows stdio single-in-flight.**

PH06's `StdioTransport` falls back to blocking readline on Windows
because `select.poll` is unavailable there. This does not affect PH07
(webview transport is its own thing); flagged here only so the
developer doesn't mistakenly inherit the blocking-readline pattern in
the new `WebviewTransport`. The new transport is fully async-aware
because it sits on `asyncio.Event`, not on a raw fd.

### Out of scope for PH07

- The JS-side bridge (`window.picolet.invoke / on / emit`) — that's
  **PH08**. PH07 only verifies that the C->Python callback fires and
  that the JS-side `window.webkit.messageHandlers.picolet.postMessage(json)`
  reaches Python. The shape of the JSON is the PH06 wire format.
- The end-to-end `hello-webview` template (button click → invoke →
  Python handler → result back to JS) — that's **PH09**.
- Windows webview via WebView2 — that's **PH10**.
- LVGL renderer — that's **PH11**/PH12 (parallel branch).
- SBOM for the webview variant — that's **PH13**. PH07 will record the
  new dynamic dependencies (libwebkit2gtk-4.1-0, libgtk-3-0,
  libgobject-2.0-0, libjavascriptcoregtk-4.1-0) in a comment block at
  the top of `_gtk_ffi.py` so PH13's runtime.toml has a clear source.
- CI release pipeline matrix entry for the webview variant — that's
  **PH15**.
- Live reload of the webview on UI-asset change — that's **PH16** (a
  property of `picolet dev`, not the runtime).
- Native OS dark-mode integration, window icon, taskbar grouping — not
  in v1 (architecture.md Out of scope).
- Multi-window apps — v1 is single-window per process (architecture.md
  D4 implies). PH07 hard-codes single-window; a multi-window helper
  is post-v1.
- Drag-and-drop, clipboard, native menus — not in v1.
- Network access from the webview (XHR / fetch to arbitrary URLs) —
  not in v1's threat model. WebKit's default policy allows file:// and
  http:// loads from the page; picolet does not restrict this in PH07.
  A future hardening phase (post-v1) may.

### Spec traceability

| Spec id | Where closed in PH07 |
|---|---|
| FR-WV-1 | `picolet_ui/_gtk_ffi.py` literally `ffi.open("libwebkit2gtk-4.1.so.0")` — the only WebKit contact point. Confirmed at runtime by gate 7. No alternative renderer is reachable from `picolet_ui`. |
| FR-WV-2 | `picolet_ui/_webview.py` constructs the URI as `"file:///rom/" + ui_root + "/" + ui_index` where `ui_root` defaults to `"ui"` and `ui_index` defaults to `"index.html"`, sourced from the runtime's parse of `/rom/picolet.toml`'s `[ui]` table. Gate 5 verifies a real `picolet build`-produced binary loads the embedded index.html. |
| FR-WV-3 | `picolet_ui/_window.py` reads `[window]` from `/rom/picolet.toml` via `_toml.py` and applies `title`, `size[0]`, `size[1]`, `resizable` to the GtkWindow. Gate 6 verifies the title and size are applied exactly. |
| FR-RT-2 (webview half) | `build-runtime.sh --target linux-x64 --variant webview` produces `picolet-runtime-linux-x64-webview` — the second of the three runtime variants per target. Gate 2. |
| NFR-2 | Gate 4. Binary size ≤ 2 MiB confirmed. Headroom > 1.4 MB given the pure-Python path. |
| NFR-8 | Gate 14. Fresh `ubuntu:22.04` + `apt install libwebkit2gtk-4.1-0` runs the artifact. No other apt packages required for runtime. `xvfb` is a test-only dependency, not a runtime one (NFR-8 carve-out). |
| FR-WV-4 | **Out of scope** — PH08. PH07 leaves a no-op `window.__picolet_recv` injected so PH08's bridge-js can wire over it without conflict. |
| FR-WV-5 | **Out of scope** — PH08. |
| FR-RT-2 (windows webview half) | **Out of scope** — PH10. |
| FR-RT-2 (lvgl variants) | **Out of scope** — PH11/PH12. |
| NFR-9 | **Out of scope** — PH10 (Windows). |

## Notes for downstream phases

**PH08 (bridge-js).** The script-message handler name is `"picolet"`.
The JS side calls `window.webkit.messageHandlers.picolet.postMessage(jsonString)`.
The Python side calls `window.__picolet_recv(jsonString)` to push to
JS. PH08 wires the user-facing `window.picolet.invoke / on / emit`
abstractions on top of these primitives. PH07 stubs `__picolet_recv`
with a no-op so PH08 can replace it cleanly.

**PH09 (e2e webview).** With PH07 + PH08 landed, the `hello-webview`
template's button click invokes a Python `@picolet.command` and the
result returns to JS. PH07's `WebviewTransport` does the entire
heavy lifting; PH09 is mostly a template + a tester driver.

**PH10 (Windows webview).** Windows uses WebView2, not WebKitGTK.
PH10 may or may not stay in pure Python — WebView2's API is COM, which
is harder over libffi than plain C. PH10's planner will revisit
D1 explicitly. The `WebviewTransport` contract is the renderer-agnostic
piece that *does* carry forward; the JS shape (`window.chrome.webview.postMessage`)
differs from WebKit's, but PH08's bridge-js abstracts that.

**PH11 (LVGL).** Reuses `picolet_ui.Window` (or a parallel
`picolet_ui.LvglDisplay`) — the renderer-agnostic window concept lands
in PH07 and is re-used.

**PH13 (SBOM).** New dynamic dependencies introduced in PH07:
- `libwebkit2gtk-4.1-0` (LGPL-2.1+, dlopen; NFR-5 OK)
- `libgtk-3-0` (LGPL-2.1+, dlopen; NFR-5 OK)
- `libgobject-2.0-0` (LGPL-2.1+, dlopen; NFR-5 OK)
- `libjavascriptcoregtk-4.1-0` (LGPL-2.1+, dlopen; NFR-5 OK)

These are listed in a header comment at the top of `_gtk_ffi.py` so
PH13's `runtime.toml` author has a single source.

## Implementation

(scrum-developer writes here, with file:line references for each
change)

## Tests

(scrum-sqe writes here)

## Verification

(scrum-tester writes Pass/Fail here)

## Blockers

(only if the phase cannot complete as planned)
