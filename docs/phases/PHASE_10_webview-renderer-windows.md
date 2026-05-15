# PH10 — Webview renderer on Windows (WebView2)

## Plan

### Goal (restated)

Land the Windows half of the webview pipeline. Produce a
`picolet-runtime-windows-x64-webview.exe` runtime variant that:

1. Opens a native Win32 top-level window whose title and size come from
   `[window]` in `picolet.toml`.
2. Hosts an Edge-Chromium WebView2 control inside that window.
3. Loads the application's root document from `/rom/<ui.root>/<index>`
   (default `index.html`) the same way the Linux runtime does — by
   reading the HTML through the MicroPython VFS and handing it to
   WebView2 with a synthetic base URI, because `file:///rom/...` is not
   visible to the OS-level URL loader.
4. Injects the PH08 `picolet-bridge-js` IIFE at document-creation time so
   `window.picolet.{invoke, on, emit}` is available before any user
   `<script>` runs.
5. Round-trips IPC: `window.chrome.webview.postMessage(jsonString)` →
   Python `WebviewTransport.recv()` → dispatcher → handler → reply →
   `ExecuteScript("window.__picolet_recv(...)")` → JS Promise resolves.
6. Passes the same end-to-end sentinel-token harness PH09 uses, driven
   under WSL interop against the Windows host's pre-installed Edge
   WebView2 Runtime.

Spec requirements closed by this phase:

| Spec id | Requirement |
|---|---|
| FR-WV-1 (Windows half) | On Windows the webview is WebView2 (Edge Chromium). |
| FR-WV-2 | Webview loads `/rom/<ui.root>/<index>`; default index `index.html`. (Windows) |
| FR-WV-3 | Window title and size from `[window]` in `picolet.toml`. (Windows) |
| FR-WV-4 | The `picolet-bridge-js` script is injected before any user frontend JS runs. (Windows) |
| FR-WV-5 | The bridge exposes `window.picolet.invoke(cmd, args) → Promise<result>` and `window.picolet.on(event, handler) → unsubscribe`. (Windows) |
| FR-RT-2 (windows-x64 webview half) | Three runtime variants per target; webview is one. PH10 closes the windows-x64 half. |
| NFR-2 | `picolet-runtime-windows-x64-webview.exe` ≤ 2 MB (excluding system webview). |
| NFR-9 | Windows artifacts run on Windows 10 21H2+ with Edge WebView2 Runtime present. |

The PH08 bridge JS, the `WebviewTransport` Python class, the dispatcher,
and the `picolet build` webview pipeline are reused unchanged in shape;
the only platform-specific surfaces are:

- A Python sibling package `picolet_ui_win` (parallel to PH07's
  `picolet_ui`) that supplies the Windows window + webview + pump.
- A small C overlay `overlay/modules/picolet_webview2/` that exposes the
  COM async dance and the JS-bridge wiring as a plain-C API, called
  from `picolet_ui_win` via libffi against the running .exe's exports.
- A one-line runtime probe in `picolet-bridge.js` to detect WebView2 vs
  WebKitGTK and select the correct outbound `postMessage` channel.

PH10 does **not** introduce a new IPC wire format, a new transport
contract, a new dispatcher, a new bridge API, or a new build pipeline.
The romfs trailer, the runtime resolver, `picolet build --target
windows-x64`, the bridge bundle copy, and the sanitised `picolet.toml`
emission all already work for the windows-x64 target as of PH04 +
PH08; PH10 only adds the renderer.

### Architectural decisions

These are the load-bearing choices. They're spelled out in full so the
developer doesn't re-litigate them at implementation time.

#### AD1 — WebView2 distribution: bundle `WebView2Loader.dll` in the romfs

**Decision.** Ship `Microsoft.Web.WebView2.Core`'s redistributable
`WebView2Loader.dll` (x64) inside the runtime's romfs at
`/rom/picolet/WebView2Loader.dll`. At runtime, extract it to a
process-private temp directory (e.g.
`%LOCALAPPDATA%\picolet\<pid>\WebView2Loader.dll`) and `LoadLibraryW` it
from there.

**Why bundle, not rely on system.** The loader DLL is **not** installed
in `C:\Windows\System32`; it ships only with the WebView2 SDK or with
applications that redistribute it. Searching `System32` first is
guaranteed to miss on every test host. Letting Windows resolve
`LoadLibraryW("WebView2Loader.dll")` against the default search path is
fragile (depends on `PATH`, current directory, and SafeDllSearchMode).
Bundling makes the load deterministic.

**Why not statically link the loader.** Microsoft's official
redistribution channel is the DLL form (BSD-style 3-clause Microsoft
Software License Terms). The loader's static-link form is not part of
the public SDK; trying to bake it into the .exe is unsupported and
would block on the dockcross MinGW toolchain anyway.

**Why romfs + extract, not direct LoadLibrary from a path.** The romfs
is a MicroPython VFS overlay — `LoadLibraryW` cannot see paths under
`/rom/`. We unpack the DLL bytes from `/rom/picolet/WebView2Loader.dll`
to a real on-disk path (created via `GetTempPathW` +
`GetModuleFileNameW`-derived process-private subdir) at first use,
then load from that path. The unpack is one-shot per process and adds
~150 KB of disk I/O at startup — invisible in user terms.

**Why not use the Edge WebView2 SDK's "Fixed Version" runtime
distribution.** Fixed Version is a full Chromium engine snapshot
(~150 MB). That violates NFR-2 by two orders of magnitude. The
**evergreen** runtime (the system-installed Edge WebView2 Runtime, the
heavy Chromium engine) is what we rely on for the actual rendering;
the loader DLL is just the stub that talks to it. NFR-9 requires that
runtime to be present on the host — Windows 11 ships it by default,
Windows 10 21H2+ pulls it via Microsoft Update.

**License footprint.** `WebView2Loader.dll` is redistributable under
Microsoft's WebView2 SDK License (permissive, BSD-flavoured). Recorded
for PH13's SBOM as `Microsoft-Edge-WebView2-Loader` with the Microsoft
WebView2 SDK license string. NFR-5 (no static GPL/AGPL link) is
unaffected — the loader is loaded dynamically and is not GPL/LGPL in
any case.

**Size impact.** The loader DLL is ~150 KB. The webview .exe itself
is expected to land around 700–800 KB before the loader (parity with
the Linux webview at 666 KB plus ~100 KB of Win32-specific Python and
the C overlay). Including the loader inside the romfs adds ~150 KB.
Total well under NFR-2's 2 MB ceiling.

#### AD2 — COM v-table dispatch: thin C overlay (`picolet_webview2`), not pure libffi

**Decision.** Ship a small native overlay C module
`overlay/modules/picolet_webview2/` that exposes the WebView2 surface as
a flat C API and is **statically linked into the runtime .exe**. Python
calls into it via libffi against the running .exe's own exports
(`ffi.open(NULL)`-equivalent for the current process), the same way
`picolet_ipc`'s helpers are reached today.

**Why a C overlay, contrary to PH07's pure-libffi precedent.** This is
the single largest design departure between PH07 and PH10 and it is
made deliberately:

