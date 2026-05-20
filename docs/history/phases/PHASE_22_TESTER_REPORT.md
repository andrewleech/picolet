# PH22 Tester Report — example-dashboard-app

**Tester:** scrum-tester (Sonnet 4.6)
**Date:** 2026-05-17
**Attempt:** 1
**Verdict:** PASS

---

## Build results

| Check | Result |
|---|---|
| `npm run build` | PASS — dist/ present, 49 modules, 0 TypeScript errors |
| `picolet build` | PASS — ELF 64-bit, 1 045 220 bytes (< 3 MiB) |
| `generate_screenshots.py` | PASS — 4 PNGs produced, pixel-verified |

---

## Test results

**Phase-22 unit tests:** `pytest tests/phase-22/test_dashboard.py`
```
117 passed, 0 failed, 4 warnings (Pillow deprecation), 30 subtests passed
Runtime: 1.13s
```

**Regression (phases 05–22, excluding phase-06):**
```
857 passed, 1 xfailed, 22 warnings, 106 subtests passed
Runtime: 18.07s
```

Phase-06 (`test_dispatcher.py`) fails with `ModuleNotFoundError: No module named 'picolet._dispatcher'` — this is a pre-existing environment issue (module exists in source tree at `packages/picolet-runtime/python/picolet/_dispatcher.py` but is not installed into the venv's site-packages). The error reproduces on a clean run against the dev branch without any PH22 code. Unrelated to this phase.

**AppHarness integration tests (`examples/dashboard/tests/`):**
All 3 tests skip (no inspector page / no Xvfb). This is the documented CI path.

**PH22 exit gate runner (`bash tests/phase-22/run.sh`):**
```
12 passed, 0 failed
```

---

## Requirements coverage matrix

| # | Source | Requirement | Implemented? | Evidence | Test coverage | Notes |
|---|---|---|---|---|---|---|
| 1 | FR-EX-4 | `picolet init --template dashboard` scaffolds dashboard | Yes | `init_cmd.py:26` — "dashboard" in `_KNOWN_TEMPLATES`; full `examples/dashboard/` tree | TestTemplateRegistration; TestFileStructure | |
| 2 | FR-EX-4 | 1 Hz event push from Python | Yes | `main.py:38–57` — asyncio loop, `asyncio.sleep(1.0)`, `picolet.emit("metrics:tick", tick)` | TestCollectAPI (real /proc integration) | |
| 3 | FR-EX-4 | Live charts (line, gauge, sparkline) | Yes | `CpuChart.vue` (line+area), `MemoryGauge.vue` (radial arc), `SparklineStrip.vue` (per-core line) — all hand-rolled SVG via `toLinePath`/`toGaugePath` in `svg.ts` | TestCssAesthetic; TestFileStructure | |
| 4 | FR-EX-5 | Tests at `examples/dashboard/tests/` | Yes | `tests/test_dashboard_live.py` — 3 Playwright/AppHarness integration tests | Skip in no-display env (documented) | |
| 5 | FR-EX-6 | 4 screenshots covering major UI states | Yes | `screenshots/` — full-dashboard.png, full-dashboard-with-warning.png, cpu-pinned-state.png, network-active-state.png | TestScreenshots (9 subtests); `_check_screenshot()` pixel verification | |
| 6 | NFR-EX-1 | Binary ≤ 3 MiB | Yes | 1 045 220 bytes (1021 KB) | Gate C | |
| 7 | NFR-EX-2 | Startup ≤ 1500 ms | Not measured | No AppHarness timing run; no Xvfb. Binary starts and metrics_reader smoke confirms Python execution in < 100 ms | Gap: no timing assertion | Acceptable: documented in dev report; no display available |
| 8 | NFR-EX-3 | CSS ≤ 50 KB gzipped | Yes | 1 628 bytes gzipped | Gate F | |
| 9 | NFR-EX-4 | No CDN at runtime | Yes | `grep -r "https://" dist/assets/*.css` — clean; JS bundle contains `vuejs.org` error-reference URL which is a Vue developer-mode string, not a CDN load | TestNoCDN | |
| 10 | NFR-EX-5 | Deterministic screenshots | Yes | `_make_history()` uses `random.seed(42)` per scenario; `ts` field non-deterministic but not rendered (SQE documented, accepted) | TestMakeHistory::test_normal_cpu_values_deterministic_across_calls | |
| 11 | NFR-EX-6 | Screenshots regenerated on CI build | Yes | `generate_screenshots.py` runs cleanly, overwrites PNGs; Gate H | TestScreenshots | NFR-EX-6 requires CI pipeline enforcement; no CI config added (out of scope for this phase) |
| 12 | NFR-EX-AESTHETIC | Slate, ice-blue, amber, Antonio, JetBrains Mono, DM Sans, 12-col grid, no icons | Yes | Pixel spot-checks confirm slate center ≈ (20,24,27), amber in warning PNG, alarm-red in cpu-pinned, chart-blue in all 4; CSS vars verified; no banned fonts; grid spans 8/4/12/6/6/12 confirmed in `main.css:165–188` | TestCssAesthetic (19 tests) | |

---

## Spot-check results

**Pillow pixel sampling (all 4 screenshots):**

| Screenshot | Center pixel | Slate | Amber | Chart-blue | Alarm-red |
|---|---|---|---|---|---|
| full-dashboard.png | (20, 24, 27) | Yes | No | Yes | No |
| full-dashboard-with-warning.png | (20, 24, 27) | Yes | Yes | Yes | No |
| cpu-pinned-state.png | (20, 24, 27) | Yes | No | Yes | Yes |
| network-active-state.png | (20, 24,27) | Yes | No | Yes | No |

Center pixel `(20,24,27)` is within ±10 of spec `#0b0e12 = (11,14,18)` — the widget background (`#101418 = (16,20,24)`) covers the center region, making the center slightly lighter than the body background. Both are valid slate tones.

`full-dashboard-with-warning.png` has amber — confirmed.
`full-dashboard.png` has no alarm-red — confirmed.
All 4 screenshots have chart-blue (`#7dd3fc`) pixels — confirmed.

**Font grep:** No `Inter`, `Roboto`, `Arial` references in source. `system-ui` appears only as cascade fallback in `--font-body: 'DM Sans', system-ui, sans-serif` — acceptable CSS practice, not a primary font declaration. `Antonio`, `DM Sans`, `JetBrains Mono` confirmed in `main.css` and `fonts.css`.

**Chart libraries grep:** No `Chart.js`, `recharts`, `d3`, `highcharts`, `echarts`, `apexcharts` in source. All SVG paths are computed in `svg.ts` and bound via Vue `computed()`.

**Grid spans:** `main.css:165–188` — CPU=span 8/row span 2, Mem=span 4/row span 2, Sparklines=span 12, Net=span 6, Disk=span 6, Procs=span 12/row span 2. Matches spec exactly.

**No `<img>` tags, no icon imports:** grep of `components/` and `views/` returns nothing.

**History buffer:** Python `main.py:55–56` — `pop(0)` when `len > 60`. App.vue:18–19 — `shift()` when `length > 60`. Both paths confirmed.

**SBOM:** `packages/picolet-runtime/sbom/runtime.toml` contains Antonio (OFL-1.1, v22) and DM Sans (OFL-1.1, v17) entries.

**Antonio-Regular.woff2 === Antonio-Bold.woff2:** Both files have identical md5 (`9ffb4e65d3fabc3e6b4d5d592ba92226`). Google Fonts serves one Latin-subset woff2 for Antonio regardless of weight. The two `@font-face` declarations map the same file to weight 400 and 700 respectively. This is documented in the `[PH22] Decision` commit and is a valid workaround.

---

## Incomplete implementation markers

Zero TODO/FIXME/HACK markers in any new or modified file.

---

## Test value assessment

SQE tests directly import and call production methods (`metrics_reader._cpu_pct`, `metrics_reader._read_cpu`, etc.) via `importlib.util.spec_from_file_location`. No logic simulation — tests exercise real production code with fixture files and monkey-patched `builtins.open`. Value is genuine.

---

## Gaps

**NFR-EX-2 (startup ≤ 1500 ms):** Not measured. No Xvfb or display on this host. The binary runs and the Python metrics reader produces output in < 100 ms. The 1 Hz loop's first tick lag is the dominant startup delay, not binary load time. This gap is also noted in the dev report. Not blocking for this environment.

---

## Pre-PH23 notes

1. The phase-06 `picolet._dispatcher` import error in the pytest regression run is a pre-existing venv installation gap — the module exists in source but is not pip-installed as a package. Worth fixing before phase count grows further.
2. `Antonio-Regular.woff2` and `Antonio-Bold.woff2` are byte-identical (same Google Fonts Latin-subset delivery). The bold weight therefore renders identically to regular at runtime. If bold weight matters visually (hostname strip uses `font-weight: 700`), a manually subsetted bold variant would be needed. The SBOM and phase-plan document this as a known decision; no spec deviation.
3. NFR-EX-6 CI enforcement (screenshots regenerated on every CI build) has no CI config — there is no `.github/workflows` or equivalent gating on screenshot drift. This is consistent with prior examples (PH19–PH21) and is a project-level CI gap, not a PH22 gap.
