# PH19 SQE Report — pydfu example app

## Test files created

All tests are under `tests/phase-19/`.

### `test_dfu_parser.py` — 20 tests

Exercises `pydfu_adapter.read_dfu_file` and `compute_crc` directly against the committed fixture and against in-test-generated DFU binaries.

| Class | Tests | What they verify |
|---|---|---|
| `TestReadDfuFileFixture` | 8 | Fixture round-trip: element count, addr (0x08000000), size (1024), data (zero bytes), key presence |
| `TestReadDfuFileRoundTrip` | 3 | Single-element and two-element synthetic DFU files parse back to the original data; `num` field is zero-indexed |
| `TestReadDfuFileErrors` | 4 | `ValueError` on bad DfuSe signature; `ValueError` on corrupted CRC field; `ValueError` on a flipped body byte; `FileNotFoundError` on nonexistent path |
| `TestComputeCrc` | 5 | `compute_crc` matches the DfuSe CRC formula (`0xFFFFFFFF & -zlib.crc32(data) - 1`) for empty, "hello world", 256×0xFF, 1 MiB zeros, and the fixture file |

### `test_mock_adapter.py` — 33 tests

Exercises `pydfu_adapter` loaded with `PICOLET_PYDFU_MOCK=1`, covering all five adapter entry points and the WinUSB guard.

| Class | Tests | What they verify |
|---|---|---|
| `TestListDfuDevicesMock` | 12 | Returns a list; exactly one device; vid==0x0483; pid==0xDF11; `id` key present; id is `"<bus>:<addr>"`; id matches bus+addr; `MOCK_EMPTY=True` returns `[]` |
| `TestGetMemoryLayoutMock` | 9 | Returns a list; at least one segment; all required keys present; addr == 0x08000000; last_addr > addr |
| `TestFlashDeviceMock` | 11 | At least one callback; final `done == total`; addr within element range; callback count == ceil(size/2048); partial block rounded up; addr/done/total are ints; done monotonically increases; multi-element accumulation; `abort_flash` does not raise |
| `TestWinUSBGuard` | 1 | `_ensure_lib` raises `NotImplementedError` with a Windows/WinUSB/FR-EX-7 message when `sys.platform == "win32"` and mock is disabled |

### `test_crc32_fallback.py` — 13 tests

Exercises `_crc32.crc32` (the pure-Python CRC32 fallback for MicroPython environments that lack `zlib.crc32`).

| Class | Tests | What they verify |
|---|---|---|
| `TestCrc32FallbackCorrectness` | 9 | Matches `zlib.crc32` for empty, "hello world", 256×0xFF, 1 MiB zeros, 1 MiB random bytes, chained/seeded call, default value=0, single byte, ASCII pangram |
| `TestCrc32FallbackTableInit` | 4 | Table is built lazily on first call; has 256 entries; first entry is 0; same table object reused on repeated calls |

### `test_vue_app_structure.py` — 71 tests

Static analysis of the Vue frontend source, CSS, fonts, template, and screenshots. No browser required.

| Class | Tests | What they verify |
|---|---|---|
| `TestPackageJson` | 4 | name=="pydfu"; vue dep present; vue-router dep present at ^4.x |
| `TestMainTs` | 5 | createApp, .mount, router usage, main.css imported, fonts.css imported |
| `TestRouterIndex` | 7 | Routes for `/`, `/flash`, `/log`; createWebHashHistory used; HomeView/FlashView/LogView imported |
| `TestAppVue` | 1 | RouterView (or router-view) referenced |
| `TestLedDotVue` | 5 | File exists; `status` prop; `led-ok`/`led-pulse` classes; `border-radius: 50%` for circular shape |
| `TestMainCss` | 21 | All 10 CSS custom properties defined; `--forge` value is #ff6b1a; `--font-mono` references JetBrains Mono; `--font-body` references IBM Plex Sans; global `border-radius: 0` present; `.btn` has `border-radius: 0`; no Inter/Roboto/Arial font families; body uses `var(--chassis)`; `.section-title` present and uppercase; `.no-animation` class present (NFR-EX-5/R7) |
| `TestFontFiles` | 7 | JetBrainsMono-Regular.woff2, IBMPlexSans-Regular.woff2, IBMPlexSans-SemiBold.woff2 exist; all have valid woff2 magic bytes (0x774F4632); JetBrains Mono > 10 KB |
| `TestPydfuTemplate` | 9 | `pydfu` in `_KNOWN_TEMPLATES`; template dir exists; picolet.toml has `{{name}}` and `framework = "vue"`; package.json has `{{name}}`; no package-lock.json; font woff2 files present; src/main.py and vite.config.ts exist |
| `TestCssBundleSize` | 1 | Gzipped CSS from `dist/assets/` <= 50 KB (NFR-EX-3); skipped if dist/ absent |
| `TestScreenshots` | 11 | All six PNG files exist; all have PNG magic bytes; all are > 1 KB |

## Test results

### Phase-19 SQE tests