1. **COM dispatch via v-tables.** WebView2's surface is exclusively
   COM. Every API call is
   `iface->lpVtbl->Method(iface, ...args)` — a pointer chase to a
   per-interface vtable, then a function-pointer call. In Python via
   libffi this needs:
   - reading `*(void **)iface` (the vtable pointer);
   - reading `*(void **)(vtable + N * sizeof(void *))` (the method at
     index N in inheritance order);
   - calling that function pointer with the right calling convention
     and the right argument layout.

   Each method call is **three** runtime steps in Python — and the
   method-index N is not stable across interface inheritance chains
   without per-interface bookkeeping (`QueryInterface`, `AddRef`,
   `Release` always at indices 0/1/2; then the parent interface's
   methods; then the leaf's). The MicroPython unix-port `modffi.c`
   has no native COM helper; we'd build that helper in Python, paying
   for it at every call.

2. **Asynchronous COM callbacks.** `CreateCoreWebView2Environment`,
   `CreateCoreWebView2Controller`, `ExecuteScript`,
   `AddScriptToExecuteOnDocumentCreated`, and `add_WebMessageReceived`
   all complete asynchronously by invoking a caller-supplied **COM
   object** that implements a specific completion-handler interface.
   The handler is itself a struct with a vtable: the caller allocates
   a struct whose first field is `lpVtbl`, fills the vtable with
   `QueryInterface`/`AddRef`/`Release`/`Invoke` function pointers,
   and passes the struct pointer in.

   Constructing such a fake COM object purely in Python is doable
   (allocate the struct via `uctypes`, generate four libffi closures,
   write their function-pointer addresses into the struct). It is
   four libffi closures **per async operation type**, all of which
   need to outlive the operation, all of which need careful refcount
   handling on AddRef/Release. The closure machinery in `modffi.c`
   marshals callback args as `mp_int_t` (PH07 risk 1) — workable for
   pointer-shaped args, but the WebView2 completion handlers also
   take `HRESULT` (int32) and other interface pointers that must be
   AddRef'd inside the callback. Every callback path is a fresh
   memory-management surface to audit.

3. **WebView2 SDK headers exist.** Microsoft ships the WebView2 SDK as
   a NuGet package with C and C++ headers
   (`Microsoft.Web.WebView2.Core.h`, etc.). The headers expose every
   interface and method index symbolically; a C overlay can call
   `env->lpVtbl->CreateCoreWebView2Controller(env, hwnd, handler)`
   directly with the compiler doing the vtable arithmetic. Bundling
   these headers into the dockcross build is mechanical (vendor the
   `Microsoft.Web.WebView2.<ver>/build/native/include/` directory under
   `packages/picolet-runtime/overlay/modules/picolet_webview2/include/`;
   no NuGet at build time, no runtime download).

4. **Async-completion shape simplifies dramatically in C.** The C
   overlay can expose a **synchronous-feeling** API to Python:
   ```c
   // Blocking until env is ready (or timeout); pumps the Win32 message
   // queue from the calling thread.  Returns a Python-opaque env handle.
   void *picolet_wv2_create_environment_blocking(int timeout_ms);
   ```
   Internally it allocates a static handler struct, calls
   `CreateCoreWebView2EnvironmentWithOptions(..., handler)`, then
   spins a `PeekMessageW` + `DispatchMessageW` loop until the
   handler's `Invoke` runs and stores the `ICoreWebView2Environment *`
   in a shared variable, or until the timeout expires. Python sees
   one libffi call returning a pointer.

   The same shape covers controller creation. For
   `WebMessageReceived` (the inbound JS → Python channel) we register
   a handler in C that pushes the received JSON string into a
   ring buffer; Python polls the ring buffer from the message-pump
   asyncio task. No Python-side libffi closure is in any hot path.

5. **Build-system cost is bounded.** The dockcross
   `windows-static-x64-posix` image ships MinGW-w64 + Windows SDK
   headers (Win32 API). The WebView2 SDK headers are vendored — no
   network at build time, no Visual Studio dependency. The C overlay
   compiles as plain MinGW C (with `__attribute__((stdcall))` on
   exported callback shims as needed; WebView2 uses the standard x64
   calling convention on x64 Windows, no `__stdcall` games needed).

6. **MinGW + WebView2 SDK is a known-good pairing.** Several
   open-source projects (Tauri's `wry`, `webview/webview`) link the
   WebView2 SDK headers under MinGW successfully. We are not
   pioneering this combination. The `webview/webview` reference C++
   header is GitHub-public and a near-direct template for the COM
   dance.

**What we trade.** Pure Python loses one platform — PH07 stays pure
Python on Linux, PH10 has a ~600-line C overlay on Windows. The Python
binding-table surface (`picolet_ui_win/_win_ffi.py`) shrinks to the
half-dozen flat C functions the overlay exports, instead of being a
direct-COM accordion. The runtime size impact of the C overlay is
~20–30 KB compiled; well inside the NFR-2 headroom.

**Decision log entry** (developer commits this as the first PH10
empty commit):

```
[PH10] Decision: thin C overlay for WebView2 COM; pure-libffi inappropriate.

WebView2's surface is COM v-table dispatch + async completion handlers
implemented as caller-supplied COM objects.  Each method call is a
pointer chase + indirect function call.  Each async handler is a
struct with a four-method vtable whose Invoke is the caller's
callback.

PH07's pure-libffi binding works on Linux because GTK/WebKitGTK is plain
C ABI with one-deep function tables, one signal connect call, and one
libffi closure for the inbound postMessage handler.  WebView2 needs
four libffi closures per async handler (QI/AddRef/Release/Invoke),
careful refcount discipline inside each, and per-call vtable arithmetic
on every method invocation.  The accumulated complexity makes pure-Python
COM brittle and slow.

PH10 ships overlay/modules/picolet_webview2/ as a static C module
compiled into the windows-x64 webview runtime.  It exposes the COM
dance as five-or-so plain C functions (create_environment_blocking,
create_controller_blocking, navigate, execute_script, set_inbound_handler,
poll_inbound).  Python calls these via libffi against the running .exe's
own exports.  No SDK headers at app-build time; we vendor the WebView2
SDK headers under overlay/modules/picolet_webview2/include/.

Size cost: ~25 KB.  NFR-2 has 1.4+ MB of headroom (PH07 webview lands
at 666 KB / 32% of ceiling); ample.

WebView2 SDK loader DLL is bundled into romfs and extracted at startup
(AD1); no static link, no GPL/LGPL contact, NFR-5 honoured.
```

#### AD3 — Async COM callbacks: handler structs in C with two-phase completion

**Decision.** All COM completion handlers (the `*CompletedHandler`
families) are implemented in C inside `picolet_webview2`. Each handler is
a static struct with a vtable of `QueryInterface` / `AddRef` /
`Release` / `Invoke`. Two flavours:

1. **One-shot completion handlers** (environment, controller, execute
   script, get cookies, etc.). These store the result in a small
   per-call state struct and set a Win32 event
   (`SetEvent(hCompletion)`). The picolet C side spins a
   `PeekMessageW` + `MsgWaitForMultipleObjects` loop until the event
   signals or a timeout expires.

2. **Persistent event handlers** (`WebMessageReceived`,
   `NavigationCompleted`). These are registered once and remain active
   for the controller's lifetime. The `Invoke` for a message-received
   event copies the inbound JSON `wstring` into a malloc'd UTF-8 buffer
   and pushes it onto a single-producer single-consumer ring buffer.
   Python polls the ring buffer from the message-pump asyncio task.

**Why a ring buffer, not a direct callback into Python.** WebView2
delivers its events from the same thread that called `Create*Controller`
(the UI thread, which in our case is the asyncio thread per AD4
below). The handler runs synchronously inside our message pump. We
could call back into Python directly from C. But that would mean
crossing the libffi boundary inside the message loop on every JS
postMessage. The ring buffer pattern moves that crossing to the
asyncio side where Python is already running.

**Why two-phase completion (event + poll) for one-shots.** Because the
C side runs the message pump itself, the completion handler runs in
the same call stack as the `Peek/Dispatch` that triggered it. We
can't `return` from the dispatch and check the result in the same C
function; we use a Win32 `HANDLE hCompletion = CreateEventW(...)` that
the handler signals from inside `Invoke`. The blocking helper waits on
that event with `MsgWaitForMultipleObjects(1, &hCompletion,
QS_ALLINPUT)` — yielding to the message pump until the event signals
or a deadline passes.

**HRESULT propagation.** Each one-shot handler captures the inbound
`HRESULT` into the state struct alongside the result pointer. The
blocking helper returns a non-zero error code to Python on COM failure;
Python raises a `RuntimeError` with the `HRESULT` formatted as
`0x%08x`. The error path is exercised by gate 7.

**Why not asyncio futures.** MicroPython's asyncio has futures, but
they're awaited from a Python coroutine. The COM dance runs in C,
under the Win32 message pump, **inside** an asyncio task tick. From C
we cannot resume a Python coroutine; the cleanest shape is to
present a blocking C call to Python and let Python wrap it in
`asyncio.to_thread`-style logic only if needed. PH10 deliberately
keeps the environment-creation and controller-creation calls
synchronous: they happen once at startup before the asyncio loop is
running.

#### AD4 — Win32 windowing: plain libffi + same-thread pump (Option C)

**Decision.** The runtime opens its top-level window via
`user32.dll`'s `RegisterClassExW` / `CreateWindowExW`. The Win32
message loop is **not** `GetMessageW`/`DispatchMessageW` in blocking
form (which would never return); instead, it is pumped from an asyncio
task using `PeekMessageW(PM_REMOVE)` + `TranslateMessage` +
`DispatchMessageW` at the same 5 ms tick PH07 uses for GTK. This is
identical in shape to PH07's `_gtk_pump` — Option C from PH07's D2 —
just with Win32 calls in place of GTK calls.

**Why this is uncontroversial.**

- `user32.dll` is plain C ABI; libffi handles it trivially.
- Window class registration is a one-time call.
- The `WindowProc` callback for `WM_*` handling is the only place we
  need a libffi closure on the Win32 side. The closure handles
  `WM_DESTROY` → `PostQuitMessage`, `WM_SIZE` → forward to the
  WebView2 controller, and falls through to `DefWindowProcW` for the
  rest. The closure is constructed via `ffi.callback("L", proc,
  "PIIL", lock=False)` (LRESULT return, HWND/UINT/WPARAM/LPARAM
  args).
- WebView2's controller is parented to the HWND via
  `controller->put_Bounds(rect)` and `controller->put_IsVisible(TRUE)`.
  The controller takes care of its own child-window creation; we just
  need to forward `WM_SIZE` so it resizes with the parent.

**Why same-thread (Option C) not worker-thread (Option B).** Same
reasoning as PH07 D2: single-threaded asyncio + Win32 message pump on
the same thread eliminates marshalling. WebView2 is single-threaded
COM affinity (STA — Single-Threaded Apartment); all WebView2 calls
must come from the thread that initialised it
(`CoInitializeEx(STA)`). The asyncio thread is that thread.

The 5 ms tick is the same starting point as PH07 (200 message-pumps
per second). Tunable via `picolet_ui_win.PUMP_INTERVAL_S`.

#### AD5 — JS bridge update: detect host channel at runtime

**Decision.** Update `packages/picolet-bridge-js/src/index.ts`'s `_send`
helper to detect at runtime which postMessage channel the host
provides, and dispatch to it. Single source-line change of substance;
unit-tested in PH10's harness; bundle re-built and the checked-in
`dist/picolet-bridge.js` updated.

Today:

```ts
function _send(msg) {
  const json = JSON.stringify(msg);
  (window as any).webkit.messageHandlers.picolet.postMessage(json);
}
```

After:

```ts
function _send(msg) {
  const json = JSON.stringify(msg);
  const w: any = window;
  if (w.webkit && w.webkit.messageHandlers && w.webkit.messageHandlers.picolet) {
    // WebKitGTK (Linux)
    w.webkit.messageHandlers.picolet.postMessage(json);
  } else if (w.chrome && w.chrome.webview && typeof w.chrome.webview.postMessage === "function") {
    // WebView2 (Windows)
    w.chrome.webview.postMessage(json);
  } else {
    throw new Error("[picolet] no host postMessage channel available");
  }
}
```

**Why feature-detect, not compile two bundles.** A single bundle that
ships on both platforms is simpler to distribute (the romfs copy step
in `picolet build` doesn't need to know the target platform), simpler to
diff in CI, and the cost of the runtime detection is one extra
property lookup per outbound message — negligible.

**Why not branch on UA string.** Both channels expose globals; checking
for the global is reliable, the UA string isn't (Edge's UA can be
overridden, and we'd be sniffing rather than feature-testing).

**Direction of the inbound channel.** `window.__picolet_recv` is unchanged
between platforms — both runtimes call it via the platform's
"evaluate-script" API. PH08 already defines it; PH10 does not touch
it.

#### AD6 — WSL interop test harness: self-driving JS + sentinel-token stdout

**Decision.** PH10's integration tests run the produced `.exe`
directly under WSL interop (`./build/foo.exe`) — stdout returns to
the WSL shell — exactly the path PH04 established for the cli
variant. The driver script greps stdout for sentinel tokens
(`PICOLET_PH10_INVOKE_OK`, `PICOLET_PH10_ERROR_OK`, `PICOLET_PH10_EVENT_OK`,
parallels of PH09's PH09-suffixed tokens). The window appears briefly
on the Windows host's desktop and then the app self-terminates after
emitting its tokens.

**Why this works under WSL interop.**

- WSL2 forwards stdin/stdout/stderr from a Windows `.exe` invocation
  back to the WSL shell verbatim. PH04's cli tests already rely on
  this.
- The Edge WebView2 Runtime is system-installed on the Windows host
  (Windows 11 ships it; the WSL2 host invariably has it). The
  runtime's `WebView2Loader.dll` is bundled in the romfs (AD1) so
  the loader DLL is always available.
- Windows desktop display is owned by the Windows host, not by
  WSL — the window flashes onto the Windows desktop, the test
  fixture's JS auto-runs on page-load, posts results back through
  the bridge, Python prints the sentinel tokens, and the process
  exits. Total wall-clock: under 5 seconds in practice.
- No Xvfb equivalent needed because the Windows host has a real
  display.

**WebView2 has no headless mode.** This is a known WebView2 limitation
and the reason PH10 cannot mimic PH07's `xvfb-run -a` purely-headless
shape. We accept a visible-briefly window in the test harness. The
fixture sets the window title to something like `PH10-TEST` so a
human watching the screen sees a small flash that self-terminates.

**Fallback when WebView2 Runtime is missing.** If the host doesn't have
the runtime, `CreateCoreWebView2EnvironmentWithOptions` returns
`HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)` or a similar diagnostic.
The C overlay maps this to a clear Python error: `RuntimeError("Edge
WebView2 Runtime not installed; install from
https://developer.microsoft.com/microsoft-edge/webview2/")`. Gate 16
verifies the message is printed (it's an opt-in negative test —
typically skipped because the runtime IS present on most hosts).

**Test fixture self-termination pattern.** Same shape as PH09:

```js
// In the test fixture's app.js, runs on page load.
(async () => {
  const greet = await window.picolet.invoke("greet", { name: "World" });
  console.log("greet returned:", greet);  // forwarded to Python via emit
  window.picolet.emit("result", { greet });
  // ... error / event tests ...
  window.picolet.emit("done", {});
})();
```

```python
# In the fixture's main.py.
@picolet.command
async def greet(args):
    return "Hello, " + args["name"]

async def watcher():
    # Wait for the "done" event from the page, with timeout.
    done = asyncio.Event()
    def on_done(_):
        print("PICOLET_PH10_INVOKE_OK")
        print("PICOLET_PH10_EVENT_OK")
        done.set()
    picolet.on("done", on_done)
    await asyncio.wait_for(done.wait(), 15)
    sys.exit(0)
```

The driver script `tests/phase-10/run.sh` invokes the .exe and asserts
all expected tokens appear in stdout:

```bash
timeout 20 ./build/hello-webview-min-e2e.exe > "$LOG" 2>&1 || true
grep -q PICOLET_PH10_INVOKE_OK "$LOG" || fail "no invoke ok"
grep -q PICOLET_PH10_ERROR_OK  "$LOG" || fail "no error ok"
grep -q PICOLET_PH10_EVENT_OK  "$LOG" || fail "no event ok"
```

### Architecture

```
                ┌────────────────────────────────────────────────────────┐
                │   picolet-runtime-windows-x64-webview.exe                │
                │   (single PE-COFF, ≤ 2 MB, frozen MicroPython)         │
                └────────────────────────────────────────────────────────┘
                          │                              │
                          │ asyncio task                 │ asyncio task
                          │ (picolet dispatcher)           │ (picolet_ui_win._loop._win_pump)
                          ▼                              ▼
              ┌─────────────────────────┐  ┌──────────────────────────────────┐
              │  picolet._dispatcher      │  │  PeekMessageW(PM_REMOVE)         │
              │  awaiting transport     │  │  TranslateMessage / DispatchMsg  │
              │  .recv()                │  │  drains Win32 message queue      │
              │                         │  │  → WebView2 handlers run on this │
              │                         │  │     stack (STA affinity).        │
              └─────────────────────────┘  └──────────────────────────────────┘
                          ▲                              │
                          │ Event.set()                  │ each tick:
                          │                              │  picolet_wv2_poll_inbound()
                          │                              │  → returns one JSON string
                          │                              │     or NULL.
                          │                              ▼
              ┌─────────────────────────┐  ┌──────────────────────────────────┐
              │  WebviewTransport       │◀─│  poll_inbound loop in            │
              │  _inbox = [json,...]    │  │  picolet_ui_win._webview2.poll()   │
              │  _recv_event            │  │  drains the ring buffer C-side   │
              └─────────────────────────┘  │  populates Python transport.     │
                                            └──────────────────────────────────┘
                                                            ▲
                                                            │ pushed C-side by
                                                            │ WebMessageReceived
                                                            │ handler Invoke
                                                            │ (C function in
                                                            │ overlay/modules/
                                                            │ picolet_webview2/)
                                            ┌──────────────────────────────────┐
                                            │  ICoreWebView2 controller +      │
                                            │  WebView2 (system-installed      │
                                            │  Edge Chromium engine)           │
                                            │  loads HTML via NavigateToString │
                                            └──────────────────────────────────┘
                                                            ▲
                                                            │ window.chrome.webview
                                                            │ .postMessage(json)
                                            ┌──────────────────────────────────┐
                                            │  index.html + picolet-bridge.js    │
                                            │  (injected at                    │
                                            │  AddScriptToExecuteOnDocument-   │
                                            │  Created — runs before user JS)  │
                                            └──────────────────────────────────┘

Outbound (Python → JS):

  WebviewTransport.send(msg)
    → json.dumps(msg)
    → picolet_ui_win._webview2.execute_script("window.__picolet_recv(" + json + ")")
    → picolet_wv2_execute_script(view_p, wide_str)   [C overlay]
    → wv2->lpVtbl->ExecuteScript(wv2, wide_str, NULL_handler)
    → WebView2 dispatches into the Chromium renderer
    → window.__picolet_recv runs in JS → resolves the pending invoke promise.
```

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` exits 0 (no regression of PH00–PH09). | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0. |
| 2 | `build-runtime.sh --target windows-x64 --variant webview` exits 0. **FR-RT-2 (windows half).** | Build succeeds inside `dockcross/windows-static-x64-posix`. Artifact at `packages/picolet-runtime/build/picolet-runtime-windows-x64-webview.exe`. |
| 3 | `import picolet_ui_win` succeeds in the runtime; no window created. | `./build/picolet-runtime-windows-x64-webview.exe -c 'import picolet_ui_win; print("picolet_ui_win-ok")'` via WSL interop → `picolet_ui_win-ok` on stdout. The import does NOT trigger `CoInitialize` or `LoadLibrary("WebView2Loader.dll")`. |
| 4 | NFR-2 size gate. | `wc -c build/picolet-runtime-windows-x64-webview.exe` → ≤ 2 097 152 bytes (2 MiB). Print actual size + % of ceiling. |
| 5 | **FR-WV-2 (Windows)**: webview loads `/rom/<ui.root>/index.html` from the romfs. | `picolet build --target windows-x64` against `tests/phase-10/fixtures/hello-webview-min/` (with `[ui] renderer="webview", root="ui"` and `ui/index.html` that sets `document.title = 'LOADED'` and posts a `loaded` event). Run under WSL interop with timeout 15; assert stdout shows `PICOLET_PH10_LOAD_OK title=LOADED`. |
| 6 | **FR-WV-3 (Windows)**: window title and size from `[window]`. | Same fixture with `[window] title="PH10 Sanity" size=[640,480] resizable=false`. Runtime emits `window: title=PH10 Sanity size=640x480 resizable=False` on stderr. Driver greps for the exact line. |
| 7 | **FR-WV-1 (Windows)**: the linked library is WebView2. Verification: (a) the embedded WebView2Loader.dll is the bundled one; (b) the runtime imports nothing webview-related from system DLLs except via `LoadLibraryW` at runtime. | Static check via PE imports: `objdump -p build/picolet-runtime-windows-x64-webview.exe \| grep "DLL Name"` lists only `KERNEL32.dll`, `USER32.dll`, `bcrypt.dll`, `msvcrt.dll`, `ws2_32.dll`. No `WebView2Loader.dll` static import. The string `WebView2Loader.dll` is present in the .rodata segment as the `LoadLibraryW` argument. The bundled DLL is reachable via `python -c "import io,os; ..."` reading `/rom/picolet/WebView2Loader.dll`. |
| 8 | **FR-WV-4 (Windows)**: bridge JS injected before user JS runs. | Fixture `tests/phase-10/fixtures/bridge-inject-order/ui/index.html` has an inline `<script>` that asserts `typeof window.picolet === "object"` and posts `{event:"ready"}` (or `{event:"missing"}`) immediately. Driver asserts `PICOLET_PH10_BRIDGE_INJECT_OK` appears in stdout. The `AddScriptToExecuteOnDocumentCreated` call happens before the first `NavigateToString`. |
| 9 | **FR-WV-5 (Windows)**: `window.picolet.invoke / on / emit` work end to end through WebView2. | Fixture `tests/phase-10/fixtures/invoke-roundtrip/` — JS calls `await window.picolet.invoke("greet", {name:"World"})`, posts result back via `picolet.emit`. Python prints `PICOLET_PH10_INVOKE_OK` and exits 0. |
| 10 | FR-IPC-2 across the wire: Python error propagates with `name` + `message` preserved. | Fixture `tests/phase-10/fixtures/error-prop/` — Python `@picolet.command` raises `ValueError`; JS `.catch(err)` posts `err.name`/`err.message` back; Python asserts and prints `PICOLET_PH10_ERROR_OK`. |
| 11 | FR-IPC-3 across the wire: Python `picolet.emit` reaches JS `picolet.on`. | Fixture `tests/phase-10/fixtures/event-push/` — Python emits `tick`, JS handler echoes back; Python prints `PICOLET_PH10_EVENT_OK`. |
| 12 | The PH08 bridge bundle dispatches to `window.chrome.webview.postMessage` on Windows. | Unit test `tests/phase-10/test_bridge_channel_detect.js` — sets only `window.chrome.webview` (no `window.webkit`), sources the rebuilt bundle, invokes `window.picolet.emit("x", null)`, asserts the captured posted JSON. Exit 0. |
| 13 | The PH08 bridge bundle still dispatches to `window.webkit.messageHandlers.picolet.postMessage` on Linux. Regression. | Unit test `tests/phase-10/test_bridge_channel_legacy.js` — sets only `window.webkit.messageHandlers.picolet`, posts emit, asserts. Exit 0. |
| 14 | PE-COFF appended-romfs trailer detection works for the webview .exe (same code path PH04 ships). | Build a fixture .exe via `picolet build`; assert `/rom/picolet.toml`, `/rom/ui/index.html`, `/rom/picolet/picolet-bridge.js`, `/rom/picolet/WebView2Loader.dll` all readable from inside the runtime via `os.listdir('/rom')`. Tested via `-c "import os; print(sorted(os.listdir('/rom')))"`. |
| 15 | `picolet_ui_win` doesn't break PH07's Linux-side import. | `./build/picolet-runtime-linux-x64-webview -c "import picolet_ui_win"` errors cleanly with `ImportError` (because `picolet_ui_win` is windows-only; manifest_webview.py freezes only `picolet_ui` on linux). Symmetric: `picolet_ui` imports cleanly on windows-x64/webview only if explicitly added to its manifest — which PH10 must NOT do. |
| 16 | (Opt-in negative) WebView2 Runtime missing → clean Python error. | Skip-unless-host-allows. If a developer host without Edge WebView2 Runtime is available, run the fixture; assert stderr contains `Edge WebView2 Runtime not installed`. Otherwise mark `SKIP: requires host without WebView2 Runtime`. |
| 17 | Idempotent warm rebuild ≤ 60 s. | Second invocation of gate-2's `build-runtime.sh` completes in less than the cold-build time; no new compile units. |
| 18 | PH09's Linux gate suite still passes — no regression from the bridge JS rebuild. | `bash tests/phase-09/run.sh` → green. |
| 19 | PH04's Windows cli gate suite still passes. | `bash tests/phase-04/run.sh` → green. |
| 20 | `picolet build` against the `hello-webview` template (PH09's) for `--target windows-x64` produces a runnable .exe. | Same template PH09 ships. `picolet build --target windows-x64` from the template's directory; the produced `.exe` runs under WSL interop, opens a window, and `window.picolet` is present (the template's button click then talks to Python — same JS code as PH09). |

Gates 2, 5, 6, 7 close FR-WV-{1 Windows, 2, 3}. Gates 8, 9 close
FR-WV-{4, 5} on Windows. Gates 10, 11 are the IPC-cross-wire gates
referenced by FR-IPC-{2,3} per spec — already covered by the dispatcher
layer but the cross-wire verification is the PH10 contribution. Gate 4
closes NFR-2. Gate 7 implicitly closes NFR-9: a runtime that loads
WebView2 only via the dynamic `WebView2Loader.dll` path will refuse to
start cleanly on a host without the Edge WebView2 Runtime — the
runtime present is exactly what NFR-9 specifies. Gates 12, 13 close
the bridge-JS feature-detect change. Gate 14 confirms the PE-COFF
appended-data path works for webview as it does for cli (FR-BP-5
non-regression). Gates 15, 18, 19 are non-regression. Gate 20 closes
the end-to-end template path for the windows-x64 target.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-WV-{1,2,3,4,5}, FR-RT-2, FR-IPC-{2,3}, NFR-2, NFR-9 normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH10 | Phase scope, deliverables, exit gate, model tiers. |
| `/home/anl/picolet/docs/architecture.md` §"IPC wire format" | Wire format used verbatim by the bridge and dispatcher; unchanged. |
| `/home/anl/picolet/CLAUDE.md` | Branch / commit / signing / dev-log conventions. |
| `/home/anl/picolet/docs/phases/PHASE_04_picolet-runtime-windows-x64-cli.md` | Windows-x64 build pipeline pattern; dockcross MinGW story; PE-COFF trailer detection; the WSL interop test path. |
| `/home/anl/picolet/docs/phases/PHASE_07_webview-renderer-linux.md` | Mirror reference. The window/webview/transport split, the same-thread pump (Option C), the JSC postMessage handler shape, the no-op `__picolet_recv` stub pattern — all reused with Windows substitutions. |
| `/home/anl/picolet/docs/phases/PHASE_08_picolet-bridge-js.md` | The bridge JS API surface, IIFE shape, esbuild build, `dist/picolet-bridge.js` injection at DOCUMENT_START. PH10 updates the `_send` helper only. |
| `/home/anl/picolet/docs/phases/PHASE_09_end-to-end-webview-linux.md` | The end-to-end template + harness pattern that PH10 mirrors on the Windows side (sentinel tokens, self-driving JS, integration-fixture-vs-template separation). |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli/mpconfigvariant.h` | The lean Windows variant to fork. The webview variant inherits the macro set; only the manifest reference changes. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli/mpconfigvariant.mk` | The .mk to fork; only `FROZEN_MANIFEST` differs. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/windows/vfs_rom_ioctl.c` | The trailer-detection mechanic; reused unchanged for the webview variant. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet_ui/_webview.py` | The Linux WebviewTransport shape that the Windows port must satisfy duck-typed. The handler/closure plumbing is the reference for `picolet_ui_win` (with COM substituted for GTK signals). |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet_ui/_loop.py` | The pump-task pattern. Windows version mirrors structurally; `gtk_main_iteration_do` → `PeekMessageW`/`DispatchMessageW`. |
| `/home/anl/picolet/packages/picolet-runtime/python/picolet_ui/_app.py` | The Application factory; PH10 ships a parallel `picolet_ui_win.Application` with the same surface. The romfs-HTML read-and-NavigateToString trick replaces the WebKit `load_html` call. |
| `/home/anl/picolet/packages/picolet-bridge-js/src/index.ts` | The `_send` helper to be modified. Existing test set under `tests/phase-08/` to be extended with channel-detect cases. |
| `/home/anl/picolet/packages/picolet-bridge-js/dist/picolet-bridge.js` | The compiled bundle to be rebuilt. CI checks it for drift. |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | The build pipeline that needs to also copy `WebView2Loader.dll` into the webview-variant romfs at `/rom/picolet/WebView2Loader.dll` (one extra small helper, parallel to `_copy_bridge_js`). |
| `/home/anl/picolet/packages/picolet-runtime/scripts/build-runtime.sh` | Lines 95–97 contain the `windows-x64/webview` PH10 stub error. PH10 replaces that stub with a real branch reusing `build_windows_x64` with `VARIANT=webview`. |
| `/home/anl/picolet/packages/picolet-runtime/manifests/manifest_webview.py` | The current Linux manifest. PH10 introduces `manifest_webview.py`'s windows-aware split (one manifest that conditionally freezes `picolet_ui` for unix and `picolet_ui_win` for windows). Implementation detail: simplest path is a single manifest with a conditional `freeze()` based on the port being built; the mbm manifest DSL supports a guard. If conditional freezing is awkward, two manifests (`manifest_webview_unix.py` and `manifest_webview_windows.py`) referenced by each port's variant `.mk` `FROZEN_MANIFEST` is the fallback. |
| `https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution` (consulted at planning time) | WebView2 distribution model: loader DLL redistributable, runtime engine system-installed. Confirms AD1's bundle-loader choice. |
| `https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller` | ICoreWebView2Controller interface methods used by AD2 (`put_Bounds`, `put_IsVisible`, `get_CoreWebView2`). |
| `https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2#add_webmessagereceived` | The inbound message channel surface used by AD3. |

### Deliverables

1. `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/mpconfigvariant.h` — forked from `picolet-cli/mpconfigvariant.h`. Identical macro set; comment header references PH10 and the WebView2 dependency.
2. `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/mpconfigvariant.mk` — forked from `picolet-cli/mpconfigvariant.mk`. Only delta: `FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview_windows.py` (or unified `manifest_webview.py` if conditional freezing is used). Adds the `picolet_webview2` C overlay to `SRC_C` via the standard variant include pattern.
3. `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/romfs_trailer.c` — copy of `picolet-cli/romfs_trailer.c` (variant-independent).
4. `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/romfs_trailer.h` — copy of `picolet-cli/romfs_trailer.h`.
5. `packages/picolet-runtime/overlay/modules/picolet_webview2/` — the C overlay module. Contents:
   - `picolet_webview2.c` — the COM dance. Exports the flat C API: `picolet_wv2_create_environment_blocking`, `picolet_wv2_create_controller_blocking`, `picolet_wv2_navigate_to_string`, `picolet_wv2_add_script_to_execute_on_document_created`, `picolet_wv2_execute_script`, `picolet_wv2_register_inbound_handler`, `picolet_wv2_poll_inbound`, `picolet_wv2_set_bounds`, `picolet_wv2_set_visible`, and the WebView2Loader.dll extract-and-load helper `picolet_wv2_load_loader_dll`.
   - `picolet_webview2.h` — public declarations matched by the libffi-side bindings in Python.
   - `include/WebView2.h` — vendored from Microsoft's WebView2 SDK NuGet (`Microsoft.Web.WebView2.<ver>/build/native/include/`). Licensed under Microsoft WebView2 SDK License (permissive; recorded for PH13).
   - `include/WebView2EnvironmentOptions.h` — same source.
   - `LICENSE.WebView2-SDK` — Microsoft's redistribution license text.
   - `mod_micropython_glue.c` (or appended to `picolet_webview2.c`) — the MicroPython module-registration stub. Single `mp_obj_module_t` named `picolet_webview2_native` registered so the .exe's symbol table exports the flat C functions reachable from libffi via the running-process dlopen.
6. `packages/picolet-runtime/overlay/modules/picolet_webview2/redist/WebView2Loader.x64.dll` — Microsoft's redistributable `WebView2Loader.dll`, x64, vendored at the SDK version pinned in the readme. Size ~150 KB.
7. `packages/picolet-runtime/manifests/manifest_webview_windows.py` — frozen manifest for the windows-x64 webview variant. Contents: same baseline as Linux's `manifest_webview.py` plus `freeze("../python", "picolet_ui_win")`. (If unified manifest approach is taken instead, modify `manifest_webview.py` to dispatch on port.)
8. `packages/picolet-runtime/python/picolet_ui_win/__init__.py` — public façade: `from ._window import Window; from ._webview import Webview, WebviewTransport; from ._app import Application, run; from ._loop import PUMP_INTERVAL_S`.
9. `packages/picolet-runtime/python/picolet_ui_win/_win_ffi.py` — libffi bindings: opens `kernel32`, `user32`, and the in-process exports of `picolet_webview2_*` from the running .exe. Declares each function's signature via `ffi.func`. Mirrors `picolet_ui/_gtk_ffi.py` shape.
10. `packages/picolet-runtime/python/picolet_ui_win/_window.py` — Win32 `RegisterClassExW` + `CreateWindowExW`. Reads `[window]` from `/rom/picolet.toml` via the same `_toml.py` parser picolet_ui uses.
11. `packages/picolet-runtime/python/picolet_ui_win/_toml.py` — copy of `picolet_ui/_toml.py` (or moved up to a shared `picolet_common/` package if developer prefers).
12. `packages/picolet-runtime/python/picolet_ui_win/_webview.py` — `Webview` class wrapping the C overlay's `create_environment_blocking` + `create_controller_blocking` + `add_script_to_execute_on_document_created`. `WebviewTransport` class (duck-type-compatible with the dispatcher's transport contract).
13. `packages/picolet-runtime/python/picolet_ui_win/_loop.py` — `_win_pump()` async task that does `PeekMessageW` + `DispatchMessageW` + `picolet_wv2_poll_inbound` per tick. The pump tick is `PUMP_INTERVAL_S = 0.005` (same as PH07).
14. `packages/picolet-runtime/python/picolet_ui_win/_app.py` — Application factory: opens `Window`, creates `Webview`, reads `/rom/<ui.root>/<ui.index>` HTML through Python, calls `picolet_wv2_navigate_to_string(view, html)` so the renderer doesn't try to resolve `file:///rom/`. Registers the bridge JS via `add_script_to_execute_on_document_created` before navigation.
15. `packages/picolet-runtime/python/picolet_ui_win/_test.py` — `run_sanity_test()` and `run_callback_probe()` parallels of `picolet_ui/_test.py` for gates 5/6/8.
16. `packages/picolet-bridge-js/src/index.ts` — modified `_send` helper per AD5.
17. `packages/picolet-bridge-js/dist/picolet-bridge.js` — rebuilt bundle, committed.
18. `packages/picolet-cli/picolet/build_cmd.py` — modified: when `variant == "webview"` and `target == "windows-x64"`, also copy `WebView2Loader.x64.dll` from the installed `picolet-runtime` package data into the romfs at `picolet/WebView2Loader.dll`. Parallel to existing `_copy_bridge_js`.
19. `packages/picolet-runtime/scripts/build-runtime.sh` — modified: replace the `windows-x64/webview` PH10 stub error (lines 95–97) with a real branch routing through `build_windows_x64` with `VARIANT=webview`. Add a build-time pre-step that copies `redist/WebView2Loader.x64.dll` into the build staging so it gets embedded into the windows-x64 runtime's romfs at runtime-pack time. (Or — preferred — leave the loader DLL out of the runtime's empty-default romfs and have `picolet build` (deliverable 18) add it at app-build time; this matches the bridge-js model.)
20. `packages/picolet-runtime/scripts/dockerfiles/windows-x64-build/` — *new directory*. The current Windows build uses `dockcross/windows-static-x64-posix` directly; PH10 may need either no Dockerfile customisation (if the vendored WebView2 headers are sufficient) or a thin wrapper image that adds `cabextract` / `unzip` if any header extraction needs to happen at image-build time. The vendored-headers approach (preferred) needs no Dockerfile change.
21. `tests/phase-10/run.sh` — tester harness. Mirrors `tests/phase-09/run.sh`. Groups:
    - A: build (gates 2, 3, 4)
    - B: runtime smoke (gate 7 — PE imports inspection)
    - C: fixtures via `picolet build` (gates 5, 6, 8, 14)
    - D: end-to-end (gates 9, 10, 11)
    - E: bridge JS unit tests (gates 12, 13)
    - F: regression (gates 18, 19, 15)
    - G: template e2e (gate 20)
22. `tests/phase-10/fixtures/hello-webview-min/` — gate 5/6 fixture.
23. `tests/phase-10/fixtures/bridge-inject-order/` — gate 8 fixture.
24. `tests/phase-10/fixtures/invoke-roundtrip/` — gate 9 fixture.
25. `tests/phase-10/fixtures/error-prop/` — gate 10 fixture.
26. `tests/phase-10/fixtures/event-push/` — gate 11 fixture.
27. `tests/phase-10/test_bridge_channel_detect.js` — gate 12 (JS unit test, node-runnable).
28. `tests/phase-10/test_bridge_channel_legacy.js` — gate 13 (JS unit test).
29. `packages/picolet-runtime/README.md` — add `## Webview variant (Windows)` section noting NFR-9: Edge WebView2 Runtime requirement; the bundled loader DLL; the unpack-to-temp behaviour; clean error if runtime missing.

### Sequence the developer follows

All from `/home/anl/picolet` on the `dev` branch.

**1. Log the architectural decisions** (one empty commit per decision, or one for all six if the body is comprehensive):

```
git commit --allow-empty -s -m "[PH10] Decision: thin C overlay for WebView2 COM; bundle WebView2Loader.dll." \
    -m "AD1: bundle loader DLL in romfs, extract at startup ..." \
    -m "AD2: native overlay/modules/picolet_webview2/ instead of pure-libffi COM ..." \
    -m "AD3: handler structs in C with two-phase Win32-event completion ..." \
    -m "AD4: Win32 + same-thread message pump (Option C, 5 ms tick) ..." \
    -m "AD5: feature-detect window.chrome.webview vs window.webkit in bridge JS ..." \
    -m "AD6: WSL interop test harness; self-driving JS + sentinel tokens ..."
```

**2. Update the PH08 bridge JS** (AD5). Edit `src/index.ts`'s `_send`,
rebuild `dist/picolet-bridge.js` via `packages/picolet-bridge-js/build.sh`,
commit both source and built bundle. Add and run the two new JS unit
tests (gates 12, 13).

```
[PH10] Update picolet-bridge-js to feature-detect WebView2 vs WebKit.

Adds window.chrome.webview.postMessage as a fallback for outbound
messages so the same bundle works on both renderers ...
```

**3. Fork the Windows webview variant config.**

```
mkdir -p packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview
cp overlay/ports/windows/variants/picolet-cli/{mpconfigvariant.h,mpconfigvariant.mk,romfs_trailer.c,romfs_trailer.h} \
   overlay/ports/windows/variants/picolet-webview/
```

Edit the .mk to point at `manifest_webview_windows.py` (or the
conditional unified manifest). Update header comments.

**4. Create the Python skeleton package.** Lay down empty stubs for
`picolet_ui_win/{__init__,_win_ffi,_window,_webview,_loop,_app,_test,_toml}.py`.
Each is a stub that raises `NotImplementedError` until the C overlay
lands. The package must import cleanly (gate 3) without doing any
Win32 or COM work — `picolet_ui_win.Window()` is where the side effects
begin.

**5. Create the C overlay scaffold.** Lay down
`overlay/modules/picolet_webview2/{picolet_webview2.c,picolet_webview2.h}`
with stub function bodies that return `E_NOTIMPL`. Vendor the WebView2
SDK headers into `include/`. Vendor the loader DLL into `redist/`.
Add `LICENSE.WebView2-SDK`.

**6. Wire the C overlay into the Windows variant build.** The Windows
port already picks up `$(wildcard $(VARIANT_DIR)/*.c)` via the
romfs_trailer mechanism (PH04). The overlay module's source needs to
be added under `SRC_C` via the variant `.mk`. Confirm the dockcross
build picks up `WebView2.h` from the vendored include path.

**7. Update `build-runtime.sh`** (deliverable 19). Replace the
`windows-x64/webview` stub with a real branch. Confirm gate 2 — the
build runs and produces a stripped .exe — using the stub C overlay
that just `printf`s and returns `E_NOTIMPL`. NFR-2 gate should pass at
this point because the runtime is still mostly cli.

**8. Update the manifest.** Either:
   - Single `manifest_webview.py` with a port discriminator and
     conditional `freeze()` calls; or
   - Two manifests (`manifest_webview_unix.py` referenced by the unix
     variant's `.mk`, `manifest_webview_windows.py` referenced by the
     windows variant's `.mk`).

   The two-manifest path is more straightforward; the single-manifest
   path is DRY-er. Developer chooses; document in a `[PH10] Decision`
   commit.

**9. Implement the loader-DLL extract path** in the C overlay. This is
the smallest piece of real work and a good first integration test.
The function reads bytes from a Python-supplied buffer (the loader DLL
file content), writes them to a process-private temp directory via
`GetTempPathW` + `GetCurrentProcessId`, and calls `LoadLibraryW` on
the resulting path. Returns the `HMODULE` or `NULL` on failure. Python
side reads `/rom/picolet/WebView2Loader.dll` and passes the bytes in.
At this point: `picolet_ui_win._webview2_ffi.load_loader()` returns a
non-zero handle. No actual COM work yet.

**10. Implement the environment + controller creation** in C. This is
the heart of the COM async dance. Two static handler structs with
vtable pointers populated at module init. The blocking helpers
allocate `HANDLE hCompletion = CreateEventW(...)`, call the WebView2
API, then loop:
```c
while (WaitForSingleObject(hCompletion, 0) != WAIT_OBJECT_0) {
    MsgWaitForMultipleObjects(1, &hCompletion, FALSE, INFINITE, QS_ALLINPUT);
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}
```
Confirm gate 3 still passes (no side effects on import).

**11. Implement the Win32 window.** `RegisterClassExW` once at module
init (lazy, on first `Window()` instantiation, mirroring PH07's
`_ensure_gtk_initialised`); `CreateWindowExW` per Window instance.
WindowProc forwards `WM_SIZE` to the WebView2 controller via
`picolet_wv2_set_bounds` and `WM_DESTROY` to `PostQuitMessage`. Confirm
gate 6 — title/size emitted to stderr in the format the driver greps
for.

**12. Implement HTML loading via `NavigateToString`.** Mirror
`picolet_ui._app._read_rom_html` and the `webkit_web_view_load_html`
trick. Confirm gate 5 with the `hello-webview-min` fixture.

**13. Implement the inbound message handler** (persistent COM handler
for `WebMessageReceived`). The C side maintains a single-producer
single-consumer ring buffer of UTF-8 JSON strings (allocated with
`malloc`, freed by the polling consumer). Python's pump task polls the
ring buffer once per tick and feeds each string into the
`WebviewTransport._deliver_raw` method (same method PH07's transport
uses). Confirm gate 9 (invoke roundtrip).

**14. Implement the outbound `ExecuteScript` path.** Wraps
`controller->lpVtbl->get_CoreWebView2(controller, &view)` (cached at
controller creation) + `view->lpVtbl->ExecuteScript(view, jsW,
no_handler)`. The C side accepts a UTF-8 string from Python and
converts to UTF-16 via `MultiByteToWideChar`. Outbound completion
handler is the do-nothing static `IDispatch`-style stub (we don't care
about the return value of evaluated scripts at the bridge level).

**15. Wire `picolet build` to copy the loader DLL** (deliverable 18).
Mirror the existing `_copy_bridge_js` helper. The loader DLL is part
of the installed `picolet-runtime` package data — exposed via
`importlib.resources` the same way the bridge bundle is. Confirm gate
14 (file present in romfs).

**16. Land the integration fixtures** (deliverables 22–26) and run the
full harness (gates 9–13, 20).

**17. Run regression** (gates 18, 19, 15).

**18. Document.** Append the `## Webview variant (Windows)` section to
`packages/picolet-runtime/README.md` (deliverable 29). Note:
- The bundled `WebView2Loader.dll` and its license.
- The NFR-9 requirement: Edge WebView2 Runtime must be installed on
  the host (default on Windows 11, available via Microsoft Update on
  Windows 10 21H2+).
- The error message users see if the runtime is missing.

### Foreseeable risks

**Risk 1: dockcross MinGW + WebView2 SDK header compatibility.**

The WebView2 SDK headers (`WebView2.h`) are designed for MSVC and use
some MSVC-specific declspec annotations and a heavy `_COM_SMARTPTR_*`
mode. MinGW-w64's `windows.h` covers most of the surface, but COM
interface declarations sometimes need careful `#define
__SPECSTRINGS_STRICT_LEVEL 0` and similar guards. There's a real
possibility the first build fails on missing macros or duplicate
typedefs.

**Mitigation.** The `webview/webview` reference project (MIT) builds
WebView2 with MinGW already; its
`webview2_mingw_compat.h`-equivalent header (a few `#define`s and
`#undef`s) can be copied as a vendored prerequisite into
`overlay/modules/picolet_webview2/include/`. If MinGW compatibility
proves intractable, the contingency is to ship the COM dispatch from
Python via libffi after all — slower to implement and slower at
runtime, but no compile-time dependency on the MSVC-flavoured headers.
The contingency converts AD2 from "C overlay" back to "pure Python
COM" and roughly doubles PH10's implementation effort; flag this as
early as possible (commit 4–5 of the sequence).

**Risk 2: WebView2 async COM callbacks fire in unexpected thread
contexts.**

WebView2's documentation says all interactions are STA-affined. In
practice the completion handler for `CreateCoreWebView2EnvironmentWithOptions`
is called from a thread the loader spawns internally to do background
work, then marshalled back to the caller's STA. If the caller's
message pump isn't running while the call is in flight, the marshal
deadlocks.

**Mitigation.** AD3's two-phase completion uses
`MsgWaitForMultipleObjects` + `PeekMessage` to keep the message pump
running while waiting for the completion event. This is the canonical
COM-safe wait pattern. Gate 2 alone won't surface a deadlock here;
gate 9's end-to-end will. If gate 9 hangs, the fallback is to put the
environment-creation call on its own thread via `CreateThread` and
marshal the result back via a window message — slower, more code, but
unambiguously correct.

**Risk 3: WebView2 Runtime not installed on the host.**

NFR-9 says "with the Edge WebView2 Runtime present". Most modern
Windows hosts have it; some don't (corporate-locked Windows 10 LTSC
images, older Server SKUs, freshly wiped dev VMs). The runtime's
absence shows up as `CreateCoreWebView2EnvironmentWithOptions`
returning `HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)` or similar.

**Mitigation.** The C overlay maps that HRESULT to a clear error
message: "Edge WebView2 Runtime not installed; install from
https://developer.microsoft.com/microsoft-edge/webview2/". This is
both the developer experience for new users and what gate 16 verifies
(opt-in negative test). The error happens at first `Window`
instantiation, not at process start, so `import picolet_ui_win` itself
remains side-effect free (gate 3).

**Risk 4: PE-COFF appended-data trailer detection collides with the
embedded WebView2Loader.dll.**

PH04 ships a trailer-detection mechanism that scans for the `PYLT`
magic at the end of the .exe to find the appended romfs. The embedded
WebView2Loader.dll is *inside* the romfs (at `/rom/picolet/`), not
appended after it — so the trailer mechanism is unchanged. But the
loader DLL itself contains a PE header and a `.rsrc` section whose
strings might trip the heuristic if anyone ever changes the magic to
something less unique.

**Mitigation.** The current magic `PYLT` (and the 4-byte tail check
`finish_artifact` performs against the stock runtime) are unaffected.
Gate 14 explicitly verifies the romfs contents (including the loader
DLL) are reachable through the VFS. The risk is theoretical for PH10;
flagged here for awareness if a future phase reworks the trailer
format.

**Risk 5: Bridge JS feature-detection regresses Linux PH09 tests.**

The change to `_send` introduces a runtime branch. If the branch logic
is wrong (e.g. checks for `window.chrome` before `window.webkit` and
some future WebKitGTK build also defines `window.chrome` as a
truthy-but-incomplete shim), Linux tests would regress.

**Mitigation.** Gate 13 explicitly re-runs the Linux-channel path with
only `window.webkit` defined. Gate 18 re-runs the full PH09 harness
under xvfb after the bundle rebuild. If gate 18 fails, revert the
`_send` change and split the bundle into per-platform builds —
contingency only, expected cost zero.

**Risk 6: NFR-2 size budget on Windows is tighter than Linux.**

Linux webview lands at 666 KB / 32% of NFR-2's 2 MB ceiling. Windows
adds: ~25 KB of C overlay, ~150 KB of `WebView2Loader.dll` inside the
romfs (counts against the runtime size at-rest because the loader is
part of the runtime's default romfs trailer — or, with the
build-time-copy approach, only against the app .exe), ~30 KB of
Win32 Python bindings (`picolet_ui_win`). Plausible total: ~870 KB
runtime + 150 KB loader = ~1 MB. Still well under 2 MB.

**Mitigation.** If we're at 1.0 MB / 50% NFR-2 we're fine. The size
gate (`finish_artifact` in `build-runtime.sh`) is variant-aware and
will catch any breach. The loader DLL can also be moved out of the
runtime's default romfs and into `picolet build`'s romfs-staging step
(deliverable 18) if the runtime size pressure becomes real; this is
the preferred design anyway because the bridge JS already follows
that pattern.

**Risk 7: Bundling the loader DLL inside the runtime's empty-default
romfs creates a chicken-and-egg situation.**

The runtime ships with an empty 4-byte sentinel romfs by default. If
the loader DLL has to be present in the romfs for the runtime to
work, then `./picolet-runtime-windows-x64-webview.exe -c "..."` (a
runtime smoke test with no app romfs appended) fails to open even a
single window. This contradicts gate 3 (which only tests import, not
window open) but blocks any runtime-level smoke test from doing more
than imports.

**Mitigation.** The loader DLL is **not** bundled in the runtime's
empty romfs. It is copied into the app romfs by `picolet build`
(deliverable 18), the same way the bridge JS is. The runtime alone
(without an appended app romfs) cannot open a webview window — but
that's already true for any webview-variant smoke test (no
`picolet.toml`, no `[window]`, no `[ui]`). Gate 3 stays a pure-import
test; window-open testing is done through `picolet build` fixtures.

**Risk 8: WSL interop GUI surfacing surprises.**

The .exe under WSL interop displays its window on the Windows host's
desktop. On a developer machine the window flashes for ~3 seconds.
On a CI host (PH15, future) WSL2 may run headless with no display
surface; WebView2 still creates the window but with no swap-chain
target, and the renderer may stall waiting for present.

**Mitigation.** Test fixtures use `controller->put_IsVisible(FALSE)`
optionally (a flag in the C overlay) so the window is created but not
visible — sufficient for the JS to run, render off-screen, and emit
events. PH10's gate-9–11 fixtures default to visible (matching the
real user experience) but document the off-screen flag for PH15 CI
use. PH10's tester gate runs on the developer's WSL2 with a real
Windows display; gate 16 (CI-shape) is deferred to PH15's scope.

