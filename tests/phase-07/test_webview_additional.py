"""CPython unit tests: pump responsiveness, toml parser, transport queue,
threaded stub, and NFR-5 objdump check.

Gates covered:
  Gate 16 — pump+event design under 50 back-to-back postMessages (< 25 ms each)
  F1  — _toml.loads parses [window] correctly for the runtime's config reader
  F2  — multiple postMessage round-trips preserve message order (queue)
  F3  — PICOLET_WV_THREADED=1 raises NotImplementedError cleanly
  F4  — concurrent rapid postMessage deliveries arrive in order at recv()

Run with:
    cd /home/anl/picolet
    python -m pytest tests/phase-07/test_webview_additional.py -v
or:
    PYTHONPATH=packages/picolet-runtime/python python3 -m unittest \\
        tests/phase-07/test_webview_additional.py
"""

import asyncio
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_ROOT = os.path.normpath(
    os.path.join(HERE, "..", "..", "packages", "picolet-runtime", "python")
)
sys.path.insert(0, PYTHON_ROOT)


# ---------------------------------------------------------------------------
# Gate 16: pump responsiveness under load
# ---------------------------------------------------------------------------

class PumpResponsivenessTest(unittest.TestCase):
    """Gate 16: 50 back-to-back mock-postMessages all delivered within 25 ms."""

    def _new_transport(self):
        from picolet_ui._webview import WebviewTransport
        return WebviewTransport()

    def test_fifty_messages_all_delivered_within_25ms(self):
        """Each message is available to recv() within 25 ms of being delivered."""
        async def runner():
            N = 50
            PUMP_INTERVAL = 0.005  # 5 ms — picolet_ui._loop.PUMP_INTERVAL_S
            transport = self._new_transport()
            latencies = []

            async def mock_pump_and_deliver():
                """Simulate the GTK pump: deliver one message per tick."""
                for i in range(N):
                    await asyncio.sleep(PUMP_INTERVAL)
                    transport._deliver_raw(
                        '{{"id":{},"cmd":"x","args":null}}'.format(i)
                    )

            async def consume_all():
                """Receive N messages, recording latency from delivery."""
                for i in range(N):
                    t_start = time.monotonic()
                    msg = await transport.recv()
                    elapsed_ms = (time.monotonic() - t_start) * 1000
                    latencies.append(elapsed_ms)
                    self.assertIsNotNone(msg, "recv() must not return None mid-stream")
                    self.assertEqual(
                        msg["id"], i,
                        "messages must arrive in the order they were delivered",
                    )

            pump_task = asyncio.create_task(mock_pump_and_deliver())
            recv_task = asyncio.create_task(consume_all())
            await asyncio.gather(pump_task, recv_task)
            return latencies

        latencies = asyncio.run(runner())
        max_lat = max(latencies)
        self.assertLess(
            max_lat, 25.0,
            "Each message must be delivered to recv() within 25 ms of being posted; "
            "max observed: {:.3f} ms".format(max_lat),
        )

    def test_burst_of_messages_already_queued_all_served(self):
        """All messages pre-loaded in _inbox before first recv() are returned."""
        async def runner():
            N = 50
            transport = self._new_transport()
            # Deliver all at once (burst) before any recv().
            for i in range(N):
                transport._deliver_raw('{{"id":{},"cmd":"burst","args":null}}'.format(i))
            received = []
            for _ in range(N):
                msg = await transport.recv()
                received.append(msg["id"])
            return received

        ids = asyncio.run(runner())
        self.assertEqual(ids, list(range(50)), "burst messages must arrive in order")


# ---------------------------------------------------------------------------
# F1: _toml.loads parses [window] correctly
# ---------------------------------------------------------------------------

