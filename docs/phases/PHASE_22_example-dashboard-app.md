# PH22 — example-dashboard-app

## Plan

### Goal

Build `examples/dashboard/` — a live system-metrics dashboard that demonstrates
Picolet for a data-dense observability use-case. Python pushes a single
`metrics:tick` event per second by reading `/proc/stat`, `/proc/meminfo`,
`/proc/net/dev`, `/proc/diskstats`, and `/proc/loadavg`. The Vue frontend
renders every metric as hand-rolled SVG paths updated on each tick.

Aesthetic: **sophisticated data-dense** — slate base, ice-blue chart strokes,
amber warning accents, condensed display type. Bloomberg-terminal information
density: every pixel earns its place.

PH22 builds on the infrastructure from PH17–PH21. The structural patterns are
fully established. The two novel concerns are:

1. **1 Hz async push loop with a 60-sample history buffer** — Python side needs
   an `asyncio.create_task` background loop that fires every second and a
   `get_history()` bootstrap command for the JS side to call on mount.
2. **Hand-rolled SVG line charts and sparklines** — pure-SVG `<path d="...">` computed
   from Vue refs, updated each tick with a 200ms `transition: d` CSS animation.

Both are resolved in the Key Research Findings section below.

---

### Spec coverage

| Spec ID | Requirement | Where in this phase |
|---|---|---|
| FR-EX-4 | `picolet init <name> --template dashboard` scaffolds a system metrics dashboard with 1 Hz event-push from Python and live charts (line, gauge, sparkline) | Chunks 1–5 (full app scaffold + Python metrics reader + Vue grid + charts + integration) + Chunk 7 (init_cmd wiring) |
| FR-EX-5 | Each example ships `tests/` with Playwright integration tests | Chunk 6 (waits for 2 consecutive `metrics:tick` events, asserts widget DOM updated) |
| FR-EX-6 | Each example ships `screenshots/` with auto-generated PNGs covering major UI states | Chunk 8 (generate_screenshots.py — four states driven by mocked `window.picolet.emit`) |
| NFR-EX-1 | Binary size ≤ 3 MiB on linux-x64-webview | Chunk 9 (Gate C) |
| NFR-EX-2 | Start-up ≤ 1500 ms first interactive frame | Chunk 9 (Gate E, AppHarness time_to_ready) |
| NFR-EX-3 | CSS does not pull a runtime CSS framework heavier than 50 KB gzipped | Chunk 3 (hand-crafted CSS; no component library; all charts are SVG, zero JS chart libraries) |
| NFR-EX-4 | No external CDN at runtime; all assets in romfs | Chunk 3 (Antonio + JetBrains Mono + DM Sans woff2 bundled in `ui/public/fonts/`; no CDN) |
| NFR-EX-5 | Deterministic screenshots; same inputs → byte-identical PNG | Chunk 8 (deterministic fixture data injected; animations disabled) |
| NFR-EX-6 | Screenshot gallery regenerated on every CI build; drift is CI failure | Chunk 8 + Chunk 9 (Gate H verifies PNGs present and valid) |
| NFR-EX-AESTHETIC | Must pass "show me the screenshot — is it memorable?" test | Chunk 3 (all aesthetic decisions spec-exact) |

---

### Dependencies

#### From v1 (already landed)

- `picolet.command` / `picolet.emit` / `picolet.run` at
  `packages/picolet-runtime/python/picolet/__init__.py`.
- `picolet._dispatcher.Dispatcher` wire format (newline-delimited JSON) at
  `packages/picolet-runtime/python/picolet/_dispatcher.py`.
- MicroPython stdlib in frozen environment: `os`, `sys`, `asyncio`, `json`,
  `time`. The `/proc` filesystem is readable directly via `open()` on the Linux
  runtime variant.

#### From PH17 (already landed)

- `picolet.testing.AppHarness` at
  `packages/picolet-testing/picolet/testing/_harness.py`.
- `picolet test --screenshot` CLI at
  `packages/picolet-cli/picolet_cli/test_cmd.py`.
- `window.picolet.__ready__ === true` contract waited on by AppHarness.

#### From PH18 (already landed)

- `[ui.frontend]` table parser + `npm run build` hook in `build_cmd.py`.
- `createWebHashHistory()` as the required Vue Router mode under `picolet://`.

#### From PH19 (already landed)

- `examples/pydfu/scripts/generate_screenshots.py` — Playwright headless
  Chromium + local HTTP server screenshot pattern. PH22 follows the same shape
  exactly: `_build_mock_js()`, `_start_file_server()`, `_new_page()`,
  `_check_screenshot()`.
- `examples/pydfu/tests/conftest.py` — AppHarness pytest fixture pattern.
- Font: `examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2` — PH22
  **copies this file verbatim** for numerals (spec calls for Roobert Mono; OFL
  fallback is JetBrains Mono). The same copy-without-redownload pattern used by
  PH21.
- `vite.config.ts` pattern: `base: './'`, `root: 'ui'`, `build.outDir: '../dist'`.
- `picolet.toml` structure.

#### From PH20 and PH21 (already landed)

- `init_cmd._KNOWN_TEMPLATES` already includes `"notes"` and `"config-editor"`;
  PH22 adds `"dashboard"` using the identical mechanism in
  `packages/picolet-cli/picolet_cli/init_cmd.py`.
- Screenshot `generate_screenshots.py` structure with `window.__initState`
  pre-population strategy from PH20/PH21.
- `App.vue` `window.__PICOLET_SCREENSHOT_MODE__` + `.no-animation` class from
  PH21 — PH22 inherits the same pattern.

#### What PH23 needs from PH22

