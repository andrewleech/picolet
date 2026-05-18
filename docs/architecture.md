# Architecture

This document captures the load-bearing design decisions made for Picolet v1.

## Frame

Picolet generalizes the pattern proven by
[pydfu-win](https://github.com/andrewleech/pydfu-win): a frozen-Python
MicroPython binary with embedded romfs and a minimal variant config
produces a single ~750 KB executable.

Picolet packages that pattern into a framework that ships:

- A pre-built MicroPython runtime per (platform, renderer) tuple.
- A CLI tool that glues a user's frozen `.mpy` + romfs assets onto a
  runtime to produce the final binary.
- Renderer modules (webview / LVGL) that expose a native GUI to the app's
  Python code, mediated by an IPC bridge.

## Decisions

### D1 — Both renderers (webview + LVGL) functional in v1

Webview is the Tauri analogue: HTML/CSS/JS frontend, system webview.
LVGL gives a true zero-system-dependency single binary. Both pull their
weight, and a renderer abstraction in the runtime is cheap once the second
implementation exists.

**Consequence**: CI release matrix is at least 6 runtime binaries
(3 platforms × 2 renderers) plus the headless CLI variant.

### D2 — Pre-built runtimes by default, `--from-source` opt-in

`picolet build` downloads a pre-compiled
`picolet-runtime-{platform}-{renderer}` artifact, then embeds the user's
`.mpy` + romfs into it.

Users who need to add a native C module, change `mpconfigvariant`, or vet
the runtime build use `picolet build --from-source`, which invokes dockcross
locally.

**Consequence**: Picolet's own CI runs the dockcross build matrix and
publishes runtime artifacts per release. End users only need Docker if
they opt into source builds.

### D3 — Sync-RPC IPC

JavaScript calls Python with `await picolet.invoke('cmd', args)` and
receives a return value. Python handlers are registered with
`@picolet.command async def`.

Push-from-Python uses a secondary event channel: `picolet.emit('topic', data)`
on the Python side, `picolet.on('topic', handler)` on the JS side.

**Consequence**: MicroPython's `asyncio` is a hard runtime dependency.
The JS bridge can generate TypeScript types from a registered command
table.

### D4 — No headless renderer; `[ui]` is optional

CLI tools omit the `[ui]` section entirely. This selects the
`picolet-runtime-{platform}-cli` variant: no webview module, no LVGL, no
window module. Smallest binary.

**Consequence**: Three runtime variants per platform (webview, lvgl, cli),
not two.

### D5 — Raw binary in v1, packaging on roadmap

`picolet build` produces a single executable in `target/<target>/`. Native
installer formats (`.msi`, `.dmg`, `.AppImage`) deferred to a
`picolet bundle` subcommand on the roadmap, post-v0.4.

## Runtime artifact matrix

```
picolet-runtime-{windows-x64,linux-x64} × {webview,lvgl,cli}
= 6 release artifacts
```

macOS is out of scope for v1 (see CLAUDE.md).

## App-level `picolet.toml` schema

```toml
[app]
name = "my-app"
version = "0.1.0"
entry = "src/main.py"

# Omit [ui] for a CLI tool — picks the *-cli runtime variant.
[ui]
renderer = "webview"        # "webview" | "lvgl"
root = "ui"

[window]                    # ignored when [ui] absent
title = "My App"
size = [900, 600]
resizable = true

[build]
targets = ["windows-x64", "linux-x64"]

[romfs]
include = ["ui", "assets"]
```

## Source layout for `picolet-runtime`

Inherits the pydfu-win submodule + overlay pattern:

- `micropython/` — git submodule pointed at `andrewleech/micropython`.
- `mbm.toml` — list of feature branches that compose the integration
  branch via [`mbm`](https://gitlab.com/alelec/micropython-branch-manager).
- `overlay/` — downstream-only files re-applied on top of the integration
  branch after each rebase.
- `manifests/` — frozen-module manifests per renderer.
- `scripts/rebuild-integration.sh` — rebases via `mbm` and re-applies the
  overlay.

## IPC wire format

JSON messages over a `postMessage` shim (webview) or an in-process queue
(LVGL — `InProcessTransport` as mandated by FR-LV-4).

Request:
```json
{ "id": 17, "cmd": "greet", "args": { "name": "World" } }
```

Reply:
```json
{ "id": 17, "ok": true, "result": "Hello, World" }
```

Error:
```json
{ "id": 17, "ok": false, "error": { "type": "ValueError", "message": "..." } }
```

Event (push, no reply expected):
```json
{ "event": "progress", "data": { "pct": 42 } }
```

## Test surface (PH17)

### PICOLET_TEST_MODE

Set `PICOLET_TEST_MODE=1` in the environment before launching a picolet
binary to enable the debug/inspect port.  The environment variable is
read at runtime; it is never compiled into release builds (NFR-TEST-2).

### Port announcement contract

When `PICOLET_TEST_MODE=1` the runtime writes exactly one line to stderr
immediately after the debug port is bound:

```
picolet:test-port=<N>
```

where `<N>` is the decimal port number (1024–65535).  The port is bound
to 127.0.0.1 only (NFR-TEST-2 loopback restriction).

- **Linux/WebKit**: `WEBKIT_INSPECTOR_SERVER=127.0.0.1:<N>` is set via
  `setenv()` before `webkit_web_view_new()`.  The port is chosen with a
  bind/getsockname/close probe so the kernel picks an ephemeral port.
- **Windows/WebView2**: `--remote-debugging-port=<N>
  --remote-debugging-address=127.0.0.1` is passed as
  `AdditionalBrowserArguments` to `CreateCoreWebView2EnvironmentWithOptions`
  via a stack-allocated `ICoreWebView2EnvironmentOptions` vtable shim.

### AppHarness

`picolet.testing.AppHarness` (in `packages/picolet-testing/`) is the
host-side helper for writing automated tests against a running picolet app.

```python
async with AppHarness(binary, env={"PICOLET_TEST_MODE": "1"}) as h:
    page = await h.page()          # Playwright Page (webview) or facade
    await page.evaluate("...")     # run JS
    png_bytes = await h.snapshot() # LVGL screenshot
```

`AppHarness` reads the `picolet:test-port=<N>` announcement from stderr,
then connects to the debug port using the appropriate protocol:

- Chromium / WebView2: Playwright `connect_over_cdp("http://127.0.0.1:<N>")`
- WebKit: custom `WebKitPage` facade using the WebKit Inspector Protocol
  (WebSocket JSON-RPC at `ws://127.0.0.1:<N>`)
- LVGL: no debug port; `snapshot()` is driven via `picolet._test` stdio
  channel; `page()` is not available

### picolet._test API (LVGL)

Available only when `PICOLET_TEST_MODE=1`.  Import raises `ImportError`
otherwise.

```python
import picolet._test as t
t.tap(x, y)          # inject pointer press+release at (x, y)
t.press(key)         # inject keypad press+release
png = t.snapshot()   # capture PNG bytes of the current screen
```

---

## Perf-check CI (C4 + C5)

`.github/workflows/perf-check.yml` enforces two startup NFRs that are too
noisy to measure reliably in WSL2.  It runs on `ubuntu-latest` (GitHub-hosted
VM) for more consistent timing.

### NFR bounds

| ID | Metric | Median cap |
|----|--------|------------|
| NFR-EX-2 | spawn → window visible + first paint | 1500 ms |
| NFR-TEST-1 | spawn → `picolet:test-port=<N>` announcement | 3000 ms |

### Methodology

`scripts/perf-check.py` (PEP 723; `uv run --no-project scripts/perf-check.py`)
drives both measurements using `AppHarness` for timing and process management:

**NFR-TEST-1** — port-announcement latency:
1. Call `AppHarness.start()` with `_xvfb_display` set; this spawns the binary
   with `PICOLET_TEST_MODE=1`, drains stderr via a daemon thread, and sets
   `spawn_ms` immediately after `Popen()` returns.
2. On the webkit/xvfb path, `start()` returns as soon as `picolet:test-port=<N>`
   is seen (no inspector attach), setting `ready_ms` at that point.
3. Elapsed time is `ready_ms - spawn_ms`.

**NFR-EX-2** — window-visible latency:
1. Same `AppHarness.start()` call as NFR-TEST-1; `spawn_ms` marks the spawn
   instant.
2. After `start()` returns (port seen, child running), call
   `xdotool search --sync --pid <child-pid>` to confirm the app's own window
   is visible in the Xvfb framebuffer.
3. Elapsed time is `time.time()*1000 - spawn_ms` (covers spawn to xdotool
   return, inclusive of the port-announcement delay).

Each NFR is measured in 5 runs per example app; the **median** is compared
against the bound.  The **max** is recorded in the JSON artifact but does not
drive the gate.

### Noise tolerance

If a single run exceeds 2× the NFR bound while the median stays within it, the
script emits a soft warning in the log and the gate passes.  This handles
transient runner noise (shared CPU, page-cache cold misses).  The script does
not auto-disable the gate regardless of noise level; persistent breaches
(3+ consecutive CI runs failing) are the signal to escalate.

### `AppHarness` timing attributes

`AppHarness` exposes two float attributes (milliseconds since epoch) after
`start()` completes:

- `spawn_ms` — set immediately after `Popen()` returns inside `_spawn()`.
  `None` when the harness is constructed with a pre-spawned `_running_proc`
  (we did not spawn the process, so the spawn instant is unknown).
- `ready_ms` — set at the end of `start()` once the debug driver is attached
  (or the port line is seen if no inspector is available).

These are used by `scripts/perf-check.py` and are also available to any test
that wants to assert on startup latency without reinventing the measurement.

### Trigger conditions

The workflow runs:
- On `workflow_dispatch` (manual).
- On a weekly schedule (Sunday 03:00 UTC).
- On `push` to `dev` touching `packages/picolet-runtime/**` or `examples/**`.

Results are uploaded as a `perf-results` workflow artifact (JSON, retained 90
days) for trend analysis across runs.

---

## Frontend toolchains (PH18, FR-VUE-1..5)

Picolet supports multi-framework frontends through the `[ui.frontend]` table
in `picolet.toml`. The default (absent `[ui.frontend]` or `framework =
"vanilla"`) uses the v1 static-file model unchanged. Vue 3 + Vite + TypeScript
is the v1.1 framework of record.

### `[ui.frontend]` table schema

```toml
[ui.frontend]
framework = "vue"          # "vanilla" | "vue" | "react" (see O4)
build_cmd = "npm run build"  # optional override; default is npm run build
dist_dir  = "dist"           # optional; Vite default output directory
dev_url   = "http://localhost:5173/"  # optional; default for picolet dev
```

**`framework`** controls whether the frontend build hook fires during
`picolet build`. When `"vanilla"`, `picolet build` uses the static `[romfs]
include` path unchanged. For any other value, the npm-based pipeline runs.

**`react`** is accepted by the validator for forward compatibility (O4 —
React is out of scope for v1.1 but the pipeline is framework-agnostic once
npm is involved; a user who provides a correct `build_cmd` and `dist_dir`
for a React project will get the same pipeline treatment as Vue).

### Host requirements

Node ≥ 18 LTS (currently Node 20 or 22 LTS) must be on PATH for any project
with a non-vanilla `[ui.frontend]`. Node is a **host build-time dependency
only** — it is not shipped in the app binary. Vue/TS toolchain output is
compiled and packed into the romfs at build time; the runtime is pure C + MicroPython.

`picolet build` checks `shutil.which("npm")` before any npm invocation and
emits a clear error with a pointer to this section when npm is absent.

### Build pipeline integration

When `framework != "vanilla"`, `picolet build` inserts two steps:

1. **Step 4b**: `npm install --prefer-offline --no-fund --no-audit` in `app_root`.
   This is idempotent: fast on a warm `node_modules/` tree, respects
   `package-lock.json` when present (D2).

2. **Step 6a**: `_copy_dist_to_ui_root` copies `<dist_dir>/` (default: `dist/`)
   into the romfs staging area at `<ui_root>/` (default: `ui/`). Vue apps
   must **not** add `"ui"` to `[romfs] include` — the build pipeline copies
   `dist/` there automatically (R2 footgun: double-copy if both are present).

The `[ui.frontend]` table is **not emitted** into the romfs `picolet.toml`
(F8). The frozen runtime does not parse it; only `[ui].root` and `[ui].index`
are emitted, pointing the runtime at the packed assets.

### `base: './'` requirement (F10, R4)

Vite projects targeting picolet must set `base: './'` in `vite.config.ts`.
The picolet:// custom URI scheme (WebKitGTK) and WebView2 resolve sub-assets
differently from a standard HTTP origin. Absolute-path assets (`/assets/main.js`)
work on Linux but break on Windows via WebView2 (scheme resolution differs).
Relative paths (`./assets/main.js`) work on both.

Asset filename hashing (e.g. `assets/main-Cn3VHXS6.js`) is intentional
and correct — the full `dist/` is packed including hashed filenames.

### `PICOLET_DEV_URL` environment variable (D1, FR-VUE-2)

`picolet dev` uses environment variables (not romfs patching) to redirect the
runtime's initial URL to the Vite dev server. When `[ui.frontend].framework
!= "vanilla"`, `picolet dev`:

1. Spawns `npm run dev` in a new process group (`start_new_session=True`)
   before the first binary build.
2. Sets `PICOLET_DEV_URL=<dev_url>` (default: `http://localhost:5173/`) in the
   launched binary's environment.

The runtime (`picolet_ui._app.Application.__init__`) reads `PICOLET_DEV_URL` at
startup. If set, it skips the romfs `picolet://` load and calls
`webkit_web_view_load_uri(view, dev_url)` directly (Linux). On Windows it
calls `picolet_wv2_navigate(controller, url)` which invokes
`ICoreWebView2->Navigate` directly (R3 resolved — meta-refresh redirect
removed).

`PICOLET_DEV_URL` is **never set in production builds**. The released binary
launched directly or via `picolet run` has no such environment variable and
loads from romfs normally.

### Process group teardown (D3)

Vite spawns child processes (ESBuild, Rollup workers). A SIGTERM to the
`npm run dev` process does not propagate to grandchildren on Linux. `picolet dev`
creates the Vite process in a new session via `start_new_session=True` and
tears it down with `os.killpg(pgid, SIGTERM)`, ensuring the full process group
is terminated on exit (CTRL-C, rebuild, or `picolet dev` normal exit).

On Windows (`sys.platform == "win32"`), `vite_proc.terminate()` is used as
a best-effort fallback. Full process-group teardown on Windows requires
`CREATE_NEW_PROCESS_GROUP + GenerateConsoleCtrlEvent` and is deferred (R3).

### `picolet.d.ts` type declaration (FR-VUE-3)

`picolet-bridge-js` builds as an IIFE and exports nothing, so `tsc --declaration`
produces nothing useful. The type declaration for `window.picolet` is
**hand-authored** at `packages/picolet-bridge-js/src/picolet.d.ts`. It augments
the global `Window` interface via ambient module augmentation.

In a monorepo / workspace setup, Vue projects reference it via:

```typescript
// ui/src/env.d.ts
/// <reference path="../../../../packages/picolet-bridge-js/src/picolet.d.ts" />
```

In a standalone project scaffolded from `hello-vue`, a local copy of
`picolet.d.ts` is bundled in `ui/src/picolet.d.ts` and referenced the same way.

### npm lockfile convention (O3)

- `examples/with-vue/` commits `package-lock.json` for reproducible,
  offline-capable builds (`npm install --prefer-offline` respects it).
- `packages/picolet-templates/picolet_templates/hello-vue/` does NOT commit a
  lockfile. Users get the latest compatible versions at `picolet init` time.
  PH23's mirror script must not copy `package-lock.json` from examples into
  templates.

**Escape hatch for air-gapped CI**: if `npm install --prefer-offline` fails
on a cold cache, override by setting `PICOLET_NPM_ARGS` (future knob, not yet
implemented in PH18).

### R2 footgun: double-copy of ui/

If a Vue app's `picolet.toml` includes `"ui"` in `[romfs] include` AND has an
active frontend build hook, the vanilla static files would be copied alongside
(or overwriting) the Vite `dist/` output. The `with-vue` template omits
`[romfs] include` entirely to avoid this. The validator does not yet detect
this combination (deferred; documented here as a footgun).