**Risk 9: WebView2 SDK version pinning vs evergreen runtime
mismatch.**

The bundled `WebView2Loader.dll` is pinned to a specific SDK version
(e.g. 1.0.2210.55). The system-installed runtime evolves
independently. The loader is supposed to be forwards-compatible (an
older loader works with a newer runtime) and backwards-compatible
within a major version. In practice a runtime upgrade has never broken
an older loader, but it's a contract not a guarantee.

**Mitigation.** Record the pinned SDK version in `picolet_webview2/`'s
README. Use the latest stable SDK at PH10 implementation time. If a
future runtime release ever breaks the loader, the fix is to bump the
vendored loader DLL — a small file replacement, no code change.

**Risk 10: Win32 message pump starvation under heavy JS activity.**

If the JS side blasts many `postMessage` calls in a tight loop, the
ring buffer in the C overlay overflows or the pump can't drain fast
enough.

**Mitigation.** The ring buffer is sized for 256 in-flight messages
(more than any reasonable bridge round-trip burst). Overflow drops
messages and prints to stderr — same drop-and-continue behaviour as
the PH07 malformed-JSON path. If practical bursts exceed 256 messages
(unlikely for any v1 use case), bump the ring size or switch to a
linked-list queue. Mirrors PH07's gate-16 risk.

