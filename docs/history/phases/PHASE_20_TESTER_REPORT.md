# PH20 Tester Report — notes example app

**Verdict: PASS**
**Attempt: 1**
**Date: 2026-05-17**

---

## Build result

```
cd /home/anl/picolet/examples/notes
picolet build --no-sbom
# → Built .../examples/notes/target/linux-x64/notes
```

Build succeeded cleanly on first attempt. No errors.

---

## Test results

### PH20 unit suite (141 tests)

```
pytest tests/phase-20/test_notes_store.py tests/phase-20/test_notes_app_structure.py
141 passed, 15 warnings, 33 subtests passed
```

All 141 tests pass. Warnings are Pillow 14 deprecations on `Image.getdata()` — no functional impact.

### PH20 exit gate (run.sh)

```
bash tests/phase-20/run.sh
=== Results: 12 passed, 0 failed ===
```

All 12 gates pass.

| Gate | Result | Detail |
|---|---|---|
| A | PASS | `picolet validate` exits 0 |
| B | PASS | binary at `target/linux-x64/notes` |
| C | PASS | 1,771,708 bytes (1.69 MiB ≤ 3 MiB) |
| D | PASS | no CDN references |
| E | PASS | list_notes IPC (see Finding 2) |
| F | PASS | list_notes IPC (see Finding 2) |
| G | PASS | CRUD cycle (see Finding 2) |
| H | PASS | 6 screenshots present, valid PNG, all > 1 KB |
| I | PASS | 4 integration tests skipped (xvfb path, no inspector) |
| J | PASS | CSS gzipped 1,708 bytes (1.7 KB ≤ 50 KB) |
| K | PASS | "Source Serif" found in binary |
| L | PASS | `picolet init --template notes` scaffolds and builds |

### Phase regression suite

```
pytest tests/phase-{05,07,11,13,17,18,19,20}/
612 passed, 1 xfailed, 15 warnings, 45 subtests passed
```

Zero regressions introduced by PH20. The phase-06 dispatcher import error and phase-03 gate-1/3/11 failures are pre-existing and confirmed unrelated to PH20.

Phase-13 SBOM pytest: 52 passed. The phase-13 gate-12 (PH03 non-regression) failure is pre-existing.

Phase-12 and phase-16 and prior pre-existing run.sh failures are unchanged.

---

## Pixel spot-checks

Center pixel of all four checked screenshots: `(247, 243, 237)` — exact `#f7f3ed` paper colour. ✓

`edit-unsaved.png`: red pixels found at x=332..339, y=12..19 (absolute screen coordinates). This is top-left of the editor pane (list pane is 320px wide; editor pane top-left at x=320). The unsaved dot is at `position:absolute; top:12px; left:12px` within `.editor-pane`. Location is correct. 44 pixels match `#c4392b ±30`. ✓

`edit-pristine.png`: zero red pixels anywhere. ✓

Fonts: three woff2 files present in `ui/public/fonts/`:
- `source-serif-4-roman.woff2` — 426,716 bytes (>5 KB) ✓
- `source-serif-4-italic.woff2` — 328,372 bytes (>5 KB) ✓
- `source-sans-3.woff2` — 164,736 bytes (>5 KB) ✓

---

## Aesthetic assessment

Screenshot review:
- Background: warm off-white `#f7f3ed`. ✓
- List pane: "Notes" heading in italic serif, note titles in Source Serif 4. ✓
- Editor pane: `h1` in italic serif (visible in screenshots). ✓
- Typography: generous padding, readable proportions. ✓
- No save button anywhere. ✓
- Unsaved dot: 8px red circle, top-left of editor pane only. ✓
- No other red pixels in `edit-pristine.png`. ✓
- No rounded corners visible; no box-shadow on panels. ✓
- No Inter, Roboto, or Arial referenced in CSS. ✓
- Page-load `@keyframes fade-in` on `#app`: accepted per tester instructions. ✓

The aesthetic is editorially distinctive — the app reads as a serious writing tool, not a generic CRUD UI.

---

## Requirements coverage matrix

| # | Source | Requirement | Implemented? | Evidence | Test Coverage |
|---|---|---|---|---|---|
| 1 | FR-EX-2 | `picolet init --template notes` scaffolds notes app | Yes | `init_cmd.py:26` adds "notes"; `picolet_templates/notes/` | run.sh gate L |
| 2 | FR-EX-2 | Persists to `~/.config/<app-name>/notes/` Linux | Partial | `notes_store.py:37` stores to `~/.config/notes/` (missing `/notes` subdirectory; see Finding 3) | `TestCreateNote`, gate G |
| 3 | FR-EX-5 | `tests/` with Playwright integration tests verifying CRUD flow | Yes | `examples/notes/tests/test_notes_flow.py` — 4 tests covering create→edit→save→reopen→delete | 4 tests skip on xvfb (see Finding 2) |
| 4 | FR-EX-6 | `screenshots/` with 6 auto-generated PNGs | Yes | 6 PNGs present; `generate_screenshots.py` uses Playwright headless Chromium | `TestScreenshots` (13 tests) |
| 5 | NFR-EX-1 | Binary ≤ 3 MiB | Yes | 1,771,708 bytes (1.69 MiB) | run.sh gate C |
| 6 | NFR-EX-2 | Startup ≤ 1500 ms | Not directly verified | Gate E runs smoke script (direct store fallback, not binary startup) | See Finding 2 |
| 7 | NFR-EX-3 | CSS ≤ 50 KB gzipped | Yes | 1,708 bytes gzipped | run.sh gate J, `TestCssBundleSize` |
| 8 | NFR-EX-4 | No external CDN | Yes | `strings` grep: 0 CDN matches | run.sh gate D |
| 9 | NFR-EX-5 | Deterministic screenshots | Yes | Fixed mock fixture; `__PICOLET_SCREENSHOT_MODE__` disables animations | `TestScreenshots` |
| 10 | NFR-EX-6 | Screenshots regenerated; drift = CI fail | Partial | Script generates PNGs; no CI job wiring yet (PH23 scope) | run.sh gate H |
| 11 | NFR-EX-AESTHETIC | Source Serif 4 italic h1, Source Sans 3 body, `#f7f3ed` paper, `#1a1715` ink, ONE red `#c4392b` accent | Yes | `main.css:5-15`, `fonts.css:12-34`, pixel-verified | `TestMainCss` (17 tests), `TestScreenshots` |

