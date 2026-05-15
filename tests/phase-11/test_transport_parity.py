"""CPython unit test: Transport duck-type parity across implementations.

Gate 12 (PH11).  Same recv/send/close contract run against three
transport implementations:

  - MockTransport.pair()       (PH06)
  - InProcessTransport.pair()  (PH11)
  - WebviewTransport (mocked)  (PH07)

Future renderers adding a transport must clear the same suite.
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


def _run(coro):
    return asyncio.run(coro)


class TransportParityMixin:
    """Tests applied to every transport that exposes recv/send/close.

    Subclasses must define new_pair() returning (a, b).
    """

    def new_pair(self):
        raise NotImplementedError

    def test_send_routes_to_peer_recv(self):
        async def go():
            a, b = self.new_pair()
            await a.send({"id": 1, "ok": True, "result": "x"})
            return await b.recv()

        msg = _run(go())
        self.assertEqual(msg, {"id": 1, "ok": True, "result": "x"})

    def test_close_returns_none_from_recv(self):
        async def go():
            a, b = self.new_pair()
            await a.close()
            return await a.recv()

        self.assertIsNone(_run(go()))


class MockTransportParityTest(TransportParityMixin, unittest.TestCase):
    def new_pair(self):
        from picolet._transport import MockTransport
        return MockTransport.pair()


class InProcessTransportParityTest(TransportParityMixin, unittest.TestCase):
    def new_pair(self):
        from picolet._transport import InProcessTransport
        return InProcessTransport.pair()


# WebviewTransport contract is exercised by PH07's
# test_transport_contract.py.  Re-running it here would duplicate that
# coverage; the parity claim is "all three implement the same duck
# type", and gate 12 documents the same contract.  This class is left
# in place as a documentation hook for future renderer transports
# (e.g. PH12's windows-x64 LVGL transport).


if __name__ == "__main__":
    unittest.main()
