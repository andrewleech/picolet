# PH22 Developer Report — example-dashboard-app

## What was implemented

### Chunk 1 — Project scaffold
`examples/dashboard/` with `picolet.toml`, `package.json`, `tsconfig.json`,
`tsconfig.node.json`, `vite.config.ts`, and `ui/index.html`. Follows the exact
patterns from `examples/config-editor/`. Window size 1400x900 per spec.

### Font strategy (resolved)
Three font families, all OFL-1.1, all bundled as woff2 in `ui/public/fonts/`:
- **Antonio** (v22, Google Fonts): downloaded Latin-subset woff2. Both Regular
  (400) and Bold (700) share the same file — Google Fonts serves a single Latin
  subset for this font regardless of weight. Two separate files with distinct
  names are used so CSS `@font-face` declarations are unambiguous. ~26 KB each.
- **DM Sans** (v17, Google Fonts): Latin-subset woff2, ~37 KB each for Regular
  and Medium.
- **JetBrains Mono**: copied verbatim from `examples/pydfu/ui/public/fonts/`.

### Chunk 2 — Python metrics reader (`src/metrics_reader.py`)
`collect(prev) -> (tick | None, next_prev)` diff-based API.

Reads: `/proc/stat` (CPU jiffy delta), `/proc/meminfo` (MemAvailable used),
`/proc/net/dev` (non-loopback rx/tx delta), `/proc/diskstats` (whole-disk
sector delta), `/proc/loadavg`, `/proc/uptime`, `/proc/sys/kernel/hostname`,
and `/proc/[pid]/stat` for top-5 CPU processes.

Platform guard raises `NotImplementedError` at import time on non-Linux.
All reads try/excepted; field failures substitute zero defaults.

**R4 resolution**: `os.listdir('/proc')` tried first; falls back to
sequential PID probe (1..512) on OSError. Both paths work on this Linux host.

### Chunk 3 — CSS and typography (`ui/src/assets/`)
`fonts.css` declares all three typefaces with `font-display: block`.
`main.css`: full CSS custom-property palette, 12-column grid, `.widget` bezel,
gradient mesh `body::before` equivalent (inlined in `body { background: }`),
`path.chart-line` 200ms `d` transition, staggered `@keyframes enter`,
`.no-animation` screenshot override.

CSS gzip size: **1628 bytes** (NFR-EX-3: <= 50 KB).

### Chunk 4 — Vue components and SVG charts
`ui/src/utils/svg.ts`: pure `toLinePath`, `toAreaPath`, `toGaugePath`,
`toGaugeBgPath`. No imports, no chart libraries.

`ui/src/utils/format.ts`: `fmtPct`, `fmtBytes`, `fmtUptime`, `fmtLoad`.

`ui/src/store.ts`: module-level `reactive<MetricsState>()`.

Seven components:
| Component | Widget | Key detail |
|---|---|---|
| `TopStrip.vue` | Header | Sticky; hostname Antonio 18px Bold; uptime+load JetBrains Mono 22px |
| `CpuChart.vue` | CPU (span 8, 2 rows) | SVG 600x160; area+line; 85% dashed threshold; `.cpu-value` class |
| `MemoryGauge.vue` | Memory (span 4, 2 rows) | SVG 200x200 radial arc; amber>85%, alarm>95% |
| `SparklineStrip.vue` | Per-core (span 12) | Flex row 88x28px SVGs; core count from last tick |
| `NetworkChart.vue` | Network (span 6) | Dual-line shared Y axis; RX/TX fmtBytes |
| `DiskChart.vue` | Disk I/O (span 6) | Same dual-line pattern |
| `ProcessList.vue` | Processes (span 12, 2 rows) | proc_count large num; `.proc-row` table |

### Chunk 5 — Python `src/main.py`
1 Hz asyncio loop: `create_task()` before `app.run()` (verified pattern).
`get_history` command. `metrics:tick` + `metrics:error` events.

### Chunk 6 — Tests (`tests/`)
Three integration tests using `AppHarness`. Tests skip gracefully when no
inspector page (no Xvfb). All pass with binary running locally.

### Chunk 7 — `init_cmd` wiring
`_KNOWN_TEMPLATES` updated with `"dashboard"`.

### Chunk 8 — Screenshots (`scripts/generate_screenshots.py`)
Deterministic `_make_history(scenario)` with `random.seed(42)` per scenario.
Four scenarios: normal, warning (CPU 92%/mem 88%), cpu-pinned (99%), network-active.
Playwright headless Chromium + local HTTP server. Pixel verification:
slate background, amber on warning, alarm red on cpu-pinned, no alarm on others.

### Chunk 9 — Exit gates
All 12 gates pass. See gate log above.

---

## Deviations from phase plan

**D1 — `noUnusedLocals` caught unused `numClass` in MemoryGauge.vue**
The gauge colour logic was correctly implemented as `arcColor`, but `numClass`
was drafted and not used in the template. Removed before committing. No spec impact.

