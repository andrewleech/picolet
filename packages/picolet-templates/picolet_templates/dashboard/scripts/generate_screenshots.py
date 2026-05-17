#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright>=1.40",
#   "pillow>=10.0",
# ]
# ///
"""
generate_screenshots.py — capture four dashboard UI screenshots via Playwright.

Drives the Vue frontend (dist/) in headless Chromium with a mock window.picolet
backend. No picolet binary or Xvfb required.

Screenshots produced (in examples/dashboard/screenshots/ by default):
    full-dashboard.png              Normal state, all metrics in healthy range.
    full-dashboard-with-warning.png CPU at 92%, memory at 88% (amber).
    cpu-pinned-state.png            CPU at 99% all cores (alarm red).
    network-active-state.png        High rx/tx values (saturated chart lines).

Each PNG is verified: >= 1000x700, valid PNG, slate background present.
warning/pinned screenshots assert amber/red pixels. Normal/network assert no alarm red.

Usage:
    uv run examples/dashboard/scripts/generate_screenshots.py
    uv run examples/dashboard/scripts/generate_screenshots.py --out-dir /custom/path

NFR-EX-5: deterministic screenshots — fixture data generated with random.seed(42)
re-seeded per scenario call.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import math
import random
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_DASH_DIR = _SCRIPT_DIR.parent
_DIST_DIR = _DASH_DIR / "dist"
_DEFAULT_OUT_DIR = _DASH_DIR / "screenshots"

VIEWPORT_W = 1400
VIEWPORT_H = 900

# ---------------------------------------------------------------------------
# Deterministic fixture data (NFR-EX-5)
# ---------------------------------------------------------------------------

def _make_history(scenario: str) -> list[dict]:
    """Generate a deterministic 60-sample history for a given scenario.

    All scenarios use random.seed(42) for reproducibility (NFR-EX-5).
    The data shape matches the Python metrics_reader.collect() payload exactly.

    Scenarios:
      "normal"         — CPU 10–40%, mem ~45%, moderate net/disk.
      "warning"        — CPU ~92%, mem ~88% (above amber thresholds).
      "cpu-pinned"     — CPU ~99% all cores (alarm red).
      "network-active" — high rx/tx (MB/s range).
    """
    random.seed(42)
    hostname = "LAP-AU-PF65PM2K"
    n_cores = 8
    ticks = []

    for i in range(60):
        ts = time.time() - (60 - i)

        if scenario == "normal":
            cpu = random.uniform(10.0, 40.0)
            mem_pct = random.uniform(42.0, 50.0)
            cores = [random.uniform(5.0, 55.0) for _ in range(n_cores)]
            net_rx = random.uniform(100_000, 500_000)
            net_tx = random.uniform(50_000, 200_000)
            disk_r = random.uniform(0, 200_000)
            disk_w = random.uniform(0, 100_000)

        elif scenario == "warning":
            # CPU spike toward the end to make the warning state visible.
            progress = i / 60.0
            cpu = 70.0 + 25.0 * min(1.0, progress * 1.5)
            cpu += random.uniform(-3.0, 3.0)
            mem_pct = 82.0 + 8.0 * min(1.0, progress * 1.5)
            mem_pct += random.uniform(-1.0, 1.0)
            cores = [cpu + random.uniform(-5.0, 5.0) for _ in range(n_cores)]
            net_rx = random.uniform(100_000, 500_000)
            net_tx = random.uniform(50_000, 200_000)
            disk_r = random.uniform(0, 200_000)
            disk_w = random.uniform(0, 100_000)

        elif scenario == "cpu-pinned":
            cpu = random.uniform(97.0, 100.0)
            mem_pct = random.uniform(60.0, 70.0)
            cores = [random.uniform(95.0, 100.0) for _ in range(n_cores)]
            net_rx = random.uniform(100_000, 300_000)
            net_tx = random.uniform(50_000, 150_000)
            disk_r = random.uniform(0, 500_000)
            disk_w = random.uniform(0, 300_000)

        elif scenario == "network-active":
            cpu = random.uniform(15.0, 35.0)
            mem_pct = random.uniform(40.0, 55.0)
            cores = [random.uniform(5.0, 45.0) for _ in range(n_cores)]
            # High network — MB/s range
            net_rx = random.uniform(5_000_000, 15_000_000)
            net_tx = random.uniform(2_000_000, 8_000_000)
            disk_r = random.uniform(0, 200_000)
            disk_w = random.uniform(0, 100_000)

        else:
            raise ValueError(f"unknown scenario: {scenario!r}")

        cpu = round(max(0.0, min(100.0, cpu)), 1)
        mem_pct = round(max(0.0, min(100.0, mem_pct)), 1)
        cores = [round(max(0.0, min(100.0, c)), 1) for c in cores]
        mem_used_mb = round(16384.0 * mem_pct / 100.0, 1)

        tick = {
            "ts": ts,
            "cpu": cpu,
            "cores": cores,
            "mem_pct": mem_pct,
            "mem_used_mb": mem_used_mb,
            "mem_total_mb": 16384.0,
            "load": [
                round(random.uniform(0.5, 4.0), 2),
                round(random.uniform(0.5, 3.5), 2),
                round(random.uniform(0.5, 3.0), 2),
            ],
            "net_rx_bps": round(net_rx, 1),
            "net_tx_bps": round(net_tx, 1),
            "disk_read_bps": round(disk_r, 1),
            "disk_write_bps": round(disk_w, 1),
            "proc_count": random.randint(350, 420),
            "top_procs": [
                {"pid": random.randint(1000, 9999), "name": name, "cpu_pct": round(random.uniform(0.1, cpu * 0.4), 1)}
                for name in ["python3", "code", "chromium", "nvim", "node"]
            ],
            "hostname": hostname,
            "uptime_s": round(3600.0 * 24 * 3 + i * 1.0, 1),
        }
        ticks.append(tick)

    return ticks


def _build_mock_js(tick_history: list[dict]) -> str:
    """Return JS that installs window.picolet mock with pre-built fixture data.

    The mock:
      - Returns tick_history for invoke('get_history', null).
      - Exposes window.picolet.emit() so the screenshot script can push
        additional ticks synchronously after mount.

    NFR-EX-5: all data is deterministic (seeded from _make_history()).
    """
    history_json = json.dumps(tick_history)

    return f"""
