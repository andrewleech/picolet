# picolet_ui_win — Win32 + WebView2 renderer for the picolet runtime (PH10).
#
# Windows-only sibling of picolet_ui (PH07's GTK/WebKitGTK).  The two
# packages have parallel surfaces; the only platform-specific code
# lives below this façade.  User code that does:
#
#     import picolet_ui_win as ui
#     ui.run(main=main_coro)
#
# is platform-symmetric with `picolet_ui` on Linux modulo the import line.
# The PH09 template / PH10 fixtures import picolet_ui or picolet_ui_win
# directly; the dispatcher transport and bridge JS are platform-agnostic.
#
# Public surface:
#
#   picolet_ui_win.Window(title=, size=, resizable=)
#   picolet_ui_win.Webview(window, transport=)
#   picolet_ui_win.WebviewTransport(webview=None)
#   picolet_ui_win.Application(title=, size=, resizable=)
#   picolet_ui_win.run(main=)               — top-level entry; auto-wires Application
#
#   picolet_ui_win.PUMP_INTERVAL_S          — Win32 pump tick (default 0.005)
#
# Importing the package is side-effect-free: no CoInitialize, no
# LoadLibraryW("WebView2Loader.dll"), no window creation.  The first
# Window() instantiation triggers the loader-DLL extract + LoadLibraryW
# and the WebView2 environment/controller creation chain.

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
