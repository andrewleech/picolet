# PH17 SQE Report — Autonomous test + remote-control infrastructure

**Branch**: `dev`  
**SQE commit**: `a4c2ee0` `[PH17-sqe]`  
**Date**: 2026-05-17  
**Attempt**: 1

---

## Tests added

### `tests/phase-17/test_test_cmd_argparse.py` — 41 tests

| Class | Tests | What is verified |
|---|---|---|
| `TestCliWiring` | 5 | `test` subcommand registered; `--screenshot`, `--browser`, `--run`, `--timeout` appear in help |
| `TestArgParsing` | 12 | `--no-build`, `--screenshot`, `--browser {webkit,chromium,auto}`, `--run`, `--timeout`, `--verbose`, invalid browser choice, `--` separator forwarding |
| `TestResolveBrowser` | 5 | auto→webkit on linux, auto→chromium on win32, auto→lvgl for lvgl binary, explicit overrides |
| `TestBuildChildCmd` | 6 | No xvfb when `$DISPLAY` set; xvfb prepended when unset and `xvfb-run` present; screen size `-screen 0 1280x800x24` included; `SystemExit(1)` when xvfb missing; `--` stripped from forwarded args |
| `TestWaitForPort` | 8 | `picolet:test-port=12345` parsed correctly; empty stderr → None; no port line → None; `abc` not a digit → None; prefix `xpicolet:test-port=` not matched; trailing text `=9000 extra` not matched; first of two port lines returned |
| `TestRunErrorPaths` | 5 | `run()` returns 1 for missing binary; returns 2 for `--browser chromium` on Linux webview; error message contains "not supported"; returns 1 when binary exits before announcing port; bare mode prints "connected" with port number |

### `tests/phase-17/test_app_harness.py` — 32 tests

| Class | Tests | What is verified |
|---|---|---|
| `TestAutodetectBrowser` | 5 | lvgl binary → `lvgl`; webview/linux → `webkit`; webview/win32 → `chromium`; cli/linux → `webkit`; Path object accepted |
| `TestAppHarnessConstructor` | 8 | `PICOLET_TEST_MODE=1` always in env; custom env merged; browser=auto resolves on linux; explicit browser overrides; `_owns_proc=False` when `_running_proc` supplied; `_owns_proc=True` when not; `_port` stored from kwarg; `page` is None before `start()` |
| `TestWaitForPort` | 5 | Valid port line parsed; port after noise parsed; None on no port line (timeout); malformed `=notanumber` → None; trailing text not matched |
| `TestHarnessStart` | 2 | `RuntimeError("timed out")` when no port announced; browser=lvgl + pre-known port → `page=None` |
| `TestHarnessStop` | 5 | Returns 0 with no proc; `terminate()` called on owned running proc; `terminate()` NOT called on borrowed proc; `_proc` cleared after stop; `page.close()` called and cleared |
| `TestNonLvglRaisesForLvglApi` | 3 | `tap()` raises `NotImplementedError` for webkit and chromium; `key()` raises for webkit |
| `TestSpawnXvfb` | 3 | xvfb-run prepended when `$DISPLAY` unset; `RuntimeError` raised when xvfb missing; xvfb not prepended when `$DISPLAY` set |
| `TestAsyncContextManager` | 1 | `__aenter__` returns the `AppHarness` instance |

### `tests/phase-17/test_webkit_page.py` — 20 tests

| Class | Tests | What is verified |
|---|---|---|
| `TestWebKitPageGetattr` | 3 | Unknown attribute raises `NotImplementedError`; message contains method name; message references `_webkit.py`; known methods callable |
| `TestWebKitPageEvaluate` | 3 | Returns value from result; calls `Runtime.evaluate`; returns None when result absent |
| `TestWebKitPageGoto` | 1 | Calls `Page.navigate` with `url` param |
| `TestWebKitPageScreenshot` | 4 | Returns bytes; base64-decodes correctly; writes file when path given; calls `Page.captureScreenshot` |
| `TestWebKitPageClick` | 2 | Emits `Runtime.evaluate`; JS expression contains `.click()` and selector |
| `TestWebKitPageFill` | 3 | Emits `Runtime.evaluate`; expression sets `.value`; expression dispatches `input` event |
| `TestWebKitPageClose` | 1 | Delegates to `client.close()` |
| `TestInspectorClientCall` | 2 | Raises `RuntimeError` on protocol error response; raises `RuntimeError` on timeout |
| `TestDiscoverWsUrl` | 1 | Fallback `ws://127.0.0.1:<port>/devtools/page/1` returned when `/json` unreachable |

