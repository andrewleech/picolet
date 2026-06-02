# PH19 — pydfu example app

## Plan

### Goal

Build `examples/pydfu/` — a DFU firmware flasher GUI that demonstrates
Picolet for a low-level hardware-adjacent application. The app detects USB
DFU devices, parses `.dfu` files, flashes over DFU, and reports per-block
progress to the JS side via events. Aesthetic is the **industrial control
panel** direction: forge-orange accents on near-black matte, status LED
dot components, monospace tabular layouts, audit-log strip.

PH19 builds on PH18's Vue 3 + Vite + TypeScript toolchain without
modifying it. The picolet-webview runtime variant is used as-is — USB
access goes through the existing `ffi` module (same libusb/pyusb stack
the pydfu-win submodule proved). No new C module or runtime variant is
required.

---

### Spec coverage

| Spec id | Requirement | Where in this phase |
|---|---|---|
| FR-EX-1 | `picolet init <name> --template pydfu` scaffolds the pydfu DFU flasher GUI; functional end-to-end | Chunks 1–4 (app scaffold + Python commands + Vue routes) + Chunk 6 (init_cmd template wiring) |
| FR-EX-5 | Each example ships `tests/` with Playwright integration tests | Chunk 5 (Playwright test suite with mock USB shim) |
| FR-EX-6 | Each example ships `screenshots/` with auto-generated PNGs for major UI states | Chunk 7 (screenshot generation + capture scripts) |
| FR-EX-7 | The pydfu example uses host filesystem (`/dev/bus/usb/*` on Linux, WinUSB on Windows) via the runtime's `ffi` module | Chunk 2 (pydfu adapter — libusb path; Windows deferred to open question O1) |
| NFR-EX-1 | Binary size ≤ 3 MiB on linux-x64-webview | Chunk 8 (exit gate Test D) |
| NFR-EX-2 | Start-up ≤ 1500 ms first interactive frame | Chunk 8 (exit gate Test E) |
| NFR-EX-3 | CSS does not pull a runtime CSS framework heavier than 50 KB gzipped | Chunk 3 (hand-crafted CSS; no component library) |
| NFR-EX-4 | No external CDN at runtime; all assets in romfs | Chunk 3 (fonts bundled in romfs as woff2; no CDN) |
| NFR-EX-5 | Deterministic screenshots; same inputs → byte-identical PNG | Chunk 7 (mock-USB drives identical states; documented antialiasing caveat) |
| NFR-EX-6 | Screenshot gallery regenerated on every CI build; drift is a CI failure | Chunk 7 + Chunk 8 (gate Test H runs `picolet test --screenshot`) |
| NFR-EX-AESTHETIC | Must pass "show me the screenshot — is it memorable?" test | Chunk 3 (LED dots, tactile buttons, noise texture, typography — all per-spec) |

---

### Dependencies

#### From v1 (already landed, confirmed via codebase research)

- `picolet.command` / `picolet.emit` / `picolet.run` decorator and helpers at
  `packages/picolet-runtime/python/picolet/__init__.py` — the full IPC
  dispatcher surface PH19's Python side registers against.
- `picolet._dispatcher.Dispatcher` wire format (newline-delimited JSON) at
  `packages/picolet-runtime/python/picolet/_dispatcher.py` — the IPC
  protocol the JS bridge and the test harness consume.
- `ffi` module in the runtime — the same pyusb-over-libffi path that
  pydfu-win proved (`/home/anl/pydfu-win/micropython/tools/pydfu.py`).
  No new C code needed; `usb.core.find` is replaced by direct libusb
  ffi calls in the Picolet-adapted pydfu.

#### From PH17 (already landed)

- `picolet.testing.AppHarness` at
  `packages/picolet/picolet/testing/_harness.py` — the Playwright
  test driver used by `examples/pydfu/tests/`.
- `picolet test --screenshot` CLI at `packages/picolet/picolet/test_cmd.py`
  — the screenshot capture pipeline.
- `window.picolet.__ready__ === true` contract (set in `picolet-bridge-js`
  `index.ts` post-PH17) — AppHarness waits on this before driving.

#### From PH18 (already landed)

- `[ui.frontend]` table parser + validator in `build_cmd.py` and
  `validator.py` — `examples/pydfu/picolet.toml` uses the same table.
- `npm install --prefer-offline` + `npm run build` hook in `build_cmd` —
  the pydfu Vite build runs through this.
- `PICOLET_DEV_URL` env-var contract in `picolet_ui/_webview.py` — `picolet dev`
  against pydfu points Vite's dev server.
- `examples/with-vue/` — the structural baseline PH19 copies and replaces.
- `_IGNORE_DIRS` including `node_modules` and `dist` in `_paths.py` —
  inherited; no change needed.

#### What PH23 needs from PH19

- `examples/pydfu/` present and buildable — PH23's mirror script copies
  it into `packages/picolet/picolet/templates/pydfu/`.
- `examples/pydfu/screenshots/` non-empty — PH23's CI screenshot job
  validates these exist.

---

### Key research findings

**F1 — pydfu.py algorithm surface for IPC commands.**
The original code at `/home/anl/pydfu-win/micropython/tools/pydfu.py` uses
`usb.core` (PyUSB). For Picolet's frozen MicroPython environment, PyUSB is not
available. Instead, the DFU protocol operations are re-implemented using
direct libusb-1.0 ffi calls (the same libffi path the runtime already uses
for GTK/WebKit on Linux). The five IPC commands map to original code as
follows:

| IPC command | pydfu.py functions it wraps |
|---|---|
| `list_devices` | `get_dfu_devices()` via `FilterDFU` — enumerates `Class=0xFE, SubClass=0x01` |
| `read_dfu(path)` | `read_dfu_file(filename)` — parses DfuSe format, returns elements array |
| `flash(device_id, dfu_path)` | `init()` + `write_elements()` + `exit_dfu()` — full flash flow |
| `abort_flash()` | `abort_request()` — optional cancel path |
| `get_memory_layout(device_id)` | `get_memory_layout(device)` — for DFU descriptor display |

The DFU protocol itself (USB control transfers: DNLOAD/GETSTATUS/CLRSTATUS/
ABORT) is unchanged; only the USB access layer changes from PyUSB to libffi
calls against `libusb-1.0.so.0`.

**F2 — USB access via ffi on Linux.**
The runtime already links against `libusb-1.0` (confirmed via pydfu-win's
overlay approach). On Linux the device nodes are at `/dev/bus/usb/<bus>/<dev>`.
`libusb_init`, `libusb_get_device_list`, `libusb_get_device_descriptor`,
`libusb_claim_interface`, and `libusb_control_transfer` are the minimal
symbol set needed. These are all in `libusb-1.0.so.0` and accessible via
the `ffi` module. The frozen `pydfu_adapter.py` wraps them using the same
`ctypes`/libffi pattern already used in `picolet_ui/_gtk_ffi.py` and
`picolet_ui/_win_ffi.py`.

