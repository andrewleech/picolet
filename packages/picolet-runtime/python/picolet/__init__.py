# picolet — runtime IPC dispatcher (FR-IPC-{1..5}).
#
# This package is the *runtime-side* picolet API, frozen into the
# picolet-runtime binary.  User app code imports `picolet` and uses the
# decorators / functions re-exported below to register commands and
# events that a peer (a JS-side webview bridge, an LVGL panel, or an
# external stdio harness) can drive.
#
# The host-side build tool `picolet build` lives in
# packages/picolet-cli/picolet/ and is a *different* `picolet` package — they
# never coexist in one Python process (one runs on the build host
# under CPython, the other inside the frozen MicroPython runtime).

from ._dispatcher import command, invoke, emit, on, run
from ._transport import Transport, StdioTransport, MockTransport
from ._errors import RemoteError

__all__ = (
    "command",
    "invoke",
    "emit",
    "on",
    "run",
    "Transport",
    "StdioTransport",
    "MockTransport",
    "RemoteError",
)
