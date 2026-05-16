"""CPython unit test: LVGL pump does not starve the dispatcher.

Gate 10 (PH11).  Drives the scheduler with back-to-back in-process
invokes interleaved with mock task_handler ticks; asserts each invoke
completes within 25 ms.

Mocks the `lvgl` module so this test runs without LVGL or SDL2
installed.  The mock's task_handler is intentionally cheap (returns
immediately) so the only blocking factor is asyncio scheduling
fairness.
"""

import asyncio
import os
import sys
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_ROOT = os.path.normpath(
    os.path.join(HERE, "..", "..", "packages", "picolet-runtime", "python")
)
sys.path.insert(0, PYTHON_ROOT)


def _make_mock_lvgl():
    """Install a fake `lvgl` module exposing tick_inc + task_handler."""
    mod = types.ModuleType("lvgl")
    mod.tick_inc = lambda ms: None
    mod.task_handler = lambda: None
    sys.modules["lvgl"] = mod
    return mod


class LvglPumpResponsivenessTest(unittest.TestCase):
    """Run 50 back-to-back invokes alongside the lvgl pump; per-call <= 25 ms."""

    def setUp(self):
        _make_mock_lvgl()
        import picolet._dispatcher as _disp
        d = _disp._default
        d._commands.clear()
        d._subscribers.clear()
        d._pending_invokes.clear()
        d._active_transport = None
        d._next_invoke_id = 1
        d._inbound_in_flight = 0

    def tearDown(self):
        sys.modules.pop("lvgl", None)

    def test_50_invokes_each_under_25ms(self):
        import picolet
        from picolet._transport import InProcessTransport
        from picolet._dispatcher import _run_with_main
        from picolet_ui._loop import _lvgl_pump

        @picolet.command
        async def echo(args):
            return args

        timings = []

        async def main_b(b):
            # Wait one tick so the dispatcher binds _active_transport.
            await asyncio.sleep(0)
            for i in range(50):
                start = time.monotonic()
                await b.send({"id": i + 1, "cmd": "echo", "args": {"i": i}})
                reply = await b.recv()
                elapsed_ms = (time.monotonic() - start) * 1000.0
                timings.append(elapsed_ms)
                if not (reply and reply.get("ok")):
                    raise RuntimeError("invoke failed: " + repr(reply))

        async def runner():
            a, b = InProcessTransport.pair()
            dispatcher_task = asyncio.create_task(_run_with_main(a, None))
            pump_task = asyncio.create_task(_lvgl_pump())
            try:
                await main_b(b)
            finally:
                for t in (pump_task, dispatcher_task):
                    t.cancel()
                    try:
                        await t
                    except BaseException:
                        pass

        asyncio.run(runner())
        self.assertEqual(len(timings), 50)
        worst = max(timings)
        self.assertLess(
            worst, 25.0,
            "lvgl pump starved dispatcher: worst invoke {:.2f} ms >= 25 ms"
            .format(worst),
        )


if __name__ == "__main__":
    unittest.main()