```
137 passed, 12 subtests passed in 0.85s
```

### Full regression (phases 05, 07, 11, 13, 17, 18, 19)

Phase-06 tests require the `picolet` package installed and were excluded from this run (same pre-existing condition as prior phases). All other specified phases:

```
2 failed, 469 passed, 1 xfailed, 12 subtests passed
```

The 2 failures are in `tests/phase-13/test_sbom_gen.py`. Both are regressions caused by PH19 (see Bugs section below).

## Spec coverage

| Requirement | Gate / test | Status |
|---|---|---|
| FR-EX-1 — `picolet init --template pydfu` scaffolds a buildable app | `TestPydfuTemplate` (9 tests) + dev gate L | Covered |
| FR-EX-1 — `list_devices` / `read_dfu` IPC commands functional | `TestListDfuDevicesMock`, `TestReadDfuFileFixture`, smoke scripts | Covered |
| FR-EX-5 — tests shipped with example | Dev's `examples/pydfu/tests/` (integration) + SQE unit tests | Covered (integration tests need binary / display; see Gaps) |
| FR-EX-6 — screenshots present | `TestScreenshots` (11 tests) | Covered (see BUG-1 below) |
| FR-EX-7 — WinUSB guard | `TestWinUSBGuard` | Covered |
| NFR-EX-3 — CSS <= 50 KB gzipped | `TestCssBundleSize` | Covered (17.6 KB; well within budget) |
| NFR-EX-4 — no CDN | dev gate D | Covered |
| NFR-EX-5 — `.no-animation` screenshot mode | `TestMainCss.test_no_animation_class_present_for_screenshot_mode` | Covered |
| R6 — pure-Python CRC32 fallback | `TestCrc32FallbackCorrectness` (9 tests) | Covered |

## Bugs found

### BUG-1 — Screenshots are placeholder 640×480 all-white images (high severity)

All six PNG files in `examples/pydfu/screenshots/` are identical-dimension (640×480 L-mode) fully white images. They are structurally valid PNGs and are each > 1 KB, so they pass the gate-H structural checks. However, they do not capture any actual UI state.

Evidence:
```
device-list-empty.png:     100% white pixels
device-list-populated.png: 100% white pixels
flash-start.png:           100% white pixels
flash-mid-progress.png:    100% white pixels
flash-complete.png:        100% white pixels
flash-error.png:           100% white pixels
```

The gate-H criterion (`> 1 KB`, valid PNG) passes, but the NFR-EX-AESTHETIC requirement (memorable screenshot, human sign-off) cannot be satisfied with blank images. The SQE tests for screenshot presence and size pass because that is all the spec gates require at the automated level. This is a developer deliverable gap — the capture scripts in `examples/pydfu/screenshots/scripts/` exist but the captures were not generated against a running binary.

### BUG-2 — OFL-1.1 not in SBOM default allowlist (medium severity, regression)

PH19 added JetBrains Mono and IBM Plex Sans (both OFL-1.1) to `packages/picolet-runtime/sbom/runtime.toml` with `link_type = "static"`. The SBOM enforcement function `sbom_gen.check_violations` checks static-linked components against `_DEFAULT_ALLOW_LICENCES`, which does not include `"OFL-1.1"`. This causes 2 pre-existing phase-13 tests to fail:

- `TestEmitAppSbom::test_warn_path_unknown_licence`
- `TestEmitAppSbom::test_default_allowlist_permits_webkitgtk`

Both tests use `emit_app_sbom` against the real repo, which now loads the PH19 font entries from `runtime.toml`. The fix is to add `"OFL-1.1"` to `_DEFAULT_ALLOW_LICENCES` in `sbom_gen.py` (or to mark the font entries with `link_type = "dynamic"` if that is the intended policy).

These were green before PH19 commits landed on `dev`.

## Gaps (not bugs — scope limitations)

**Integration tests require binary + display.** The Playwright-based tests in `examples/pydfu/tests/` (`test_device_list.py`, `test_flash_flow.py`) need the compiled binary and either a real display or xvfb. They were not run in this SQE pass. The dev exit gate skips them under `--skip-slow`. These are covered by dev gate I when a display is available.

**IPC dispatcher path.** Testing the `flash` command's async task + event emission through `asyncio.get_event_loop().create_task(picolet.emit(...))` requires either the running binary or a test double for `picolet.emit`. The `picolet` package is not available in the plain-pytest environment. The mock adapter flash tests cover the progress callback contract; the IPC wiring is covered by the integration tests (gated on binary/display).

## Recommendation

PH19 is **conditionally acceptable**:
- All 137 SQE unit tests pass.
- The dev exit gate passes 10/12 gates (2 skipped for display).
- BUG-2 must be fixed before the SBOM pipeline is used (it breaks phase-13 regression tests).
- BUG-1 (blank screenshots) blocks NFR-EX-AESTHETIC human sign-off; the developer should re-run `bash examples/pydfu/screenshots/capture_screenshots.sh` against a running binary and commit the actual captures.