**Risk 11: Python `_win_ffi.py`'s in-process symbol resolution.**

The Python side needs to call `picolet_wv2_*` symbols exported by the
running .exe. MicroPython's `ffi.open()` with no argument or with the
process's own .exe path is the mechanism. The Windows port's `ffi`
support for "open the current process" needs verification — it's
straightforward on Linux (`ffi.open(None)` works), the Windows
equivalent uses `GetModuleHandleW(NULL)`.

**Mitigation.** Confirm at the first integration test (step 9 in the
sequence — the loader-DLL extract test) that `ffi.open(None)` or
equivalent reaches the in-process symbols. If it doesn't, the
contingency is to expose the `picolet_webview2_*` symbols via a tiny
DLL that we either statically link into the .exe (the linker will
export the symbols if we mark them with `__declspec(dllexport)`) or
ship as a sibling DLL. Both are mechanical; flag at first sign of
trouble.

**Risk 12: Co-existence with the unix port's `picolet_ui` package.**

Both `picolet_ui` and `picolet_ui_win` should be importable on their
respective platforms. The frozen manifest for each variant must pick
exactly the right one. If a careless edit puts both in the same
manifest, the Windows runtime tries to `import picolet_ui` and crashes
because libgtk-3.so.0 is not available.

**Mitigation.** The unified-manifest path (option 1 in step 8) needs
careful conditional freezing. The two-manifest path (option 2) is
trivially safe. Gate 15 explicitly tests this: `picolet_ui_win` cannot
import on Linux; `picolet_ui` cannot import on Windows. The two-manifest
path is preferred unless the developer surfaces an issue with it.