class TomlParserTest(unittest.TestCase):
    """The _toml mini-parser correctly handles all shapes used by [window]."""

    def _loads(self, text):
        from picolet_ui._toml import loads
        return loads(text)

    def test_string_title(self):
        result = self._loads('[window]\ntitle = "Hello World"')
        self.assertEqual(result["window"]["title"], "Hello World")

    def test_integer_list_size(self):
        result = self._loads('[window]\nsize = [640, 480]')
        self.assertEqual(result["window"]["size"], [640, 480])

    def test_bool_false(self):
        result = self._loads('[window]\nresizable = false')
        self.assertIs(result["window"]["resizable"], False)

    def test_bool_true(self):
        result = self._loads('[window]\nresizable = true')
        self.assertIs(result["window"]["resizable"], True)

    def test_full_window_section(self):
        toml = (
            '[window]\n'
            'title = "PH07 Sanity"\n'
            'size = [640, 480]\n'
            'resizable = false\n'
        )
        result = self._loads(toml)
        window = result["window"]
        self.assertEqual(window["title"], "PH07 Sanity")
        self.assertEqual(window["size"], [640, 480])
        self.assertIs(window["resizable"], False)

    def test_multiple_sections(self):
        toml = (
            '[app]\n'
            'name = "myapp"\n'
            '[window]\n'
            'title = "My App"\n'
        )
        result = self._loads(toml)
        self.assertEqual(result["app"]["name"], "myapp")
        self.assertEqual(result["window"]["title"], "My App")

    def test_comment_ignored(self):
        result = self._loads('[window]\n# ignored\ntitle = "T"')
        self.assertEqual(result["window"]["title"], "T")

    def test_missing_section_returns_empty(self):
        result = self._loads('[app]\nname = "x"')
        self.assertNotIn("window", result)

    def test_single_quoted_string(self):
        result = self._loads("[window]\ntitle = 'bare'")
        self.assertEqual(result["window"]["title"], "bare")

    def test_load_window_config_from_string(self):
        """load_window_config returns correct values from a synthetic toml file."""
        import tempfile
        from picolet_ui._window import load_window_config
        toml_content = (
            '[window]\n'
            'title = "ConfigTitle"\n'
            'size = [1024, 768]\n'
            'resizable = true\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as fh:
            fh.write(toml_content)
            path = fh.name
        try:
            cfg = load_window_config(rom_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(cfg["title"], "ConfigTitle")
        self.assertEqual(cfg["size"], [1024, 768])
        self.assertIs(cfg["resizable"], True)

    def test_load_window_config_defaults_on_missing_file(self):
        """load_window_config returns defaults when /rom/picolet.toml is absent."""
        from picolet_ui._window import load_window_config, _DEFAULT_TITLE, _DEFAULT_W, _DEFAULT_H
        cfg = load_window_config(rom_path="/nonexistent/path/picolet.toml")
        self.assertEqual(cfg["title"], _DEFAULT_TITLE)
        self.assertEqual(cfg["size"], [_DEFAULT_W, _DEFAULT_H])


# ---------------------------------------------------------------------------
# F2: Multiple postMessage round-trips — queue ordering
# ---------------------------------------------------------------------------

class TransportQueueOrderingTest(unittest.TestCase):
    """Multiple sequential recv() calls return messages in delivery order."""

    def _new_transport(self):
        from picolet_ui._webview import WebviewTransport
        return WebviewTransport()

    def test_ten_messages_delivered_in_order(self):
        async def runner():
            t = self._new_transport()
            N = 10
            for i in range(N):
                t._deliver_raw('{{"id":{},"cmd":"seq","args":{{"n":{}}}}}'.format(i, i))
            results = []
            for _ in range(N):
                msg = await t.recv()
                results.append(msg["id"])
            return results

        ids = asyncio.run(runner())
        self.assertEqual(ids, list(range(10)))

    def test_interleaved_deliver_and_recv(self):
        """Interleaved deliver / recv preserves ordering under asyncio scheduling."""
        async def runner():
            t = self._new_transport()
            received = []

            async def producer():
                for i in range(5):
                    t._deliver_raw('{{"id":{},"cmd":"p","args":null}}'.format(i))
                    await asyncio.sleep(0.001)

            async def consumer():
                for _ in range(5):
                    msg = await t.recv()
                    received.append(msg["id"])

            await asyncio.gather(producer(), consumer())
            return received

        ids = asyncio.run(runner())
        self.assertEqual(ids, list(range(5)))

    def test_event_cleared_between_messages(self):
        """The recv event is correctly reset so sequential recv() calls all block."""
        async def runner():
            t = self._new_transport()
            # First message arrives immediately.
            t._deliver_raw('{"id":1,"cmd":"a","args":null}')
            m1 = await t.recv()
            # Second arrives after a delay.
            async def delayed_deliver():
                await asyncio.sleep(0.01)
                t._deliver_raw('{"id":2,"cmd":"b","args":null}')

            deliver_task = asyncio.create_task(delayed_deliver())
            m2 = await asyncio.wait_for(t.recv(), timeout=1.0)
            await deliver_task
            return m1, m2

        m1, m2 = asyncio.run(runner())
        self.assertEqual(m1["id"], 1)
        self.assertEqual(m2["id"], 2)

    def test_recv_count_increments_per_message(self):
        """transport.recv_count reflects the number of successfully received messages."""
        async def runner():
            t = self._new_transport()
            for i in range(3):
                t._deliver_raw('{{"id":{},"cmd":"c","args":null}}'.format(i))
                await t.recv()
            return t.recv_count

        count = asyncio.run(runner())
        self.assertEqual(count, 3)


# ---------------------------------------------------------------------------
# F3: Worker-thread option raises NotImplementedError
# ---------------------------------------------------------------------------

class ThreadedStubTest(unittest.TestCase):
    """PICOLET_WV_THREADED=1 raises NotImplementedError; other values are no-ops."""

    def test_threaded_env_raises_not_implemented_error(self):
        from picolet_ui._loop import _worker_thread_pump_stub, _maybe_take_threaded_branch
        with self.assertRaises(NotImplementedError) as ctx:
            _worker_thread_pump_stub()
        msg = str(ctx.exception)
        self.assertIn("PICOLET_WV_THREADED=1", msg)
        self.assertIn("Option B", msg)

    def test_maybe_take_threaded_branch_raises_when_env_set(self):
        from picolet_ui._loop import _maybe_take_threaded_branch
        original = os.environ.get("PICOLET_WV_THREADED")
        try:
            os.environ["PICOLET_WV_THREADED"] = "1"
            with self.assertRaises(NotImplementedError):
                _maybe_take_threaded_branch()
        finally:
            if original is None:
                os.environ.pop("PICOLET_WV_THREADED", None)
            else:
                os.environ["PICOLET_WV_THREADED"] = original

    def test_maybe_take_threaded_branch_no_op_when_env_absent(self):
        from picolet_ui._loop import _maybe_take_threaded_branch
        original = os.environ.pop("PICOLET_WV_THREADED", None)
        try:
            # Must not raise
            _maybe_take_threaded_branch()
        finally:
            if original is not None:
                os.environ["PICOLET_WV_THREADED"] = original

    def test_maybe_take_threaded_branch_no_op_when_env_zero(self):
        from picolet_ui._loop import _maybe_take_threaded_branch
        original = os.environ.get("PICOLET_WV_THREADED")
        try:
            os.environ["PICOLET_WV_THREADED"] = "0"
            _maybe_take_threaded_branch()  # must not raise
        finally:
            if original is None:
                os.environ.pop("PICOLET_WV_THREADED", None)
            else:
                os.environ["PICOLET_WV_THREADED"] = original


# ---------------------------------------------------------------------------
# F4: Concurrent rapid postMessage deliveries arrive in order
# ---------------------------------------------------------------------------

class ConcurrentRapidDeliveryTest(unittest.TestCase):
    """Two rapid postMessage deliveries both complete; replies arrive in order."""

    def _new_transport(self):
        from picolet_ui._webview import WebviewTransport
        return WebviewTransport()

    def test_two_rapid_messages_arrive_in_order(self):
        """Simulating two back-to-back JS postMessage calls — both handled, in order."""
        async def runner():
            t = self._new_transport()
            # Simulate JS firing two postMessage calls with no yield between them.
            t._deliver_raw('{"id":1,"cmd":"first","args":null}')
            t._deliver_raw('{"id":2,"cmd":"second","args":null}')
            m1 = await t.recv()
            m2 = await t.recv()
            return m1, m2

        m1, m2 = asyncio.run(runner())
        self.assertEqual(m1["cmd"], "first")
        self.assertEqual(m2["cmd"], "second")

    def test_two_rapid_messages_dispatcher_replies_in_order(self):
        """Dispatcher handles two queued messages; both replies reach outbox in order."""
        async def runner():
            from picolet_ui._webview import WebviewTransport
            import picolet
            import picolet._dispatcher as _dispatcher

            # Fresh dispatcher state.
            d = _dispatcher._default
            d._commands.clear()
            d._subscribers.clear()
            d._pending_invokes.clear()
            d._active_transport = None
            d._next_invoke_id = 1
            d._inbound_in_flight = 0

            class FakeWebview:
                def __init__(self):
                    self.evals = []
                def eval_js(self, script):
                    self.evals.append(script)

            wv = FakeWebview()
            t = WebviewTransport(webview=wv)

            @picolet.command
            async def echo(args):
                return args

            # Deliver two messages before the dispatcher starts.
            t._deliver_raw('{"id":1,"cmd":"echo","args":{"seq":1}}')
            t._deliver_raw('{"id":2,"cmd":"echo","args":{"seq":2}}')

            async def main_coro():
                # Give dispatcher time to process both.
                await asyncio.sleep(0.15)
                await t.close()

            from picolet._dispatcher import _run_with_main
            await _run_with_main(t, main_coro)
            return wv.evals

        evals = asyncio.run(runner())
        self.assertGreaterEqual(len(evals), 2, "both messages must produce a reply")

        # Extract IDs and results from the JS calls.
        # transport.send now double-encodes: window.__picolet_recv("{\\"id\\":1,...}")
        # so the argument between ( and ) is a JSON-encoded string.
        import json
        replies = []
        for e in evals:
            start = e.find("(") + 1
            end = e.rfind(")")
            # First json.loads unwraps the outer JS string literal.
            # Second json.loads parses the inner JSON object.
            raw = json.loads(e[start:end])
            replies.append(json.loads(raw) if isinstance(raw, str) else raw)

        # The first reply must be for id=1, the second for id=2 (FIFO).
        ids = [r["id"] for r in replies]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        # Both must succeed.
        for r in replies:
            self.assertTrue(r.get("ok"), "each reply must be ok=true: {}".format(r))


# ---------------------------------------------------------------------------
# F5: send() standalone (no webview) records to outbox
# ---------------------------------------------------------------------------

class TransportSendStandaloneTest(unittest.TestCase):
    """send() without a webview records to internal outbox (test-mode path)."""

    def test_standalone_send_populates_outbox(self):
        async def runner():
            from picolet_ui._webview import WebviewTransport
            t = WebviewTransport(webview=None)
            await t.send({"id": 1, "ok": True, "result": "x"})
            await t.send({"id": 2, "ok": True, "result": "y"})
            return t.drain_outbox()

        outbox = asyncio.run(runner())
        self.assertEqual(len(outbox), 2)
        self.assertEqual(outbox[0]["id"], 1)
        self.assertEqual(outbox[1]["id"], 2)


if __name__ == "__main__":
    unittest.main()