**F3 — Mock USB shim design for tests.**
The integration tests cannot assume a physical DFU device. The mock strategy:
- `examples/pydfu/src/pydfu_adapter.py` exposes a `_set_mock(mock_obj)`
  function that replaces the real USB layer with a fixture object implementing
  the same interface.
- `PICOLET_PYDFU_MOCK=1` env var causes `pydfu_adapter` to import
  `pydfu_mock.py` (also in `src/`) and call `_set_mock` at module init.
- `examples/pydfu/tests/conftest.py` sets `PICOLET_PYDFU_MOCK=1` in the
  harness env dict so all test runs use the mock shim automatically.
- The mock provides: one simulated device (`vid=0x0483 pid=0xdf11`), a
  simulated flash that emits one progress event per block (deterministic
  timing — each block is "instant" in the mock), and a simulated error
  mode triggered by a special DFU path suffix `*.error.dfu`.

This approach avoids modifying `@picolet.command` signatures or introducing
a new `--mock-usb` CLI flag into the runtime binary — the mock is purely a
module-level swap in frozen Python, invisible to the IPC layer.

**F4 — romfs read-only constraint and font files.**
The romfs is read-only after `picolet build`. Font woff2 files ship inside
the romfs at `/rom/ui/fonts/` (the `ui/public/fonts/` path in the Vite
source tree, which Vite copies verbatim to `dist/fonts/` during build,
which then lands in romfs). The webview serves them via the `picolet://`
scheme at `picolet:///ui/fonts/JetBrainsMono.woff2` etc. No writable data
directory is needed — pydfu is a read-only flasher; it reads `.dfu` files
from the host FS (arbitrary user-chosen paths) via `open()` in frozen
Python, which has full host FS access.

**F5 — Font choices: JetBrains Mono + IBM Plex Sans.**
The spec's display font is "Berkeley Mono (or PP Neue Machina)" — both
commercial. The spec explicitly lists JetBrains Mono as an OFL alternative
and IBM Plex Sans as the body font (OFL). PH19 uses:
- **JetBrains Mono** (OFL-1.1) for display / monospace — the `font-family:
  'JetBrains Mono', monospace` stack. Subset: Latin + subset of special
  chars used in the UI. Download source: `https://github.com/JetBrains/
  JetBrainsMono/releases` (woff2 subset). Commit the woff2 files directly;
  no build-time subsetting step (the variable font is ~130 KB woff2 for the
  full Latin range — within the asset budget).
- **IBM Plex Sans** (OFL-1.1) for body text (section subtitles, labels,
  descriptions). Subset: Latin. ~80 KB woff2. Download from
  `https://github.com/IBM/plex/releases`.
- Both files go under `ui/public/fonts/` in the source tree.
- CSS `@font-face` rules live in `ui/src/assets/fonts.css`, imported once
  in `ui/src/main.ts`.
- Total font weight in romfs: ~210 KB. This keeps the 3 MiB binary budget
  comfortably (runtime ~750 KB + Vue bundle ~300 KB + fonts ~210 KB =
  ~1.26 MB, well under 3 MiB).

**F6 — `[ui.frontend]` pipeline interaction.**
The build pipeline from PH18 (`build_cmd._run_frontend_build` +
`_copy_dist_to_ui_root`) handles `examples/pydfu/` without modification.
The `picolet.toml` for pydfu uses `framework = "vue"`, identical to
`with-vue`. The only `build_cmd.py` consideration is that `ui/public/fonts/`
is copied verbatim by Vite to `dist/fonts/` — this is Vite's default for
files in `public/` (they are not processed, just copied). No extra config.

**F7 — `init_cmd.py` `--template pydfu` wiring.**
`init_cmd._KNOWN_TEMPLATES` already includes `"hello-vue"`. PH19 adds
`"pydfu"`. The template dir lives at
`packages/picolet/picolet/templates/pydfu/`. The `_copy_template`
function already handles `.vue` and `.woff2` (binary copy for non-text
extensions). Font files are byte-copied correctly. The `{{name}}`
substitution applies to `.py`, `.toml`, `.ts`, `.vue`, `.html`, `.json`
only — woff2 files are not touched.

**F8 — picolet-webview runtime is sufficient; no new C module.**
pydfu-win demonstrated that the libusb/libffi path works for USB access
without a custom C module. The `ffi` module in the runtime binary can bind
`libusb-1.0.so.0` symbols at runtime. The frozen `pydfu_adapter.py` uses
`import ffi; lib = ffi.open("libusb-1.0.so.0")` and declares the required
symbols. This is the same pattern as `picolet_ui/_gtk_ffi.py` (`ffi.open(
"libgtk-3.so.0")`). No runtime rebuild is needed for PH19 — the existing
`picolet-runtime-linux-x64-webview` binary is used as-is.

**F9 — Windows USB is deferred.**
FR-EX-7 says "WinUSB on Windows". The pydfu-win submodule has a
WinUSB/libffi overlay for Windows, but integrating it into the frozen
Python requires binding different symbols than Linux (`SetupDiEnumDeviceInterfaces`,
`WinUsb_Initialize`, `WinUsb_ControlTransfer`). This is non-trivial and
out of scope for PH19 as a first pass. Linux is the primary target.
The `pydfu_adapter.py` includes a `sys.platform == "win32"` guard that
raises `NotImplementedError("Windows USB not yet implemented in PH19")`.
This is recorded as Open question O1.

**F10 — Background asyncio task pattern for `flash` command.**
The `flash` command must push `dfu:progress` events while running. The
pattern from `examples/with-vue/src/main.py` is the `_ticker` coroutine
pattern: `asyncio.create_task(...)` inside the command handler, with the
task pushing events via `await picolet.emit(...)`. The command returns
immediately with `{"status": "started", "task_id": ...}` so the JS side
is not blocked. Progress events carry `{"addr": int, "done": int,
"total": int, "pct": int}`. A completion event `dfu:done` or error event
`dfu:error` is emitted when the task finishes.

**F11 — File picker approach.**
The spec routes are `/`, `/flash`, `/log`. There is no native file-picker
IPC command in the current runtime (no `window.showOpenFilePicker` equivalent
exposed). For `/flash`, the file-pick UX is a text input for the `.dfu` file
path (matching the industrial control-panel aesthetic — a path field, not a
GUI picker). An optional `read_dir(path)` command can list `.dfu` files in a
given directory for a quick-pick list. This avoids any native dialog
dependency. The path field is pre-populated with the user's last-used path
stored in a Vue `localStorage` key (writable from JS, no Python involvement).

**F12 — Test conftest pattern.**
The Playwright tests in `examples/pydfu/tests/` use `AppHarness` directly,
following the PH17/PH18 pattern. `conftest.py` provides a `harness` pytest
fixture that:
1. Sets `env={"PICOLET_PYDFU_MOCK": "1"}` in the harness constructor.
2. Yields the started harness.
3. Calls `await harness.stop()` in the fixture teardown.
The fixture is async (`pytest-asyncio`). Each test navigates to the target
route, exercises the UI via Playwright page API, and asserts DOM state.