### Out of scope for PH10

- macOS targets. (Deferred to v1.1.)
- LVGL renderer. (PH11/PH12.)
- SBOM emission for the windows-x64 webview variant. (PH13 — but
  PH10 will leave a comment block at the top of `picolet_webview2.c`
  listing the new dynamic dependencies for PH13's runtime.toml
  author.)
- CI release pipeline matrix entry for the windows-x64 webview
  variant. (PH15.)
- Hot-reload / `picolet dev` integration on Windows. (PH16.)
- App icon / Win32 VERSIONINFO embedding. (Spec out of scope for
  v1.)
- Multi-window apps. (v1 is single-window per process.)
- Code signing of the produced .exe. (Spec out of scope for v1.)
- Auto-update of the bundled WebView2Loader.dll. (Spec out of scope
  for v1.)
- WebView2's "User Data Folder" management — the runtime accepts
  WebView2's default placement under `%LOCALAPPDATA%\...`; surfacing
  a configurable path is deferred.
- Native OS dark-mode integration, taskbar grouping. (v1 out of
  scope.)
- WebView2 DevTools integration (F12). (Available by default in
  development builds; not configured-off; not part of any gate.)

### Spec traceability

| Spec id | Where closed in PH10 |
|---|---|
| FR-WV-1 (Windows half) | `overlay/modules/picolet_webview2/picolet_webview2.c` calls `CreateCoreWebView2EnvironmentWithOptions` from the vendored `WebView2.h` against the bundled `WebView2Loader.dll` — confirming WebView2 is the renderer. Gate 7 verifies the .exe has no static import of WebView2Loader.dll and that the SONAME literal `"WebView2Loader.dll"` is present in `.rodata` as the `LoadLibraryW` argument. Gate 9 exercises the full round-trip through the WebView2 renderer process. |
| FR-WV-2 (Windows) | `picolet_ui_win/_app.py` constructs the URI / HTML path as `"/rom/" + ui_root + "/" + ui_index` and feeds the HTML to `picolet_wv2_navigate_to_string`. Gate 5 verifies via the `hello-webview-min` fixture. |
| FR-WV-3 (Windows) | `picolet_ui_win/_window.py` reads `[window]` from `/rom/picolet.toml` and applies title/size/resizable to `CreateWindowExW`. Gate 6 asserts the stderr line. |
| FR-WV-4 (Windows) | `picolet_ui_win/_app.py` calls `picolet_wv2_add_script_to_execute_on_document_created` with the bridge JS bundle before any `picolet_wv2_navigate_to_string`. Gate 8 asserts `window.picolet` is defined when the first user `<script>` runs. |
| FR-WV-5 (Windows) | Same bundle, same API surface (`invoke / on / emit`) — the JS side change is just the postMessage channel detection. Gates 9, 10, 11 exercise invoke/error/event end to end. |
| FR-RT-2 (windows-x64 webview half) | `build-runtime.sh --target windows-x64 --variant webview` produces `picolet-runtime-windows-x64-webview.exe`. Gate 2. |
| FR-IPC-2 (across the Windows wire) | Round-trip through `WebviewTransport` on Windows preserves return values and exception type+message. Gates 9, 10. |
| FR-IPC-3 (across the Windows wire) | `picolet.emit` from Python reaches JS `picolet.on()`. Gate 11. |
| NFR-2 | Gate 4. |
| NFR-9 | Gate 16 (opt-in negative — missing-runtime error message). The positive case is implicit in every gate that successfully runs the .exe (the host has the runtime). |
| NFR-5 (regression constraint) | No static link of GPL/LGPL. `WebView2Loader.dll` is loaded via `LoadLibraryW`; the WebView2 engine is system-installed. The C overlay itself is MIT-licensed picolet code. Gate 7's PE-imports inspection confirms no static GPL/LGPL imports. |
| FR-WV-1 (Linux half) | **Out of scope** — closed by PH07. |
| FR-WV-{2,3,4,5} (Linux) | **Out of scope** — closed by PH07/PH08/PH09. |
| FR-RT-2 (cli variants) | **Out of scope** — closed by PH01/PH04. |
| FR-RT-2 (lvgl variants) | **Out of scope** — PH11/PH12. |
| NFR-8 | **Out of scope** — Linux-only (PH07). |

