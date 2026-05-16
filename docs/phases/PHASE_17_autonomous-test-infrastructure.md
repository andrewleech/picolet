# PH17 — Autonomous test + remote-control infrastructure

## Plan

### Goal

Make every Picolet runtime variant scriptable from outside the process so
example apps (PH18–PH22) can be screenshotted, exercised, and asserted
on without human hands. PH17 lands three deliverables: (1) a
`PICOLET_TEST_MODE=1` env-var-gated debug surface in both webview runtime
variants — WebKit Web Inspector on Linux, Chromium DevTools Protocol on
Windows — each announcing the chosen port on stderr as
`picolet:test-port=<N>`; (2) an LVGL `picolet._test` module exposing
`tap(x, y)`, `press(key)`, and `snapshot() -> bytes` (PNG) wired to
`lv_snapshot_take` and a synthetic-indev pair; and (3) a host-side
`picolet test` CLI subcommand with `--screenshot` and `--run` modes,
backed by a `picolet.testing.AppHarness` Playwright helper that connects
to either engine.

PH17 is additive — no existing v1 FR/NFR is touched. Every later
example phase's exit gate depends on `picolet test --screenshot` working
against its app.

### Spec coverage

| Spec id | Requirement | Where in this phase |
|---|---|---|
| FR-TEST-1 | Each webview runtime variant accepts `PICOLET_TEST_MODE=1` and exposes the underlying engine's debugging protocol on a runtime-chosen port; announce via `picolet:test-port=<N>` on stderr. | Chunks 1 (Linux/WebKit) + 2 (Windows/WebView2). |
| FR-TEST-2 | The LVGL runtime variant exposes a Python-side `picolet._test` API (registered when `PICOLET_TEST_MODE=1`) that synthesises pointer + key events and captures the LVGL display buffer to a PNG. | Chunk 3 (LVGL `_test` module + PNG encoder). |
| FR-TEST-3 | `picolet test [--browser webkit\|chromium] <binary>` runs the app under Playwright with the debugging port pre-attached; returns a Playwright `Page` handle. | Chunks 4 (`picolet test`) + 5 (`AppHarness`). |
| FR-TEST-4 | `picolet test --screenshot <png-path> <binary>` builds (if needed), launches, waits for page-ready, captures, terminates. Headless via xvfb on Linux; native window on Windows. | Chunks 4 (CLI) + 5 (harness `screenshot()`) + 6 (xvfb wrapper). |
| FR-TEST-5 | A Python helper `picolet.testing.AppHarness` wraps the test workflow. | Chunk 5. Lives in `picolet-cli`, not the runtime — see Decision D2 below. |
| FR-TEST-6 | LVGL example apps' tests use the same harness shape, swapping the Playwright driver for the LVGL-side `_test` API. | Chunk 5 — `AppHarness` exposes a renderer-agnostic `tap/key/screenshot` surface; for LVGL apps it drives via the in-process `picolet._test` calls reached over stdio (the dispatcher), not Playwright. |
| NFR-TEST-1 | `picolet test` startup overhead ≤ 3 seconds per test run on linux-x64-webview. | Chunk 7 — gate Test G measures this against `hello-webview`. |
| NFR-TEST-2 | The CDP debugging port is bound to `127.0.0.1` only. `PICOLET_TEST_MODE` must never be enabled in release builds. | Chunks 1 + 2 (bind `127.0.0.1` explicitly); chunk 7 (gate Test E asserts the env var is absent from release artefacts via `strings`). |

No FR/NFR outside the above is touched.

### Dependencies

**From v1 (already landed):**

- `picolet_ui.Webview` (Linux) at `packages/picolet-runtime/python/picolet_ui/_webview.py:285`
  — owns `webkit_web_view_new`, the user-content manager, and load_uri.
- `picolet_ui._gtk_ffi` at `_gtk_ffi.py` — adds `webkit_settings_*` and
  `webkit_web_view_get_settings` here (~3 new ffi.func declarations).
- `picolet_ui.Webview` (Windows) at `_webview.py:134` — owns the
  `picolet_wv2_create_environment_blocking` invocation. The current
  signature passes `NULL` for `environmentOptions`; PH17 replaces it.
- `picolet_webview2.c` at
  `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/picolet_webview2.c:389`
  — the env-creation callsite. PH17 builds an
  `ICoreWebView2EnvironmentOptions` COM object in C, populates
  `AdditionalBrowserArguments`, and threads it through.
- `WebView2_min.h` at
  `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/include/WebView2_min.h:343`
  — declares the env-options arg as `void *`; PH17 adds the
  `ICoreWebView2EnvironmentOptions` vtable definition here.
- `LV_USE_SNAPSHOT=1` is already on at
  `packages/picolet-runtime/overlay/lib/lv_binding_micropython/lv_conf.h:1030`,
  and the binding exposes `lv.snapshot_take(obj, cf)` / `lv.snapshot_free` /
  `lv.snapshot_buf_size_needed` (gen/lv_mpy_example.c:35596–35599). Reuse.
- `lv.indev_create()`, `lv.indev_t.set_type(...)`,
  `lv.indev_t.set_read_cb(cb)`, and `lv.INDEV_TYPE.{POINTER,KEYPAD}` are
  bound (gen/lv_mpy_example.c:32124, 4138–4141). Reuse for synthetic devs.
- `picolet_cli` argparse subcommand registration shape at
  `packages/picolet-cli/picolet_cli/__main__.py:54`. Add `test_cmd` here.
- `picolet_cli.run_cmd.run` rebuild-if-stale pattern at
  `packages/picolet-cli/picolet_cli/run_cmd.py:62` — `picolet test` reuses the
  same `resolve_app` + `sources_newer_than` + `build_cmd.run` flow.

**What later phases need from PH17:**

- PH18 — `tests/phase-18/run.sh` calls `picolet test` against the
  Vue baseline to assert `picolet.invoke` round-trips.
- PH19/PH20/PH21/PH22 — each example's exit gate is "screenshots
  present + Playwright test green". Both require `picolet test`.
- PH23 — CI regenerate-screenshots job runs `picolet test --screenshot`
  in a loop over `examples/*/`.

### Architectural decisions