---

### Aesthetic spec

All values are mandatory; the developer must not deviate without recording a
decision commit.

#### CSS custom properties

```css
:root {
  --forge:       #ff6b1a;   /* primary accent — buttons, LED active */
  --chassis:     #0a0c0e;   /* body background */
  --surface:     #12161a;   /* raised surfaces (panels, header) */
  --rule:        #1f2226;   /* 1px pane dividers */
  --text-pri:    #e8eaed;   /* primary text */
  --text-sec:    #7a8390;   /* secondary / label text */
  --led-ok:      #4ade80;   /* green status LED */
  --led-warn:    #facc15;   /* amber status LED */
  --led-alarm:   #ef4444;   /* red status LED */
  --led-idle:    #2a2e34;   /* inactive LED (dark grey dot) */
  --font-mono:   'JetBrains Mono', monospace;
  --font-body:   'IBM Plex Sans', system-ui, sans-serif;
}
```

#### Layout grid

Three rows, full-height viewport, no scrolling on the outer shell:

```
┌────────────────────────────────────────┐  ← header-rail: 40px
│  PYDFU  ●  SERIAL: STM32F4xx  ●  IDLE │     brushed-aluminium gradient
│        (forge-accent border-bottom)    │     linear-gradient(180deg, #1c2028 0%, #12161a 100%)
├─────────────────┬──────────────────────┤  ← main-pane: flex-grow 1
│ device-list 40% │  detail-pane 60%     │     1px --rule vertical divider
│                 │                      │
│  (tabular mono  │  descriptor tables   │
│   LED dots)     │  progress bar        │
│                 │  path input          │
├────────────────────────────────────────┤  ← audit-strip: 120px
│ AUDIT LOG > [timestamp] event ...      │     terminal-green #4ade80 mono
│             [timestamp] event ...      │     background: #050708
│             autoscrolls, filterable    │     overflow-y: auto
└────────────────────────────────────────┘
```

Route `/log` expands the audit-strip to full height (replaces main-pane).

#### Section title style

```css
.section-title {
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-sec);
}
```

#### LED dot component

```vue
<!-- LedDot.vue -->
<!-- status: 'ok' | 'warn' | 'alarm' | 'idle' | 'pulse' -->
<template>
  <span class="led-dot" :class="[`led-${status}`]" />
</template>

<style scoped>
.led-dot {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.1);
}
.led-idle   { background: var(--led-idle); }
.led-ok     { background: var(--led-ok); }
.led-warn   { background: var(--led-warn); }
.led-alarm  { background: var(--led-alarm); }
.led-pulse  { background: var(--forge); animation: pulse 0.5s ease-in-out infinite; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}
</style>
```

#### Tactile button style

```css
.btn {
  font-family: var(--font-mono);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-pri);
  background: #1c2028;
  border: 1px solid #2e3540;
  padding: 6px 14px;
  cursor: pointer;
  border-radius: 0;            /* no rounded corners per spec */
  box-shadow:
    0 1px 0 rgba(255,255,255,0.08),   /* top highlight */
    0 -1px 0 rgba(0,0,0,0.4),         /* bottom shadow */
    inset 0 1px 0 rgba(255,255,255,0.05);
  transition: box-shadow 80ms, transform 80ms;
}
.btn:active {
  transform: translateY(1px);
  box-shadow:
    inset 0 2px 4px rgba(0,0,0,0.5);  /* push-in shadow inversion */
}
.btn-primary { background: var(--forge); border-color: #cc4d0a; color: #fff; }
```

#### Noise texture

Inline data URI in the body `::before` pseudo-element. A 1×1 RGBA noise
tile at ~4% opacity:

```css
body::before {
  content: '';
  position: fixed; inset: 0; pointer-events: none; z-index: 9999;
  background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE...");
  /* base64 of a ~400 B 1×1 RGBA noise PNG generated with Python Pillow */
  opacity: 0.04;
}
```

The noise PNG (base64 string) is computed once with:
```python
from PIL import Image, ImageFilter
import io, base64, random
img = Image.new('RGBA', (64,64))
pixels = [( random.randint(0,255),)*3 + (255,) for _ in range(64*64)]
img.putdata(pixels); buf = io.BytesIO()
img.save(buf, 'PNG'); print(base64.b64encode(buf.getvalue()).decode())
```
The exact base64 string is committed; it does not need regeneration.

#### No rounded corners

`border-radius: 0` on all panes, cards, and buttons. `border-radius` is
only permitted on the LED dot (which must be circular).

---

### Implementation breakdown

Seven chunks ordered by dependency. Each chunk is independently testable.

---

#### Chunk 1 — `examples/pydfu/` scaffold (structure, picolet.toml, Vite config)

**Goal**: Lay down the directory structure and configuration so `picolet build`
runs cleanly even before any real Python or Vue logic is present.

**Files to create:**

- `examples/pydfu/picolet.toml`:
  ```toml
  [app]
  name = "pydfu"
  version = "0.1.0"
  entry = "src/main.py"

  [ui]
  renderer = "webview"
  root = "ui"
  index = "index.html"

  [ui.frontend]
  framework = "vue"
  build_cmd = "npm run build"
  dist_dir = "dist"
  dev_url = "http://localhost:5173/"

  [window]
  title = "PyDFU"
  size = [1200, 800]
  resizable = true
  ```

- `examples/pydfu/package.json` — mirrors `examples/with-vue/package.json`
  but `name = "pydfu"`. Same dep versions: `vue@^3`, `vite@^5`,
  `@vitejs/plugin-vue@^5`, `vue-tsc@^2`, `typescript@^5`. No extra deps.

- `examples/pydfu/vite.config.ts` — identical to `examples/with-vue/vite.config.ts`
  with `name: "pydfu"`. Key: `base: './'`, `build.outDir: 'dist'`,
  `build.emptyOutDir: true`.

- `examples/pydfu/tsconfig.json` / `tsconfig.node.json` — copy from
  `examples/with-vue/`, name substituted.

- `examples/pydfu/ui/index.html` — Vite entry referencing `src/main.ts`.
  Title: `PyDFU`.

- `examples/pydfu/ui/src/main.ts` — `createApp(App).mount('#app')`.

- `examples/pydfu/ui/src/env.d.ts` — triple-slash reference to `picolet.d.ts`.

- `examples/pydfu/ui/src/App.vue` — stub: renders `<router-view />` once
  Vue Router is added in Chunk 3. For this chunk: a plain `<div>PyDFU
  Loading…</div>` so the build is exercisable.

- `examples/pydfu/ui/public/fonts/` — directory placeholder (actual woff2
  files added in Chunk 3).

- `examples/pydfu/src/main.py` — stub: `import picolet; import picolet_ui as ui;
  def main(): app = ui.Application(); app.run(); main()`. Enough to build.

**Exercise:**
```bash
cd /home/anl/picolet/examples/pydfu
npm install --prefer-offline
picolet build --no-sbom
# binary exists at target/linux-x64/pydfu
```

