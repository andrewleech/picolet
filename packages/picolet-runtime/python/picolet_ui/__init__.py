# picolet_ui — picolet runtime UI renderer facade.
#
# PH07 (webview, linux):    WebKitGTK 4.1 webview renderer.
# PH10 (webview, windows):  WebView2 (Edge Chromium) webview renderer.
# PH11 (lvgl, linux):       SDL2-backed LVGL renderer.
# PH12 (lvgl, windows):     SDL2-backed LVGL renderer.
#
# The webview backend is selected at import time via sys.platform —
# Linux pulls libwebkit2gtk-4.1 / libgtk-3 via libffi; Windows pulls
# the in-process picolet_webview2 C overlay (statically linked into the
# .exe).  The public surface is identical on both platforms; user
# code imports `picolet_ui` and never references the backend directly.
#
# Public surface (webview):
#
#   picolet_ui.Window(title=, size=, resizable=)
#   picolet_ui.Webview(window, root_uri=, transport=)
#   picolet_ui.WebviewTransport(webview=None)
#   picolet_ui.Application(title=, size=, resizable=, root_uri=)
#
# Public surface (lvgl):
#
#   picolet_ui.LvglDisplay(title=, width=, height=)
#
# Top-level entry point (renderer-aware):
#
#   picolet_ui.run(main=)               — reads /rom/picolet.toml [ui]
#                                       renderer and dispatches to
#                                       webview or lvgl boot path
#
# Tunables exposed at the module level:
#
#   picolet_ui.PUMP_INTERVAL_S          — webview pump tick (s),    default 0.005
#   picolet_ui.LVGL_TICK_MS             — lvgl pump tick (ms),      default 5
#
# Importing this module is cheap on every variant.  The webview FFI
# imports succeed without a DISPLAY on Linux (gtk_init is the first
# symbol that needs X and is only called inside Window.__init__) and
# without a Win32 window station on Windows (CoInitializeEx and
# WebView2Loader.dll only happen at first Webview() construction).
# The lvgl-side import of the `lvgl` C module happens lazily inside
# `LvglDisplay.__init__` and `_lvgl_pump`, so `import picolet_ui` on the
# webview/cli variants does not require the lvgl C module to exist.

import sys


# --- Loop pump intervals (always exposed) ---
from ._loop import PUMP_INTERVAL_S, LVGL_TICK_MS

# --- Webview surface ---
# Linux: _window / _webview / _app use libffi to dlopen
# libwebkit2gtk-4.1 / libgtk-3 at import time.
#
# Windows: the same modules pick the win32 branch via sys.platform,
# binding the in-process picolet_webview2 C overlay (statically linked
# into the .exe; ffi.open(None) -> GetModuleHandle(NULL)).
#
# On either platform the libraries may be absent (e.g. the lvgl variant
# does not link the overlay; the GTK FFI module may not load against an
# lvgl-only host).  Guard the import so `import picolet_ui` stays cheap
# in those cases.
try:
    from ._window import Window
    from ._webview import Webview, WebviewTransport
    from ._app import Application
    _HAVE_WEBVIEW = True
except (ImportError, OSError):
    # Check whether a peer renderer (LVGL) is available before silently
    # swallowing the error.  If neither renderer is present, re-raise so
    # the user sees the original _safe_open error message with the
    # apt-install hint rather than a cryptic "no renderer available" later.
    try:
        import lvgl as _lv  # noqa: F401  — probe only; consumed inside _lvgl
        from ._lvgl import LvglDisplay
        _HAVE_LVGL = True
    except (ImportError, OSError):
        LvglDisplay = None
        _HAVE_LVGL = False
    if not _HAVE_LVGL:
        raise  # neither renderer; surface the original error
    Window = None
    Webview = None
    WebviewTransport = None
    Application = None
    _HAVE_WEBVIEW = False

if _HAVE_WEBVIEW:
    # --- LVGL surface (PH11; only on lvgl variant) ---
    # The `lvgl` C module is a USER_C_MODULE in the lvgl variant only.  On
    # cli/webview variants the import raises ImportError; guard so
    # `import picolet_ui` works there too.
    try:
        import lvgl as _lv  # noqa: F401  — probe only; consumed inside _lvgl
        from ._lvgl import LvglDisplay
        _HAVE_LVGL = True
    except (ImportError, OSError):
        LvglDisplay = None
        _HAVE_LVGL = False


def _detect_renderer(rom_path="/rom/picolet.toml"):
    """Read /rom/picolet.toml [ui].renderer and return 'webview' | 'lvgl'.

    Falls back to whichever surface is available.  When both are
    available (only on a build that happened to link both — not the
    case in PH11), webview wins for backwards compatibility with PH07
    callers.
    """
    try:
        with open(rom_path, "r") as fh:
            text = fh.read()
    except OSError:
        text = ""
    if text:
        try:
            from ._toml import loads
            parsed = loads(text)
            ui = parsed.get("ui") or {}
            r = ui.get("renderer")
            if r in ("webview", "lvgl"):
                return r
        except Exception:
            pass
    # Fallback by capability.
    if _HAVE_LVGL and not _HAVE_WEBVIEW:
        return "lvgl"
    if _HAVE_WEBVIEW:
        return "webview"
    if _HAVE_LVGL:
        return "lvgl"
    raise RuntimeError(
        "picolet_ui.run: no renderer available — built without "
        "webview FFI or lvgl USER_C_MODULES; check the variant build"
    )


def run(main=None):
    """Top-level renderer-aware entry point.

    Reads /rom/picolet.toml [ui].renderer and dispatches:

      renderer = "webview"  → opens a webview Application
                              (PH07's picolet_ui.run shape)
      renderer = "lvgl"     → opens an LvglDisplay and enters the
                              lvgl pump loop with an
                              InProcessTransport pair (FR-LV-4)
    """
    renderer = _detect_renderer()
    if renderer == "webview":
        if not _HAVE_WEBVIEW:
            raise RuntimeError(
                "picolet_ui.run: picolet.toml requests renderer=webview "
                "but the webview surface is not available on this "
                "runtime variant"
            )
        from ._app import run as _webview_run
        return _webview_run(main=main)
    if renderer == "lvgl":
        if not _HAVE_LVGL:
            raise RuntimeError(
                "picolet_ui.run: picolet.toml requests renderer=lvgl "
                "but the lvgl surface is not available on this "
                "runtime variant"
            )
        from ._lvgl import run as _lvgl_run
        return _lvgl_run(main=main)
    raise RuntimeError(
        "picolet_ui.run: unknown renderer: " + repr(renderer)
    )


__all__ = (
    "Window",
    "Webview",
    "WebviewTransport",
    "Application",
    "LvglDisplay",
    "run",
    "PUMP_INTERVAL_S",
    "LVGL_TICK_MS",
)