- `examples/dashboard/` present and buildable. PH23's mirror script copies it to
  `packages/picolet-templates/picolet_templates/dashboard/`.
- `examples/dashboard/screenshots/` non-empty. PH23's CI screenshot job validates.

---

### Key research findings

**F1 — Reading `/proc/stat` for CPU usage requires diffing cumulative jiffies.**

`/proc/stat` is a cumulative counter file. Each line for a CPU begins `cpuN`
followed by jiffies in: user, nice, system, idle, iowait, irq, softirq,
steal. CPU % between two readings is:

```
delta_idle = idle_now - idle_prev
delta_total = total_now - total_prev  # sum of all fields
cpu_pct = 100.0 * (1.0 - delta_idle / delta_total)  if delta_total > 0 else 0.0
```

The background task must retain the previous sample in state. On the first tick
the prev state is populated but no event is emitted (there is no valid delta
yet). The second tick begins a valid 60-sample history. This is a 1-second
startup lag — acceptable; the frontend shows dashes until the first event
arrives.

Per-core data comes from the `cpu0`, `cpu1`, ... lines below the `cpu` aggregate
line. Read all `cpuN` lines to support variable core counts. Parse with a single
`split()` call per line.

`/proc/stat` parsing is pure Python with no imports beyond `open()`. It runs
inside MicroPython without issues.

**F2 — `/proc/meminfo` gives used/total in kB.**

Relevant lines: `MemTotal: N kB`, `MemFree: N kB`, `MemAvailable: N kB`,
`Buffers: N kB`, `Cached: N kB`. Conventional "used" for display:

```
used = total - available
pct  = 100 * used / total   if total > 0 else 0
```

`MemAvailable` is more useful than `MemFree` (accounts for reclaimable caches).
Parse: `line.split()` → `[key, value, 'kB']`.

**F3 — `/proc/net/dev` gives cumulative bytes rx/tx per interface.**

Format: two header lines, then one line per interface:
`  eth0: RX_bytes RX_packets ... TX_bytes TX_packets ...`

Fields (0-indexed after the interface name colon split): rx_bytes=0, tx_bytes=8.
Delta between ticks gives bytes/second. Aggregate across all non-loopback
interfaces for the dashboard display. The task stores prev rx/tx bytes. On
the first tick the delta is 0 (no previous baseline); this is displayed as 0.

**F4 — `/proc/diskstats` gives cumulative read/write sectors per device.**

Relevant columns (1-indexed): major(1), minor(2), name(3), reads_completed(4),
sectors_read(6), writes_completed(8), sectors_written(10). One sector = 512
bytes. Delta sectors × 512 / delta_time gives bytes/second. Aggregate across
all non-partition disks (names that do not end in a digit, or filter by
`sd[a-z]$` / `vd[a-z]$` / `nvme..n..`). Store prev sectors per device name.

**F5 — `/proc/loadavg` is trivial.**

`0.42 0.38 0.41 2/1024 12345` — split on space, first three are load 1/5/15.
Fourth field is `running/total` threads. Fifth is last PID. Parse with
`split()`.

**F6 — Process list: `/proc/[pid]/stat` or count `/proc/[pid]` directories.**

Total process count: count directory entries in `/proc/` that are purely numeric
(these are PID directories). In MicroPython, `os.listdir("/proc")` returns a
flat list of names; filter with a try/int() guard.

Top-5 by CPU: read `/proc/[pid]/stat` for each PID to get `utime + stime` (fields
14+15, 0-indexed, in jiffies). Diff between ticks gives CPU jiffies. Sort
descending. Read `/proc/[pid]/comm` (or `/proc/[pid]/status` `Name:` line) for
the process name. This is moderately expensive: on a 1000-process system it
means 1000 `open()` calls per second. Mitigate by limiting the scan to the
first 512 PIDs sorted numerically and also limiting re-read of comm to the top-N
candidates. Document this as a known approximation in the code comment.

**F7 — The 1 Hz asyncio push loop pattern.**

The existing examples use `asyncio.create_task()` for background work (see
`pydfu/src/main.py` flash task). The metrics loop uses the same pattern:

```python
import asyncio
import picolet

_history: list[dict] = []
_HISTORY_MAX = 60
_prev: dict = {}   # previous-tick cumulative counters

async def _metrics_loop():
    while True:
        await asyncio.sleep(1.0)
        tick = _collect()  # reads /proc files, diffs with _prev
        if tick is not None:
            _history.append(tick)
            if len(_history) > _HISTORY_MAX:
                _history.pop(0)
            picolet.emit("metrics:tick", tick)

@picolet.command
async def get_history(args):
    return {"history": _history}

def main():
    import picolet_ui as ui
    asyncio.get_event_loop().create_task(_metrics_loop())
    app = ui.Application()
    app.run()

main()
```

The `asyncio.get_event_loop().create_task()` call before `app.run()` schedules
the task into the event loop that `app.run()` will drive. This is the same
pattern used by `pydfu/src/main.py`. The `picolet_ui._loop.run()` does not replace
the event loop; it creates tasks on the existing one and calls
`asyncio.run()` or equivalent. In MicroPython's asyncio this is correct:
`create_task()` before the loop starts queues the coroutine for execution when
the loop runs.

**F8 — Windows: raise `NotImplementedError`.**

The `/proc` filesystem does not exist on Windows. The `_collect()` function must
check `sys.platform` and raise `NotImplementedError("dashboard requires Linux
(/proc not available on {sys.platform})")` as its first line — matching the
`NotImplementedError` guard pattern used in `pydfu_adapter.py`.