---

#### Chunk 2 — Python backend: `pydfu_adapter.py` + `main.py` commands

**Goal**: Implement the five IPC commands using a libusb-ffi adapter on
Linux, with a `PICOLET_PYDFU_MOCK=1` swap path for tests.

**Pattern reference:**
- `packages/picolet-runtime/python/picolet_ui/_gtk_ffi.py` — how `ffi.open()`
  and `lib.func()` are used to bind shared library symbols.
- `packages/picolet-runtime/python/picolet/__init__.py` — how `@picolet.command`
  is declared.
- `examples/with-vue/src/main.py` — the `asyncio.create_task` + `picolet.emit`
  pattern for background tasks.

**Files to create:**

- `examples/pydfu/src/pydfu_adapter.py` — the USB adapter module.

  Structure:
  ```python
  import os, sys, struct
  
  _mock = None
  
  def _set_mock(obj):
      global _mock
      _mock = obj
  
  if os.getenv("PICOLET_PYDFU_MOCK") == "1":
      from pydfu_mock import MockUSB
      _set_mock(MockUSB())
  
  # ---- libusb ffi bindings (Linux only) ----
  # If not mocked and not Linux: raise NotImplementedError on first use.
  _lib = None
  
  def _ensure_lib():
      global _lib
      if _lib is not None:
          return _lib
      if sys.platform == "win32":
          raise NotImplementedError("Windows USB not yet implemented in PH19")
      import ffi
      _lib = ffi.open("libusb-1.0.so.0")
      # Declare symbol signatures here:
      # libusb_init, libusb_exit, libusb_get_device_list,
      # libusb_get_device_descriptor, libusb_open, libusb_claim_interface,
      # libusb_control_transfer, libusb_release_interface, libusb_close,
      # libusb_free_device_list
      return _lib
  
  def list_dfu_devices():
      """Return list of {"bus":int, "addr":int, "vid":int, "pid":int}."""
      if _mock: return _mock.list_dfu_devices()
      # Real libusb path: iterate device list, check bDeviceClass etc.
      ...
  
  def read_dfu_file(path):
      """Parse a .dfu file. Return list of element dicts or raise ValueError."""
      # Pure Python — same algorithm as pydfu.py:read_dfu_file()
      # Uses open() (host FS access; no USB needed).
      ...
  
  def flash_device(device_id, elements, progress_cb):
      """Flash elements to device. Calls progress_cb(addr, done, total) per block."""
      if _mock: return _mock.flash_device(device_id, elements, progress_cb)
      # Real libusb path: init → write_elements loop → exit_dfu
      ...
  
  def abort_flash():
      if _mock: return _mock.abort_flash()
      ...
  
  def get_memory_layout(device_id):
      if _mock: return _mock.get_memory_layout(device_id)
      ...
  ```

  The `read_dfu_file` function is a pure-Python re-implementation of
  `pydfu.py`'s `read_dfu_file`, `consume`, `named`, `compute_crc`,
  `cstring` — no PyUSB dependency. It reads the file via `open()` from
  the host filesystem, which the frozen runtime permits.

- `examples/pydfu/src/pydfu_mock.py` — test mock:
  ```python
  class MockUSB:
      MOCK_VID = 0x0483; MOCK_PID = 0xdf11
      
      def list_dfu_devices(self):
          return [{"bus": 1, "addr": 1, "vid": self.MOCK_VID,
                   "pid": self.MOCK_PID, "manufacturer": "STMicro",
                   "product": "STM32 DFU"}]
      
      def get_memory_layout(self, device_id):
          return [{"addr": 0x08000000, "last_addr": 0x080FFFFF,
                   "size": 1048576, "num_pages": 256, "page_size": 4096}]
      
      def read_dfu_file(self, path):
          # Delegated to pydfu_adapter.read_dfu_file — pure Python, no mock needed
          raise NotImplementedError("use pydfu_adapter.read_dfu_file directly")
      
      def flash_device(self, device_id, elements, progress_cb):
          import time
          total = sum(e["size"] for e in elements)
          done = 0
          for elem in elements:
              for block in range(0, elem["size"], 2048):
                  chunk = min(2048, elem["size"] - block)
                  done += chunk
                  progress_cb(elem["addr"] + block, done, total)
          # "*.error.dfu" sentinel triggers simulated error:
          # checked by flash command before calling this
      
      def abort_flash(self): pass
  ```

- `examples/pydfu/src/main.py` — full IPC command surface:
  ```python
  import asyncio
  import picolet
  import picolet_ui as ui
  import pydfu_adapter as dfu
  
  @picolet.command
  async def list_devices(args):
      return dfu.list_dfu_devices()
  
  @picolet.command
  async def read_dfu(args):
      path = args.get("path") if isinstance(args, dict) else args
      return dfu.read_dfu_file(path)
  
  @picolet.command
  async def get_memory_layout(args):
      device_id = args.get("device_id") if isinstance(args, dict) else args
      return dfu.get_memory_layout(device_id)
  
  _flash_task = None
  
  @picolet.command
  async def flash(args):
      global _flash_task
      device_id = args["device_id"]
      dfu_path   = args["dfu_path"]
      
      elements = dfu.read_dfu_file(dfu_path)
      if not elements:
          return {"ok": False, "error": "no elements in dfu file"}
      
      # Error sentinel for mock
      if dfu_path.endswith(".error.dfu"):
          await picolet.emit("dfu:error", {"message": "simulated flash error"})
          return {"ok": False, "error": "simulated error"}
      
      async def _run():
          def _progress(addr, done, total):
              pct = (done * 100) // total if total else 0
              asyncio.get_event_loop().create_task(
                  picolet.emit("dfu:progress",
                             {"addr": addr, "done": done,
                              "total": total, "pct": pct}))
          try:
              dfu.flash_device(device_id, elements, _progress)
              await picolet.emit("dfu:done", {"ok": True})
          except Exception as e:
              await picolet.emit("dfu:error", {"message": str(e)})
      
      _flash_task = asyncio.create_task(_run())
      return {"ok": True, "status": "started"}
  
  @picolet.command
  async def abort_flash(args):
      global _flash_task
      if _flash_task and not _flash_task.done():
          _flash_task.cancel()
      dfu.abort_flash()
      return {"ok": True}
  
  def main():
      app = ui.Application()
      app.run()
  
  main()
  ```

**Exercise:**
```bash
cd /home/anl/picolet/examples/pydfu
PICOLET_PYDFU_MOCK=1 python -c "
import sys; sys.path.insert(0, 'src')
import pydfu_adapter as d
print(d.list_dfu_devices())   # [{'bus':1, 'addr':1, ...}]
print(d.read_dfu_file.__doc__)
"
```

---

#### Chunk 3 — Vue frontend: aesthetic, fonts, components, routes

**Goal**: Build the complete Vue 3 frontend with the industrial control-panel
aesthetic, including font loading, LED dot components, tactile buttons, and
all three routes.

**Files to create / modify:**

