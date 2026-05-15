"""CPython unit test: InProcessTransport.pair satisfies the Transport contract.

Gate 7 (PH11).  Drives the PH06 dispatcher with a paired
InProcessTransport and asserts:

  - picolet.invoke round-trips Python-to-Python through the dispatcher.
  - The remote handler's return value is the invoke's result.
  - A KeyError raised in the handler propagates with type+message
    preserved on the caller's side (FR-IPC-2).
"""

import asyncio
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_ROOT = os.path.normpath(
    os.path.join(HERE, "..", "..", "packages", "picolet-runtime", "python")
)
sys.path.insert(0, PYTHON_ROOT)


class InProcessTransportPairTest(unittest.TestCase):
    """The pair routes messages bidirectionally and respects close()."""

    def test_send_recv_roundtrip(self):
        from picolet._transport import InProcessTransport

        async def runner():
            a, b = InProcessTransport.pair()
            await a.send({"hello": "world"})
            msg = await b.recv()
            return msg

        result = asyncio.run(runner())
        self.assertEqual(result, {"hello": "world"})

    def test_close_returns_none_on_recv(self):
        from picolet._transport import InProcessTransport

        async def runner():
            a, b = InProcessTransport.pair()
            await a.close()
            r = await a.recv()
            return r

        self.assertIsNone(asyncio.run(runner()))

    def test_close_wakes_peer_recv(self):
        from picolet._transport import InProcessTransport

        async def runner():
            a, b = InProcessTransport.pair()
            # Start b.recv first, then close a.  b.recv should
            # return None.
            recv_task = asyncio.create_task(b.recv())
            await asyncio.sleep(0)
            await a.close()
            r = await asyncio.wait_for(recv_task, 1.0)
            return r

        self.assertIsNone(asyncio.run(runner()))


class InProcessDispatcherTest(unittest.TestCase):
    """End-to-end: picolet.invoke over InProcessTransport.pair."""

    def setUp(self):
        # Clear the module-level command registry between tests.
        import picolet._dispatcher as d
        d._commands.clear()
        d._subscribers.clear()
        d._pending_invokes.clear()
        d._active_transport = None
        d._next_invoke_id = 1

    def test_invoke_roundtrip(self):
        import picolet
        from picolet._transport import InProcessTransport
        from picolet._dispatcher import _run_with_main

        @picolet.command
        async def greet(args):
            return "hello " + args["name"]

        result_holder = []

        async def main_b(b):
            # Wait one tick so the dispatcher binds _active_transport.
            await asyncio.sleep(0)
            # Send a request directly through b and await the reply.
            await b.send({"id": 1, "cmd": "greet", "args": {"name": "world"}})
            reply = await b.recv()
            result_holder.append(reply)

        async def runner():
            a, b = InProcessTransport.pair()
            dispatcher_task = asyncio.create_task(_run_with_main(a, None))
            try:
                await main_b(b)
            finally:
                dispatcher_task.cancel()
                try:
                    await dispatcher_task
                except BaseException:
                    pass

        asyncio.run(runner())
        self.assertEqual(len(result_holder), 1)
        reply = result_holder[0]
        self.assertEqual(reply.get("ok"), True)
        self.assertEqual(reply.get("result"), "hello world")

    def test_error_propagation(self):
        import picolet
        from picolet._transport import InProcessTransport
        from picolet._dispatcher import _run_with_main

        @picolet.command
        async def boom(args):
            raise KeyError("missing-thing")

        result_holder = []

        async def main_b(b):
            await asyncio.sleep(0)
            await b.send({"id": 7, "cmd": "boom", "args": None})
            reply = await b.recv()
            result_holder.append(reply)

        async def runner():
            a, b = InProcessTransport.pair()
            dispatcher_task = asyncio.create_task(_run_with_main(a, None))
            try:
                await main_b(b)
            finally:
                dispatcher_task.cancel()
                try:
                    await dispatcher_task
                except BaseException:
                    pass

        asyncio.run(runner())
        self.assertEqual(len(result_holder), 1)
        reply = result_holder[0]
        self.assertEqual(reply.get("ok"), False)
        err = reply.get("error") or {}
        self.assertEqual(err.get("type"), "KeyError")
        # Message contains the original arg (formatted varies between
        # MicroPython and CPython); check substring.
        self.assertIn("missing-thing", err.get("message", ""))


if __name__ == "__main__":
    unittest.main()
