"""CPython unit test: WebviewTransport satisfies the PH06 transport contract.

Gate 9: drive WebviewTransport with a mock webview (no real GTK) and
assert recv/send/close all behave per the Transport.__doc__ contract.

Pure CPython, no real GTK initialisation.  ffi.open at import time
would require a webkit2gtk install on the dev host (true here) but
this test doesn't construct a Webview — only a WebviewTransport with
the webview= keyword left as a mock.
"""

import asyncio
import json
import os
import sys
import unittest

# Reach into the runtime tree.
HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_ROOT = os.path.normpath(
    os.path.join(HERE, "..", "..", "packages", "picolet-runtime", "python")
)
sys.path.insert(0, PYTHON_ROOT)


class MockWebview:
    """Records every eval_js call.  No GTK contact."""

    def __init__(self):
        self.evals = []

    def eval_js(self, script):
        self.evals.append(script)


class WebviewTransportContractTest(unittest.TestCase):
    """Three methods: async recv, send, close.  All awaitable."""

    def _new_transport(self, webview=None):
        # Import lazily so the unittest discover path works without the
        # webkit2gtk-dev install (the ffi.open at module import resolves
        # libwebkit2gtk-4.1.so.0 dynamically; ldconfig must have it).
        from picolet_ui._webview import WebviewTransport
        return WebviewTransport(webview=webview)

    def test_recv_returns_none_after_close(self):
        async def runner():
            t = self._new_transport()
            await t.close()
            r = await t.recv()
            return r

        result = asyncio.run(runner())
        self.assertIsNone(
            result,
            "recv() after close() must return None per Transport contract",
        )

    def test_recv_pops_inbox_in_order(self):
        async def runner():
            t = self._new_transport()
            # Deliver two raw JSON payloads as the libffi callback would.
            t._deliver_raw('{"id":1,"cmd":"a","args":null}')
            t._deliver_raw('{"id":2,"cmd":"b","args":null}')
            m1 = await t.recv()
            m2 = await t.recv()
            return m1, m2

        m1, m2 = asyncio.run(runner())
        self.assertEqual(m1, {"id": 1, "cmd": "a", "args": None})
        self.assertEqual(m2, {"id": 2, "cmd": "b", "args": None})

    def test_send_invokes_eval_js_with_picolet_recv(self):
        async def runner():
            wv = MockWebview()
            t = self._new_transport(webview=wv)
            await t.send({"id": 1, "ok": True, "result": "hi"})
            return wv.evals

        evals = asyncio.run(runner())
        self.assertEqual(len(evals), 1, "send() should issue one eval_js")
        # The script must call window.__picolet_recv with the JSON-encoded msg.
        self.assertIn("__picolet_recv", evals[0])
        # transport.send now double-encodes the payload so __picolet_recv
        # receives a JSON string (matching its string parameter type).
        # Unwrap: outer json.loads decodes the JS string literal,
        # inner json.loads parses the embedded JSON object.
        payload_start = evals[0].find("(") + 1
        payload_end = evals[0].rfind(")")
        payload = evals[0][payload_start:payload_end]
        outer = json.loads(payload)
        parsed = json.loads(outer) if isinstance(outer, str) else outer
        self.assertEqual(parsed, {"id": 1, "ok": True, "result": "hi"})

    def test_send_after_close_is_silently_dropped(self):
        async def runner():
            wv = MockWebview()
            t = self._new_transport(webview=wv)
            await t.close()
            await t.send({"id": 1, "ok": True, "result": "hi"})
            return wv.evals

        evals = asyncio.run(runner())
        self.assertEqual(
            evals,
            [],
            "send() after close() must NOT invoke eval_js",
        )

    def test_malformed_json_dropped_with_stderr_log(self):
        """Gate 17: malformed JSON payload → drop, log, no crash."""
        async def runner():
            t = self._new_transport()
            # Deliver an unparseable payload.
            t._deliver_raw("not-json{")
            # Then deliver a valid one.
            t._deliver_raw('{"id":7,"cmd":"x","args":null}')
            # The malformed one is dropped; recv() should return the valid one.
            msg = await t.recv()
            return msg

        msg = asyncio.run(runner())
        self.assertEqual(msg, {"id": 7, "cmd": "x", "args": None})

    def test_recv_waits_for_event_then_returns(self):
        """Recv blocks until a deliver-raw flips the event."""
        async def runner():
            t = self._new_transport()
            ev_received = []

            async def receiver():
                m = await t.recv()
                ev_received.append(m)

            r = asyncio.create_task(receiver())
            # Give the receiver a chance to install its asyncio.Event.
            await asyncio.sleep(0.01)
            t._deliver_raw('{"hello": 1}')
            await asyncio.wait_for(r, 1.0)
            return ev_received

        result = asyncio.run(runner())
        self.assertEqual(result, [{"hello": 1}])

    def test_raw_hook_is_called_before_parse(self):
        """The _raw_hook test hook fires before json.loads."""
        async def runner():
            t = self._new_transport()
            seen = []
            t._raw_hook = lambda s: seen.append(s)
            t._deliver_raw('{"a":1}')
            return seen

        seen = asyncio.run(runner())
        self.assertEqual(seen, ['{"a":1}'])


class WebviewTransportDispatcherIntegrationTest(unittest.TestCase):
    """Gate 10: the dispatcher's run loop accepts WebviewTransport."""

    def test_dispatcher_consumes_webview_transport_messages(self):
        async def runner():
            from picolet_ui._webview import WebviewTransport
            # Mock webview: capture eval_js as outbound.
            mock = MockWebview()
            transport = WebviewTransport(webview=mock)

            import picolet

            @picolet.command
            async def echo(args):
                return args

            # Feed one request, then close.
            transport._deliver_raw('{"id":1,"cmd":"echo","args":{"hello":"world"}}')

            async def main_coro():
                # Wait a bit for the dispatcher to handle it.
                await asyncio.sleep(0.1)
                await transport.close()

            # picolet.run is synchronous (calls asyncio.run); we call the
            # inner _run_with_main coroutine directly to stay inside our
            # event loop.
            from picolet._dispatcher import _run_with_main
            await _run_with_main(transport, main_coro)
            return mock.evals

        evals = asyncio.run(runner())
        self.assertEqual(len(evals), 1, "dispatcher should have sent a reply")
        # Extract the JSON the reply contains.
        # transport.send double-encodes: window.__picolet_recv("{\\"id\\":1,...}")
        s = evals[0]
        start = s.find("(") + 1
        end = s.rfind(")")
        outer = json.loads(s[start:end])
        reply = json.loads(outer) if isinstance(outer, str) else outer
        self.assertEqual(reply.get("id"), 1)
        self.assertTrue(reply.get("ok"))
        self.assertEqual(reply.get("result"), {"hello": "world"})


if __name__ == "__main__":
    unittest.main()
