#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright>=1.40",
#   "pillow>=10.0",
# ]
# ///
"""
generate_screenshots.py — capture six notes UI screenshots via Playwright.

Drives the Vue frontend (dist/) in headless Chromium with a mock window.picolet
backend. No picolet binary or Xvfb required; Playwright's headless Chromium
renders the actual CSS/fonts and produces non-blank PNG captures at 1200×800.

Usage:
    uv run examples/notes/scripts/generate_screenshots.py
    uv run examples/notes/scripts/generate_screenshots.py --out-dir /custom/path

Screenshots produced (in <repo>/examples/notes/screenshots/ by default):
    list-empty.png          / route, list_notes returns []
    list-populated.png      / route, list_notes returns 3-note fixture
    edit-pristine.png       /edit/:slug, note loaded, no changes
    edit-unsaved.png        /edit/:slug, body modified, unsaved dot visible
    edit-typing-mid.png     /edit/:slug, mid-typing state
    search-active.png       / route, search query active, filtered results

Each PNG is verified: ≥ 1000×700, valid PNG magic bytes, at least one
warm off-white pixel (~#f7f3ed ±15) and at least one near-ink dark pixel
(< 60 each channel). edit-unsaved and edit-typing-mid additionally assert
at least one mark-red pixel (~#c4392b ±30).
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
_NOTES_DIR = _SCRIPT_DIR.parent
_DIST_DIR = _NOTES_DIR / "dist"
_DEFAULT_OUT_DIR = _NOTES_DIR / "screenshots"

VIEWPORT_W = 1200
VIEWPORT_H = 800

# ---------------------------------------------------------------------------
# Deterministic mock fixture (F8 / NFR-EX-5)
# ---------------------------------------------------------------------------

_MOCK_NOTES = [
    {
        "slug": "meeting-notes-1747000000",
        "title": "Meeting Notes",
        "created": 1747000000,
        "updated": 1747086400,
    },
    {
        "slug": "weekend-reading-1746913600",
        "title": "Weekend Reading",
        "created": 1746913600,
        "updated": 1746999000,
    },
    {
        "slug": "project-ideas-1746827200",
        "title": "Project Ideas",
        "created": 1746827200,
        "updated": 1746827200,
    },
]

_MOCK_BODY = """# Meeting Notes

Discussion points from the Tuesday sync.

## Decisions

- Proceed with the new storage layout.
- Review scheduled for end of week.

## Action items

1. Draft the updated spec section.
2. Run the benchmark suite.
3. Send the summary to the team.
"""

_MOCK_BODY_TYPING = """# Meeting Notes

Discussion points from the Tuesday sync.

## Decisions

- Proceed with the new storage layout.
- Review scheduled for end of week.
- Add unit tests for the parser module.

## Action items

1. Draft the updated spec section.
2. Run the benchmark suite.
"""


def _build_mock_js(*, notes: list | None = None, body: str | None = None) -> str:
    """Return JS that installs window.picolet before Vue boots."""
    if notes is None:
        notes = []
    if body is None:
        body = _MOCK_BODY

    notes_json = json.dumps(notes)
    body_json = json.dumps(body)
    first_note = json.dumps(notes[0]) if notes else json.dumps({
        "slug": "meeting-notes-1747000000",
        "title": "Meeting Notes",
        "created": 1747000000,
        "updated": 1747086400,
    })

    return f"""
