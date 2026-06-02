# picolet — runtime IPC dispatcher (FR-IPC-{1..5}).
#
# This package is the *runtime-side* picolet API, frozen into the
# picolet-runtime binary.  User app code imports `picolet` and uses the
# decorators / functions re-exported below to register commands and
# events that a peer (a JS-side webview bridge, an LVGL panel, or an
# external stdio harness) can drive.
#
# The host-side build tool `picolet build` lives in
# packages/picolet/picolet/ as the `picolet.cli` package; the two
# never share a name now (the post-review A2 rename eliminated the
# collision and the PYTHONPATH discipline that previously masked it).

from ._dispatcher import command, invoke, emit, on, run
from ._transport import Transport, StdioTransport, InProcessTransport, MockTransport
from ._errors import RemoteError

__all__ = (
    "command",
    "invoke",
    "emit",
    "on",
    "run",
    "Transport",
    "StdioTransport",
    "InProcessTransport",
    "RemoteError",
)

# MockTransport is kept as a module attribute for backwards-compatibility with
# existing test code, but it is not part of the public API (__all__).  New
# test code should import it from picolet._testing instead.

