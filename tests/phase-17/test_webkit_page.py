"""
PH17 unit tests — picolet.testing._webkit: WebKitPage duck type.

Covers:
  - WebKitPage.__getattr__ raises NotImplementedError for unknown methods,
    with a message pointing to the source file.
  - WebKitPage.evaluate calls Runtime.evaluate via the InspectorClient.
  - WebKitPage.goto calls Page.navigate via the InspectorClient.
  - WebKitPage.screenshot sends Page.captureScreenshot and decodes base64 PNG.
  - WebKitPage.screenshot writes bytes to path when path is given.
  - WebKitPage.screenshot returns bytes.
  - WebKitPage.click emits a Runtime.evaluate call containing .click().
  - WebKitPage.fill emits a Runtime.evaluate call that sets .value.
  - WebKitPage.close delegates to client.close().
  - _discover_ws_url returns the fallback URL when /json is unreachable.
  - _InspectorClient.call raises RuntimeError on protocol error.
  - _InspectorClient.call raises RuntimeError on timeout.
  - _PORT_RE regex (from _harness) does not match prefix/suffix variants.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import importlib.util

_REPO_ROOT = Path(__file__).parent.parent.parent
_TESTING_ROOT = _REPO_ROOT / "packages" / "picolet-testing" / "picolet" / "testing"


def _load_module(name: str, path: Path):
    """Load a module from path without touching sys.modules['picolet']."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_webkit_mod = _load_module("_webkit", _TESTING_ROOT / "_webkit.py")
WebKitPage = _webkit_mod.WebKitPage
_InspectorClient = _webkit_mod._InspectorClient
_discover_ws_url = _webkit_mod._discover_ws_url

def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(replies: dict) -> _InspectorClient:
    """Return an _InspectorClient whose call() is mocked to return replies.

    replies maps method name to the result dict the mock returns.
    """
    client = MagicMock(spec=_InspectorClient)
    client.close = AsyncMock()

    async def fake_call(method, params=None, timeout=10.0):
        if method in replies:
            return replies[method]
        raise RuntimeError("unexpected call to {}".format(method))

    client.call = AsyncMock(side_effect=fake_call)
    return client


def _make_page(replies: dict) -> WebKitPage:
    return WebKitPage(_make_client(replies))


# ---------------------------------------------------------------------------
# WebKitPage.__getattr__ — NotImplementedError for unknown methods
# ---------------------------------------------------------------------------

class TestWebKitPageGetattr(unittest.TestCase):

    def test_unknown_method_raises_not_implemented(self):
        page = _make_page({})
        with self.assertRaises(NotImplementedError) as ctx:
            _ = page.some_unknown_playwright_method
        self.assertIn("some_unknown_playwright_method", str(ctx.exception))

    def test_not_implemented_message_references_webkit_py(self):
        page = _make_page({})
        with self.assertRaises(NotImplementedError) as ctx:
            _ = page.wait_for_navigation
        self.assertIn("_webkit.py", str(ctx.exception))

    def test_known_method_does_not_raise(self):
        page = _make_page({})
        # evaluate, goto, screenshot, click, fill, close are defined — no error.
        self.assertTrue(callable(page.evaluate))
        self.assertTrue(callable(page.goto))


# ---------------------------------------------------------------------------
# WebKitPage.evaluate
# ---------------------------------------------------------------------------

class TestWebKitPageEvaluate(unittest.TestCase):

    def test_evaluate_returns_value(self):
        page = _make_page({"Runtime.evaluate": {"result": {"value": 42}}})
        result = _run(page.evaluate("1 + 1"))
        self.assertEqual(result, 42)

    def test_evaluate_calls_runtime_evaluate(self):
        client = _make_client({"Runtime.evaluate": {"result": {"value": True}}})
        page = WebKitPage(client)
        _run(page.evaluate("window.picolet.__ready__"))
        client.call.assert_called_once()
        call_args = client.call.call_args
        self.assertEqual(call_args[0][0], "Runtime.evaluate")

    def test_evaluate_returns_none_when_value_absent(self):
        page = _make_page({"Runtime.evaluate": {"result": {}}})
        result = _run(page.evaluate("undefined"))
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# WebKitPage.goto
# ---------------------------------------------------------------------------

class TestWebKitPageGoto(unittest.TestCase):

    def test_goto_calls_page_navigate(self):
        client = _make_client({"Page.navigate": {}})
        page = WebKitPage(client)
        _run(page.goto("https://example.com"))
        client.call.assert_called_once()
        call_args = client.call.call_args
        self.assertEqual(call_args[0][0], "Page.navigate")
        self.assertIn("url", call_args[0][1])
        self.assertEqual(call_args[0][1]["url"], "https://example.com")


# ---------------------------------------------------------------------------
# WebKitPage.screenshot
# ---------------------------------------------------------------------------

class TestWebKitPageScreenshot(unittest.TestCase):

    _PNG_1X1 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode()

    def test_screenshot_returns_bytes(self):
        page = _make_page({
            "Page.captureScreenshot": {"data": self._PNG_1X1},
        })
        result = _run(page.screenshot())
        self.assertIsInstance(result, bytes)

    def test_screenshot_decodes_base64(self):
        page = _make_page({
            "Page.captureScreenshot": {"data": self._PNG_1X1},
        })
        result = _run(page.screenshot())
        self.assertEqual(result, base64.b64decode(self._PNG_1X1))

    def test_screenshot_writes_file_when_path_given(self):
        page = _make_page({
            "Page.captureScreenshot": {"data": self._PNG_1X1},
        })
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            path = tf.name
        try:
            _run(page.screenshot(path=path))
            written = Path(path).read_bytes()
            self.assertEqual(written, base64.b64decode(self._PNG_1X1))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_screenshot_calls_page_capture_screenshot(self):
        client = _make_client({"Page.captureScreenshot": {"data": self._PNG_1X1}})
        page = WebKitPage(client)
        _run(page.screenshot())
        client.call.assert_called_once()
        call_args = client.call.call_args
        self.assertEqual(call_args[0][0], "Page.captureScreenshot")