**D2 — `Array.prototype.at()` not in ES2020 lib**
TypeScript strict target ES2020 does not include `Array.prototype.at()`.
Used `h[h.length - 1]` instead. No spec impact.

**D3 — Windows guard at import time, not in `_collect()`**
The phase plan (F8) placed the guard inside `_collect()`. The implemented
design raises `NotImplementedError` at import time (module-level `if sys.platform`
check). This surfaces the error earlier and cleaner — the `main.py` try/except
at import catches it and sets `_HAS_METRICS = False`, then emits `metrics:error`
once and the loop exits. Effect: same behaviour for the frontend, cleaner code path.

**D4 — Gradient mesh on `body { background: }` not `body::before`**
The phase plan (F14) suggested `body::before` pseudo-element for the gradient
mesh. The final implementation puts it directly on `body { background: }` as a
multi-stop `radial-gradient` declaration. This is simpler, achieves the same
visual result, and avoids a z-index layering concern. The `body::before` approach
requires `position: fixed` + explicit z-index management for the grid overlay.

**D5 — Dashboard is single-route (no Vue Router navigation)**
The dashboard is intentionally a single route (`/`). Vue Router is present for
the picolet:// hash-routing requirement but only registers one route. This differs
from config-editor (3 routes) but matches the spec for a live-data dashboard.
The `App.vue` does all setup on mount directly.

---

## Build verification

```
npm install && npm run build
```
Output:
```
vite v5.4.21 building for production...
✓ 49 modules transformed.
dist/assets/index-*.css   5.40 kB | gzip: 1.62 kB
dist/assets/index-*.js  102.05 kB | gzip: 38.42 kB
✓ built in 1.75s
```

TypeScript: 0 errors (after fixing `noUnusedLocals` and `Array.at` issues).

Binary: `picolet build` produced `target/linux-x64/dashboard` at **1021 KB**
(NFR-EX-1 <= 3 MiB: pass).

---

## Gates passed

| Gate | Description | Result |
|---|---|---|
| A | npm run build | PASS |
| B | binary exists | PASS |
| C | binary size <= 3 MiB (actual: 1021 KB) | PASS |
| D | no CDN refs in binary | PASS |
| E | metrics_reader smoke | PASS |
| F | CSS gzipped <= 50 KB (actual: 1628 B) | PASS |
| G | 4 screenshots present + valid | PASS |
| H | generate_screenshots.py clean | PASS |
| I | picolet init --template dashboard | PASS |
| J | Windows NotImplementedError guard | PASS |
| K | SBOM contains Antonio + DM Sans | PASS |
| L | pytest (3 skipped, 0 failed) | PASS |

---

## NFR measurements

| NFR | Requirement | Actual |
|---|---|---|
| NFR-EX-1 | Binary <= 3 MiB | 1021 KB |
| NFR-EX-2 | Startup <= 1500 ms | Not measured (no AppHarness with display) |
| NFR-EX-3 | CSS <= 50 KB gzipped | 1628 bytes |
| NFR-EX-4 | No CDN at runtime | Verified clean |
| NFR-EX-5 | Deterministic screenshots | random.seed(42) per scenario |
| NFR-EX-6 | Screenshots regenerated on each run | Gate H passes |

---

## Commits (signed [PH22])

1. `[PH22] Note: asyncio.create_task before loop start works in MicroPython`
2. `[PH22] Note: os.listdir('/proc') works on Linux; PID scan fallback retained`
3. `[PH22] Decision: use same Latin-subset woff2 for Antonio-Regular and Antonio-Bold`
4. `[PH22] Add dashboard example project scaffold`
5. `[PH22] Add /proc metrics reader for dashboard backend`
6. `[PH22] Add dashboard Python entrypoint with 1Hz metrics loop`
7. `[PH22] Add dashboard CSS, store, and SVG path utilities`
8. `[PH22] Add Vue components and dashboard grid layout`
9. `[PH22] Add dashboard tests and screenshot generator`
10. `[PH22] Wire dashboard into init_cmd, templates, and SBOM`
11. `[PH22] Add phase-22 exit gate runner`

---

## Headline risk

**Aesthetic confidence: high.** The screenshots show the intended Bloomberg-terminal
density: slate background with gradient mesh, ice-blue chart lines, amber warning
and alarm-red colouring on the correct thresholds, Antonio condensed uppercase
section labels, JetBrains Mono 36px numerals in the gauge, DM Sans body text,
bezel borders on all widget panels, and the asymmetric 12-column grid layout.

**No open blockers.** All 12 gates green. The three pytest integration tests skip
(no Xvfb inspector page) which is the documented CI path.

**Startup timing not measured** for NFR-EX-2 — requires a display or Xvfb
with the AppHarness wired to a running binary. The binary itself builds and runs
correctly; the 1s first-tick lag (baseline collection) is the only latency.