## Notes for downstream phases

**PH11/PH12 (LVGL).** Both LVGL variants use SDL2, which is plain C
ABI and pure-libffi friendly — no COM, no async vtable callbacks. PH11
on Linux mirrors PH07's pure-Python shape; PH12 on Windows mirrors
that with the SDL2 Windows backend. PH10's C-overlay design does **not**
set a precedent for PH11/PH12; the COM-specific machinery doesn't carry
over.

**PH13 (SBOM).** New dynamic dependencies introduced by PH10:

- `WebView2Loader.dll` (Microsoft WebView2 SDK License, dlopen at
  runtime; bundled in the app romfs by `picolet build`).
- The system-installed Edge WebView2 Runtime itself (Microsoft Edge
  Software License Terms; reached transitively via the loader, never
  redistributed by us). Recorded in SBOM as a runtime-only "required
  host component", parallel to Linux's `libwebkit2gtk-4.1-0` entry.

These are listed in a header comment at the top of
`picolet_webview2.c`.

**PH15 (CI release pipeline).** The windows-x64 webview variant joins
the 3 × 2 matrix. The CI runner needs to be a Windows host (for the
WebView2 Runtime) or a WSL2-on-Windows runner. The gate-16-style
opt-in negative test (missing-runtime error) is naturally not
exercised in CI; gates 5/6/8/9/10/11/20 are.

**PH16 (`picolet dev`).** File-watch + rebuild + restart works the same
on Windows as Linux; no PH10-specific work.

**`picolet bundle` (post-v1).** When `picolet bundle` ships, the windows
webview installer should declare a dependency on the Edge WebView2
Runtime via Windows Installer's standard mechanism, so end-user
installs auto-fetch the runtime if missing. PH10 does not implement
this; it's a post-v1 packaging concern.

## Implementation

(scrum-developer writes here)

## Tests

(scrum-sqe writes here)

## Verification

(scrum-tester writes here)

## Blockers
