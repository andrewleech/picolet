# PHASE 26 — macOS webview runtime variant

## Goal

Wire the PH25 WKWebView binding into the full runtime build pipeline
and produce `picolet-runtime-macos-{x64,arm64}-webview` artifacts. Validate
all four example apps build and pass their test suites against these
binaries. Update storage paths for notes and config-editor to use
macOS-appropriate locations.

## Prerequisites

- PH25 complete and green (WKWebView C glue and Python layer working).
- PH24 complete (macOS cli builds in CI).

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-WV-MAC-1 | macOS webview variant exists and uses WKWebView |
| FR-WV-MAC-4 | Window opens, IPC bridge round-trips in production mode |
| FR-WV-MAC-6 | bridge-js works; `window.picolet.*` functional |
| FR-EX-MAC-1 | All four example apps build and run on macOS webview |
| FR-EX-MAC-4 | notes persists to `~/Library/Application Support/` |
| FR-EX-MAC-5 | config-editor uses `~/Library/Application Support/` |
| NFR-MAC-2 | webview artifact ≤ 2 MiB |
| NFR-MAC-4 | No runtime package deps beyond WebKit.framework |
| NFR-MAC-8 | No GPL/AGPL static link |

## Dependencies

- PH25 (WKWebView binding fully implemented).
- PH18 (Vue toolchain) — example apps are Vue-based.
- PH19–PH22 (example apps exist).

## Key research findings

### macOS storage paths

The existing `notes` example stores notes at:
- Linux: `~/.config/notes/notes/`
- Windows: `%APPDATA%\notes\notes\`

macOS convention is `~/Library/Application Support/<app-name>/`. The
Python code reads these in the example's `main.py` via:
```python
if sys.platform == "win32":
    base = os.getenv("APPDATA", "")
elif sys.platform == "darwin":
    base = os.path.expanduser("~/Library/Application Support")
else:
    base = os.path.expanduser("~/.config")
```

This is a straightforward code-only change in each example's `main.py`.

### mpconfigvariant.mk Darwin flags

The webview variant `.mk` needs to compile `picolet_webview_mac.c` and
link the system frameworks on Darwin. The existing file:
```
/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-webview/mpconfigvariant.mk
```
must be updated with Darwin-conditional lines (added in PH25 for the
C source selection; confirmed to work in CI in this phase).

### picolet-bridge-js on macOS

The bridge-js bundle (`packages/picolet-bridge-js/`) is already frozen
into the webview romfs via `manifests/manifest_webview_unix.py`. No
macOS-specific changes needed — the same JS bundle works in WKWebView.

### Picolet:// scheme on macOS

The `_app.py` macOS branch must call `_register_picolet_scheme` before
the WKWebView is created (scheme registration is part of the
`WKWebViewConfiguration` — immutable after the WKWebView is initialized).
The scheme handler itself reads from `/rom/<path>` through the Python
VFS — platform-neutral.

## Files to modify

### `examples/notes/src/main.py`

Add macOS storage path:
```python
import sys, os
if sys.platform == "win32":
    _BASE = os.getenv("APPDATA", "") + "\\notes"
elif sys.platform == "darwin":
    _BASE = os.path.expanduser("~/Library/Application Support/notes")
else:
    _BASE = os.path.expanduser("~/.config/notes")
```

### `examples/config-editor/src/main.py`

Same pattern for `~/Library/Application Support/config-editor/schemas/`.

### `packages/picolet-runtime/python/picolet_ui/_app.py`

Confirm the three-way dispatch added in PH25 works end-to-end in
production mode (non-test, no `PICOLET_DEV_URL`).

## Integration points

### CI job for webview variant

The stub macOS CI job from PH24 is extended to include `webview` variant:
```yaml
matrix:
  include:
    - os: macos-13
      target: macos-x64
      variant: webview
    - os: macos-14
      target: macos-arm64
      variant: webview
```

Install step adds no extra brew packages — WebKit.framework is system-provided.

### Example app tests on macOS

The Playwright tests for webview examples use `AppHarness` with `browser="webkit"`.
On macOS, AppHarness spawns the binary with `PICOLET_TEST_MODE=1`, reads
`picolet:test-port=<N>` from stderr, and connects `_webkit.py` to the
WK inspector WebSocket. The same test code that works on Linux should
work on macOS without modification.

Window-visible detection for perf tests: on macOS, use:
```python
# Instead of xdotool search --pid
import subprocess
result = subprocess.run(["pgrep", "-P", str(proc.pid)], ...)
```
This is a minimal proxy for "window is showing" on macOS CI. A proper
Accessibility API check is overkill for the perf gate.

## Testing strategy

1. Build `macos-x64-webview` in CI.
2. Run all four example apps' test suites against the macOS binary using
   AppHarness + webkit path.
3. Verify screenshot PNG > 1 KB for each example.
4. Run notes example, create a note, quit, restart, verify note persists
   to `~/Library/Application Support/notes/`.
5. Verify binary size ≤ 2 MiB.

## Success criteria

- [ ] `picolet-runtime-macos-x64-webview` and `picolet-runtime-macos-arm64-webview`
      build in CI without errors.
- [ ] All four example apps (notes, config-editor, dashboard, pydfu) build
      against the macOS webview runtime.
- [ ] `AppHarness` attaches to the WK inspector and the test suites pass.
- [ ] Screenshots are non-empty PNGs.
- [ ] Notes storage goes to `~/Library/Application Support/notes/`.
- [ ] Binary size ≤ 2 MiB for both architectures.

## Risks

1. **Playwright test compatibility on macOS CI**: The test harness may
   need a `--headed=false` equivalent for macOS. macOS does not have
   xvfb; headless operation requires WKWebView in a non-displayed
   window. `NSWindow.orderOut:` keeps the window off-screen. Verify
   that screenshots still work in this mode.

2. **Timing differences on macOS**: macOS scheduler differs from Linux.
   The existing 3-second `NFR-TEST-1` port-announcement timeout may be
   tight on a cold runner. Monitor CI results and adjust if needed.

## Model tier recommendation

planner `sonnet`, developer `sonnet`, sqe `sonnet`, tester `sonnet`.
The hard work (C glue) is in PH25. This phase is wiring and verification.