(function() {{
  const _notes = {notes_json};
  const _body = {body_json};
  const _firstNote = {first_note};
  const _handlers = {{}};

  window.picolet = {{
    __ready__: true,

    invoke: async function(cmd, args) {{
      if (cmd === 'list_notes') return _notes;
      if (cmd === 'load_note') return {{
        slug: (args && args.slug) || _firstNote.slug,
        title: _firstNote.title,
        created: _firstNote.created,
        updated: _firstNote.updated,
        body: _body,
      }};
      if (cmd === 'save_note') return {{
        ok: true,
        slug: args && args.slug,
        title: _firstNote.title,
        created: _firstNote.created,
        updated: (Date.now() / 1000 | 0),
      }};
      if (cmd === 'rename_note') return {{
        ok: true,
        slug: args && args.slug,
        title: (args && args.title) || _firstNote.title,
        created: _firstNote.created,
        updated: (Date.now() / 1000 | 0),
      }};
      if (cmd === 'create_note') return {{
        slug: 'new-note-1747100000',
        title: (args && args.title) || 'Untitled',
        created: 1747100000,
        updated: 1747100000,
      }};
      if (cmd === 'delete_note') return {{ ok: true }};
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
    "(function(){var s=document.createElement('style');"
    "s.textContent='*,*::before,*::after{"
    "animation-duration:0ms!important;"
    "transition-duration:0ms!important}';"
    "document.head && document.head.appendChild(s);"
    "window.__PICOLET_SCREENSHOT_MODE__=true;})()"
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
            pw.chromium.executable_path,
            str(Path.home() / ".cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
            str(Path.home() / ".cache/ms-playwright/chromium-1148/chrome-linux/chrome"),
        ]
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

        # ---- 1. list-empty --------------------------------------------------
        print("[1/6] list-empty", file=sys.stderr)
        mock = _build_mock_js(notes=[])
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".note-list-empty", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "list-empty.png"
        await page.screenshot(path=str(path), full_page=False)
        results["list-empty"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 2. list-populated ----------------------------------------------
        print("[2/6] list-populated", file=sys.stderr)
        mock = _build_mock_js(notes=_MOCK_NOTES)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".note-item", timeout=8000)
        await asyncio.sleep(0.5)
        path = out_dir / "list-populated.png"
        await page.screenshot(path=str(path), full_page=False)
        results["list-populated"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 3. edit-pristine -----------------------------------------------
        print("[3/6] edit-pristine", file=sys.stderr)
        mock = _build_mock_js(notes=_MOCK_NOTES, body=_MOCK_BODY)
        page, ctx = await _new_page(mock)
        slug = _MOCK_NOTES[0]["slug"]
        await page.goto(f"{base_url}/#/edit/{slug}")
        await page.wait_for_selector(".editor-pane", timeout=8000)
        await asyncio.sleep(0.6)
        path = out_dir / "edit-pristine.png"
        await page.screenshot(path=str(path), full_page=False)
        results["edit-pristine"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 4. edit-unsaved (body modified, dot visible) -------------------
        print("[4/6] edit-unsaved", file=sys.stderr)
        mock = _build_mock_js(notes=_MOCK_NOTES, body=_MOCK_BODY)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/edit/{slug}")
        await page.wait_for_selector(".editor-pane", timeout=8000)
        await asyncio.sleep(0.4)
        # Modify the textarea to trigger unsaved state.
        textarea = page.locator("textarea.note-body")
        await textarea.fill(_MOCK_BODY + "\n\nAdditional notes added.")
        await page.wait_for_selector(".unsaved-dot", timeout=2000)
        await asyncio.sleep(0.3)
        path = out_dir / "edit-unsaved.png"
        await page.screenshot(path=str(path), full_page=False)
        results["edit-unsaved"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 5. edit-typing-mid (mid-typing state) --------------------------
        print("[5/6] edit-typing-mid", file=sys.stderr)
        mock = _build_mock_js(notes=_MOCK_NOTES, body=_MOCK_BODY)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/edit/{slug}")
        await page.wait_for_selector(".editor-pane", timeout=8000)
        await asyncio.sleep(0.4)
        textarea = page.locator("textarea.note-body")
        await textarea.fill(_MOCK_BODY_TYPING)
        await page.wait_for_selector(".unsaved-dot", timeout=2000)
        # Click on textarea to give it focus (cursor visible).
        await textarea.click()
        await asyncio.sleep(0.3)
        path = out_dir / "edit-typing-mid.png"
        await page.screenshot(path=str(path), full_page=False)
        results["edit-typing-mid"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        # ---- 6. search-active -----------------------------------------------
        print("[6/6] search-active", file=sys.stderr)
        mock = _build_mock_js(notes=_MOCK_NOTES)
        page, ctx = await _new_page(mock)
        await page.goto(f"{base_url}/#/")
        await page.wait_for_selector(".note-item", timeout=8000)
        await asyncio.sleep(0.4)
        # Type a search query to filter the list.
        search = page.locator(".search-input")
        await search.fill("meeting")
        await asyncio.sleep(0.3)
        path = out_dir / "search-active.png"
        await page.screenshot(path=str(path), full_page=False)
        results["search-active"] = path
        await ctx.close()
        print(f"  → {path}", file=sys.stderr)

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Pixel verification
# ---------------------------------------------------------------------------


def _check_screenshot(path: Path, *, expect_red: bool = False) -> None:
    """Assert basic quality requirements for a screenshot PNG."""
    from PIL import Image

    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path.name}: not a valid PNG"

    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 1000 and h >= 700, (
        f"{path.name}: dimensions {w}×{h} are below 1000×700"
    )

    pixels = list(img.getdata())  # type: ignore[deprecated]

    # Warm off-white paper: #f7f3ed = (247, 243, 237)
    has_paper = any(
        abs(r - 247) <= 15 and abs(g - 243) <= 15 and abs(b - 237) <= 15
        for r, g, b in pixels
    )
    assert has_paper, f"{path.name}: no paper-colour pixels (~#f7f3ed)"

    # Near-ink dark: #1a1715 = (26, 23, 21) — any very dark pixel counts.
    has_ink = any(r < 60 and g < 60 and b < 60 for r, g, b in pixels)
    assert has_ink, f"{path.name}: no ink-dark pixels (~#1a1715)"

    if expect_red:
        # Mark red: #c4392b = (196, 57, 43)
        has_red = any(
            abs(r - 196) <= 30 and abs(g - 57) <= 30 and abs(b - 43) <= 30
            for r, g, b in pixels
        )
        assert has_red, f"{path.name}: no mark-red pixels (expected unsaved dot ~#c4392b)"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help="directory to write PNG files (default: examples/notes/screenshots/)",
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
            "Run 'npm run build' (or 'picolet build --no-sbom') in examples/notes/ first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Output directory: {out_dir}", file=sys.stderr)
    results = asyncio.run(_capture_all(out_dir))

    if not args.no_verify:
        print("\nVerifying screenshots…", file=sys.stderr)
        _red_screenshots = {"edit-unsaved", "edit-typing-mid"}
        for name, path in sorted(results.items()):
            _check_screenshot(path, expect_red=(name in _red_screenshots))
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