- `examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2` — downloaded
  from the JetBrains Mono OFL release. Variable font woff2 preferred;
  Regular weight woff2 as fallback if variable is too large.
- `examples/pydfu/ui/public/fonts/IBMPlexSans-Regular.woff2`
- `examples/pydfu/ui/public/fonts/IBMPlexSans-SemiBold.woff2` — for labels.

- `examples/pydfu/ui/src/assets/fonts.css`:
  ```css
  @font-face {
    font-family: 'JetBrains Mono';
    src: url('/fonts/JetBrainsMono-Regular.woff2') format('woff2');
    font-weight: 400 700;
    font-display: block;   /* block prevents FOUT in webview */
  }
  @font-face {
    font-family: 'IBM Plex Sans';
    src: url('/fonts/IBMPlexSans-Regular.woff2') format('woff2');
    font-weight: 400;
    font-display: block;
  }
  @font-face {
    font-family: 'IBM Plex Sans';
    src: url('/fonts/IBMPlexSans-SemiBold.woff2') format('woff2');
    font-weight: 600;
    font-display: block;
  }
  ```

- `examples/pydfu/ui/src/assets/main.css` — global styles:
  - CSS custom properties block (all `--forge`, `--chassis`, etc. as
    specified in the Aesthetic spec section above).
  - `body { background: var(--chassis); color: var(--text-pri); margin: 0;
    font-family: var(--font-body); font-size: 13px; }`.
  - `body::before` noise texture pseudo-element (4% opacity data URI).
  - `.btn`, `.btn-primary`, `.btn-danger` tactile button styles.
  - `.section-title` uppercase monospace label style.
  - `* { box-sizing: border-box; }`.
  - Hard 1px rules: `.pane-divider { border-left: 1px solid var(--rule); }`.

- `examples/pydfu/ui/src/main.ts` — updated to import `./assets/main.css`
  and `./assets/fonts.css`, install Vue Router.

- `examples/pydfu/ui/src/router/index.ts` — Vue Router with three routes:
  - `"/"` → `HomeView.vue`
  - `"/flash"` → `FlashView.vue`
  - `"/log"` → `LogView.vue`

- `examples/pydfu/ui/src/components/LedDot.vue` — LED dot component per the
  Aesthetic spec. Props: `status: 'ok' | 'warn' | 'alarm' | 'idle' | 'pulse'`.

- `examples/pydfu/ui/src/components/AuditStrip.vue` — the 120px
  terminal-green log strip at the bottom. Props: `entries: LogEntry[]`.
  Auto-scrolls to bottom when entries change (`watchEffect` + `nextTick` +
  `scrollTop = scrollHeight`). Monospace, `#4ade80` text on `#050708`
  background.

- `examples/pydfu/ui/src/components/HeaderRail.vue` — 40px header bar.
  Displays: app name, device serial if connected (centre), global status LED
  (right). Background: `linear-gradient(180deg, #1c2028 0%, #12161a 100%)`.
  Border bottom: `1px solid var(--forge)`.

- `examples/pydfu/ui/src/components/DeviceList.vue` — 40%-width left pane.
  Lists devices as monospace rows with LED status dots. Selection emits
  `select-device` event to parent. Shows "NO DFU DEVICES" in dim text when
  empty. Auto-refreshes every 500 ms via `setInterval` (calls
  `window.picolet.invoke("list_devices")`).

- `examples/pydfu/ui/src/components/DeviceDetail.vue` — 60%-width right pane.
  Shows memory layout table when a device is selected; DFU descriptor fields
  in tabular monospace.

- `examples/pydfu/ui/src/views/HomeView.vue` — route `/`. Asymmetric
  main-pane: `<DeviceList>` (40%) + `<DeviceDetail>` (60%) separated by a
  1px vertical rule. Header: `<HeaderRail>`. Footer: `<AuditStrip>`.

- `examples/pydfu/ui/src/views/FlashView.vue` — route `/flash`. Left pane:
  file-path input (monospace, full-width) + `[READ FILE]` button to invoke
  `read_dfu`. Right pane: parsed DFU descriptor display + `[FLASH]` and
  `[ABORT]` buttons. Progress bar: a `<div>` whose width is `pct + "%"`,
  coloured `--forge`. Listens for `dfu:progress`, `dfu:done`, `dfu:error`
  events via `window.picolet.on(...)`.

- `examples/pydfu/ui/src/views/LogView.vue` — route `/log`. The
  `<AuditStrip>` expanded to full height. A text filter input at the top
  (monospace, 1px border, no border-radius). Clears log button.

- `examples/pydfu/ui/src/App.vue` — top-level shell. Renders
  `<HeaderRail>`, `<RouterView>`, and global event subscriptions for the
  audit log. Maintains a `logEntries` ref that all routes append to.
  Provides `logEntries` via Vue `provide`/`inject`.

- `examples/pydfu/package.json` — add `vue-router@^4` as a dependency.

**Note on CSS framework:** no Tailwind, no component library. All CSS is
hand-crafted in `main.css` + `<style scoped>` blocks. Total CSS budget is
well under 50 KB gzipped (NFR-EX-3). Tailwind is explicitly not used here
because the design language is custom enough that Tailwind's purged output
would not add value over hand-crafted rules.

**Exercise:**
```bash
cd /home/anl/picolet/examples/pydfu
npm install --prefer-offline
npm run typecheck    # vue-tsc --noEmit must exit 0
npm run build        # Vite build must succeed
# Inspect dist/ — should contain index.html, assets/, fonts/
ls dist/fonts/       # JetBrainsMono-Regular.woff2 IBMPlexSans-Regular.woff2 ...
```

---

#### Chunk 4 — Integration: build, run, manual smoke-test

**Goal**: Produce a working binary that starts, shows the UI, and (with a
real DFU device or the mock env var) exercises the core flows. Confirm the
aesthetic is correct via manual visual inspection and a screenshot.

**Files to modify / verify:**

- `examples/pydfu/picolet.toml` — no changes needed from Chunk 1.
- Verify `picolet build` succeeds: `cd examples/pydfu && picolet build --no-sbom`.
- Verify binary size ≤ 3 MiB.
- Run the binary: `PICOLET_PYDFU_MOCK=1 ./target/linux-x64/pydfu` — window
  must open within 1500 ms showing the home route with "NO DFU DEVICES" in
  the device list pane (mock returns 0 devices until manually extended).
  Update mock to return one device so the populated state is reachable.

**Screenshots captured manually in this chunk** (before Chunk 7 automates
it):
- Window open, device list empty.
- Window open, device list with one mock device selected.

**Size check:**
```bash
wc -c examples/pydfu/target/linux-x64/pydfu
# Must be <= 3145728 (3 MiB)
```

---

#### Chunk 5 — Playwright integration tests + mock shim

**Goal**: `examples/pydfu/tests/` with Playwright tests covering the
documented user flows: device discovery, DFU read, flash progress, flash
complete, flash error.

**Files to create:**