The error should surface early: check in `_metrics_loop()` before the sleep, not
inside `_collect()` per call. On non-Linux platforms, the loop immediately stops
after the first iteration and emits a single `metrics:error` event with the
message for the frontend to display.

**F9 — SVG path `d` attribute generation for line charts.**

A line chart over N samples mapped to a viewport of `W × H` pixels:

```typescript
function toLinePath(values: number[], max: number, w: number, h: number): string {
  if (values.length < 2) return ''
  const n = values.length
  const step = w / (n - 1)
  const pts = values.map((v, i) => {
    const x = i * step
    const y = h - (v / max) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return 'M ' + pts.join(' L ')
}
```

For a filled area chart (used when a subtle fill under the line is desired):

```typescript
function toAreaPath(values: number[], max: number, w: number, h: number): string {
  if (values.length < 2) return ''
  const line = toLinePath(values, max, w, h)
  const n = values.length
  const step = w / (n - 1)
  const lastX = ((n - 1) * step).toFixed(1)
  return line + ` L ${lastX},${h} L 0,${h} Z`
}
```

These are **pure TypeScript functions** — no imports, no dependencies. They live
in `ui/src/utils/svg.ts`. Each chart component calls them inside a `computed()`.

The 200ms cubic-bezier transition is declared in the global CSS on the `path`
element:

```css
path.chart-line {
  transition: d 200ms cubic-bezier(0.4, 0, 0.2, 1);
}
```

Note: CSS `transition: d` (transitioning the `d` attribute of an SVG path) is
supported in Chrome 93+. WebKitGTK (used on Linux) tracks upstream WebKit;
current WebKitGTK 2.42+ supports it. Add a note in the code comment that older
WebKit versions silently skip the transition (the path still updates, just
without animation). This is acceptable per spec.

**F10 — Radial gauge (memory) via SVG arc.**

A radial gauge is an `<svg>` with two overlapping `<path>` elements:
1. Background arc: full sweep at low opacity.
2. Value arc: partial sweep proportional to fill %.

The arc path uses the SVG `arc` command:

```typescript
function toGaugePath(pct: number, cx: number, cy: number, r: number): string {
  // Sweep from 225° to 315° (270° total arc, bottom-gap style)
  const startAngle = 225 * Math.PI / 180
  const sweepAngle = 270 * Math.PI / 180 * (pct / 100)
  const endAngle = startAngle + sweepAngle
  const x1 = cx + r * Math.cos(startAngle)
  const y1 = cy + r * Math.sin(startAngle)
  const x2 = cx + r * Math.cos(endAngle)
  const y2 = cy + r * Math.sin(endAngle)
  const large = sweepAngle > Math.PI ? 1 : 0
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
}
```

**F11 — Sparkline strip (per-core).**

Same `toLinePath()` utility, but with a very small viewport (e.g., `88 × 28` px
per core). The strip lays out `N` sparkline `<svg>` elements in a flex row, one
per core. Each sparkline tracks its own 60-sample history. The core count is
determined from the first `metrics:tick` event.

**F12 — Dual-line chart (network rx/tx, disk read/write).**

Two overlapping `<path>` elements inside a single `<svg>`. Path 1 (`--chart:
#7dd3fc`) for the first series, path 2 (`--chart-2: #c4b5fd`) for the second.
The `max` for the Y scale is `Math.max(...rxHistory, ...txHistory)` — both
series share the same Y axis to make relative magnitudes visible.

**F13 — Font strategy.**

Spec calls for:
- Display (headings, widget titles): GT America Condensed → OFL fallback **Antonio**.
  Download from Google Fonts as `Antonio-Regular.woff2` and
  `Antonio-Bold.woff2`. License: SIL OFL 1.1 (confirmed).
- Numerals (large metric values): Roobert Mono → OFL fallback **JetBrains Mono**.
  Already at `examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2`.
  Copy verbatim, do not re-download.
- Body (labels, units): GT Walsheim → OFL fallback **DM Sans**.
  Download from Google Fonts as `DMSans-Regular.woff2` and `DMSans-Medium.woff2`.
  License: SIL OFL 1.1 (confirmed).

All fonts go in `ui/public/fonts/`. No CDN. This matches NFR-EX-4.

**F14 — Gradient mesh background.**

Three stacked `radial-gradient` declarations on the `body::before` pseudo-element
(following the `body::before` noise-texture pattern from `examples/pydfu/ui/src/assets/main.css`):

```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(ellipse at 20% 30%, rgba(125, 211, 252, 0.06) 0%, transparent 55%),
    radial-gradient(ellipse at 75% 70%, rgba(245, 158, 11, 0.05) 0%, transparent 50%),
    radial-gradient(ellipse at 55% 10%, rgba(196, 181, 253, 0.04) 0%, transparent 45%);
}
```

The `z-index: 0` keeps the mesh behind the widget grid (`z-index: 1` or
`position: relative` on the grid container). This is purely declarative CSS —
no JS required.

**F15 — Mock data shape for screenshots.**

The `generate_screenshots.py` mock must inject a realistic 60-sample history.
The mock `window.picolet` object:
- Responds to `invoke('get_history', null)` with a pre-built 60-tick dataset.
- Exposes `window.picolet.emit('metrics:tick', payload)` so the screenshot script
  can drive two more ticks synchronously after mount.

For screenshot determinism (NFR-EX-5), the history must be generated with a
seeded pseudo-random function in pure JS (or pre-generated in Python as a
literal JSON blob embedded in the mock). The pre-generated literal JSON approach
is simpler and more reliable: generate the fixture once in Python and embed it
as a string constant in `generate_screenshots.py`. Use a fixed seed for
`random.seed(42)` in the Python generation code.