#### D1 — Keep the inspector behind `PICOLET_TEST_MODE`, not behind a build flag

The alternatives were:

| Option | Description | Verdict |
|---|---|---|
| **A: build-time flag** | A `--enable-debug-port` runtime build that ships separately. | **Rejected.** Doubles the artefact matrix (3 variants × 2 platforms × 2 debug modes = 12). NFR-TEST-2's "must never be enabled in release builds" then becomes "ship a separate release lane", which is wasteful for a feature only test harnesses use. |
| **B: env-var-gated (chosen)** | The same release binary respects `PICOLET_TEST_MODE=1` at startup. CI release pipeline asserts the env var is NOT set in any release context. | **Selected.** Matches the spec wording exactly ("accepts `PICOLET_TEST_MODE=1`"). The env var name itself is the build-time string the audit greps for. |

**Counter-risk.** A user accidentally setting `PICOLET_TEST_MODE=1` in
their environment then exposes a debug port — but only on 127.0.0.1
(NFR-TEST-2). The `picolet:test-port=` stderr line surfaces the leak.
Acceptable.

#### D2 — `AppHarness` lives in `picolet-cli`, not in the runtime

FR-TEST-5 says "A Python helper `picolet.testing.AppHarness`". Two
interpretations:

| Interpretation | Where the module lands | Verdict |
|---|---|---|
| **A: `picolet.testing` is runtime-frozen** | `packages/picolet-runtime/python/picolet/testing.py`; frozen into every variant. | **Rejected.** AppHarness needs `subprocess`, `playwright`, `tempfile`, `socket` — none of which the runtime ships (NFR-2 budget, MICROPY_PY_DEFLATE=0, no `socket` module). The runtime is a 666 KB MicroPython; Playwright weighs orders of magnitude more. |
| **B: `picolet.testing` is a host-side CPython package shipped with `picolet-cli` (chosen)** | `packages/picolet-cli/picolet_cli/testing/__init__.py`, re-exported as `picolet.testing` via a thin shim package. | **Selected.** Host-side tests run under CPython + `uv run`. The spec wording is preserved — user `import picolet.testing` resolves at test time, not at runtime. |

**Naming the import path.** The user writes `from picolet.testing import
AppHarness`. The simplest way to make that work without colliding with
the runtime-frozen `picolet` package on the host is to ship a separate
host-only PyPI namespace package. `packages/picolet-testing/picolet/testing/`
is the layout: a sibling top-level `picolet/` directory that contains
only the `testing` submodule, declared as a PEP 420 namespace package
so it composes with anything else named `picolet` on the host's
`sys.path`. `picolet-cli` declares `picolet-testing` as an install
dependency. (Alternative considered: place the harness directly at
`picolet_cli.testing` and require users to write `from picolet_cli.testing
import AppHarness`. Rejected — FR-TEST-5 specifies the import path
`picolet.testing.AppHarness`.)

#### D3 — Playwright over both engines: connect, do not launch

Playwright supports two attach-shapes:

- `playwright.chromium.connect_over_cdp(endpoint)` — connects to an
  already-running Chromium that has `--remote-debugging-port=<N>` open.
  Speaks CDP (Chrome DevTools Protocol).
- For WebKit, **there is no `connect_over_cdp`** — Playwright's
  WebKit harness assumes Playwright launched the browser itself. The
  WebKitGTK Web Inspector speaks a **WebKit-flavoured Inspector
  Protocol** which is NOT CDP.

This is the load-bearing finding for PH17 (see Research F3 below). The
options:

| Option | Description | Verdict |
|---|---|---|
| **A: Force Chromium everywhere** | Drop WebKit on Linux; ship WebView2-equivalent in Linux too (e.g. `webview2-loader` lookalike, or CEF). | **Rejected.** v1 spec is firm on WebKitGTK 4.1 for Linux (FR-WV-1). |
| **B: Two driver paths in the harness (chosen)** | `AppHarness(browser="chromium")` uses Playwright's `chromium.connect_over_cdp`. `AppHarness(browser="webkit")` does NOT use Playwright's WebKit driver; instead it speaks the WebKit Inspector Protocol directly via WebSocket — and exposes a **Playwright-compatible subset** Page-like object that covers what the example apps actually need (`goto`, `wait_for_selector`, `screenshot`, `evaluate`, `click`, `type`). | **Selected.** FR-TEST-3 says "returns a Playwright `Page` handle"; we deliver a duck-typed Page that supports the subset of the Playwright Page API the example apps exercise. The WebKit path is a thin custom adapter, ~200 LOC. |

This is the single most load-bearing decision in PH17. If a future
example needs a Playwright Page method we haven't proxied, the harness
raises `NotImplementedError("AppHarness webkit Page: <method> not yet
proxied")` with a concrete pointer to the file to extend.

**Note for FR-TEST-3 wording.** The spec says "Returns a Playwright
`Page` handle" — we deliver an object satisfying the Page **interface**
the apps exercise. If the developer or audit prefers literal Playwright
Page, the only true path is to switch Linux off WebKitGTK; that's an
escalation and would need a v1-spec amendment. Recorded as the lone
**Open question O1** below.

