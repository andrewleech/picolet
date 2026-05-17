#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright>=1.40",
#   "pillow>=10.0",
# ]
# ///
"""
generate_screenshots.py — capture five config-editor UI screenshots via Playwright.

Drives the Vue frontend (dist/) in headless Chromium with a mock window.picolet
backend. No picolet binary or Xvfb required.

Screenshots produced (in examples/config-editor/screenshots/ by default):
    file-picker.png          /  route, path partially typed, suggestions shown
    edit-toml.png            /edit route, TOML file loaded, no errors
    edit-yaml-with-errors.png /edit route, YAML loaded, validation errors (magenta)
    diff-add.png             /diff route, diff showing added lines
    diff-delete.png          /diff route, diff showing deleted lines

Each PNG is verified: ≥ 1000×700, valid PNG, near-black phosphor background,
phosphor-green text. edit-yaml-with-errors additionally asserts magenta pixels.
The other four assert magenta is ABSENT (NFR-EX-AESTHETIC: magenta only on errors).

Usage:
    uv run examples/config-editor/scripts/generate_screenshots.py
    uv run examples/config-editor/scripts/generate_screenshots.py --out-dir /custom/path
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_CE_DIR = _SCRIPT_DIR.parent
_DIST_DIR = _CE_DIR / "dist"
_DEFAULT_OUT_DIR = _CE_DIR / "screenshots"

VIEWPORT_W = 1200
VIEWPORT_H = 800

# ---------------------------------------------------------------------------
# Deterministic mock fixtures (NFR-EX-5)
# ---------------------------------------------------------------------------

_MOCK_TOML_DOC = {
    "server": {"host": "localhost", "port": 8080, "debug": False},
    "database": {"url": "postgresql://localhost/myapp", "pool_size": 5},
}

_MOCK_YAML_DOC = {
    "logging": {"level": "info", "file": "/var/log/myapp.log"},
    "cache": {"backend": "redis", "ttl": 300},
}

_MOCK_ERRORS = [
    {"path": "logging.level", "message": "value 'info' not in enum ['debug', 'warning', 'error']"},
    {"path": "cache.ttl", "message": "expected type integer, got str"},
]

_MOCK_DIFF_ADD = [
    "--- original",
    "+++ new",
    "@@ -1,4 +1,5 @@",
    " [server]",
    ' host = "localhost"',
    "-port = 8080",
    "+port = 9090",
    "+timeout = 30",
    " debug = false",
]

_MOCK_DIFF_DEL = [
    "--- original",
    "+++ new",
    "@@ -1,6 +1,4 @@",
    " [database]",
    '-url = "postgresql://localhost/myapp"',
    "-pool_size = 5",
    "-max_connections = 20",
    '+url = "postgresql://prod-db/myapp"',
]

_MOCK_DIR_ENTRIES = [
    {"name": "etc", "is_dir": True},
    {"name": "home", "is_dir": True},
    {"name": "tmp", "is_dir": True},
    {"name": "config.toml", "is_dir": False},
]


def _build_mock_js(
    *,
    list_dir_result=None,
    list_schemas_result=None,
    load_result=None,
    validate_result=None,
    save_result=None,
    init_state=None,
) -> str:
    """Return JS that installs window.picolet and optionally pre-populates store."""
    if list_dir_result is None:
        list_dir_result = []
    if list_schemas_result is None:
        list_schemas_result = []
    if validate_result is None:
        validate_result = {"ok": True, "errors": []}

    load_json = json.dumps(load_result or {"ok": False, "error": "no file loaded"})
    validate_json = json.dumps(validate_result)
    save_json = json.dumps(save_result or {"ok": True, "diff": []})
    dir_json = json.dumps(list_dir_result)
    schemas_json = json.dumps(list_schemas_result)
    init_json = json.dumps(init_state) if init_state else "null"

    return f"""
