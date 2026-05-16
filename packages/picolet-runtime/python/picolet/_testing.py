# picolet._testing — test helpers for picolet IPC code.
#
# This module is not part of the public picolet API and is not frozen into any
# runtime variant.  It is imported by host-side CPython tests only.
#
# MockTransport lives here so that test code has an explicit, stable import
# path (``from picolet._testing import MockTransport``) that is clearly
# test-only, separate from the production transports in _transport.py.

from ._transport import MockTransport

__all__ = ("MockTransport",)