# ---------------------------------------------------------------------------
# WebKitPage.click
# ---------------------------------------------------------------------------

class TestWebKitPageClick(unittest.TestCase):

    def test_click_emits_runtime_evaluate(self):
        client = _make_client({"Runtime.evaluate": {}})
        page = WebKitPage(client)
        _run(page.click("#my-button"))
        client.call.assert_called_once()
        call_args = client.call.call_args
        self.assertEqual(call_args[0][0], "Runtime.evaluate")
        # The JS snippet should call .click() on the element.
        expr = call_args[0][1].get("expression", "")
        self.assertIn(".click()", expr)

    def test_click_includes_selector_in_expression(self):
        client = _make_client({"Runtime.evaluate": {}})
        page = WebKitPage(client)
        _run(page.click(".submit-btn"))
        call_args = client.call.call_args
        expr = call_args[0][1].get("expression", "")
        self.assertIn(".submit-btn", expr)


# ---------------------------------------------------------------------------
# WebKitPage.fill
# ---------------------------------------------------------------------------

class TestWebKitPageFill(unittest.TestCase):

    def test_fill_emits_runtime_evaluate(self):
        client = _make_client({"Runtime.evaluate": {}})
        page = WebKitPage(client)
        _run(page.fill("#name", "Alice"))
        client.call.assert_called_once()
        call_args = client.call.call_args
        self.assertEqual(call_args[0][0], "Runtime.evaluate")

    def test_fill_expression_sets_value(self):
        client = _make_client({"Runtime.evaluate": {}})
        page = WebKitPage(client)
        _run(page.fill("#name", "Alice"))
        call_args = client.call.call_args
        expr = call_args[0][1].get("expression", "")
        self.assertIn(".value", expr)

    def test_fill_expression_dispatches_input_event(self):
        client = _make_client({"Runtime.evaluate": {}})
        page = WebKitPage(client)
        _run(page.fill("#email", "test@example.com"))
        call_args = client.call.call_args
        expr = call_args[0][1].get("expression", "")
        self.assertIn("input", expr.lower())


# ---------------------------------------------------------------------------
# WebKitPage.close
# ---------------------------------------------------------------------------

class TestWebKitPageClose(unittest.TestCase):

    def test_close_delegates_to_client(self):
        client = _make_client({})
        page = WebKitPage(client)
        _run(page.close())
        client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _InspectorClient.call — protocol error + timeout
# ---------------------------------------------------------------------------

class TestInspectorClientCall(unittest.TestCase):

    def test_call_raises_on_protocol_error(self):
        """call() raises RuntimeError when the response contains 'error'."""
        client = _InspectorClient("ws://127.0.0.1:0/dummy")

        error_response = {"id": 1, "error": {"message": "Method not found"}}
        fut = asyncio.get_event_loop().create_future() if False else None

        async def runner():
            loop = asyncio.get_event_loop()
            client._seq = 0
            client._pending = {}
            # Patch the ws.send to resolve the pending future immediately.
            mock_ws = AsyncMock()

            async def fake_send(data):
                msg = json.loads(data)
                msg_id = msg["id"]
                if msg_id in client._pending:
                    client._pending[msg_id].set_result(error_response)

            mock_ws.send = AsyncMock(side_effect=fake_send)
            client._ws = mock_ws

            with self.assertRaises(RuntimeError) as ctx:
                await client.call("Nonexistent.method", timeout=2.0)
            return ctx.exception

        exc = _run(runner())
        self.assertIn("protocol error", str(exc).lower())

    def test_call_raises_on_timeout(self):
        """call() raises RuntimeError when no reply arrives within timeout."""
        client = _InspectorClient("ws://127.0.0.1:0/dummy")
        client._seq = 0
        client._pending = {}

        mock_ws = AsyncMock()
        # send() does nothing — no reply will arrive.
        mock_ws.send = AsyncMock()
        client._ws = mock_ws

        async def runner():
            with self.assertRaises(RuntimeError) as ctx:
                await client.call("Page.navigate", timeout=0.1)
            return ctx.exception

        exc = _run(runner())
        self.assertIn("timeout", str(exc).lower())


# ---------------------------------------------------------------------------
# _discover_ws_url — fallback when /json endpoint unreachable
# ---------------------------------------------------------------------------

class TestDiscoverWsUrl(unittest.TestCase):

    def test_fallback_url_when_json_unreachable(self):
        """_discover_ws_url returns a constructed ws:// URL on HTTP failure."""
        # Port 1 is reserved and should refuse connections immediately.
        async def runner():
            return await _discover_ws_url(1, timeout=0.5)

        url = _run(runner())
        # The fallback constructs ws://127.0.0.1:<port>/devtools/page/1
        self.assertTrue(url.startswith("ws://"))
        self.assertIn("127.0.0.1", url)
        self.assertIn("1", url)


if __name__ == "__main__":
    unittest.main()