The `full-dashboard-with-warning` screenshot requires injecting CPU at 92%
(above the amber warning threshold of 85%) and memory at 88%.

**F16 — Font loading race in screenshot mode.**

The `generate_screenshots.py` screenshot script must wait for fonts to load
before capturing. In Playwright:

```python
await page.evaluate("document.fonts.ready")
await asyncio.sleep(0.3)   # allow Vue reactive updates to settle
```

This ensures Antonio/DM Sans glyphs are present in the screenshot rather than
the fallback system font, which would fail the aesthetic check. The same
`await asyncio.sleep(0.3)` technique is already used in
`examples/config-editor/scripts/generate_screenshots.py`.

**F17 — Widget bezel CSS.**

The spec calls for: `1px var(--bg-2) border + box-shadow: 0 1px 0 0
rgba(255,255,255,0.04) top highlight`. Applied to every `.widget` element:

```css
.widget {
  border: 1px solid var(--bg-2);
  box-shadow: 0 1px 0 0 rgba(255, 255, 255, 0.04);
  background: var(--bg-1);
  position: relative;
}
```

The `box-shadow` `0 1px 0 0` means: x=0, y=1px (below top edge), blur=0,
spread=0 — a 1px line at the top of the box. This is the "bezel highlight"
effect.

---

### Aesthetic spec

```
Palette:
  --bg-0: #0b0e12     body background (darkest)
  --bg-1: #101418     widget background
  --bg-2: #161b22     widget border colour + secondary surface
  --ink: #e5e7eb      primary text
  --ink-soft: #94a3b8 labels / units / dim text
  --chart: #7dd3fc    primary chart stroke (ice-blue)
  --chart-2: #c4b5fd  secondary chart stroke (lavender, rx/tx pair)
  --accent: #f59e0b   warning accent (amber)
  --alarm: #ef4444    alarm (red — CPU > 95% or memory > 95%)

Typography:
  display / widget titles:  'Antonio', sans-serif (OFL fallback for GT America Condensed)
  numerals (large values):  'JetBrains Mono', monospace (OFL fallback for Roobert Mono)
  body / labels / units:    'DM Sans', system-ui, sans-serif (OFL fallback for GT Walsheim)

Layout: 12-column CSS grid, 8px gap
  CPU line chart:       col 1–8, row 1–2  (spans 8 of 12 cols, 2 rows)
  Memory gauge:         col 9–12, row 1–2 (spans 4 of 12 cols, 2 rows)
  Per-core sparklines:  col 1–12, row 3   (full width, 1 row)
  Network throughput:   col 1–6, row 4    (6 cols)
  Disk I/O:             col 7–12, row 4   (6 cols)
  Process list:         col 1–12, row 5   (full width, short height)

Numerals:
  font-size: 60px; font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono'
  Units: font-size: 11px; text-transform: uppercase; color: var(--ink-soft)
  Warning threshold: --accent applied when value > 85% (CPU, memory)
  Alarm threshold:   --alarm applied when value > 95%

Widget bezel:
  border: 1px solid var(--bg-2)
  box-shadow: 0 1px 0 0 rgba(255,255,255,0.04)

Chart lines:
  stroke: var(--chart)  (primary), var(--chart-2) (secondary)
  stroke-width: 1.5
  fill: none  (line chart), or a 4% opacity fill variant for area charts
  path.chart-line { transition: d 200ms cubic-bezier(0.4, 0, 0.2, 1); }

Background gradient mesh:
  Three radial-gradients on body::before at ~5% opacity
  chart-blue at 20%/30%, accent-amber at 75%/70%, chart-2-lavender at 55%/10%
```

---

### Open questions

**O1 — CSS `transition: d` support on WebKitGTK.**
Chrome 93+ and recent WebKit support SVG path `d` attribute transitions. WebKitGTK
on CI machines may be an older version. If the transition is silently ignored, the
chart still updates correctly (just without the 200ms smooth transition). This is
acceptable per spec. Document in the phase commit. No workaround needed unless
testing reveals actual breakage.

**O2 — Process list scan cost at 1 Hz.**
Scanning all PID directories in `/proc` at 1 Hz is cheap on a lightly-loaded
system but can be several hundred `open()` calls on a busy server. The implementation
caps at the first 512 numerically-sorted PIDs (which biases toward long-running
processes rather than newly spawned ones). If this proves too expensive it can
be raised as a post-phase optimisation. Document the cap in a code comment.

