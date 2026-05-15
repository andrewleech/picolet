# picolet_ui — WebKitGTK 4.1 webview renderer for the picolet runtime.
#
# PH07.  Linux-only.  Pure libffi bindings; no native modules.  See
# docs/phases/PHASE_07_webview-renderer-linux.md for the design.
#
# Public surface:
#
#   picolet_ui.Window(title=, size=, resizable=)
#   picolet_ui.Webview(window, root_uri=, transport=)
#   picolet_ui.WebviewTransport(webview=None)
#   picolet_ui.Application(title=, size=, resizable=, root_uri=)
#   picolet_ui.run(main=)               — top-level entry; auto-wires Application
#
#   picolet_ui.PUMP_INTERVAL_S          — GTK pump tick in seconds (default 0.005)
#
# Importing this module is cheap and does NOT call gtk_init — that
# happens on the first `Window()` instantiation.  This lets the
# import-only gate (gate 3) pass on a host without DISPLAY.

# The dot-imports below trigger _gtk_ffi.py's ffi.open at module load.
# Without a DISPLAY, the libraries still open fine — gtk_init is the
# first symbol that needs X.
from ._window import Window
from ._webview import Webview, WebviewTransport
from ._app import Application, run
from ._loop import PUMP_INTERVAL_S

__all__ = (
    "Window",
    "Webview",
    "WebviewTransport",
    "Application",
    "run",
    "PUMP_INTERVAL_S",
)