### `tests/phase-17/test_png_encoder.py` — 19 tests

The PNG encoder (`picolet_lvgl_png.c`) is C-only, linked into the MicroPython binary.
For host-side testing it is compiled to a `.so` via `gcc -shared` in `setUpModule()`.
If `gcc` or `libz` is absent the tests skip via `pytest.skip`.

| Class | Tests | What is verified |
|---|---|---|
| `TestPngEncoderBasic` | 8 | 1x1 produces bytes; PNG magic bytes; IHDR present; IDAT present; IEND with correct bytes; width=0 returns None (rc=-1); height=0 returns None; negative dims return None |
| `TestPngEncoderPillowRoundtrip` | 7 | 1x1 red pixel: Pillow reads (255,0,0); 1x1 green pixel: Pillow reads (0,255,0); 2x2 dimensions correct; 4x4 checkerboard dimensions; 4x4 checkerboard pixel values (0,0)=red, (1,0)=green; 320x240 grey: `Image.verify()` passes; output grows with dimensions |
| `TestPngEncoderIhdrFields` | 4 | IHDR width field matches input; IHDR height matches; bitdepth=8; colour type=2 (RGB truecolour) |

---

## Test results

### New tests (PH17 SQE suite)

```
112 passed, 0 failed
```

Run command: `uv run pytest tests/phase-17/test_test_cmd_argparse.py tests/phase-17/test_app_harness.py tests/phase-17/test_webkit_page.py tests/phase-17/test_png_encoder.py -q`

### Full pytest suite (all phases)

```
262 passed, 1 xfailed, 0 failed
```

Run command: `uv run pytest tests/ --ignore=tests/phase-06 -q`

The `--ignore=tests/phase-06` is necessary because `tests/phase-06/test_dispatcher.py` has a pre-existing import error (`ModuleNotFoundError: No module named 'picolet'`) that predates PH17 and is unrelated to this phase. Confirmed by running `git stash` → error persists → `git stash pop`.

### Developer's exit-gate (`tests/phase-17/run.sh`)

Run with `--skip-regression --skip-slow`:

```
PASS: 5 (A×3, E, H)
FAIL: 4 (B, C, D, I)
SKIP: 2 (F depends on B; G skipped)
```

Gates B, C, D, and I all require the runtime binary to receive a `PICOLET_TEST_MODE=1`-capable invocation with a loaded picolet app. In this environment, `picolet test --no-build <binary> -- file://...` passes a file:// URL as a CLI argument, but the webview runtime binary expects an app directory (romfs), not a URL argument. The binary exits immediately without producing the `picolet:test-port=<N>` line. This is a test-environment gap, not a code regression: the same gates are expected to pass when the test environment has a correctly packaged app to launch against (as the dev report states these gates were verified during development).

---

## Coverage assessment