**Why not pyppeteer + webkit-inspector for both.** pyppeteer is
CDP-only and unmaintained. Implementing the small WebKit subset
directly against the public Web Inspector wire format (documented in
WebKit's `Inspector/protocol/` JSON files in WebKitGTK source) is
honest and minimal.

#### D4 — Port allocation: bind 0, read back, then export

For both engines, the port number must be runtime-chosen. The
mechanism:

1. Open a TCP listening socket bound to `127.0.0.1:0` from C (we are
   inside the runtime process; no `import socket` available).
2. Read back the assigned port via `getsockname`.
3. **Close the socket** immediately. The OS may reuse the same port for
   a brief window (TIME_WAIT does not apply to a never-accepted listen
   socket), and WebKit/WebView2 will re-bind it microseconds later.
4. Set the env var / browser arg with that port number.
5. `fprintf(stderr, "picolet:test-port=%d\n", port); fflush(stderr);`

This is racey in the worst case — between step 3 and step 4 another
process could grab the port. Mitigations:

- The race window is microseconds; we accept it for v1.
- The harness's wait-for-ready loop (Chunk 5) does an HTTP HEAD on
  `http://127.0.0.1:<N>/json/version` (CDP) or
  `http://127.0.0.1:<N>/` (WebKit Inspector) and retries with backoff
  for up to 10 s. A failed first connect indicates the race; the
  harness can re-spawn.

**Alternative considered: let the engine pick the port itself.** WebKit
2.x respects `WEBKIT_INSPECTOR_SERVER=127.0.0.1:0` and writes the
chosen port to stderr in a log line — but the format is not stable
across versions. Chromium also accepts
`--remote-debugging-port=0` and writes the port to a file specified by
`--remote-debugging-pipe` or to stderr. Both engine-side paths have
fragility we'd own; the explicit-bind path makes the contract ours.

Recorded as **Open question O2**: revisit if the explicit-bind race
fires in practice.

#### D5 — PNG encoder: vendor `stb_image_write.h`

LVGL provides the raw RGB888 framebuffer via `lv_snapshot_take`. The
output must be a PNG (FR-TEST-2 wording: "captures the LVGL display
buffer to a PNG"). MicroPython's `deflate` module is **off** in the
picolet runtime (`mpconfigvariant_picolet_common.h:131`), and reintroducing
it pulls in ~12 KB of code plus widens the surface area.

The lightweight path is to vendor [`stb_image_write.h`](https://github.com/nothings/stb)
(MIT/public-domain) and link a tiny C shim that calls
`stbi_write_png_to_func`. The single-header library is ~3 KB compiled
when the PNG-only macros are set; it provides its own minimal
zlib-equivalent. This is ~10 KB total against NFR-3's 2 MiB ceiling.

| Option | Description | Verdict |
|---|---|---|
| **A: Re-enable `MICROPY_PY_DEFLATE_COMPRESS`** | Pull in the existing modddeflate + uzlib, write PNG manifestly in Python. | **Rejected for NFR-3.** PH11's lvgl size budget is the tight one; uzlib + the Python encoder adds ~15 KB and the encoder is non-trivial to get right. |
| **B: Vendor `stb_image_write.h` (chosen)** | Single-header MIT/public-domain, ~3–10 KB compiled. C shim exposes `picolet_lvgl_png_encode(rgb, w, h, *out_bytes_ptr, *out_size_ptr)` to Python via libffi. | **Selected.** Same pattern as `picolet_webview2.c` for Windows. New file `overlay/modules/picolet_lvgl_test/picolet_lvgl_png.c` linked into the lvgl variant when `PICOLET_TEST_BUILD=1` is set (a default-1 build-only knob that's separate from `PICOLET_TEST_MODE` runtime; see Open question O3). |
| **C: Defer PNG; emit raw RGB888** | Save raw bytes; let the host harness encode. | **Rejected.** FR-TEST-2 spells PNG. The harness wrapping it would also have to ship a PNG encoder; net cost is identical and clarity is lost. |

PH17 takes B. **SBOM addition** (PH13's runtime.toml): `stb_image_write`
v1.16 (MIT-or-public-domain). Recorded in chunk 3.

#### D6 — `picolet._test` registration: a module that imports for its side effects

FR-TEST-2 says the API is "registered when `PICOLET_TEST_MODE=1`". The
cleanest path:

- Always freeze `picolet/_test.py` into the lvgl variant.
- The module's top level is a guard: `if not os.getenv("PICOLET_TEST_MODE"):
  raise ImportError("picolet._test is gated on PICOLET_TEST_MODE=1")`.
- User app code does `if os.getenv("PICOLET_TEST_MODE"): from picolet
  import _test as picolet_test` — or, more typically, the example's
  `tests/conftest.py` calls into it via stdio commands.

The module name `picolet._test` is required by the spec. Important: this
is NOT the same as `picolet_ui._test` (which exists today at
`packages/picolet-runtime/python/picolet_ui/_test.py` and is the PH07/PH11
gate-test driver). The two coexist; the gate-test driver becomes
`picolet_ui._sanity` in a renaming sub-step (Chunk 3) to avoid confusion.
The webview variants do NOT freeze `picolet._test` (LVGL-only — FR-TEST-2
is explicit; the webview variants get their debug surface via Chunks
1+2 instead).

#### D7 — `xvfb-run -a` wrapper, autodetected

When `$DISPLAY` is unset on Linux, `picolet test` invokes the child as
`xvfb-run -a -s "-screen 0 1280x800x24" <binary>` instead of `<binary>`
directly. The `-a` flag picks a free server number; the `-s`
`-screen 0 1280x800x24` matches the v1 gate fixture sizing. No
`xvfb-run` ⇒ harness exits with a clear error pointing at
`apt install xvfb`.

We do NOT introduce a Python-managed Xvfb (e.g. `pyvirtualdisplay`); the
`xvfb-run` wrapper is enough and lives outside the harness's process
tree, simplifying teardown.

On Windows, no equivalent — the headed window is acceptable per the
spec ("native window on Windows (WSL interop)"). A future PH could add
WebView2 headless mode (`--headless`).

### Implementation breakdown

Each chunk is independently testable and lands one or more commits on
`dev`. Total: 8 chunks ordered by topological dependency.

#### Chunk 1 — Linux/WebKit inspector wiring

Goal: in the Linux webview variant, when `PICOLET_TEST_MODE=1` is set,
enable WebKit's developer tooling and inspector server on a
runtime-chosen 127.0.0.1 port. Print `picolet:test-port=<N>\n` to stderr
before the first GTK iteration.

**Files touched:**

- `packages/picolet-runtime/python/picolet_ui/_gtk_ffi.py` — add three
  bindings:
  - `webkit_web_view_get_settings` (`p ← p`)
  - `webkit_settings_set_enable_developer_extras` (`v ← pi`)
  - `webkit_settings_set_enable_write_console_messages_to_stdout` (`v ← pi`)
    (optional; useful for stderr capture during tests)
- `packages/picolet-runtime/python/picolet_ui/_webview.py` — extend
  `Webview.__init__` (linux branch). After
  `_gtk_ffi.webkit_web_view_new()` and before any `load_uri`:
  - If `os.getenv("PICOLET_TEST_MODE") == "1"`:
    - Open a TCP listener on 127.0.0.1:0 via a tiny libffi binding to
      `socket(2)` + `bind(2)` + `listen(2)` + `getsockname(2)` + `close(2)`
      against `libc.so.6`. (`picolet_ui._test_port`, a new ~40-line
      helper file.)
    - Set the env var `WEBKIT_INSPECTOR_SERVER=127.0.0.1:<port>` via
      `setenv` (libc) **before** `webkit_web_view_new` (see Risk R1).
      This means the env-var setting must move earlier in
      `Webview.__init__` — restructure accordingly.
    - Get the view's settings and call
      `set_enable_developer_extras(settings, 1)`.
    - Write `picolet:test-port=<port>\n` to stderr and `fflush`. Use
      `sys.stderr.write` + `sys.stderr.flush()`.

**On-the-wire protocol (Linux):**

- The WebKit Web Inspector server listens on `127.0.0.1:<port>` and
  serves a small HTTP+WebSocket service. `GET /` returns an HTML page
  for human use; the Inspector front-end loads JS-side from inside
  WebKit itself. The wire protocol on the WebSocket is JSON-RPC
  similar to (but not identical to) CDP — see WebKitGTK source
  `Source/JavaScriptCore/inspector/protocol/*.json`.
- Per-page targets are listed at `GET /targets` (older versions: just
  `/`); each target has a `webSocketDebuggerUrl` analogous to CDP.
- The harness's webkit driver (Chunk 5) speaks this protocol directly.

**Exercise:** `PICOLET_TEST_MODE=1 picolet_ui._sanity.run_sanity_test()` —
expect a `picolet:test-port=<N>` line on stderr, and `curl
http://127.0.0.1:<N>/` returns an HTML page with `WebInspector` in it.

#### Chunk 2 — Windows/WebView2 CDP wiring

Goal: in the Windows webview variant, when `PICOLET_TEST_MODE=1`, build
an `ICoreWebView2EnvironmentOptions` that sets
`--remote-debugging-port=<port> --remote-debugging-address=127.0.0.1`
in `AdditionalBrowserArguments`, and announce the port on stderr.

**Files touched:**

- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/include/WebView2_min.h`
  — add the `ICoreWebView2EnvironmentOptions` interface declarations
  (vtable struct with `get_AdditionalBrowserArguments` /
  `put_AdditionalBrowserArguments` and the other five members the
  v1 interface defines — `Language`, `TargetCompatibleBrowserVersion`,
  `AllowSingleSignOnUsingOSPrimaryAccount` getter+setter — even if
  unused, the vtable layout must be complete).
- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/picolet_webview2.h`
  — adjust the `picolet_wv2_create_environment_blocking` signature to
  accept a wide-string browser-args argument:
  ```c
  void *picolet_wv2_create_environment_blocking(const wchar_t *extra_args, int32_t timeout_ms);
  ```
  Old call sites in `_webview.py` pass `NULL`. The chunk-1 path passes
  the `--remote-debugging-port=<N> --remote-debugging-address=127.0.0.1`
  string. Backwards-compat: keep both, or transition cleanly — see
  Open question O4.
- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/picolet_webview2.c`
  — implement:
  - `picolet_wv2_pick_test_port()` — returns int32_t, binds
    `127.0.0.1:0`, calls `getsockname`, closes, returns the port (or
    -1 on failure).
  - An `ICoreWebView2EnvironmentOptions` vtable shim allocated on the
    stack inside `picolet_wv2_create_environment_blocking` when
    `extra_args != NULL`, threading it into
    `g_pfn_create_env(NULL, NULL, options, &ctx->base)`.
- `packages/picolet-runtime/python/picolet_ui/_win_ffi.py` — bind the
  `picolet_wv2_pick_test_port` symbol.
- `packages/picolet-runtime/python/picolet_ui/_webview.py` (windows branch)
  — read `PICOLET_TEST_MODE`; if set, call the port picker, format the
  args UTF-16, pass through `_ensure_environment(extra_args=...)`;
  print `picolet:test-port=<N>\n` to stderr.

**On-the-wire protocol (Windows):**

- Chromium's CDP HTTP discovery endpoint is at
  `http://127.0.0.1:<N>/json/version` (returns engine version + the
  browser WebSocket URL). Per-target discovery at `/json` lists
  pages; each has a `webSocketDebuggerUrl`. Playwright's
  `chromium.connect_over_cdp(endpoint_url)` accepts either the HTTP
  endpoint (`http://127.0.0.1:<N>`) or the WebSocket browser URL —
  use the HTTP form, more forgiving on connect timing.

**Exercise:** Build the Windows webview runtime, run with
`PICOLET_TEST_MODE=1` and a stub `index.html`, then from WSL
`curl http://127.0.0.1:<N>/json/version` returns JSON containing a
`"webSocketDebuggerUrl"` key.

#### Chunk 3 — LVGL `picolet._test` API + PNG encoder

Goal: ship `picolet._test` (`tap`, `press`, `snapshot`) in the lvgl
runtime variant, plus the PNG encoder C overlay.

**Files touched:**

- `packages/picolet-runtime/overlay/modules/picolet_lvgl_test/` — new
  directory:
  - `picolet_lvgl_png.h` — exposes one symbol:
    ```c
    /* Encode RGB888 data (width*height*3 bytes) to PNG bytes.
     * The encoder allocates the output buffer via malloc; caller
     * must picolet_lvgl_png_free() it. Returns 0 on success. */
    int32_t picolet_lvgl_png_encode(const uint8_t *rgb888,
                                  int32_t width, int32_t height,
                                  uint8_t **out_bytes, size_t *out_size);
    void picolet_lvgl_png_free(uint8_t *bytes);
    ```
  - `picolet_lvgl_png.c` — `#define STBI_WRITE_NO_STDIO` and
    `#define STB_IMAGE_WRITE_IMPLEMENTATION` and include
    `stb_image_write.h`; thin wrapper that captures the output via
    `stbi_write_png_to_func`.
  - `include/stb_image_write.h` — vendored, single header,
    pinned version + SHA recorded in chunk commit body.
- `packages/picolet-runtime/overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.mk`
  — add the new .c to SRC_C (or pick up via the existing
  `$(wildcard $(VARIANT_DIR)/*.c)` if that pattern applies; verify
  during implementation).
- `packages/picolet-runtime/python/picolet/_test.py` — **new** file. Top
  of file guards on `os.getenv("PICOLET_TEST_MODE") != "1"` with an
  `ImportError`. Exposes:
  ```python
  def tap(x: int, y: int) -> None: ...
  def press(key: int) -> None: ...
  def snapshot() -> bytes: ...  # PNG bytes
  ```
  - Implementation details:
    - On first call to `tap` or `press`, lazily create two indev
      devices: one `INDEV_TYPE.POINTER` and one `INDEV_TYPE.KEYPAD`.
      Each has a `read_cb` that drains a Python queue of pending
      synthesised events.
    - `tap(x, y)` pushes a `(x, y, PRESSED)` event followed by an
      `(x, y, RELEASED)` after one tick.
    - `press(key)` pushes a `(key, PRESSED)` then `RELEASED`.
    - `snapshot()` calls `lv.snapshot_take(lv.screen_active(),
      lv.COLOR_FORMAT.RGB888)`, copies the resulting image-descriptor
      bytes out (via `uctypes.bytes_at(dsc.data, dsc.data_size)`),
      calls `lv.snapshot_free(dsc)`, then dispatches into
      `picolet_lvgl_png_encode` via libffi (against the in-process
      symbol surface — same trick as `picolet_webview2.c` on Windows).
- `packages/picolet-runtime/manifests/manifest_lvgl.py` — extend
  `freeze("../python", "picolet")` (already there) to ensure `_test.py`
  is picked up. Verify: it is, because `freeze("../python", "picolet")`
  walks the package.
- `packages/picolet-runtime/python/picolet_ui/_test.py` — **rename** to
  `packages/picolet-runtime/python/picolet_ui/_sanity.py`. Update one
  reference in the gate test runner (search for
  `run_sanity_test\|run_callback_probe\|run_lvgl_sanity_test\|
  run_ipc_probe\|run_lvgl_render_probe` references; updates in
  whatever tests/phase-NN/ scripts call them). This is a renames-only
  hygiene step to free the name `picolet._test` for the spec'd API.

**Exercise:**

```python
PICOLET_TEST_MODE=1 picolet-runtime-linux-x64-lvgl -c "
import os, lvgl as lv, picolet_ui
from picolet_ui._lvgl import LvglDisplay
LvglDisplay()
scr = lv.screen_active()
btn = lv.button(scr); btn.set_size(100, 50); btn.center()
import asyncio, picolet._test as t
async def go():
    for _ in range(10):
        lv.tick_inc(5); lv.task_handler(); await asyncio.sleep(0.005)
    png = t.snapshot()
    open('/tmp/snap.png', 'wb').write(png)
    print('SNAP_OK len=', len(png))
asyncio.run(go())
"
```

The harness asserts `/tmp/snap.png` is a valid PNG via `Pillow`.

#### Chunk 4 — `picolet test` CLI subcommand

Goal: the `picolet test` entry point.

**Files touched:**

- `packages/picolet-cli/picolet_cli/test_cmd.py` — new. Argparse:
  ```
  picolet test [--target TARGET]
             [--no-build]
             [--browser {webkit,chromium,auto}]
             [--screenshot PATH]
             [--run SCRIPT_PY]
             [--timeout SECONDS]
             [--verbose]
             [-- arg1 arg2 ...]
  ```
  - Modes:
    - bare: spawn, wait for `picolet:test-port=<N>`, print `connected
      browser=<…> port=<N> binary=<…>` to stdout, then terminate the
      child after a 1s grace.
    - `--screenshot PATH`: spawn, attach via `AppHarness`, wait for
      `window.picolet.__ready__ === true`, call `harness.screenshot(PATH)`,
      terminate.
    - `--run SCRIPT`: spawn, attach via `AppHarness`, run `SCRIPT`
      with `AppHarness` pre-bound in the script's globals as
      `harness`. The script may use `await harness.page.click(...)`
      etc. Script can also be a single test file — exit code is
      the test's exit code.
  - `--browser auto` (default): on linux-x64 → `webkit`, on
    windows-x64 → `chromium`. Override is rare.
- `packages/picolet-cli/picolet_cli/__main__.py` — register `test_cmd` in
  `_build_parser` (line ~52). One line.
- `packages/picolet-cli/picolet_cli/_paths.py` — no change (reuse
  `resolve_app`, `sources_newer_than`).

**Wait-for-port logic:** spawn the binary with `PICOLET_TEST_MODE=1` set
in the child env, capture stderr line-by-line until a line matches the
regex `^picolet:test-port=(\d+)$`. Timeout: 10 s. After that, the child
continues running with stderr forwarded to ours.

**Exercise:** `picolet test --no-build packages/picolet-runtime/build/picolet-runtime-linux-x64-webview`
exits 0 with a printed port line.

#### Chunk 5 — `AppHarness` host-side helper

Goal: the `picolet.testing.AppHarness` class.

**Files touched:**

- `packages/picolet-testing/` — new package. Layout:
  - `pyproject.toml` — declares the package, depends on `playwright`
    and `websockets` (the latter for the WebKit Inspector path).
  - `picolet/testing/__init__.py` — re-exports `AppHarness`.
  - `picolet/testing/_harness.py` — the main class.
  - `picolet/testing/_chromium.py` — Playwright `connect_over_cdp` path.
  - `picolet/testing/_webkit.py` — WebKit Inspector Protocol thin client
    + Page-shaped duck.
- `packages/picolet-cli/pyproject.toml` — add `picolet-testing` as a
  dependency.

**API surface:**

```python
class AppHarness:
    def __init__(self, binary, browser="auto", env=None, args=(), timeout=10.0):
        ...

    async def start(self) -> "AppHarness":
        """Spawn the child, wait for picolet:test-port=<N>, attach, wait
        for window.picolet.__ready__ === true.  Returns self."""

    page: "PageLike"   # Playwright Page (chromium) or WebKit adapter

    async def screenshot(self, path) -> None:
        """Calls page.screenshot({path: path, full_page: True})."""

    async def stop(self) -> int:
        """SIGTERM the child, await exit, return rc."""

    async def __aenter__(self): return await self.start()
    async def __aexit__(self, *exc): await self.stop()
```

**Browser="auto" routing:**

- Reads the binary's name suffix (`-webview` ⇒ on linux uses webkit, on
  windows uses chromium; `-lvgl` ⇒ the LVGL-side path, see below) and
  the host platform.
- Override via explicit `browser=`.

**LVGL-side AppHarness (FR-TEST-6):** when the binary is a `-lvgl`
variant, the harness skips the inspector port and instead drives the
app via stdio commands routed to the existing `picolet` dispatcher (the
`StdioTransport` path already in PH06). The harness exposes the same
`tap(x, y)`, `press(key)`, `screenshot(path)` calls; they each issue
an `await picolet.invoke("__test__.tap", {"x": x, "y": y})`-style request
that the lvgl variant's `picolet._test` module registers as
`@picolet.command` handlers (a small registration shim that lives in
`picolet/_test.py`'s `__init__` block). This keeps FR-TEST-6's "same
harness shape" honest — no per-app conditional plumbing.

**Wait-for-ready signal:** `window.picolet.__ready__` is set to `true` by
the bridge-js after `__picolet_recv` is wired up. PH17 adds one line at
`packages/picolet-bridge-js/src/index.ts:108` (end of the `__picolet_recv`
assignment block):

```typescript
(window as any).picolet.__ready__ = true;
```

This is a 1-line touch — covered by an extra commit but staying in the
same chunk for atomicity.

#### Chunk 6 — xvfb autodetect

Goal: `picolet test` runs the child under `xvfb-run -a -s "-screen 0
1280x800x24"` when `$DISPLAY` is unset on Linux.

**Files touched:**

- `packages/picolet-cli/picolet_cli/test_cmd.py` — wrap the subprocess
  arglist before `subprocess.Popen` is called. If
  `sys.platform == "linux"` and `not os.environ.get("DISPLAY")` and
  `shutil.which("xvfb-run")`, prepend `["xvfb-run", "-a", "-s",
  "-screen 0 1280x800x24"]`. Print a clear error if `xvfb-run` is
  needed but missing.

**Exercise:** `unset DISPLAY; picolet test --screenshot /tmp/x.png …`
works inside a WSL2 shell with `xvfb` installed.

#### Chunk 7 — Phase tests + release-build assertion

Goal: `tests/phase-17/run.sh` exercises every gate.

**Files touched:**

- `tests/phase-17/run.sh` — new. Modelled on `tests/phase-16/run.sh`
  (same `pass`/`fail`/`skip` helpers, same `--skip-regression` flag).
  Gates:
  - **Test A**: `picolet test --help` shows the subcommand.
  - **Test B**: `picolet test --no-build <hello-webview>` exits 0 and
    prints a `picolet:test-port=…` line within 3 s.
  - **Test C**: `picolet test --no-build --screenshot /tmp/wv.png
    <hello-webview>` produces a PNG > 1 KB. Validate via `python -c
    "from PIL import Image; Image.open('/tmp/wv.png').verify()"`.
  - **Test D**: `picolet test --no-build --screenshot /tmp/lv.png
    <hello-lvgl>` produces a PNG > 1 KB. Same Pillow verify.
  - **Test E**: NFR-TEST-2 — `strings
    packages/picolet-runtime/build/picolet-runtime-linux-x64-webview |
    grep -c PICOLET_TEST_MODE` must be **non-zero** (the runtime
    references the env var name as a literal — it has to, to read it).
    The release-pipeline-level "must not be enabled" check is
    different: it asserts `PICOLET_TEST_MODE` is not in the **process
    environment** of the CI release job, not that the string is absent
    from the binary. Refine the spec wording here? See **Open question
    O5**. PH17's gate Test E asserts the runtime binary does NOT
    contain the literal `PICOLET_TEST_MODE=1` (i.e. no debug-enabled
    config got baked in at compile time).
  - **Test F**: NFR-TEST-2 — netstat after spawn shows port bound to
    `127.0.0.1`, not `0.0.0.0`.
  - **Test G**: NFR-TEST-1 — spawn-to-`__ready__` ≤ 3 s wall-clock
    (`time` the harness, assert).
  - **Test H**: `picolet test --browser chromium <linux-binary>` fails
    cleanly with a clear message (can't use chromium against a
    WebKitGTK binary).
  - **Test I**: Bridge JS has `window.picolet.__ready__ === true` after
    bundle parses (Playwright `evaluate`).
- `.github/workflows/release.yml` — extend to assert
  `PICOLET_TEST_MODE` is not set in the release job's environment.
  (One-line `env -0 | grep -q '^PICOLET_TEST_MODE=' && exit 1 || true`.)

#### Chunk 8 — Build verification + docs

Goal: tie it together with at least one Linux build and one Windows
build green, plus update `docs/architecture.md` with the test surface.

**Files touched:**

- `docs/architecture.md` — append a "Test surface" section describing
  `PICOLET_TEST_MODE`, the stderr port announcement contract, and
  `AppHarness`.
- Linux build: `make -C packages/picolet-runtime/overlay/ports/unix
  VARIANT=picolet-webview`, `VARIANT=picolet-lvgl`.
- Windows build via dockcross: same incantation as PH10/PH12.
- Empty commit `[PH17] Note: linux + windows webview builds green
  with WebKit inspector + WebView2 CDP`.

### Key research findings

**F1** — `picolet_ui._test` (the legacy gate-driver module) already
exists at `packages/picolet-runtime/python/picolet_ui/_test.py`. PH17 must
not collide with it; the rename to `picolet_ui._sanity` (chunk 3) frees
the spec-required name `picolet._test` for the new public API.

**F2** — `lv.snapshot_take`, `lv.snapshot_free`, `lv.snapshot_buf_size_needed`,
`lv.indev_create`, `lv.indev_set_read_cb`, `lv.indev_set_type`, and
`lv.INDEV_TYPE.{POINTER,KEYPAD}` are all already exposed by the
generated `lv_mpy_example.c` (lines 4138, 31781, 31817, 34081, 34228,
35596–35599). `LV_USE_SNAPSHOT=1` is already on in `lv_conf.h:1030`.
**No LVGL config change required.** Implementation is pure Python +
libffi-to-shim.

**F3** — Playwright's WebKit driver does NOT support
`connect_over_cdp`. WebKitGTK's Web Inspector speaks a
WebKit-flavoured Inspector Protocol on a WebSocket served by
`WEBKIT_INSPECTOR_SERVER`. The two are conceptually similar (JSON-RPC,
target listing, page screenshotting, JS evaluate) but the message
shapes differ in subtle ways. **This is the load-bearing finding** —
see D3. The harness's webkit path is custom-built ~200 LOC over the
public Inspector protocol, exposing a Playwright-Page-shaped duck.

**F4** — `MICROPY_PY_DEFLATE` is set to 0 in
`mpconfigvariant_picolet_common.h:131`. PNG encoding cannot use
MicroPython's stdlib zlib. Vendor `stb_image_write.h` (chunk 3, D5).

**F5** — `ICoreWebView2EnvironmentOptions` is NOT declared in the
project's `WebView2_min.h`. `picolet_webview2.c:412` calls
`g_pfn_create_env(NULL, NULL, NULL, &ctx->base)` — third arg
(`environmentOptions`) is hard-coded NULL. Chunk 2 must extend the
header with the v1 EnvironmentOptions interface and the C overlay must
construct an instance with `AdditionalBrowserArguments` populated.

**F6** — `WEBKIT_INSPECTOR_SERVER` must be set in the environment
**before** the `WebKitWebView` is created. Setting it after has no
effect (R1). The current `Webview.__init__` reads env vars after view
creation — restructure (chunk 1).

**F7** — `webkit_settings_set_enable_developer_extras` is a public
WebKitGTK 4.1 symbol. Without `developer_extras` enabled, the inspector
server still starts when `WEBKIT_INSPECTOR_SERVER` is set, but the
right-click "Inspect Element" menu and richer JS introspection
endpoints are gated on it. Enable for completeness.

**F8** — `getsockname` on a freshly-bound port-0 socket returns the
assigned port. The race window between `close` and the next process
binding the same port is microseconds on Linux/Windows (no TIME_WAIT
on a never-accepted listen socket). Mitigation: harness retries on
connect failure for up to 10 s.

**F9** — Bridge JS at `packages/picolet-bridge-js/src/index.ts:114`
assigns the `window.picolet` object synchronously at IIFE-evaluate time;
the `__picolet_recv` stub is also assigned before then (line 68). PH17
adds `window.picolet.__ready__ = true` once both are wired — a 1-line
change. Conceptually the "ready" signal is the post-bridge-load
moment; for richer apps a future `window.picolet.ready()` Promise could
be added.

**F10** — The picolet-cli subcommand registration shape at
`__main__.py:52–58` is straightforward; `test_cmd.add_parser(subparsers)`
slots in alongside `dev_cmd.add_parser`.

**F11** — `xvfb-run -a` is in the Ubuntu `xvfb` package; it sets
`DISPLAY`, picks a free server number, runs the child, cleans up on
exit. The `-s "-screen 0 1280x800x24"` arg sizes the virtual display
to the LVGL/webview window default (1280×800; the v1 gate fixture).

**F12** — `playwright.chromium.connect_over_cdp(endpoint_url_or_ws)`
is async; it negotiates over HTTP first to find the browser-level WS
URL, then attaches. Required version: Playwright ≥ 1.30 (the
`connect_over_cdp` shape stabilised then; current LTS is 1.42).
Declare `playwright >= 1.40` in `picolet-testing/pyproject.toml`.

**F13** — The current `picolet_webview2.c:412` call to
`g_pfn_create_env` uses a refcount-1 ctx struct freed by the helper
+ Invoke pair (AD3 in PH10). The chunk-2 extension keeps that
contract: the env-options shim is a separate stack-allocated COM
object whose lifetime is bounded by the
`picolet_wv2_create_environment_blocking` call — no heap allocation,
no per-call refcount management needed (it's a const-after-init
options object, not a completion handler).

### Open questions / decisions to surface

**O1** — FR-TEST-3's literal wording "returns a Playwright `Page`
handle". The chunk-5 approach returns a Playwright Page for chromium
and a Playwright-Page-shaped duck for webkit. If the audit reads the
spec literally and rejects the duck, the only true fix is to drop
WebKitGTK in favour of a CDP-speaking Linux engine, which is a
v1-spec amendment, not a PH17 deliverable. **Surface to user before
chunk 5 starts.**

**O2** — The bind-0-then-close-then-reopen port-allocation race (D4).
The window is microseconds and the harness retries; we believe this
is safe but it has not been measured under load. If gate-test F or G
flakes in CI, this is the suspect. Contingency: switch to
engine-side port-0 (parse port from engine stderr).

**O3** — Whether `PICOLET_TEST_BUILD` is a build-time knob or always on.
The PNG encoder C overlay is ~10 KB; "always on" is the lazier path
and keeps NFR-3 well under budget. Decision: **always on**. The
runtime-side gating is the env var, not the build. Recorded here only
because the C-overlay was initially scoped behind a build flag in the
v1.1-plan; we are simplifying.

**O4** — Backwards-compat on the
`picolet_wv2_create_environment_blocking` signature change. Two options:
  (a) Add a parallel symbol `picolet_wv2_create_environment_blocking_v2`
  that takes the args; leave the original. Existing callers unchanged.
  (b) Change the existing symbol in place; the one caller in
  `_webview.py` updates atomically.
  Recommendation: **(b)** — there are no external API consumers; this
  is a runtime-internal C overlay.

**O5** — NFR-TEST-2's "the CI release pipeline asserts the variable's
absence in the artefact". Read literally, this asks `grep PICOLET_TEST_MODE
<binary>` to return nothing. But the runtime **must** contain the
literal string `PICOLET_TEST_MODE` because it calls `getenv("PICOLET_TEST_MODE")`
— a string-table entry. The honest read is "the variable's absence in
the **build environment**" — i.e. the CI release job's `env` must not
have `PICOLET_TEST_MODE=1` set. Chunk 7's Test E codifies the env-not-set
check; the build-time-bake-in check is separate (asserts no
`-DPICOLET_TEST_MODE=1` style flag in the build). **Surface to user
before chunk 7 lands** — if the literal-grep reading is preferred, the
runtime would need to obfuscate the env-var name (e.g. construct it
from concat at runtime), which is uglier than the env-not-set
interpretation.

### Exit gate

A successful PH17 has all of the following true, verified by
`bash tests/phase-17/run.sh` exiting 0:

| Check | What it proves | Command |
|---|---|---|
| Test A | `picolet test` is wired | `picolet test --help` |
| Test B | FR-TEST-1 (Linux); stderr port line ≤ 3 s | `picolet test <linux-webview>` |
| Test C | FR-TEST-4 + FR-TEST-1 (Linux) | `picolet test --screenshot /tmp/wv.png <linux-webview>` plus PIL verify |
| Test D | FR-TEST-2 + FR-TEST-4 (LVGL) | `picolet test --screenshot /tmp/lv.png <linux-lvgl>` plus PIL verify |
| Test E | NFR-TEST-2 (no build-time bake-in) | `! grep -aFq 'PICOLET_TEST_MODE=1' <binary>` |
| Test F | NFR-TEST-2 (loopback only) | `ss -lnt sport = :<port> | grep 127.0.0.1` |
| Test G | NFR-TEST-1 (≤ 3 s) | `time picolet test --screenshot …` ≤ 3 s |
| Test H | FR-TEST-3 (clean error) | `picolet test --browser chromium <linux-webview>` exits non-zero with a clear stderr message |
| Test I | bridge ready | `picolet test --run scripts/assert_ready.py <linux-webview>` |
| Test J | FR-TEST-1 (Windows) | dockcross-built `windows-x64-webview.exe` under WSL interop returns `picolet:test-port=…` and `curl http://127.0.0.1:<N>/json/version` ⇒ valid CDP JSON. |

Plus: one Linux build and one Windows build green via the standard
runtime build commands (per `CLAUDE.md`'s build policy).

### Risks / footguns

**R1** — `WEBKIT_INSPECTOR_SERVER` is read by WebKit **once at engine
init time**. Setting it after `webkit_web_view_new()` is a no-op. The
chunk-1 restructure of `Webview.__init__` moves the env-var assignment
before the view creation. The unit test for this lives in chunk 7
gate Test B (the port line must appear before any user JS runs).

**R2** — `WebKitWebInspectorServer` (the underlying class) has been
removed in some downstream WebKit builds. WebKitGTK 4.1 still ships
it as of 2.44; verify against the runtime's `apt`-pinned version.
Failure mode: `WEBKIT_INSPECTOR_SERVER` set but no port opens. Gate
Test B catches this.

**R3** — Playwright's `connect_over_cdp` can succeed before the page
has fully loaded; `harness.start()` must wait for
`window.picolet.__ready__ === true` (a polled `page.evaluate`), not
just a connection. Implemented in chunk 5.

**R4** — Chromium's `--remote-debugging-port=0` makes the engine
pick the port. The chunk-2 approach picks the port **before** giving
it to the engine. This is more code (chunk 2's
`picolet_wv2_pick_test_port`) but gives us a stable announcement
moment. Switching to engine-side picking is the chunk-2 contingency
if `AdditionalBrowserArguments` rejects an explicit port for any
reason (haven't seen it; it's documented as supported).

**R5** — `xvfb-run -a` returns the child's exit code, but mangles
SIGINT propagation under some shells. The harness uses `Popen` and
manages signals itself; the `xvfb-run` invocation is `-e /dev/stderr`
so its own diagnostics don't disappear.

**R6** — `stb_image_write.h` `stbi_write_png_to_func` uses an internal
buffer that grows; for a 1280×800×3 RGB888 image (~3 MB raw) the
encoder needs ~3–4 MB of scratch heap. Verify against MicroPython's
default unix heap and gc.add_heap'd extension; PH11 already uses
gc.add_heap for the lvgl variant.

**R7** — The synthetic indev `read_cb` runs from inside
`lv.task_handler()` on the asyncio thread (PH11's pump). The callback
must not allocate or block. The chunk-3 implementation uses a
pre-allocated event ring buffer (uctypes array, 32 entries) and a
simple int index; the Python-side `tap()` pushes into the ring, the
read_cb pops. No allocation in the hot path.

**R8** — `lv.snapshot_take(scr, lv.COLOR_FORMAT.RGB888)` returns a
`lv.img_dsc_t` whose `data` pointer is owned by LVGL until
`lv.snapshot_free` is called. Race: if the screen redraws between
take and the bytes-copy, the buffer is stale. Mitigation: do the
copy synchronously inside one asyncio tick, before yielding.

**R9** — Windows `WebView2EnvironmentOptions` is a COM object the
SDK expects to AddRef/Release. Our stack-allocated shim's
AddRef/Release are no-ops (refcount stays at 1; the lifetime is
bounded by the call frame). This works because WebView2 invokes
the options getters synchronously during `CreateCoreWebView2Environment`
and never retains the pointer. If a future SDK version retains it,
the shim must move to heap allocation with proper refcounting (~10
extra lines, same shape as the Env/Ctrl handler ctx in
`picolet_webview2.c`).

### Model tier recommendations

The v1.1-plan defaults for PH17 are:

| Role | Default | Recommended | Rationale |
|---|---|---|---|
| planner | opus | **opus** (this artefact) | Cross-cutting design, two engine protocols, COM glue. |
| developer | opus | **opus** | WebView2 COM v-table extension + WebKit Inspector wire format + libffi C-overlay extension on Linux. Multi-platform C/COM work is exactly the "opus tier" call. |
| sqe | sonnet | **sonnet** | Test authoring is mechanical once the design exists. |
| tester | opus | **opus** | NFR-TEST-1 timing assertions across both platforms, Windows-via-WSL-interop validation, and "this duck-typed Page is acceptable" judgement. The tester needs to evaluate the WebKit driver completeness against what example apps will need — a design call, not a check-box. |

No deviation from v1.1-plan PH17 defaults recommended.
