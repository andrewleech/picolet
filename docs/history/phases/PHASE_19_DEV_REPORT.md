# PH19 Developer Report

## Iteration 2 — SQE regression fixes

### Bug 1: OFL-1.1 not in SBOM default allow-list

`packages/picolet/picolet/sbom_gen.py` — added `"OFL-1.1"` to
`_DEFAULT_ALLOW_LICENCES`. PH19 added JetBrains Mono and IBM Plex Sans
(both OFL-1.1, `link_type = "static"`) to `runtime.toml` without extending
the allow-list. The omission caused 2 phase-13 tests to fail on any SBOM
enforcement call that loaded the real `runtime.toml`.

`SIL-OFL-1.1` was not added — it is not a registered SPDX identifier.

Verified: `pytest tests/phase-13/` — 34 passed (was 32).

### Bug 2: Screenshots were blank 640×480 grayscale placeholders

**Root cause.** The original capture approach (`xwd` against the Xvfb root
window) ran before GTK composited the application frame. The resulting PNGs
were structurally valid (> 1 KB, correct magic bytes) but contained only
white pixels at 640×480 in L (grayscale) mode.

**Why the AppHarness / WebKit inspector path did not produce real captures.**
On Linux, `AppHarness._spawn()` starts its own Xvfb instance and stores the
display number in `self._xvfb_display`. The `start()` method treats a non-None
`_xvfb_display` as a signal that the WebKit Remote Inspector is inaccessible
(it is: `WEBKIT_INSPECTOR_SERVER` announces a port number but the binary
unbinds that port before WebKitGTK binds it, and `ss -tlnp` confirms it never
listens). This causes `self.page = None` and the fallback to `_xwd_screenshot`.

**Fix.** Replace the capture approach entirely. The Vue frontend in
`examples/pydfu/dist/` is a self-contained SPA that only needs `window.picolet`
to be defined. A headless Chromium session driven by Playwright can:

1. Serve `dist/` over a local HTTP server.
2. Inject a mock `window.picolet` implementation via `add_init_script` before
   the Vue app boots.
3. Drive the UI to each of the 6 named states by navigating hash routes,
   clicking elements, and controlling mock event timing.
4. Capture via `page.screenshot()` which returns real Chromium-rendered PNG.

This approach needs no picolet binary, no Xvfb, and no WebKit inspector.

**New file:** `examples/pydfu/scripts/generate_screenshots.py` — PEP 723
inline-deps script (`uv run` compatible). Uses `playwright>=1.40` and
`pillow>=10.0`. Falls back to the chromium-1134 installation if the default
`pw.chromium.executable_path` (headless-shell) is not installed.

**Screenshots replaced:** all 6 PNGs in `examples/pydfu/screenshots/`
are now 1200×800 full-colour renders. Pixel verification confirms
forge-orange (`#ff6b1a ±40`) and near-black chassis pixels in each file.

### Test results after fixes

```
pytest tests/phase-13/ tests/phase-19/ — 189 passed
pytest tests/phase-05/ tests/phase-07/ tests/phase-11/ tests/phase-13/
      tests/phase-17/ tests/phase-18/ tests/phase-19/ — 471 passed, 1 xfailed
bash tests/phase-19/run.sh --skip-slow — PASS (10 pass, 2 skip)
```

### Commits

| SHA | Subject |
|-----|---------|
| `943c924` | [PH19] Fix: add OFL-1.1 to SBOM default allow-list |
| `e1c7b49` | [PH19] Add generate_screenshots.py — Playwright/Chromium screenshot driver |
| `39ab33e` | [PH19] Replace placeholder screenshots with actual Playwright renders |