- `examples/pydfu/tests/conftest.py`:
  ```python
  import asyncio, pytest, sys
  from pathlib import Path
  from picolet.testing import AppHarness
  
  BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "pydfu"
  
  @pytest.fixture
  async def harness():
      h = AppHarness(
          str(BINARY),
          env={"PICOLET_PYDFU_MOCK": "1"},
      )
      await h.start()
      yield h
      await h.stop()
  ```

- `examples/pydfu/tests/test_device_list.py`:
  ```python
  """Test: device list auto-refresh shows mock device."""
  import pytest
  pytestmark = pytest.mark.asyncio
  
  async def test_device_list_populated(harness):
      page = harness.page
      await page.goto("picolet:///ui/index.html")  # or await until __ready__
      # Wait for DeviceList to render with mock device
      await page.wait_for_selector(".device-row")
      text = await page.inner_text(".device-row")
      assert "0483" in text  # VID in hex
      assert "df11" in text  # PID in hex
  
  async def test_device_list_empty_state(harness):
      # With no mock devices (override: PICOLET_PYDFU_MOCK=1 returns 1 device
      # by default; test device-list-empty via a separate fixture that uses
      # PICOLET_PYDFU_MOCK_EMPTY=1 or by navigating before the first refresh tick)
      page = harness.page
      # Check initial render shows loading/empty state before first 500ms refresh
      # This is timing-dependent; use --mock-empty env var in future
      pytest.skip("empty-state timing test; covered by screenshot")
  ```

- `examples/pydfu/tests/test_flash_flow.py`:
  ```python
  """Test: full flash flow with mock USB."""
  import asyncio, pytest
  from pathlib import Path
  pytestmark = pytest.mark.asyncio
  
  DFU_FIXTURE = Path(__file__).parent / "fixtures" / "test.dfu"
  
  async def test_flash_complete(harness):
      page = harness.page
      await page.goto("picolet:///ui/index.html#/flash")
      await page.wait_for_selector(".path-input")
      await page.fill(".path-input", str(DFU_FIXTURE))
      await page.click(".btn-read-dfu")
      await page.wait_for_selector(".dfu-elements-table")
      # Click FLASH
      await page.click(".btn-flash")
      # Wait for dfu:done event reflected in DOM
      await page.wait_for_selector(".flash-status-done", timeout=15000)
      text = await page.inner_text(".flash-status-done")
      assert "COMPLETE" in text.upper()
  
  async def test_flash_error(harness):
      page = harness.page
      await page.goto("picolet:///ui/index.html#/flash")
      await page.fill(".path-input", str(DFU_FIXTURE.with_suffix(".error.dfu")))
      await page.click(".btn-read-dfu")
      await page.click(".btn-flash")
      await page.wait_for_selector(".flash-status-error", timeout=15000)
  ```

- `examples/pydfu/tests/fixtures/test.dfu` — a minimal valid DfuSe file
  (not a real firmware; just a structurally valid stub with correct CRC and
  a single 1 KB element). Generated once with a small Python script and
  committed as a binary fixture. The mock's `flash_device` does not validate
  element content — any structurally valid .dfu file works.

- `examples/pydfu/tests/gen_fixture.py` — the one-time script that
  generates `test.dfu`. Committed but not run in CI:
  ```python
  # /// script
  # dependencies = []
  # ///
  """Generate a minimal valid DfuSe test fixture."""
  import struct, zlib
  ...
  ```

- `examples/pydfu/tests/pytest.ini` (or `pyproject.toml` addendum):
  ```ini
  [pytest]
  asyncio_mode = auto
  ```

**Exercise:**
```bash
cd /home/anl/picolet/examples/pydfu
# binary must exist (Chunk 4)
uv run --with pytest --with pytest-asyncio pytest tests/ -v
```

---

#### Chunk 6 — `init_cmd` template wiring (`--template pydfu`)

**Goal**: `picolet init <name> --template pydfu` scaffolds a buildable copy of
the pydfu app with `{{name}}` substituted.

**Files to create:**

- `packages/picolet/picolet/templates/pydfu/` — structurally identical
  to `examples/pydfu/` with `{{name}}` in the appropriate places:
  - `picolet.toml`: `name = "{{name}}"`, window title `"{{name}}"`.
  - `package.json`: `"name": "{{name}}"`.
  - `ui/index.html`: `<title>{{name}}</title>`.
  - `src/main.py`: comment `# {{name}} — DFU flasher (picolet example)`.
  - `.vue` files: heading / identifier text where the app name appears.
  - Font woff2 files: byte-copied verbatim (handled by `_copy_template`
    already, since `.woff2` is not in `_TEXT_EXTENSIONS`).

**Files to modify:**

- `packages/picolet/picolet/init_cmd.py`:
  - `_KNOWN_TEMPLATES`: add `"pydfu"`.
  - `add_parser` help string: add `"pydfu"` to the listed templates.

**Note:** Per the PH23 convention, the `packages/picolet/pydfu/`
copy is maintained manually until PH23's mirror script automates it. The
`examples/pydfu/` copy is the authoritative source; changes there must be
manually reflected in the template.

**Exercise:**
```bash
cd /tmp
picolet init test-dfu-app --template pydfu
cd test-dfu-app
picolet validate                       # must exit 0
npm install --prefer-offline
picolet build --no-sbom                # must produce target/linux-x64/test-dfu-app
```

---

#### Chunk 7 — Screenshots (`screenshots/` directory)

**Goal**: Produce all six required screenshots via `picolet test --screenshot`
against the mock-USB binary.

**Required screenshots:**

| Filename | Route / state | How to reach |
|---|---|---|
| `device-list-empty.png` | `/` — no devices (mock returns empty list) | Extra env var `PICOLET_PYDFU_MOCK_EMPTY=1` or intercept before first 500ms tick |
| `device-list-populated.png` | `/` — one mock device visible + selected | Default mock (1 device) |
| `flash-start.png` | `/flash` — DFU file loaded, flash not yet started | After `read_dfu` returns, before clicking FLASH |
| `flash-mid-progress.png` | `/flash` — progress bar at ~50% | Mid-flash (mock provides multiple blocks) |
| `flash-complete.png` | `/flash` — `dfu:done` received | After mock flash completes |
| `flash-error.png` | `/flash` — `dfu:error` received | Using `.error.dfu` sentinel path |

**Files to create:**

- `examples/pydfu/screenshots/` — directory; initially populated by running
  `capture_screenshots.sh`.

- `examples/pydfu/screenshots/capture_screenshots.sh` — the capture script:
  ```bash
  #!/usr/bin/env bash
  # Captures all six pydfu screenshots via picolet test --screenshot.
  # Requires: binary built, display available (or xvfb-run).
  REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
  BINARY="$REPO_ROOT/examples/pydfu/target/linux-x64/pydfu"
  SHOTS="$REPO_ROOT/examples/pydfu/screenshots"
  PICOLET="uv run -p picolet-cli picolet"
  
  # Screenshot helpers use AppHarness --run mode with inline scripts that:
  # 1. Navigate to route
  # 2. Wait for DOM state
  # 3. Call harness.screenshot(path)
  # Each script is a one-shot Python file in screenshots/scripts/
  for script in "$SHOTS/scripts/"*.py; do
      name="$(basename "$script" .py)"
      PICOLET_PYDFU_MOCK=1 $PICOLET test --run "$script" "$BINARY"
      echo "captured: $name.png"
  done
  ```

