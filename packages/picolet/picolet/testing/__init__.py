"""
picolet.testing — host-side test infrastructure for Picolet apps.

Public API:
    AppHarness   — spawn, attach, drive, assert, terminate.

Usage:
    from picolet.testing import AppHarness

    async with AppHarness("path/to/picolet-runtime-linux-x64-webview") as h:
        await h.page.goto("about:blank")  # not needed — page already loaded
        text = await h.page.evaluate("document.title")
        await h.screenshot("/tmp/shot.png")
"""
from picolet.testing._harness import AppHarness

__all__ = ["AppHarness"]
