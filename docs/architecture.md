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
