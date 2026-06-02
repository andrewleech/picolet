"""
picolet.testing._webkit — WebKit Inspector Protocol thin client + Page-shaped duck.

WebKitGTK's Web Inspector speaks a JSON-RPC protocol over WebSocket at
WEBKIT_INSPECTOR_SERVER=127.0.0.1:<port>.  This is NOT Chrome DevTools Protocol
(CDP) — Playwright's WebKit driver cannot connect_over_cdp to it (F3).

This module implements a minimal Page-like adapter that wraps the WebKit
Inspector Protocol WebSocket connection and exposes the subset of the
Playwright Page API that Picolet example tests exercise:

    goto(url)                 → None
    wait_for_selector(sel)    → element handle stub
    screenshot(path, **kw)   → None  (saves PNG)
    evaluate(expr)            → result (JSON)
    click(selector)           → None
    type(selector, text)      → None
    fill(selector, text)      → None
    close()                   → None

Any method not listed above raises NotImplementedError pointing here.

Wire format reference:
    WebKit Inspector Protocol JSON files at WebKitGTK source:
    Source/JavaScriptCore/inspector/protocol/*.json

Endpoint discovery:
    GET http://127.0.0.1:<port>/json  → JSON array of target descriptors
    Each descriptor has a "webSocketDebuggerUrl" (ws://... or http://...)

    On older WebKitGTK builds the page listing may be at GET /  (HTML).
    We fall back to constructing the WS URL as ws://127.0.0.1:<port>/devtools/page/1
    if /json doesn't return valid JSON.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import urllib.request
from typing import Any


class _InspectorClient:
    """WebSocket connection to the WebKit Inspector server on one target."""

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: list[dict] = []
        self._recv_task: asyncio.Task | None = None

    async def connect(self, timeout: float = 10.0) -> None:
        import websockets  # type: ignore[import]

        deadline = asyncio.get_event_loop().time() + timeout
        last_exc: Exception | None = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                self._ws = await websockets.connect(
                    self._ws_url,
                    open_timeout=2.0,
                    close_timeout=2.0,
                )
                break
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.25)

        if self._ws is None:
            raise RuntimeError(
                "picolet.testing._webkit: cannot connect to {}: {}".format(
                    self._ws_url, last_exc
                )
            )
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        fut.set_result(msg)
                else:
                    self._events.append(msg)
        except Exception:
            pass
        finally:
            # Resolve any pending futures with a cancelled error.
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()

    async def call(self, method: str, params: dict | None = None,
                   timeout: float = 10.0) -> dict:
        """Send a JSON-RPC request and await the response."""
        self._seq += 1
        msg_id = self._seq
        msg = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        await self._ws.send(json.dumps(msg))

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise RuntimeError(
                "picolet.testing._webkit: timeout waiting for reply to {}".format(method)
            )

        if "error" in result:
            raise RuntimeError(
                "picolet.testing._webkit: protocol error in {}: {}".format(
                    method, result["error"]
                )
            )
        return result.get("result", {})

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


async def _discover_ws_url(port: int, timeout: float = 10.0) -> str:
    """Discover the WebSocket debugger URL for the first inspectable target.

    Tries GET /json first; falls back to a constructed ws:// URL.
    """
    url = "http://127.0.0.1:{}/json".format(port)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                data = json.loads(resp.read())
            if isinstance(data, list) and data:
                ws_url = data[0].get("webSocketDebuggerUrl")
                if ws_url:
                    return ws_url
        except Exception:
            pass
        await asyncio.sleep(0.25)

    # Fallback: construct the URL.
    return "ws://127.0.0.1:{}/devtools/page/1".format(port)


class WebKitPage:
    """Playwright-Page-shaped duck for the WebKit Inspector Protocol (FR-TEST-3).

    Exposes the subset of the Playwright async Page API needed by Picolet
    example tests.  Unimplemented methods raise NotImplementedError with a
    pointer to this file.

    This is NOT a Playwright Page object — it is a protocol-compatible
    duck type for the WebKit-specific path (D3).
    """

    def __init__(self, client: _InspectorClient):
        self._client = client

    def __getattr__(self, name: str):
        raise NotImplementedError(
            "AppHarness webkit Page: '{}' not yet proxied.  "
            "Extend picolet/testing/_webkit.py:WebKitPage to add it.".format(name)
        )

    async def goto(self, url: str, **kwargs) -> None:
        """Navigate the page to url."""
        await self._client.call("Page.navigate", {"url": url})

    async def wait_for_selector(self, selector: str, timeout: float = 5000.0,
                                **kwargs) -> Any:
        """Poll until the selector matches an element in the DOM.

        Returns a stub (not a full ElementHandle) since the WebKit Inspector
        Protocol does not expose ElementHandle semantics directly.

        Args:
            selector: CSS selector string.
            timeout: milliseconds to wait (Playwright convention).
        """
        deadline = asyncio.get_event_loop().time() + timeout / 1000.0
        while asyncio.get_event_loop().time() < deadline:
            result = await self._client.call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.querySelector({!r}) !== null".format(selector)
                    ),
                    "returnByValue": True,
                },
            )
            value = result.get("result", {}).get("value")
            if value:
                return True
            await asyncio.sleep(0.1)
        raise RuntimeError(
            "picolet.testing._webkit: wait_for_selector('{}') timed out".format(selector)
        )

    async def screenshot(self, path: str | None = None, **kwargs) -> bytes:
        """Capture a PNG screenshot of the current page.

        Saves to ``path`` if given.  Returns the PNG bytes regardless.

        The WebKit Inspector Protocol's Page.captureScreenshot returns
        base64-encoded PNG data.
        """
        result = await self._client.call(
            "Page.captureScreenshot", {"format": "png"}
        )
        b64 = result.get("data", "")
        png_bytes = base64.b64decode(b64)
        if path:
            with open(path, "wb") as fh:
                fh.write(png_bytes)
        return png_bytes

    async def evaluate(self, expression: str, **kwargs) -> Any:
        """Evaluate a JS expression and return its JSON-serialisable result."""
        result = await self._client.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        return result.get("result", {}).get("value")

    async def click(self, selector: str, **kwargs) -> None:
        """Click the first element matching selector.

        Uses Runtime.evaluate to invoke .click() on the element — the
        WebKit Inspector Protocol has no dedicated 'click' domain method
        that works reliably cross-version.
        """
        script = (
            "(function(){{ var el = document.querySelector({!r}); "
            "if(!el) throw new Error('not found: {!r}'); el.click(); }})()".format(
                selector, selector
            )
        )
        await self._client.call(
            "Runtime.evaluate", {"expression": script, "returnByValue": False}
        )

    async def type(self, selector: str, text: str, **kwargs) -> None:
        """Type text into the element matching selector."""
        await self.click(selector)
        for ch in text:
            await self._client.call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.querySelector({!r}).value += {!r}".format(
                            selector, ch
                        )
                    ),
                    "returnByValue": False,
                },
            )
            await asyncio.sleep(0.01)

    async def fill(self, selector: str, text: str, **kwargs) -> None:
        """Fill the element matching selector with text (clears first)."""
        script = (
            "(function(){{ var el = document.querySelector({!r}); "
            "if(!el) throw new Error('not found: {!r}'); "
            "el.value = {!r}; el.dispatchEvent(new Event('input',{{bubbles:true}})); "
            "}})()".format(selector, selector, text)
        )
        await self._client.call(
            "Runtime.evaluate", {"expression": script, "returnByValue": False}
        )

    async def close(self) -> None:
        """Close the WebSocket connection."""
        await self._client.close()


async def attach_webkit(port: int, timeout: float = 10.0) -> WebKitPage:
    """Connect to the WebKit Inspector server on port and return a WebKitPage.

    Args:
        port: The inspector port announced by the runtime on stderr.
        timeout: Seconds to retry before raising.

    Returns:
        A WebKitPage (duck-typed Playwright Page subset) for the running app.
    """
    ws_url = await _discover_ws_url(port, timeout=timeout)
    client = _InspectorClient(ws_url)
    await client.connect(timeout=timeout)
    return WebKitPage(client)