- `examples/pydfu/screenshots/scripts/device_list_populated.py` — drives
  the harness to navigate to `/`, wait for `.device-row`, then screenshot.

- `examples/pydfu/screenshots/scripts/flash_complete.py` — navigates to
  `/flash`, fills path, clicks READ, clicks FLASH, waits for
  `.flash-status-done`, screenshots.

- (one script per screenshot)

- The actual PNG files are committed to the repo after first generation.

**NFR-EX-5 determinism note:** The mock USB produces the same state on every
run (fixed device identity, fixed block count, no timing variation). However,
CSS `animation: pulse` introduces per-frame variation in LED dot opacity. To
make screenshots deterministic, add a class `.no-animation` to the root
element when `window.__PICOLET_SCREENSHOT_MODE__ === true`, and set that flag
in the screenshot scripts via `page.evaluate(...)`. The `.no-animation` class
disables all CSS animations:
```css
.no-animation * { animation: none !important; transition: none !important; }
```

**Exercise:**
```bash
cd /home/anl/picolet/examples/pydfu
bash screenshots/capture_screenshots.sh
# Each screenshot/*.png must be > 1 KB and valid PNG:
python3 -c "from PIL import Image; [Image.open(f).verify() for f in __import__('glob').glob('screenshots/*.png')]"
```

---

#### Chunk 8 — Phase tests and exit gate

**Goal**: `tests/phase-19/run.sh` exercises all FR-EX and NFR-EX gates.
Mirrors the structure of `tests/phase-18/run.sh`.

**Files to create:**

- `tests/phase-19/run.sh`:

  | Gate | What it proves | Command |
  |---|---|---|
  | A | FR-EX-1: scaffold — `picolet validate` in `examples/pydfu/` exits 0 | `picolet validate` |
  | B | FR-EX-1: `picolet build --no-sbom` in `examples/pydfu/` produces binary | `picolet build --no-sbom` |
  | C | NFR-EX-1: binary ≤ 3 MiB | `wc -c target/linux-x64/pydfu ≤ 3145728` |
  | D | NFR-EX-4: no CDN references | `strings pydfu | grep -cE "cdn.\|unpkg.\|jsdelivr."` = 0 |
  | E | NFR-EX-2: startup ≤ 1500 ms | AppHarness `time_to_ready` assertion |
  | F | FR-EX-1: `list_devices` IPC round-trip (mock) | `picolet test --run tests/phase-19/smoke_list_devices.py` |
  | G | FR-EX-1: `read_dfu` IPC round-trip | `picolet test --run tests/phase-19/smoke_read_dfu.py` |
  | H | FR-EX-6: screenshots present and valid PNG | `python3 -c "PIL verify loop"` against `examples/pydfu/screenshots/` |
  | I | FR-EX-5: Playwright test suite passes (mock USB) | `pytest examples/pydfu/tests/ -v` |
  | J | FR-EX-1 + NFR-EX-3: CSS < 50 KB gzip | `du -b examples/pydfu/dist/assets/*.css ≤ 51200` |
  | K | NFR-EX-AESTHETIC: font files present in binary | `strings pydfu | grep -q "JetBrains Mono"` |
  | L | FR-EX-1: `picolet init --template pydfu` scaffolds a buildable app | `picolet init + npm install + picolet build` in tempdir |

- `tests/phase-19/smoke_list_devices.py` — AppHarness `--run` script:
  ```python
  # Run via: picolet test --run <this> <binary>
  import asyncio
  from picolet.testing import AppHarness
  
  async def main():
      async with AppHarness(binary, env={"PICOLET_PYDFU_MOCK": "1"}) as h:
          devices = await h.page.evaluate(
              "window.picolet.invoke('list_devices')"
          )
          assert isinstance(devices, list), f"expected list, got {type(devices)}"
          print(f"list_devices: OK ({len(devices)} device(s))")
  
  asyncio.run(main())
  ```

- `tests/phase-19/smoke_read_dfu.py` — similar, passes the fixture path to
  `read_dfu` and asserts the result has an `"elements"` key.

**Exercise:**
```bash
cd /home/anl/picolet
bash tests/phase-19/run.sh --verbose
# All gates PASS or SKIP (no FAIL)
```

---

### Open questions

**O1 — Windows USB (WinUSB via libffi) is deferred.**
FR-EX-7 specifies WinUSB on Windows. The pydfu-win submodule has a
working Windows USB overlay (`/home/anl/pydfu-win/micropython/tools/pydfu.py`
uses a Windows-specific backend via `usb.backend.libusb1`). Porting this
to the frozen MicroPython ffi layer requires binding `SetupDiGetClassDevs`,
`WinUsb_Initialize`, and `WinUsb_ControlTransfer` from `WinUsb.dll` and
`SetupAPI.dll`. This is the same COM/Win32 ffi work pattern as PH10's
WebView2 wiring, but scoped to USB. **Decision needed from user:** is
Windows USB a PH19 requirement or a post-v1.1 item? The plan documents it
as deferred with a `NotImplementedError` guard; the Linux path satisfies
FR-EX-7 for the primary target platform.

**O2 — File picker for `.dfu` files.**
The current design uses a typed path input field rather than a native file
picker dialog. A native file picker would require either a new `@picolet.command
async def pick_file(filter)` backed by `zenity` / `GTK FileChooserDialog`
on Linux and `IFileOpenDialog` on Windows, or a browser-side
`<input type="file">` approach (which works inside WebKitGTK/WebView2 and
would give a native OS picker for free). The browser-side `<input type="file">`
is the path of least resistance and would make the UX cleaner. **Decision
needed:** use native `<input type="file">` (simpler, no Python command
needed) or the text-path input (per current plan, more control-panel feel)?

**O3 — `dfu:progress` emission from a blocking libusb call.**
The real `flash_device` calls `libusb_control_transfer` per block, which is
a blocking ffi call. In MicroPython's asyncio, blocking calls hold the event
loop. The progress callback inside `write_memory` fires synchronously every
two blocks. For the real device path, each block takes ~10–100 ms on the USB
bus — long enough that the event loop cannot service other commands during
flashing. Mitigation: wrap the flash loop in a separate OS thread (not
available in MicroPython) or accept that the event loop is blocked during
flash (the UI shows progress only after each USB transfer completes — still
functional, just not smoothly live-updating). The mock does not have this
problem (it is synchronous but instant). **Flag for developer:** the real USB
path will block the asyncio loop during flashing. For v1.1 this is acceptable
(one device at a time, no concurrent commands expected during flash). Post-v1.1
mitigation would be to run libusb in a thread via `asyncio.loop.run_in_executor`.