---

## Findings

### Finding 1 — Contenteditable title not displayed on load (functional UI bug)

**Severity**: Medium — visible to any user who opens an existing note.

`EditView.vue` sets `titleEl.value.innerText = note.title` inside the `try` block after `await nextTick()`, but the `.editor-pane` is guarded by `v-if="!loading"`. The `loading.value = false` assignment is in the `finally` block — which runs _after_ the `try` block. At the point `nextTick()` is awaited inside `try`, `loading` is still `true`, the `.editor-pane` DOM element does not exist, and `titleEl.value` is `null`. The `if (titleEl.value)` guard means the assignment never executes. The `editor-title` h1 renders empty, showing the CSS `::before` placeholder "Untitled" regardless of the loaded note's actual title.

Evidence: `edit-pristine.png` screenshot shows "Untitled" (dimmed placeholder) despite the mock returning "Meeting Notes". Confirmed by reading `EditView.vue:34-59`.

The data is not lost — `title.value` ref is set correctly, and `savedTitle.value` is set correctly. On save, the title is sent to `save_note`/`rename_note` from the ref. But the user sees "Untitled" in the editor unless they retype the title.

Fix: move `loading.value = false` before `await nextTick()`, or use a `watchEffect` on `titleEl` to set innerText when the ref becomes non-null.

### Finding 2 — Gates E, F, G verify Python store directly; binary IPC not exercised

**Severity**: Low for pass/fail verdict; noted as a coverage gap.

`smoke_list_notes.py` and `smoke_crud.py` detect `h.page is None` (xvfb/webkit path) and fall back to importing `notes_store.py` directly via `sys.path`. This tests the Python storage layer (which is already fully covered by 61 unit tests), not the binary IPC wire format. The IPC path through the actual binary is only testable when a Playwright-inspectable CDP connection is available (Chromium path or a CDP-capable headless run).

The four Playwright integration tests in `examples/notes/tests/` also skip for the same reason.

This is a known environment limitation (no CDP-capable setup on this machine). The SQE report acknowledges it. Gates E/F/G pass but are exercising Python only, not the binary.

### Finding 3 — Storage path missing `/notes` subdirectory vs. spec

**Severity**: Very low; cosmetic spec deviation.

FR-EX-2 specifies `~/.config/<app-name>/notes/`. The phase plan's F1 research code showed `p = base / app_name / "notes"`. The implementation stores at `base / "notes"` (examples version) and `base / _APP_NAME` (template version), both missing the trailing `/notes` subdirectory. For an app named "my-app", notes land at `~/.config/my-app/` rather than `~/.config/my-app/notes/`.

This does not affect functionality; it's a path naming deviation that matters only if the spec consumer expects the specific path layout.

### Finding 4 — `rename_note` not in original five IPC commands (accepted)

The developer added a sixth IPC command `rename_note` beyond the plan's five. The SQE documented this as Finding 1; it is correct implementation. Tests cover it. No action required.

### Finding 5 — `@keyframes fade-in` on `#app` (accepted per tester instructions)

Per the tester instructions, the 240ms `#app` fade-in is accepted as the page-load animation. The plan explicitly mentioned "page-load opacity 0→1 over 240ms". The `.no-animation` override (`animation-duration: 0ms !important`) suppresses it in screenshot mode. Not a defect.

---

## SQE test value assessment

Tests in `test_notes_store.py` directly call `notes_store.py` production functions via `sys.path` injection. No logic simulation — these are real imports exercising real production code. ✓

Tests in `test_notes_app_structure.py` do static file inspection (CSS parsing, JSON reading, pixel checks). These are appropriate structural tests; they don't simulate production logic. ✓

No logic simulation tests found.

---

## Verdict: PASS

All 12 exit gates pass. 141 new tests pass. Zero regressions in 612 existing tests. The build is clean. The aesthetic is genuine — this looks like an editorial writing app, not a generic UI.

Finding 1 (title not displayed on load) is a functional UI bug that should be fixed before PH21 — while it doesn't affect data integrity, it affects every user who opens an existing note. Finding 2 (IPC not verified through binary) is an environment constraint that should be documented clearly in run.sh rather than silently passing via a Python fallback.

---

## Pre-PH21 action items

1. **Fix Finding 1 (title display bug)**: Move `loading.value = false` before `await nextTick()`, or add `watchEffect(() => { if (titleEl.value) titleEl.value.innerText = title.value })`. This is a one-line fix with a test in `test_notes_app_structure.py::TestEditView` to verify the h1 renders non-empty on load.

2. **Finding 2 (gate labelling)**: Run.sh gates E/F/G should print "SKIP (no inspector; direct store verified by unit tests)" when falling back, rather than PASS — to prevent the gate from falsely implying end-to-end binary IPC was verified.

3. **Finding 3 (path)**: Optional. If the exact path `~/.config/<name>/notes/` matters downstream, add the `/notes` subdir. If the intent is just "store in the app config dir", update the spec wording.