(function() {{
  const _loadResult = {load_json};
  const _validateResult = {validate_json};
  const _saveResult = {save_json};
  const _dirEntries = {dir_json};
  const _schemas = {schemas_json};
  const _initState = {init_json};

  if (_initState) {{
    window.__initState = _initState;
  }}

  const _handlers = {{}};

  window.picolet = {{
    __ready__: true,

    invoke: async function(cmd, args) {{
      if (cmd === 'list_dir') return _dirEntries;
      if (cmd === 'list_schemas') return _schemas;
      if (cmd === 'load') return _loadResult;
      if (cmd === 'validate') return _validateResult;
      if (cmd === 'save') return _saveResult;
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
    "(function(){{"
    "var s=document.createElement('style');"
    "s.textContent='*,*::before,*::after{{"
    "animation-duration:0ms!important;"
    "transition-duration:0ms!important}}';"
    "document.head && document.head.appendChild(s);"
    "window.__PICOLET_SCREENSHOT_MODE__=true;"
    "}})()"
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

        # ---- 1. file-picker -------------------------------------------------
        print("[1/5] file-picker", file=sys.stderr)
        mock = _build_mock_js(
            list_dir_result=_MOCK_DIR_ENTRIES,
            list_schemas_result=["myapp", "test"],
        )
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".picker-view", timeout=8000)
        # Type a path to trigger the directory listing display
        await page.fill("input.file-path-input", "/")
        # Trigger input event to show suggestions
        await page.locator("input.file-path-input").dispatch_event("input")
        await asyncio.sleep(0.5)
        path = out_dir / "file-picker.png"
        await page.screenshot(path=str(path), full_page=False)
        results["file-picker"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 2. edit-toml ---------------------------------------------------
        print("[2/5] edit-toml", file=sys.stderr)
        mock = _build_mock_js(
            load_result={"ok": None, "format": "toml", "document": _MOCK_TOML_DOC, "schema_hint": None},
            validate_result={"ok": True, "errors": []},
            save_result={"ok": True, "diff": _MOCK_DIFF_ADD},
            init_state={
                "filePath": "/etc/myapp.toml",
                "format": "toml",
                "document": _MOCK_TOML_DOC,
                "schemaName": "",
                "errors": [],
                "diff": [],
            },
        )
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/edit")
        await page.wait_for_selector(".edit-view", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "edit-toml.png"
        await page.screenshot(path=str(path), full_page=False)
        results["edit-toml"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 3. edit-yaml-with-errors ----------------------------------------
        print("[3/5] edit-yaml-with-errors", file=sys.stderr)
        mock = _build_mock_js(
            load_result={"ok": None, "format": "yaml", "document": _MOCK_YAML_DOC, "schema_hint": "myapp"},
            validate_result={"ok": True, "errors": _MOCK_ERRORS},
            init_state={
                "filePath": "/etc/myapp.yaml",
                "format": "yaml",
                "document": _MOCK_YAML_DOC,
                "schemaName": "myapp",
                "errors": _MOCK_ERRORS,
                "diff": [],
            },
        )
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/edit")
        await page.wait_for_selector(".edit-view", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "edit-yaml-with-errors.png"
        await page.screenshot(path=str(path), full_page=False)
        results["edit-yaml-with-errors"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 4. diff-add ----------------------------------------------------
        print("[4/5] diff-add", file=sys.stderr)
        mock = _build_mock_js(
            init_state={
                "filePath": "/etc/myapp.toml",
                "format": "toml",
                "document": _MOCK_TOML_DOC,
                "schemaName": "",
                "errors": [],
                "diff": _MOCK_DIFF_ADD,
            },
        )
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/diff")
        await page.wait_for_selector(".diff-view", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "diff-add.png"
        await page.screenshot(path=str(path), full_page=False)
        results["diff-add"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 5. diff-delete -------------------------------------------------
        print("[5/5] diff-delete", file=sys.stderr)
        mock = _build_mock_js(
            init_state={
                "filePath": "/etc/myapp.toml",
                "format": "toml",
                "document": _MOCK_TOML_DOC,
                "schemaName": "",
                "errors": [],
                "diff": _MOCK_DIFF_DEL,
            },
        )
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/diff")
        await page.wait_for_selector(".diff-view", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "diff-delete.png"
        await page.screenshot(path=str(path), full_page=False)
        results["diff-delete"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Pixel verification
# ---------------------------------------------------------------------------


def _check_screenshot(
    path: Path,
    *,
    expect_magenta: bool = False,
    assert_no_magenta: bool = False,
) -> None:
    """Assert basic quality requirements for a screenshot PNG."""
    from PIL import Image

    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path.name}: not a valid PNG"

    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 1000 and h >= 700, f"{path.name}: dimensions {w}×{h} below 1000×700"

    pixels = list(img.getdata())  # type: ignore[deprecated]

    # Phosphor background: #0d1b0d = (13, 27, 13)
    has_bg = any(r < 40 and g < 60 and b < 40 for r, g, b in pixels)
    assert has_bg, f"{path.name}: no near-black phosphor background pixels"

    # Phosphor green: #a3ff7c = (163, 255, 124)
    has_green = any(
        abs(r - 163) <= 40 and abs(g - 255) <= 10 and abs(b - 124) <= 40
        for r, g, b in pixels
    )
    assert has_green, f"{path.name}: no phosphor-green pixels"

    if expect_magenta:
        # Magenta: #ff5cd1 = (255, 92, 209)
        has_magenta = any(
            abs(r - 255) <= 20 and abs(g - 92) <= 30 and abs(b - 209) <= 30
            for r, g, b in pixels
        )
        assert has_magenta, f"{path.name}: no magenta pixels (expected validation error)"

    if assert_no_magenta:
        has_magenta = any(
            abs(r - 255) <= 20 and abs(g - 92) <= 30 and abs(b - 209) <= 30
            for r, g, b in pixels
        )
        assert not has_magenta, (
            f"{path.name}: unexpected magenta pixels — magenta must appear ONLY on validation errors"
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
        help="directory to write PNG files (default: examples/config-editor/screenshots/)",
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
            "Run 'npm run build' (or 'picolet build --no-sbom') in examples/config-editor/ first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Output directory: {out_dir}", file=sys.stderr)
    results = asyncio.run(_capture_all(out_dir))

    if not args.no_verify:
        print("\nVerifying screenshots...", file=sys.stderr)
        # edit-yaml-with-errors must have magenta; all others must NOT.
        _magenta_screenshots = {"edit-yaml-with-errors"}
        _no_magenta_screenshots = {"file-picker", "edit-toml", "diff-add", "diff-delete"}
        for name, path in sorted(results.items()):
            _check_screenshot(
                path,
                expect_magenta=(name in _magenta_screenshots),
                assert_no_magenta=(name in _no_magenta_screenshots),
            )
            from PIL import Image
            img = Image.open(path)
            size_kb = path.stat().st_size // 1024
            print(
                f"  OK  {path.name}  {img.size[0]}×{img.size[1]}  {size_kb} KB",
                file=sys.stderr,
            )

    print(f"\nAll {len(results)} screenshots written to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