(function() {{
  const _history = {history_json};
  const _handlers = {{}};

  window.picolet = {{
    __ready__: true,

    invoke: async function(cmd, args) {{
      if (cmd === 'get_history') return {{ history: _history }};
      throw new Error('unknown command: ' + cmd);
    }},

    on: function(event, handler) {{
      if (!_handlers[event]) _handlers[event] = [];
      _handlers[event].push(handler);
      return function() {{
        _handlers[event] = (_handlers[event] || []).filter(h => h !== handler);
      }};
    }},

    emit: function(t, d) {{
      (_handlers[t] || []).forEach(h => {{ try {{ h(d); }} catch(e) {{}} }});
    }},

    _drainPending: function() {{}},
  }};
}})();
"""


_DISABLE_ANIMATIONS_JS = (
    "(function(){"
    "var s=document.createElement('style');"
    "s.textContent='*,*::before,*::after{"
    "animation-duration:0ms!important;"
    "transition-duration:0ms!important}';"
    "document.head && document.head.appendChild(s);"
    "window.__PICOLET_SCREENSHOT_MODE__=true;"
    "})()"
)

# ---------------------------------------------------------------------------
# Local HTTP server
# ---------------------------------------------------------------------------


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802
        pass


def _start_file_server(directory: Path) -> tuple[int, threading.Thread]:
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
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)

    port, _server_thread = _start_file_server(_DIST_DIR)
    base_url = f"http://127.0.0.1:{port}"
    print(f"Serving dist/ at {base_url}", file=sys.stderr)

    results: dict[str, Path] = {}

    async with async_playwright() as pw:
        _candidate_paths = [
            str(Path.home() / ".cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
            str(Path.home() / ".cache/ms-playwright/chromium-1148/chrome-linux/chrome"),
        ]
        try:
            _candidate_paths.insert(0, pw.chromium.executable_path)
        except Exception:
            pass
        _exec = next(
            (p for p in _candidate_paths if Path(p).is_file()),
            None,
        )
        launch_kwargs: dict = {"headless": True}
        if _exec:
            launch_kwargs["executable_path"] = _exec

        browser = await pw.chromium.launch(**launch_kwargs)

        async def _new_page(mock_js: str):
            ctx = await browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            )
            await ctx.add_init_script(mock_js)
            await ctx.add_init_script(_DISABLE_ANIMATIONS_JS)
            page = await ctx.new_page()
            return page, ctx

        async def _capture_scenario(
            label: str,
            num: int,
            total: int,
            scenario: str,
            filename: str,
        ) -> Path:
            print(f"[{num}/{total}] {label}", file=sys.stderr)
            history = _make_history(scenario)
            mock = _build_mock_js(history)
            page, ctx = await _new_page(mock)
            await page.goto(f"{base_url}/#/")
            # Wait for the dashboard grid to appear.
            await page.wait_for_selector(".dashboard-grid", timeout=10000)
            # Wait for fonts to render (F16 in PH22 plan).
            await page.evaluate("document.fonts.ready")
            await asyncio.sleep(0.3)
            path = out_dir / filename
            await page.screenshot(path=str(path), full_page=False)
            await ctx.close()
            print(f"  -> {path}", file=sys.stderr)
            return path

        # ---- 1. full-dashboard (normal state) -------------------------------
        results["full-dashboard"] = await _capture_scenario(
            "full-dashboard (normal)", 1, 4, "normal", "full-dashboard.png"
        )

        # ---- 2. full-dashboard-with-warning ---------------------------------
        results["full-dashboard-with-warning"] = await _capture_scenario(
            "full-dashboard-with-warning (CPU 92%, mem 88%)", 2, 4, "warning",
            "full-dashboard-with-warning.png"
        )

        # ---- 3. cpu-pinned-state --------------------------------------------
        results["cpu-pinned-state"] = await _capture_scenario(
            "cpu-pinned-state (CPU 99%)", 3, 4, "cpu-pinned", "cpu-pinned-state.png"
        )

        # ---- 4. network-active-state ----------------------------------------
        results["network-active-state"] = await _capture_scenario(
            "network-active-state (high rx/tx)", 4, 4, "network-active",
            "network-active-state.png"
        )

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Pixel verification
# ---------------------------------------------------------------------------


def _check_screenshot(
    path: Path,
    *,
    expect_amber: bool = False,
    expect_alarm: bool = False,
    assert_no_alarm: bool = False,
) -> None:
    """Assert basic quality requirements for a screenshot PNG."""
    from PIL import Image

    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path.name}: not a valid PNG"

    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 1000 and h >= 700, f"{path.name}: dimensions {w}x{h} below 1000x700"

    pixels = list(img.getdata())  # type: ignore[deprecated]

    # Slate/dark background: #0b0e12 = (11, 14, 18) — nearby dark pixels.
    has_bg = any(r < 30 and g < 35 and b < 45 for r, g, b in pixels)
    assert has_bg, f"{path.name}: no slate-dark background pixels found"

    if expect_amber:
        # Amber: #f59e0b = (245, 158, 11)
        has_amber = any(
            abs(r - 245) <= 30 and abs(g - 158) <= 30 and abs(b - 11) <= 30
            for r, g, b in pixels
        )
        assert has_amber, f"{path.name}: no amber warning pixels (expected CPU/mem warning)"

    if expect_alarm:
        # Alarm red: #ef4444 = (239, 68, 68)
        has_alarm = any(
            abs(r - 239) <= 30 and g < 120 and abs(b - 68) <= 40
            for r, g, b in pixels
        )
        assert has_alarm, f"{path.name}: no alarm-red pixels (expected CPU pinned state)"

    if assert_no_alarm:
        has_alarm = any(
            r > 200 and g < 100 and b < 100
            for r, g, b in pixels
        )
        assert not has_alarm, (
            f"{path.name}: unexpected alarm-red pixels — alarm colour should only appear at > 95%"
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
        help="directory to write PNG files (default: examples/dashboard/screenshots/)",
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
            "Run 'npm run build' (or 'picolet build --no-sbom') in examples/dashboard/ first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Output directory: {out_dir}", file=sys.stderr)
    results = asyncio.run(_capture_all(out_dir))

    if not args.no_verify:
        print("\nVerifying screenshots...", file=sys.stderr)
        _amber_screenshots = {"full-dashboard-with-warning"}
        _alarm_screenshots = {"cpu-pinned-state"}
        _no_alarm_screenshots = {"full-dashboard", "network-active-state"}
        for name, path in sorted(results.items()):
            _check_screenshot(
                path,
                expect_amber=(name in _amber_screenshots),
                expect_alarm=(name in _alarm_screenshots),
                assert_no_alarm=(name in _no_alarm_screenshots),
            )
            from PIL import Image
            img = Image.open(path)
            size_kb = path.stat().st_size // 1024
            print(
                f"  OK  {path.name}  {img.size[0]}x{img.size[1]}  {size_kb} KB",
                file=sys.stderr,
            )

    print(f"\nAll {len(results)} screenshots written to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