**O4 — `device_id` representation for multi-device disambiguation.**
The `list_devices` response uses `{"bus": int, "addr": int, ...}`. The `flash`
command takes `device_id`. The agreed representation is `"<bus>:<addr>"` as
a string (e.g. `"1:1"`). This must be consistent between `list_devices`,
`get_memory_layout`, and `flash`. Document in the IPC command interface
comments in `src/main.py`.

---

### Exit gate

A successful PH19 has all of the following true, verified by
`bash tests/phase-19/run.sh` exiting 0:

| Check | Proves | Key command |
|---|---|---|
| Gate A | FR-EX-1 scaffold | `picolet validate` in `examples/pydfu/` |
| Gate B | FR-EX-1 build | `picolet build --no-sbom` → binary exists |
| Gate C | NFR-EX-1 size | `wc -c` ≤ 3 MiB |
| Gate D | NFR-EX-4 no CDN | `strings | grep CDN` = 0 |
| Gate E | NFR-EX-2 startup | AppHarness `time_to_ready` ≤ 1500 ms |
| Gate F | FR-EX-1 list_devices IPC | `smoke_list_devices.py` exits 0 |
| Gate G | FR-EX-1 read_dfu IPC | `smoke_read_dfu.py` exits 0 |
| Gate H | FR-EX-6 screenshots | All six PNGs present + valid, each > 1 KB |
| Gate I | FR-EX-5 tests | `pytest examples/pydfu/tests/` exits 0 |
| Gate J | NFR-EX-3 CSS size | CSS ≤ 50 KB gzipped |
| Gate K | NFR-EX-AESTHETIC fonts | `strings pydfu | grep "JetBrains Mono"` matches |
| Gate L | FR-EX-1 template | `picolet init --template pydfu` scaffolds + builds |

Plus: one Linux build green (Gate B). Windows build is deferred per O1.

The NFR-EX-AESTHETIC gate (`memorable screenshot`) is human-judged. Gate H's
screenshot validity check (valid PNG, > 1 KB) is the automated proxy; a
human reviewer must sign off that the screenshot is visually correct before
the tester role marks PH19 PASS.

---

### Risks / footguns

**R1 — libusb symbol availability in the runtime binary.**
The existing webview runtime links against `libgtk-3.so.0` and
`libwebkit2gtk-4.1.so.0` via libffi at runtime. `libusb-1.0.so.0` must also
be present on the target Linux host. On Ubuntu 22.04/24.04, `libusb-1.0-0`
is installed by default. If the CI host lacks it, `ffi.open("libusb-1.0.so.0")`
raises `OSError`. Mitigation: the `pydfu_adapter` catches this and surfaces a
clear `RuntimeError("libusb-1.0 not found; install libusb-1.0-0")`.

**R2 — USB device permission denied.**
On Linux, `/dev/bus/usb/<bus>/<dev>` is owned by `root:plugdev` by default.
The test user must be in the `plugdev` group, or a udev rule must grant
access. The mock path bypasses this entirely. For real-device tests, document
in `examples/pydfu/README.md` that the user needs either `sudo` or a udev
rule. Gate tests use the mock path — no permission issue in CI.

**R3 — `dfu:progress` event rate and WebKit IPC backpressure.**
If the mock emits progress events faster than the JS side can consume them,
the IPC queue may back up. The mock emits one event per 2 KB block; for a
1 MB DFU file that is 512 events. At the dispatcher's `MAX_INBOUND_IN_FLIGHT
= 1024` cap this is safe. The real USB path is self-throttled by the USB
transfer time (~10–100 ms each), so backpressure is not an issue there.

**R4 — Vue Router hash mode vs. history mode.**
The `picolet://` custom scheme handler does not support HTML5 history-mode
routing (no server-side fallback for arbitrary paths). Vue Router must use
`createWebHashRouter()` (hash-based: `picolet:///ui/index.html#/flash`) rather
than `createWebHistory()`. Confirmed: the `examples/with-vue` baseline uses
the default hash router implicitly (single route). The pydfu app must
explicitly use `createWebHashHistory()`.

**R5 — Font-face `font-display: block` and first-frame timing.**
`font-display: block` makes the browser hide text during font loading (up to
3 s block period). In WebKitGTK, woff2 fonts served from `picolet://` are read
from romfs (no network) and load near-instantly (~1–5 ms). The block period
is effectively zero. However, if the runtime or WebKit introduces an async
roundtrip to the scheme handler, the first frame may briefly show invisible
text. Mitigation: if visual glitching is observed, switch to `font-display:
swap`. The AppHarness `wait_for_selector` in screenshot scripts adds a 200 ms
settle delay after the target element appears; this covers any font-swap flash.

**R6 — `read_dfu_file` CRC validation on MicroPython.**
The original pydfu.py uses `zlib.crc32`. MicroPython's `zlib` module (when
`MICROPY_PY_DEFLATE=0`) may not include `crc32`. Confirm: in the runtime
variant, `uzlib` is available but the `zlib.crc32` CPython API may not be
present. If absent, the CRC validation in `read_dfu_file` must use a
pure-Python CRC32 implementation (30 lines, trivially vendored). Check:
`import uzlib; hasattr(uzlib, "crc32")` in the runtime. The mock path avoids
this: `pydfu_mock.py` does not call `read_dfu_file`.

**R7 — Screenshot determinism with LED pulse animation.**
The `animation: pulse` CSS animation on LED dots changes the LED opacity
at 2 Hz. If a screenshot captures mid-animation, it differs byte-for-byte
from one captured at a different frame. Mitigation: inject `window.
__PICOLET_SCREENSHOT_MODE__ = true` via `page.evaluate(...)` before
screenshotting, and add CSS rule `.no-animation * { animation: none
!important; }` on the `:root` when this flag is set. The screenshot scripts
must set this flag before capturing. Document as a known antialiasing caveat
per NFR-EX-5.

---

### Model tier recommendations

| Role | v1.1-plan default | Recommended | Rationale |
|---|---|---|---|
| planner | opus | **sonnet** (this artefact) | Primarily app-building work on top of established PH17/18 infrastructure. The load-bearing decisions (mock USB design, font choices, Vue Router hash mode) are tractable at sonnet tier. |
| developer | opus | **sonnet** | The libusb ffi bindings in `pydfu_adapter.py` are the most complex part, but they follow the exact same pattern as `_gtk_ffi.py`. The Vue frontend is standard Vue 3 + hand-crafted CSS — no novel framework work. The v1.1-plan recommends opus for this developer role; given the strong PH17/18 baseline, sonnet should suffice. Flag to user: if the libusb ffi symbol declarations hit unexpected API surface (struct layouts, error code handling), escalate that specific chunk to opus. |
| sqe | sonnet | **sonnet** | Test authoring against an established mock/harness pattern. |
| tester | opus | **opus** | The NFR-EX-AESTHETIC gate is a human design judgement (memorable screenshot). The tester must also validate that the industrial control-panel feel is genuinely distinctive, not generic. Keep at opus. |
