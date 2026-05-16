"""
picolet.testing._chromium — Playwright connect_over_cdp path.

Connects to an already-running Chromium/WebView2 process that has
--remote-debugging-port=<N> open (announced on stderr as
'picolet:test-port=<N>').  Returns a Playwright Page object — the literal
playwright.async_api.Page (FR-TEST-3, Chromium path).
"""
from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Browser, Page


async def attach_chromium(port: int, timeout: float = 10.0) -> "Page":
    """Connect over CDP to port and return the first Page.

    Retries the connection for up to ``timeout`` seconds to handle the brief
    window between port announcement and the Chromium engine accepting
    connections (D4/F8 race).

    Args:
        port: The CDP remote debugging port announced by the runtime.
        timeout: Seconds to retry before raising.

    Returns:
        A Playwright Page object attached to the running app.

    Raises:
        RuntimeError: if the connection cannot be established within timeout.
    """
    from playwright.async_api import async_playwright

    endpoint = "http://127.0.0.1:{}".format(port)

    playwright = await async_playwright().start()
    deadline = asyncio.get_event_loop().time() + timeout
    last_exc: Exception | None = None

    browser: "Browser | None" = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            browser = await playwright.chromium.connect_over_cdp(endpoint)
            break
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.25)

    if browser is None:
        await playwright.stop()
        raise RuntimeError(
            "picolet.testing: cannot connect to CDP on port {}: {}".format(port, last_exc)
        )

    # Get the first available page.
    contexts = browser.contexts
    if contexts:
        pages = contexts[0].pages
    else:
        pages = []

    page: "Page | None" = pages[0] if pages else None
    if page is None:
        # No page yet — create one (WebView2 may not have a page before navigation).
        ctx = await browser.new_context()
        page = await ctx.new_page()

    # Wrap cleanup so the caller can close via page.close() if desired.
    _orig_close = page.close

    async def _close_all():
        try:
            await _orig_close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass

    page.close = _close_all  # type: ignore[assignment]
    return page
