#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright>=1.40",
#   "pillow>=10.0",
# ]
# ///
"""
generate_screenshots.py — capture six pydfu UI screenshots via Playwright.

Drives the Vue frontend (dist/) in headless Chromium with a mock window.picolet
backend. No picolet binary or Xvfb required; Playwright's headless Chromium
renders the actual CSS/fonts and produces non-blank PNG captures at 1200×800.

Usage:
    uv run examples/pydfu/scripts/generate_screenshots.py
    uv run examples/pydfu/scripts/generate_screenshots.py --out-dir /custom/path

Screenshots produced (in <repo>/examples/pydfu/screenshots/ by default):
    device-list-empty.png       / route, list_devices returns []
    device-list-populated.png   / route, list_devices returns 2 mock devices
    flash-start.png             /flash route, DFU file loaded, ready to flash
    flash-mid-progress.png      /flash route, flash running at ~55% progress
    flash-complete.png          /flash route, flash done successfully
    flash-error.png             /flash route, flash failed with error message

Each PNG is verified to be ≥ 800×600, have valid magic bytes, contain at least
one pixel with the forge-orange hue (#ff6b1a ± tolerance), and at least one
near-black pixel (the chassis background colour).

Run from CI via plain `uv run` — no DISPLAY required (Playwright headless).
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import os
import struct
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_EXAMPLES_PYDFU = _SCRIPT_DIR.parent
_DIST_DIR = _EXAMPLES_PYDFU / "dist"
_DEFAULT_OUT_DIR = _EXAMPLES_PYDFU / "screenshots"

# Target viewport size — matches picolet.toml [window] size = [1200, 800].
VIEWPORT_W = 1200
VIEWPORT_H = 800

# ---------------------------------------------------------------------------
# Mock window.picolet JS injected before the Vue app initialises.
# ---------------------------------------------------------------------------

# Two mock DFU devices returned by list_devices in "populated" state.
_MOCK_DEVICES = [
    {"bus": 1, "addr": 3, "vid": 0x0483, "pid": 0xDF11,
     "manufacturer": "STMicroelectronics", "product": "STM32 DFU Device"},
    {"bus": 1, "addr": 5, "vid": 0x0483, "pid": 0xDF11,
     "manufacturer": "STMicroelectronics", "product": "STM32 BOOTLOADER"},
]

# DFU elements returned by read_dfu (single-element firmware for the flash views).
_MOCK_ELEMENTS = [
    {"num": 0, "addr": 0x08000000, "size": 262144},
]


def _build_mock_picolet_js(
    *,
    devices: list | None = None,
    flash_events: list | None = None,
) -> str:
    """Return the JS snippet that installs window.picolet before Vue boots.

    Parameters
    ----------
    devices:
        Return value for list_devices.  [] for empty state.
    flash_events:
        Sequence of event dicts to emit after flash() is invoked.  Each dict
        has {"event": "dfu:progress"|"dfu:done"|"dfu:error", "data": {...}}.
        Events are fired 50 ms apart via setTimeout chains.
    """
    if devices is None:
        devices = []
    if flash_events is None:
        flash_events = []

    import json

    devices_json = json.dumps(devices)
    events_json = json.dumps(flash_events)

    return f"""
(function() {{
  const _devices = {devices_json};
  const _flashEvents = {events_json};
  const _handlers = {{}};

  function _fire(event, data) {{
    const hs = _handlers[event] || [];
    hs.forEach(h => {{ try {{ h(data); }} catch(e) {{}} }});
  }}

  function _scheduleFlashEvents() {{
    _flashEvents.forEach(function(ev, i) {{
      setTimeout(function() {{
        _fire(ev.event, ev.data);
      }}, (i + 1) * 80);
    }});
  }}

  window.picolet = {{
    __ready__: true,

    invoke: async function(cmd, args) {{
      if (cmd === 'list_devices') return _devices;
      if (cmd === 'read_dfu') return {json.dumps(_MOCK_ELEMENTS)};
      if (cmd === 'get_memory_layout') return [
        {{"addr": 0x08000000, "last_addr": 0x080FFFFF, "size": 1048576}}
      ];
      if (cmd === 'flash') {{
        _scheduleFlashEvents();
        return {{ ok: true, status: "started" }};
      }}
      if (cmd === 'abort_flash') return {{ ok: true }};
      throw new Error('unknown command: ' + cmd);
    }},

    on: function(event, handler) {{
      if (!_handlers[event]) _handlers[event] = [];
      _handlers[event].push(handler);
      return function() {{
        _handlers[event] = (_handlers[event] || []).filter(h => h !== handler);
      }};
    }},

    emit: function(topic, data) {{
      _fire(topic, data);
    }},

    _drainPending: function(reason) {{}},
  }};
}})();
"""


# ---------------------------------------------------------------------------
# Local HTTP server for the dist/ directory
# ---------------------------------------------------------------------------

class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with logging suppressed."""

    def log_message(self, fmt, *args):  # noqa: N802
        pass


