# PH17 Dev Report — Autonomous test + remote-control infrastructure

**Branch**: `dev`  
**Commits**: 8 signed `[PH17]` commits (ad0851a … cad53bb)  
**Date**: 2026-05-17

---

## Chunks completed

| Chunk | Description | FR/NFR | Status |
|-------|-------------|--------|--------|
| 1 | Linux/WebKit inspector port wiring | FR-TEST-1 | Done |
| 2 | Windows/WebView2 CDP wiring | FR-TEST-1 | Done |
| 3 | LVGL `picolet._test` API + PNG encoder | FR-TEST-2 | Done |
| 4+5 | `picolet test` CLI + `AppHarness` | FR-TEST-3..6 | Done |
| 6 | xvfb-run autodetect | FR-TEST-4 | Done (embedded in Chunks 4+5) |
| 7 | Phase-17 test script + release-build env assertion | NFR-TEST-1, NFR-TEST-2 | Done |
| 8 | Build verification + docs update | — | Done |

---

## Files created

### Runtime — Linux/WebKit inspector

- `packages/picolet-runtime/python/picolet_ui/_test_port.py` — picks a free 127.0.0.1 TCP port via libffi POSIX socket/bind/getsockname/close.
- `packages/picolet-runtime/python/picolet_ui/_webview.py` — (modified) if `PICOLET_TEST_MODE=1`: call `pick_test_port()`, set `WEBKIT_INSPECTOR_SERVER` before `webkit_web_view_new()`, enable developer extras, write `picolet:test-port=<N>` to stderr.

### Runtime — Windows/WebView2 CDP

- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/picolet_webview2.h` — (modified) add `picolet_wv2_pick_test_port()`; update `picolet_wv2_create_environment_blocking` signature to accept `extra_browser_args`.
- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/include/WebView2_min.h` — (modified) add `PicoletWv2EnvOptions` struct + vtable; update `PFN_CreateCoreWebView2EnvironmentWithOptions` third arg type.
- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/picolet_webview2.c` — (modified) add winsock2/wchar includes, `picolet_wv2_pick_test_port()`, `g_env_opts_vtbl` shim, updated `picolet_wv2_create_environment_blocking`.
- `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/mpconfigvariant.mk` — (modified) add explicit `-lws2_32`.
- `packages/picolet-runtime/python/picolet_ui/_win_ffi.py` — (modified) add `picolet_wv2_pick_test_port` FFI binding; update `picolet_wv2_create_environment_blocking` FFI signature.
- `packages/picolet-runtime/python/picolet_ui/_webview.py` — (modified) if `PICOLET_TEST_MODE=1`: call `picolet_wv2_pick_test_port()`, pass `--remote-debugging-port=<N> --remote-debugging-address=127.0.0.1` as `extra_browser_args`, write `picolet:test-port=<N>` after controller creation.

### Runtime — LVGL picolet._test

- `packages/picolet-runtime/overlay/modules/picolet_lvgl_test/picolet_lvgl_png.h` — PNG encoder header.
- `packages/picolet-runtime/overlay/modules/picolet_lvgl_test/picolet_lvgl_png.c` — PNG encoder using libz.so.1 via dlopen (no stb, no static zlib).
- `packages/picolet-runtime/overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.mk` — (modified) add `picolet_lvgl_png.c` to `SRC_C`, INC, `LDFLAGS_USERMOD` (`--export-dynamic`, `-ldl`).
- `packages/picolet-runtime/python/picolet/_test.py` — `tap()`, `press()`, `snapshot()` with ring buffer input injection (R7: no allocation in hot path).
- `packages/picolet-runtime/python/picolet_ui/_sanity.py` — renamed from `_test.py` to free the `picolet._test` name; phase-07/11/12 tests updated to import `picolet_ui._sanity`.

### CLI — picolet test subcommand

- `packages/picolet-cli/picolet_cli/test_cmd.py` — `picolet test` subcommand; reads `picolet:test-port=<N>` from stderr; auto-detects browser (webkit/chromium); xvfb-run wrapping; bare/screenshot/run modes.
- `packages/picolet-cli/picolet_cli/__main__.py` — (modified) register `test_cmd`.
- `packages/picolet-cli/pyproject.toml` — (modified) add `picolet-testing` dependency.

### picolet.testing host package

- `packages/picolet-testing/pyproject.toml`
- `packages/picolet-testing/picolet/__init__.py` — PEP 420 namespace package.
- `packages/picolet-testing/picolet/testing/__init__.py` — re-exports `AppHarness`.
- `packages/picolet-testing/picolet/testing/_harness.py` — `AppHarness` class; start/stop, stderr port reader, `_wait_for_ready` (polls `window.picolet.__ready__ === true`), LVGL stdio path.
- `packages/picolet-testing/picolet/testing/_chromium.py` — `attach_chromium()` via `playwright.chromium.connect_over_cdp`.
- `packages/picolet-testing/picolet/testing/_webkit.py` — `WebKitPage` duck facade over the WebKit Inspector Protocol (WebSocket JSON-RPC).

### Bridge JS

- `packages/picolet-bridge-js/src/index.ts` — (modified) add `(window as any).picolet.__ready__ = true` after `window.picolet` assignment.

### Tests and docs

- `tests/phase-17/run.sh` — 9 gates (A–I): CLI wiring, port announcement, webview screenshot, LVGL screenshot, release build env assertion, loopback port, startup timing, chromium/Linux-webview error, bridge `__ready__` flag.
- `docs/architecture.md` — (modified) append "Test surface" section.
- `docs/phases/PHASE_17_DEV_REPORT.md` — this file.
- `.github/workflows/release.yml` — (modified) add step asserting `PICOLET_TEST_MODE` is not set in the release job environment (NFR-TEST-2).
- `pyproject.toml` (root workspace) — (modified) add `packages/picolet-testing` to workspace members.

### Micropython submodule (Chunk 8 fix)

- `packages/picolet-runtime/micropython/ports/windows/variants/picolet-webview/picolet_webview2.h` — mirrored from overlay.
- `packages/picolet-runtime/micropython/ports/windows/variants/picolet-webview/include/WebView2_min.h` — mirrored from overlay.
- `packages/picolet-runtime/micropython/ports/windows/variants/picolet-webview/picolet_webview2.c` — mirrored from overlay; also fixed `add_*/Create*` comment (the `*/` was terminating the block comment, causing MinGW compile errors).
- `packages/picolet-runtime/micropython/ports/windows/variants/picolet-webview/mpconfigvariant.mk` — mirrored from overlay.

---

## Deviations from plan

### D1 — PNG encoder via libz dlopen instead of stb_image_write.h

The plan called for vendoring `stb_image_write.h` as a single-header public-domain file. No internet access is available in the build environment, so stb could not be downloaded. The implementation uses `libz.so.1` via `dlopen()` instead, writing a minimal PNG encoder (IHDR/IDAT/IEND, filter-type-0 per scanline). The result is functionally equivalent. This approach avoids any vendored file and uses a system library already present on Linux (NFR-5 compliant: libz is LGPL-2.1+ and loaded dynamically, not statically linked).

### D2 — Chunk 6 embedded in Chunks 4+5

The phase plan listed Chunk 6 (xvfb-run autodetect) as a separate chunk. It was implemented inline within `test_cmd.py` and `AppHarness` during Chunks 4+5. A note commit was created for the audit trail.

### D3 — micropython submodule files patched directly

The overlay application workflow (`rebuild-integration.sh`) copies overlay files into the micropython submodule. In this case, the submodule files needed to match the overlay before a `rebuild-integration.sh` run could be triggered. The submodule files were patched directly so the Windows dockcross build passes immediately. Both the overlay and submodule copies are now consistent.

### D4 — Pre-existing MinGW comment bug fixed

The `add_*/Create*` text in a block comment (pre-PH17, present in both overlay and submodule) terminated the C comment prematurely when compiled by MinGW-w64. This was a latent bug not caught by the Linux builds (which never compile `picolet_webview2.c`). Fixed by rewording to `add_X/CreateX callbacks`.

---

## Build verification

| Target | Variant | Result | Binary size |
|--------|---------|--------|-------------|
| linux-x64 | cli | GREEN | 653 KB / 1024 KB ceiling |
| linux-x64 | webview | GREEN | 711 KB / 2048 KB ceiling |
| linux-x64 | lvgl | GREEN | 1680 KB / 2048 KB ceiling |
| windows-x64 | cli | GREEN | 413 KB / 1024 KB ceiling |
| windows-x64 | webview | GREEN | — |
| windows-x64 | lvgl | RED (pre-existing) | 2051 KB / 2048 KB ceiling |

The `windows-x64/lvgl` NFR-3 violation (2048 bytes over limit) is confirmed pre-existing: `git stash` of all PH17 changes reproduces the same failure. Not introduced by PH17.

---

## FR/NFR coverage

| Requirement | Coverage |
|-------------|----------|
| FR-TEST-1 | `picolet:test-port=<N>` announced on stderr; inspector bound to 127.0.0.1 only |
| FR-TEST-2 | `picolet._test.tap()`, `.press()`, `.snapshot()` on LVGL |
| FR-TEST-3 | `picolet test <binary>` returns Playwright-compatible page facade |
| FR-TEST-4 | `picolet test --screenshot <png>` + xvfb-run autodetect |
| FR-TEST-5 | `picolet.testing.AppHarness` in `packages/picolet-testing` |
| FR-TEST-6 | LVGL harness same `snapshot()` / stdio path via `AppHarness` |
| NFR-TEST-1 | Harness retries up to 10 s for port; binary itself has no added startup cost |
| NFR-TEST-2 | Port bound to 127.0.0.1; release workflow asserts `PICOLET_TEST_MODE` not set |
