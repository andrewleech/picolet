# picolet_ui — picolet runtime UI renderer facade.
#
# PH07 (webview): WebKitGTK 4.1 webview renderer.  Linux-only at PH07;
#                 PH10 added the Windows WebView2 variant.
# PH11 (lvgl):    SDL2-backed LVGL renderer.  Linux-only at PH11;
#                 PH12 closes the Windows half.
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
# imports (libffi.dlopen of libwebkit2gtk-4.1, libgtk-3) succeed
# without a DISPLAY; gtk_init is the first symbol that needs X and is
# only called inside Window.__init__.  The lvgl-side import of the
# `lvgl` C module happens lazily inside `LvglDisplay.__init__` and
# `_lvgl_pump`, so `import picolet_ui` on the webview/cli variants does
# not require the lvgl C module to exist.

import sys


# --- Loop pump intervals (always exposed) ---
from ._loop import PUMP_INTERVAL_S, LVGL_TICK_MS

# --- Webview surface (PH07; safe to import on lvgl variant) ---
# The webview submodules use libffi to dlopen libwebkit2gtk-4.1 / libgtk-3
# at import time.  Those libraries are present on the build container
# and on user systems that have installed libwebkit2gtk-4.1-0.  On the
# lvgl variant they may be absent; guard the import so `import picolet_ui`
# stays cheap even when only SDL2 is present.
try:
    from ._window import Window
    from ._webview import Webview, WebviewTransport
    from ._app import Application
    _HAVE_WEBVIEW = True
except (ImportError, OSError):
    Window = None
    Webview = None
    WebviewTransport = None
    Application = None
    _HAVE_WEBVIEW = False

# --- LVGL surface (PH11; only on lvgl variant) ---
# The `lvgl` C module is a USER_C_MODULE in the lvgl variant only.  On
# cli/webview variants the import raises ImportError; guard so
# `import picolet_ui` works there too.
try:
    import lvgl as _lv  # noqa: F401  — probe only; consumed inside _lvgl
    from ._lvgl import LvglDisplay
    _HAVE_LVGL = True
except ImportError:
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
        except (ValueError, Exception):
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