def _start_file_server(directory: Path) -> tuple[int, threading.Thread]:
    """Start a local HTTP server serving `directory` on a random free port.

    Returns (port, thread).  The thread is a daemon so it exits with the
    process automatically.
    """
    handler = lambda *args, **kwargs: _SilentHandler(  # noqa: E731
        *args, directory=str(directory), **kwargs
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return port, t


# ---------------------------------------------------------------------------
# Screenshot driver
# ---------------------------------------------------------------------------

async def _capture_all(out_dir: Path) -> dict[str, Path]:
    """Capture all six screenshots using Playwright Chromium headless.

    Returns a mapping of screenshot-name → written path.
    """
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)

    # Serve the built dist/ over HTTP so the Vue router and asset URLs work.
    port, _server_thread = _start_file_server(_DIST_DIR)
    base_url = f"http://127.0.0.1:{port}"
    print(f"Serving dist/ at {base_url}", file=sys.stderr)

    results: dict[str, Path] = {}

    async with async_playwright() as pw:
        # Prefer the chromium already installed alongside playwright.  On some
        # environments pw.chromium.executable_path points to a headless-shell
        # binary that requires a separate `playwright install` step.  Fall back
        # to the full chromium-1134 build (installed by picolet-cli / PH17) which
        # works identically for headless screenshot capture.
        _candidate_paths = [
            pw.chromium.executable_path,
            # Known fallback: chromium-1134 installed alongside picolet-cli deps.
            str(Path.home() / ".cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
        ]
        _exec = next(
            (p for p in _candidate_paths if Path(p).is_file()),
            None,
        )
        launch_kwargs: dict = {"headless": True}
        if _exec:
            launch_kwargs["executable_path"] = _exec
        browser = await pw.chromium.launch(**launch_kwargs)

        async def _new_page(mock_js: str) -> "Page":
            ctx = await browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            )
            await ctx.add_init_script(mock_js)
            page = await ctx.new_page()
            # Disable CSS transitions so progress bars etc. are in their final
            # computed state at screenshot time (NFR-EX-5 / .no-animation intent).
            await ctx.add_init_script(
                "(function(){var s=document.createElement('style');"
                "s.textContent='*,*::before,*::after{"
                "animation-duration:0ms!important;"
                "transition-duration:0ms!important}';"
                "document.head && document.head.appendChild(s);})()"
            )
            return page, ctx

        # ---- 1. device-list-empty ----------------------------------------
        print("[1/6] device-list-empty", file=sys.stderr)
        mock = _build_mock_picolet_js(devices=[])
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".device-list", timeout=8000)
        await asyncio.sleep(0.8)   # allow the 500 ms poll to fire and render
        path = out_dir / "device-list-empty.png"
        await page.screenshot(path=str(path), full_page=False)
        results["device-list-empty"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 2. device-list-populated ------------------------------------
        print("[2/6] device-list-populated", file=sys.stderr)
        mock = _build_mock_picolet_js(devices=_MOCK_DEVICES)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".device-list", timeout=8000)
        await asyncio.sleep(0.8)
        path = out_dir / "device-list-populated.png"
        await page.screenshot(path=str(path), full_page=False)
        results["device-list-populated"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 3. flash-start (DFU file loaded, ready to press FLASH) ------
        print("[3/6] flash-start", file=sys.stderr)
        mock = _build_mock_picolet_js(devices=_MOCK_DEVICES)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/flash")
        await page.wait_for_selector(".flash-view", timeout=8000)
        # Fill in a DFU path and simulate read_dfu so elements appear.
        await page.fill(".path-input", "/firmware/stm32_app.dfu")
        await page.click("button.btn-read-dfu")
        # Wait for the elements table to appear (read_dfu resolved).
        await page.wait_for_selector(".dfu-elements-table", timeout=5000)
        await asyncio.sleep(0.3)
        path = out_dir / "flash-start.png"
        await page.screenshot(path=str(path), full_page=False)
        results["flash-start"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 4. flash-mid-progress (~55%) --------------------------------
        print("[4/6] flash-mid-progress", file=sys.stderr)
        # Emit progress events: 20%, 40%, 55%, 60% — then pause so we
        # screenshot at ~55% (the 55% event fires, then we take the shot
        # before 60% fires).
        total = 262144
        flash_events = [
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": int(total * 0.20),
                      "total": total, "pct": 20}},
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": int(total * 0.40),
                      "total": total, "pct": 40}},
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": int(total * 0.55),
                      "total": total, "pct": 55}},
        ]
        mock = _build_mock_picolet_js(devices=_MOCK_DEVICES, flash_events=flash_events)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/flash")
        await page.wait_for_selector(".flash-view", timeout=8000)
        await page.fill(".path-input", "/firmware/stm32_app.dfu")
        await page.click("button.btn-read-dfu")
        await page.wait_for_selector(".dfu-elements-table", timeout=5000)
        # Select first device (click it in the device list on home — but
        # flash view needs a selected device.  Inject it via JS directly.)
        await page.evaluate(
            """() => {
              // Expose selectedDevice to the app by dispatching a custom event
              // that HomeView handles via setSelectedDevice. For FlashView we
              // inject via the provide/inject mechanism by accessing the
              // __vue_app__ internals — simplest approach: pre-set localStorage
              // and reload so App.vue picks up the device from its own state.
              // Alternatively we set the ref directly.
              // This route: call startFlash directly since the button just
              // calls window.picolet.invoke("flash", ...) anyway.
              // We'll click the FLASH button instead after selecting via JS.
            }"""
        )
        # Select device via JS: set the selectedDevice ref via the global app.
        await page.evaluate("""() => {
          // Walk the Vue component tree to find the App component instance
          // and call setSelectedDevice — fragile but sufficient for screenshot.
          try {
            const app = document.querySelector('#app').__vue_app__;
            const instance = app._context.app._instance;
            // The provide is on the root component; traverse to find it.
            // Simplest: call invoke flash directly with a hard-coded device_id.
            // startFlash in FlashView calls invoke("flash", {device_id, dfu_path}).
            // We'll just override invoke to capture the call and trigger events.
          } catch(e) {}
        }""")
        # The cleanest approach: pre-set a selected device by navigating to / first,
        # clicking a device, then navigating to /flash.
        # Reload and do the full flow:
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".device-list", timeout=8000)
        await asyncio.sleep(0.7)
        # Click the first device row to select it.
        await page.click(".device-row")
        await asyncio.sleep(0.2)
        # Navigate to flash view.
        await page.click("a.nav-link[href='#/flash'], a[href*='flash']")
        await asyncio.sleep(0.3)
        await page.wait_for_selector(".flash-view", timeout=5000)
        await page.fill(".path-input", "/firmware/stm32_app.dfu")
        await page.click("button.btn-read-dfu")
        await page.wait_for_selector(".dfu-elements-table", timeout=5000)
        await page.click("button.btn-flash")
        # Wait until the progress bar shows up (flash started).
        await page.wait_for_selector(".progress-section", timeout=5000)
        # The flash_events fire every 80 ms; wait 350 ms to land at 55%.
        await asyncio.sleep(0.35)
        path = out_dir / "flash-mid-progress.png"
        await page.screenshot(path=str(path), full_page=False)
        results["flash-mid-progress"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 5. flash-complete -------------------------------------------
        print("[5/6] flash-complete", file=sys.stderr)
        total = 262144
        flash_events_complete = [
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": int(total * 0.50),
                      "total": total, "pct": 50}},
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": total,
                      "total": total, "pct": 100}},
            {"event": "dfu:done", "data": {"ok": True}},
        ]
        mock = _build_mock_picolet_js(devices=_MOCK_DEVICES,
                                    flash_events=flash_events_complete)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".device-list", timeout=8000)
        await asyncio.sleep(0.7)
        await page.click(".device-row")
        await asyncio.sleep(0.2)
        await page.click("a.nav-link[href='#/flash'], a[href*='flash']")
        await asyncio.sleep(0.3)
        await page.wait_for_selector(".flash-view", timeout=5000)
        await page.fill(".path-input", "/firmware/stm32_app.dfu")
        await page.click("button.btn-read-dfu")
        await page.wait_for_selector(".dfu-elements-table", timeout=5000)
        await page.click("button.btn-flash")
        await page.wait_for_selector(".flash-status-done", timeout=5000)
        await asyncio.sleep(0.2)
        path = out_dir / "flash-complete.png"
        await page.screenshot(path=str(path), full_page=False)
        results["flash-complete"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 6. flash-error ----------------------------------------------
        print("[6/6] flash-error", file=sys.stderr)
        flash_events_error = [
            {"event": "dfu:progress",
             "data": {"addr": 0x08000000, "done": int(total * 0.30),
                      "total": total, "pct": 30}},
            {"event": "dfu:error",
             "data": {"message": "USB transfer failed: LIBUSB_ERROR_PIPE (mock)"}},
        ]
        mock = _build_mock_picolet_js(devices=_MOCK_DEVICES,
                                    flash_events=flash_events_error)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".device-list", timeout=8000)
        await asyncio.sleep(0.7)
        await page.click(".device-row")
        await asyncio.sleep(0.2)
        await page.click("a.nav-link[href='#/flash'], a[href*='flash']")
        await asyncio.sleep(0.3)
        await page.wait_for_selector(".flash-view", timeout=5000)
        await page.fill(".path-input", "/firmware/stm32_app.dfu")
        await page.click("button.btn-read-dfu")
        await page.wait_for_selector(".dfu-elements-table", timeout=5000)
        await page.click("button.btn-flash")
        await page.wait_for_selector(".flash-status-error", timeout=5000)
        await asyncio.sleep(0.2)
        path = out_dir / "flash-error.png"
        await page.screenshot(path=str(path), full_page=False)
        results["flash-error"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Pixel validation
# ---------------------------------------------------------------------------

def _check_screenshot(path: Path) -> None:
    """Assert basic quality requirements for a screenshot PNG.

    Checks:
    - File exists and has PNG magic bytes.
    - Dimensions ≥ 800×600.
    - At least one pixel is forge-orange (#ff6b1a ±40).
    - At least one pixel is near-black (all channels < 50).
    """
    from PIL import Image

    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path.name}: not a valid PNG"

    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 800 and h >= 600, (
        f"{path.name}: dimensions {w}×{h} are below 800×600"
    )

    pixels = list(img.getdata())  # type: ignore[deprecated]  # Pillow ≥14 prefers get_flattened_data; keep compat with ≥10

    # forge-orange: #ff6b1a = (255, 107, 26)
    tol = 40
    target_r, target_g, target_b = 255, 107, 26
    has_forge = any(
        abs(r - target_r) <= tol and abs(g - target_g) <= tol and abs(b - target_b) <= tol
        for r, g, b in pixels
    )
    assert has_forge, (
        f"{path.name}: no forge-orange pixels found (expected ~#ff6b1a)"
    )

    has_dark = any(r < 50 and g < 50 and b < 50 for r, g, b in pixels)
    assert has_dark, (
        f"{path.name}: no near-black pixels found (expected chassis background)"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help="directory to write PNG files (default: examples/pydfu/screenshots/)",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="skip pixel-level verification after capture",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    out_dir: Path = args.out_dir.resolve()

    if not _DIST_DIR.is_dir():
        print(
            f"ERROR: dist/ not found at {_DIST_DIR}\n"
            "Run 'npm run build' (or 'picolet build --no-sbom') in examples/pydfu/ first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Output directory: {out_dir}", file=sys.stderr)
    results = asyncio.run(_capture_all(out_dir))

    if not args.no_verify:
        print("\nVerifying screenshots…", file=sys.stderr)
        for name, path in sorted(results.items()):
            _check_screenshot(path)
            from PIL import Image
            img = Image.open(path)
            size_kb = path.stat().st_size // 1024
            print(f"  OK  {path.name}  {img.size[0]}×{img.size[1]}  {size_kb} KB",
                  file=sys.stderr)

    print(f"\nAll {len(results)} screenshots written to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
