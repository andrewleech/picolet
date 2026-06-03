"""
picolet.testing — host-side test infrastructure for Picolet apps.

Public API:
    AppHarness   — spawn, attach, drive, assert, terminate (webview / LVGL).
    TuiHarness   — Phase-7 pty-attached driver for picolet-tui binaries.

Usage:
    from picolet.testing import AppHarness, TuiHarness

    async with AppHarness("path/to/picolet-runtime-linux-x64-webview") as h:
        text = await h.page.evaluate("document.title")
        await h.screenshot("/tmp/shot.png")

    async with TuiHarness("path/to/picolet-tui-app") as h:
        await h.wait_idle()
        await h.send("hello")
        await h.press("enter")
        assert h.cells_at(0, 0, 5) == "hello"
"""
from picolet.testing._harness import AppHarness
from picolet.testing._tui import HarnessError, TuiHarness

__all__ = ["AppHarness", "TuiHarness", "HarnessError"]