**O3 — Core count variability.**
The per-core sparkline strip dynamically adds/removes columns when core count
changes. Since Linux does not hot-plug CPU cores during normal operation, this
is a first-render concern only. The Vue component reads the core count from the
first `metrics:tick` event and creates `n_cores` sparkline `<svg>` elements. If
`metrics:tick` events ever change the core count (they won't in practice), the
component reactively updates. No explicit teardown needed.

**O4 — `asyncio.sleep(1.0)` drift.**
`asyncio.sleep(1.0)` may drift over time because it measures sleep duration, not
wall-clock intervals. For a live dashboard showing second-by-second metrics this
is acceptable — the display frequency may slip by a few milliseconds per minute.
If tighter timing is needed in a future phase, replace with a drift-corrected
loop that computes `next_fire = now + 1.0` and sleeps `next_fire - time.time()`.
Document in commit. Out of scope for PH22.

**O5 — Antonio font weight.**
The Google Fonts `Antonio` family ships Regular (400) and Bold (700). The spec
says "condensed display" without specifying weight. Use Regular for widget
titles and Bold for the hostname/uptime strip. Both weights are downloaded as
`Antonio-Regular.woff2` and `Antonio-Bold.woff2`.

---

### Implementation breakdown

#### Chunk 1 — Project scaffold

Create the `examples/dashboard/` directory tree:

```
examples/dashboard/
  picolet.toml
  package.json
  tsconfig.json
  tsconfig.node.json
  vite.config.ts
  src/
    main.py
    metrics_reader.py
  ui/
    index.html
    public/
      fonts/
        Antonio-Regular.woff2
        Antonio-Bold.woff2
        JetBrainsMono-Regular.woff2   ← copied from examples/pydfu/
        DMSans-Regular.woff2
        DMSans-Medium.woff2
    src/
      env.d.ts
      main.ts
      picolet.d.ts
      router/
        index.ts
      assets/
        fonts.css
        main.css
      utils/
        svg.ts
        format.ts
      store.ts
      App.vue
      views/
        DashboardView.vue
      components/
        TopStrip.vue
        CpuChart.vue
        MemoryGauge.vue
        SparklineStrip.vue
        NetworkChart.vue
        DiskChart.vue
        ProcessList.vue
  screenshots/         (initially empty; filled by Chunk 8)
  scripts/
    generate_screenshots.py
  tests/
    pytest.ini
    conftest.py
    test_dashboard_live.py
  target/             (build output; gitignored)
```

`picolet.toml`:
```toml
[app]
name = "dashboard"
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
title = "System Dashboard"
size = [1400, 900]
resizable = true
```

`vite.config.ts` follows the identical pattern from
`examples/config-editor/vite.config.ts`: `base: './'`, `root: 'ui'`,
`build.outDir: '../dist'`.

`package.json` follows the pattern from `examples/config-editor/package.json`;
dependencies: `vue`, `vue-router`, `@vitejs/plugin-vue`, `vite`, `typescript`,
`vue-tsc`. No chart libraries.

**Outcome**: `npm install && npm run build` succeeds; `dist/` produced.

#### Chunk 2 — Python metrics reader (`src/metrics_reader.py`)

`metrics_reader.py` is the only Python file that touches `/proc`. It has no
picolet imports — pure data collection.

Public API:

```python
def collect(prev: dict) -> tuple[dict, dict]:
    """Read all /proc sources and compute the current tick payload.

    Returns (tick, next_prev) where tick is the event payload dict and
    next_prev is the new accumulated state to pass as prev on the next call.

    On the very first call (prev == {}), returns (None, initial_prev).
    Returns (None, prev) on any read error (log to stderr; do not crash).
    """
```

Payload shape (`tick` dict):

```python
{
  "ts": float,             # time.time() at collection
  "cpu": float,            # aggregate CPU % 0–100
  "cores": [float, ...],  # per-core CPU % list
  "mem_pct": float,        # memory used %
  "mem_used_mb": float,    # used memory in MiB
  "mem_total_mb": float,   # total memory in MiB
  "load": [float, float, float],  # 1, 5, 15 min load avg
  "net_rx_bps": float,     # aggregate rx bytes/sec
  "net_tx_bps": float,     # aggregate tx bytes/sec
  "disk_read_bps": float,  # aggregate disk read bytes/sec
  "disk_write_bps": float, # aggregate disk write bytes/sec
  "proc_count": int,       # total process count
  "top_procs": [           # top-5 by CPU (may be fewer)
    {"pid": int, "name": str, "cpu_pct": float},
    ...
  ],
  "hostname": str,         # socket.gethostname() cached at startup
  "uptime_s": float,       # /proc/uptime first field
}
```

All `/proc` reads wrapped in try/except; any failure for an individual field
substitutes a sensible zero/null default so the frontend never receives a
structurally incomplete tick.

Platform guard at top of module:

```python
import sys
if sys.platform != "linux":
    raise NotImplementedError(
        "dashboard metrics require Linux (/proc); "
        "running on {} is not supported".format(sys.platform)
    )
```

The `NotImplementedError` is raised at import time on non-Linux, which surfaces
early in `src/main.py` startup. A `metrics:error` event is emitted by the
catch block in `_metrics_loop()` before the loop exits.

Internal helpers (all in `metrics_reader.py`):
- `_read_cpu(prev)` — parses `/proc/stat`, returns `(cpu_pct, cores_pct_list, next_cpu_prev)`.
- `_read_mem()` — parses `/proc/meminfo`.
- `_read_net(prev)` — parses `/proc/net/dev`, returns `(rx_bps, tx_bps, next_net_prev)`.
- `_read_disk(prev)` — parses `/proc/diskstats`, returns `(read_bps, write_bps, next_disk_prev)`.
- `_read_loadavg()` — parses `/proc/loadavg`.
- `_read_uptime()` — parses `/proc/uptime`.
- `_read_procs(prev)` — scans `/proc/[pid]/stat` + `/proc/[pid]/comm`, returns `(count, top5, next_proc_prev)`.

**Outcome**: `python3 -c "import sys; sys.path.insert(0, 'src'); import metrics_reader; print(metrics_reader.collect({}))"` on Linux prints a two-tuple with a non-None tick dict.

#### Chunk 3 — Vue frontend: layout, CSS, typography

`ui/src/assets/fonts.css`:

```css
@font-face {
  font-family: 'Antonio';
  font-weight: 400;
  src: url('../public/fonts/Antonio-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'Antonio';
  font-weight: 700;
  src: url('../public/fonts/Antonio-Bold.woff2') format('woff2');
}
@font-face {
  font-family: 'DM Sans';
  font-weight: 400;
  src: url('../public/fonts/DMSans-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'DM Sans';
  font-weight: 500;
  src: url('../public/fonts/DMSans-Medium.woff2') format('woff2');
}
@font-face {
  font-family: 'JetBrains Mono';
  font-weight: 400;
  src: url('../public/fonts/JetBrainsMono-Regular.woff2') format('woff2');
}
```

`ui/src/assets/main.css` defines the full CSS custom-property set, body reset,
gradient mesh `body::before`, `.widget` bezel, the 12-column grid, `path.chart-line`
transition, large numeral style, unit label style, warning/alarm colour overrides,
and the `.no-animation` screenshot-mode override. No external CSS framework.

`DashboardView.vue` implements the grid as `display: grid; grid-template-columns:
repeat(12, 1fr); gap: 8px`. Each widget is a `<div class="widget">` with
`grid-column` / `grid-row` spans matching the spec layout.

**Outcome**: `npm run build` produces a `dist/` where the page loads in a browser
(serving `dist/` via `python3 -m http.server`) with the correct dark background,
gradient mesh visible, font rendering as Antonio/DM Sans, and no layout overflow.

#### Chunk 4 — Vue frontend: components and SVG charts

`ui/src/utils/svg.ts` — `toLinePath()`, `toAreaPath()`, `toGaugePath()`.
All functions are pure (no side effects, no imports).

`ui/src/utils/format.ts` — `fmtPct(v: number): string` (one decimal place),
`fmtBytes(v: number): string` (auto-selects B/KB/MB/GB),
`fmtUptime(s: number): string` (HH:MM:SS format).

`ui/src/store.ts` — module-level `reactive()` state holding:

```typescript
interface MetricsState {
  history: Tick[]       // last 60 ticks
  latest: Tick | null   // most recent tick
  initialized: boolean  // true after get_history() returns
  error: string | null  // set on metrics:error event
}
```

`Tick` interface mirrors the Python payload shape exactly (TypeScript interface,
not a class).

`App.vue` — on mount:
1. Calls `window.picolet.invoke('get_history', null)` → populates `state.history`.
2. Registers `window.picolet.on('metrics:tick', handler)` → each tick appends to
   `state.history` (dropping oldest if > 60) and sets `state.latest`.
3. Registers `window.picolet.on('metrics:error', handler)` → sets `state.error`.
4. Sets `window.picolet.__ready__ = true` after registration (FR-TEST condition
   waited on by AppHarness).

Components (`ui/src/components/`):

- **`TopStrip.vue`**: displays hostname, uptime (formatted HH:MM:SS), and
  load-avg 1/5/15 as big tabular numerals. No chart. Width: 100% (spans all
  12 columns via `.top-strip` outside the grid). Hostname uses Antonio Bold
  28px; numerals use JetBrains Mono 60px tabular-nums; units use DM Sans 11px
  uppercase ink-soft.

- **`CpuChart.vue`**: receives `history: Tick[]` prop. Computes cpu values as
  `history.map(t => t.cpu)`. SVG viewport `100% × 160px`. `toLinePath()` for
  the line, `toAreaPath()` for the subtle fill (4% opacity chart-blue).
  Current value displayed as a numeral overlay at top-right. Warning threshold
  at 85% → `--accent` colour; alarm at 95% → `--alarm` colour.

- **`MemoryGauge.vue`**: receives `latest: Tick | null` prop. SVG `200 × 200px`
  centred in the widget. Two arcs: background (dim) + value arc using
  `toGaugePath()`. Current `mem_pct` as a 60px numeral at centre. Used/total
  in MiB as a dim label below.

- **`SparklineStrip.vue`**: receives `history: Tick[]` prop. Derives core count
  from `history.at(-1)?.cores.length ?? 0`. Renders a flex row of `<svg>`
  elements, one per core. Each SVG is `88 × 28px`. Uses `toLinePath()`. Core
  index label below each sparkline in 9px DM Sans.

- **`NetworkChart.vue`**: dual-line chart (`--chart` for rx, `--chart-2` for
  tx). SVG viewport `100% × 120px`. Shared Y axis (`Math.max` of both series).
  Legend: two colour dots + "RX" / "TX" labels in 11px DM Sans. Current rx/tx
  formatted with `fmtBytes()`.

- **`DiskChart.vue`**: same dual-line pattern as NetworkChart but for disk
  read/write. Uses `--chart` for read, `--chart-2` for write.

- **`ProcessList.vue`**: receives `latest: Tick | null`. Displays `proc_count`
  as a large numeral plus a `<table>` of `top_procs` (PID, name, CPU%). Table
  rows: DM Sans 12px. CPU column uses `--accent` when > 50%.

All components use `computed()` for the SVG path strings (not watchers) — the
path string is always derived from the latest prop value.

**Outcome**: `npm run build` + serve `dist/` → browser shows all seven widget
regions with placeholder/zero data, correct colours, chart SVGs rendered (with
horizontal flat lines at y=0), bezel borders visible.

#### Chunk 5 — Python `src/main.py` and integration

```python
# dashboard — live system-metrics dashboard (picolet example).
#
# Python side: 1 Hz asyncio task reads /proc sources, maintains a 60-sample
# circular history, and pushes metrics:tick events.
#
# IPC commands:
#   get_history()  -> {"history": [...]}  # bootstrap the frontend
#
# Events pushed:
#   metrics:tick   payload (see metrics_reader.collect() docstring)
#   metrics:error  {"message": str}       # on non-Linux or read failure
#
# FR-EX-4, FR-EX-5, FR-EX-6.
import sys
import asyncio
import picolet
import picolet_ui as ui

try:
    import metrics_reader
    _HAS_METRICS = True
except NotImplementedError as e:
    _HAS_METRICS = False
    _METRICS_ERROR = str(e)

_history: list = []
_HISTORY_MAX = 60
_prev: dict = {}


@picolet.command
async def get_history(args):
    return {"history": _history}


async def _metrics_loop():
    global _prev
    if not _HAS_METRICS:
        picolet.emit("metrics:error", {"message": _METRICS_ERROR})
        return
    while True:
        await asyncio.sleep(1.0)
        try:
            tick, _prev = metrics_reader.collect(_prev)
        except Exception as e:
            picolet.emit("metrics:error", {"message": str(e)})
            continue
        if tick is not None:
            _history.append(tick)
            if len(_history) > _HISTORY_MAX:
                _history.pop(0)
            picolet.emit("metrics:tick", tick)


def main():
    loop = asyncio.get_event_loop()
    loop.create_task(_metrics_loop())
    app = ui.Application()
    app.run()


main()
```

Build and run: `picolet build && ./target/linux-x64/dashboard` — window opens,
metrics stream starts after 1 second, browser console shows `metrics:tick`
events firing.

**Outcome**: The built binary runs on Linux, emits `metrics:tick` once per
second, `get_history()` returns up to 60 items, window is visible. Verified
manually with console.log in `App.vue`'s tick handler.

#### Chunk 6 — Tests (`tests/`)

`tests/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

`tests/conftest.py` — exact same structure as `examples/config-editor/tests/conftest.py`:

```python
from pathlib import Path
import pytest
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "dashboard"

@pytest.fixture
async def harness():
    h = AppHarness(str(BINARY))
    await h.start()
    yield h
    await h.stop()
```

`tests/test_dashboard_live.py`:

```python
"""Integration test: wait for 2 consecutive metrics:tick events, assert DOM updated."""
import asyncio
import pytest

pytestmark = pytest.mark.asyncio

async def test_metrics_tick_fires(harness):
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    # Wait for the first metrics:tick to arrive (up to 5s — the loop fires
    # 1s after start, and AppHarness waits for __ready__ first).
    tick_count = await page.evaluate("""() => {
        return new Promise((resolve) => {
            let count = 0
            const unsub = window.picolet.on('metrics:tick', () => {
                count++
                if (count >= 2) {
                    unsub()
                    resolve(count)
                }
            })
            setTimeout(() => resolve(count), 4000)
        })
    }""")
    assert tick_count >= 2, f"expected >= 2 metrics:tick events, got {tick_count}"

async def test_cpu_widget_updates(harness):
    """Assert the CPU numeral DOM element updates between ticks."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    # Wait for first tick to populate .cpu-value
    await page.wait_for_selector(".cpu-value", timeout=5000)
    val1 = await page.locator(".cpu-value").inner_text()

    # Wait for a second tick (1s interval; allow 3s)
    await asyncio.sleep(2.5)
    val2 = await page.locator(".cpu-value").inner_text()

    # Values should be present (not empty/dash) and the DOM was updated.
    assert val1 != "" and val1 != "--", f"cpu-value was empty after first tick: {val1!r}"
    # Note: val1 == val2 is permitted (CPU might not change). The test only
    # verifies that the DOM is populated, not that the value changes.
    assert val2 != "" and val2 != "--", f"cpu-value was empty after second tick: {val2!r}"

async def test_process_list_populated(harness):
    """Assert top-procs table has rows."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    await page.wait_for_selector(".proc-row", timeout=5000)
    rows = page.locator(".proc-row")
    count = await rows.count()
    assert count >= 1, f"expected at least 1 process row, got {count}"
```

**Outcome**: `cd examples/dashboard && pytest tests/` passes on Linux (requires
the binary to be built first). The tests skip gracefully when no WebKit
inspector is available (CI path with no display).

#### Chunk 7 — `init_cmd` wiring

Edit `packages/picolet-cli/picolet_cli/init_cmd.py` to add `"dashboard"` to
`_KNOWN_TEMPLATES` (the same one-line addition made by PH20 for `"notes"` and
PH21 for `"config-editor"`). No other changes needed.

**Outcome**: `picolet init --list-templates` includes `dashboard`.

#### Chunk 8 — Screenshot generation (`scripts/generate_screenshots.py`)

Produces four PNGs in `examples/dashboard/screenshots/`:
1. `full-dashboard.png` — normal state, all metrics in normal range.
2. `full-dashboard-with-warning.png` — CPU at 92%, memory at 88% (amber numerals).
3. `cpu-pinned-state.png` — CPU at 99% for all cores (alarm red).
4. `network-active-state.png` — high rx/tx values (saturated chart lines).

Pattern: identical to `examples/config-editor/scripts/generate_screenshots.py`.
Uses Playwright headless Chromium + local HTTP server serving `dist/`.

`_build_mock_js(tick_history, n_extra_ticks=2)` — builds `window.picolet` mock
that:
- Returns `{"history": tick_history}` for `invoke('get_history', null)`.
- After the page mounts, injects `n_extra_ticks` additional ticks via
  `window.picolet.emit('metrics:tick', payload)` (called from within the page
  via `page.evaluate()`).

The `tick_history` is a list of 60 dicts generated by a deterministic Python
function `_make_history(scenario)` inside the script. `scenario` is one of
`"normal"`, `"warning"`, `"cpu-pinned"`, `"network-active"`. Each scenario
returns a fixed sequence using `random.seed(42)` (re-seeded per scenario call).

Pixel verification for each screenshot:
- All four: slate background (R < 25, G < 30, B < 30 for at least one pixel).
- `full-dashboard-with-warning` and `cpu-pinned-state`: amber/red pixels present
  (verify `--accent` #f59e0b or `--alarm` #ef4444 colour range).
- `full-dashboard` and `network-active-state`: no alarm pixels (CPU < 95%).

Disable animations before capture:
```python
_DISABLE_ANIMATIONS_JS = (
    "(function(){"
    "var s=document.createElement('style');"
    "s.textContent='*,*::before,*::after{"
    "animation-duration:0ms!important;"
    "transition-duration:0ms!important}';"
    "document.head&&document.head.appendChild(s);"
    "window.__PICOLET_SCREENSHOT_MODE__=true;"
    "})()"
)
```

Wait for fonts after page load:
```python
await page.evaluate("document.fonts.ready")
await asyncio.sleep(0.3)
```

**Outcome**: `uv run examples/dashboard/scripts/generate_screenshots.py` produces
four PNGs in `examples/dashboard/screenshots/`, each ≥ 1000×700 px, passing
pixel verification.

#### Chunk 9 — Exit gate verification

Gate checklist:

**A. Build green**
```
cd examples/dashboard && npm install && npm run build
```
`dist/` produced, no TypeScript errors.

**B. Binary build**
```
picolet build   # from examples/dashboard/
ls target/linux-x64/dashboard
```
Binary exists, `file target/linux-x64/dashboard` confirms ELF executable.

**C. Binary size ≤ 3 MiB (NFR-EX-1)**
```
wc -c target/linux-x64/dashboard
```
Must be < 3145728 bytes.

**D. `picolet test --screenshot` smoke**
```
picolet test --screenshot /tmp/dash-smoke.png target/linux-x64/dashboard
```
`/tmp/dash-smoke.png` produced, `file /tmp/dash-smoke.png` confirms PNG,
`wc -c /tmp/dash-smoke.png` confirms > 1000 bytes.

**E. Startup time ≤ 1500 ms (NFR-EX-2)**
AppHarness `time_to_ready` assertion in the test suite, or manual timing:
```python
from picolet.testing import AppHarness
import asyncio, time
async def check():
    t0 = time.time()
    h = AppHarness("examples/dashboard/target/linux-x64/dashboard")
    await h.start()
    print(f"ready in {time.time()-t0:.2f}s")
    await h.stop()
asyncio.run(check())
```

**F. `metrics:tick` events fire**
The `test_metrics_tick_fires` test in `tests/test_dashboard_live.py` must pass.

**G. Screenshots generated and pixel-verified**
```
uv run examples/dashboard/scripts/generate_screenshots.py
```
All four PNGs produced, no assertion errors from `_check_screenshot()`.

**H. Screenshots present and non-empty**
```
ls -lh examples/dashboard/screenshots/
```
Four PNGs present, each > 50 KB.

**I. Aesthetic check (NFR-EX-AESTHETIC)**
Human visual review of the four screenshots. Must exhibit: slate background,
ice-blue chart lines, amber warning colouring, Antonio condensed headers,
JetBrains Mono numerals, DM Sans body text, gradient mesh visible, bezel
borders on widgets, dense layout matching Bloomberg-terminal energy.

**J. `picolet init --list-templates` includes `dashboard`**

**K. No CDN references in `dist/` (NFR-EX-4)**
```
grep -r "https://" examples/dashboard/dist/ || echo "clean"
```

**L. CSS framework size ≤ 50 KB gzipped (NFR-EX-3)**
```
gzip -c examples/dashboard/dist/assets/index-*.css | wc -c
```

---

### Risks

**R1 — CSS `transition: d` not animated on CI WebKitGTK.**
Impact: charts update correctly but without smooth animation. Mitigation:
document the WebKitGTK version dependency in a `[PH22] Note` commit.
The spec says "200ms cubic-bezier transition on path `d`" — if it silently
degrades, the exit gate is still met (charts update, screenshots look correct).
Action: check WebKitGTK version in CI; add a code comment. No spec deviation.

**R2 — `asyncio.create_task()` before `app.run()` race.**
`create_task()` requires a running event loop. In MicroPython's asyncio, calling
`create_task()` before `asyncio.run()` / the `app.run()` call may behave
differently than in CPython. The pydfu example uses `create_task()` inside a
command handler (which already executes within the loop). The metrics loop
needs the task started *before* the first JS command arrives.
Mitigation: follow the exact pattern documented in F7; if it does not work in
MicroPython, move the `create_task()` call to the first invocation of
`get_history()` (lazy start). Document the resolution as a `[PH22] Decision`
commit.

**R3 — Font download size budget.**
Antonio (Regular + Bold) + DM Sans (Regular + Medium) adds ~200–300 KB of woff2
to the romfs. Combined with the Vue bundle this approaches the NFR-EX-1 3 MiB
limit. Check Gate C. If the binary exceeds 3 MiB, drop `Antonio-Bold.woff2`
and use `font-weight: 400` throughout (the difference is minor at the headline
level). This is a fallback option; do not pre-emptively omit bold.

**R4 — `os.listdir("/proc")` in MicroPython.**
MicroPython's `os.listdir()` may not work on the Linux `/proc` virtual
filesystem if the VFS layer doesn't support it. Alternative: use `open()` with a
known PID directly and iterate `/proc/` via a generator that increments PID from
1 to `_MAX_PID_SCAN = 512`. This is slower but guaranteed to work without
relying on `os.listdir()`. Document the chosen approach in a code comment.

---

### Model tiers

- Planner: `sonnet` (this document).
- Developer: `opus` — SVG dataviz path math, MicroPython asyncio task loop,
  `/proc` parsing edge cases, and the aesthetic density work all warrant opus.
- SQE: `sonnet`.
- Tester: `sonnet`.

---

## Developer log

_(appended by developer agent)_

---

## SQE report

_(appended by SQE agent)_

---

## Tester report

_(appended by tester agent)_