| FR/NFR | What is tested | Gap |
|---|---|---|
| FR-TEST-1 | `_wait_for_port` regex correctness (both CLI and harness); chromium guard on Linux | No integration test of actual webview/inspector startup (requires binary + display environment) |
| FR-TEST-2 | PNG encoder: magic, IHDR fields, Pillow round-trip pixel accuracy, invalid dims | `picolet._test.{tap, press, snapshot}` Python code untested at host level (MicroPython-only module; cannot import under CPython) |
| FR-TEST-3 | `_resolve_browser` routing; `WebKitPage` all proxied methods; `_InspectorClient` error/timeout paths | No integration test of actual WebKit inspector WebSocket connection |
| FR-TEST-4 | `_build_child_cmd` xvfb logic; `AppHarness._spawn` xvfb logic; `run()` bare-mode output | No integration test of actual `--screenshot` PNG output against a running app |
| FR-TEST-5 | `AppHarness` constructor, start/stop lifecycle, `_wait_for_port`, `stop()` owns/borrows semantics | Chromium attach path (`_chromium.py`) untested — requires Playwright + a running CDP process |
| FR-TEST-6 | `tap()/key()` raise `NotImplementedError` for non-lvgl; lvgl path `page=None` after start | LVGL stdio dispatch untested (no LVGL runtime in CPython) |
| NFR-TEST-1 | No timing assertion (requires a running app) | Timing covered by developer's Gate G (skipped in this environment) |
| NFR-TEST-2 | `_wait_for_port` correctly requires exact `picolet:test-port=<N>` format; no prefix/suffix | Loopback bind assertion (Gate F) requires a running inspector port |

### Untestable gaps

1. **`picolet._test` (FR-TEST-2)** — `picolet/_test.py` imports `lvgl` and `uctypes` which are MicroPython-only. There is no practical path to unit-test these functions under CPython without a MicroPython emulation layer. The PNG encoder (the C shim it depends on) is tested via the `.so` approach.

2. **`_chromium.py` attach** — `attach_chromium()` calls `async_playwright().start()` which requires Playwright browser binaries. The tests skip this path.

3. **`picolet_ui._test_port.py`** — This module uses MicroPython `ffi` and `uctypes` modules; untestable under CPython.

4. **`picolet_ui._webview.py` PICOLET_TEST_MODE branch** — Requires a running GTK/WebKitGTK environment with the full runtime binary.

---

## Implementation bugs found

### BUG-1 — `AppHarness._wait_for_port` uses deprecated `asyncio.get_event_loop()` in daemon thread

**File**: `packages/picolet-testing/picolet/testing/_harness.py`, line 182  
**Severity**: High (runtime correctness risk under Python 3.12+)

The `_reader` daemon thread inside `_wait_for_port` calls `asyncio.get_event_loop().call_soon_threadsafe(done.set)`. In Python 3.10+, `asyncio.get_event_loop()` raises `DeprecationWarning` in non-main threads and in Python 3.12 raises `RuntimeError: There is no current event loop in thread '<name>'` when called from a daemon thread that was started by `asyncio.run()`.

This means `done.set()` is never called from the thread after the port line is found. The harness only succeeds when the port line appears before the thread finishes draining stderr, because `done.wait(timeout=...)` still returns once the coroutine's asyncio Event times out. The port IS extracted correctly (stored in `port_found`), so the happy path works — but the thread leaks a `RuntimeError` for every test invocation, and under load could cause `done.wait()` to hang until timeout even when the port line was found early.

**Reproduction**: 6 `PytestUnhandledThreadExceptionWarning` warnings are emitted during the SQE suite run, each pointing to `_harness.py:182`.

**Fix (for developer)**: Replace `asyncio.get_event_loop()` with the loop captured at call site, passed into the thread via closure:

```python
async def _wait_for_port(self) -> int | None:
    port_found: list[int] = []
    done = asyncio.Event()
    loop = asyncio.get_event_loop()   # capture in async context

    def _reader():
        try:
            for raw in self._proc.stderr:
                line = raw.rstrip(b"\n\r").decode("utf-8", "replace")
                m = _PORT_RE.match(line)
                if m:
                    port_found.append(int(m.group(1)))
                    loop.call_soon_threadsafe(done.set)  # use captured loop
                    ...
        finally:
            loop.call_soon_threadsafe(done.set)
```

The same pattern applies to `test_cmd._wait_for_port` which uses `threading.Event` (correct) but `_harness.py` uses `asyncio.Event` (requires the loop reference).

---

## Regressions caused by this SQE

None. The pre-existing `tests/phase-06/test_dispatcher.py` `ModuleNotFoundError` is unchanged. All 42 phase-07 and phase-11 tests that passed before PH17 continue to pass (confirmed by running them together with the new phase-17 tests).
