# picolet_ui._lvgl — LVGL display facade for the lvgl variant (PH11).
#
# Linux-only (PH11).  Windows LVGL lands in PH12 and shares this file.
#
# Public surface:
#
#   LvglDisplay(title, width, height) — open an SDL2 window of the given
#                                       size, call lv.init(), configure
#                                       the active display.
#
# `LvglDisplay.__init__` reads /rom/picolet.toml's [window] table for the
# default title and size if any of the constructor args are None.  This
# mirrors PH07's Window class behaviour.
#
# All lv.* calls are deferred until LvglDisplay is constructed so that
# `import picolet_ui` works on cli/webview variants (the lvgl C module
# does not exist there and the import would otherwise fail).

import sys


_DEFAULT_WINDOW_TITLE = "picolet"
_DEFAULT_WINDOW_SIZE = (800, 600)


def _load_window_config(rom_path="/rom/picolet.toml"):
    """Read [window] from /rom/picolet.toml; return (title, width, height)."""
    title = _DEFAULT_WINDOW_TITLE
    width, height = _DEFAULT_WINDOW_SIZE
    try:
        with open(rom_path, "r") as fh:
            text = fh.read()
    except OSError:
        return title, width, height
    from ._toml import loads
    parsed = loads(text)
    window = parsed.get("window") or {}
    if "title" in window and isinstance(window["title"], str):
        title = window["title"]
    sz = window.get("size")
    if isinstance(sz, list) and len(sz) == 2:
        try:
            width = int(sz[0])
            height = int(sz[1])
        except (TypeError, ValueError):
            pass
    return title, width, height


class LvglDisplay:
    """SDL2-backed LVGL display.  Window opens on construction.

    Mirrors PH07's Window class shape: the window opens immediately
    (no lazy-show step) so the asyncio pump can take over without an
    extra round-trip.  PH11 closes FR-LV-1's 'desktop window' clause
    via this class — gate 5 exercises it under xvfb.
    """

    def __init__(self, title=None, width=None, height=None):
        import lvgl as lv

        if title is None or width is None or height is None:
            cfg_title, cfg_w, cfg_h = _load_window_config()
            if title is None:
                title = cfg_title
            if width is None:
                width = cfg_w
            if height is None:
                height = cfg_h

        self.title = title
        self.width = width
        self.height = height

        # lv.init() is idempotent in v9; calling it twice is benign but
        # we still guard against re-entry.
        if not getattr(lv, "_picolet_inited", False):
            lv.init()
            try:
                lv._picolet_inited = True
            except (AttributeError, TypeError):
                # Read-only namespace on some MicroPython builds; harmless.
                pass

        # Create the SDL2-backed display.  The lv_binding_micropython
        # SDL driver exposes itself as lv.sdl_window_create (or via the
        # SDL module per the binding's driver/SDL split).  Probe both
        # surfaces — older binding builds expose the function on lv
        # directly, newer ones expose it on lv.SDL or lv.sdl.
        self._display = None
        if hasattr(lv, "sdl_window_create"):
            self._display = lv.sdl_window_create(width, height)
            if hasattr(lv, "sdl_window_set_title"):
                lv.sdl_window_set_title(self._display, title)
        else:
            raise RuntimeError(
                "picolet_ui._lvgl: lv_binding_micropython does not expose "
                "sdl_window_create.  Check that LV_USE_SDL=1 in lv_conf.h "
                "and that libsdl2-dev was present at runtime build time."
            )

        # Log the window config the same way PH07 does so the gate
        # harness can grep stderr for it.
        sys.stderr.write(
            "window: title={} size={}x{}\n".format(title, width, height)
        )

    @property
    def display(self):
        return self._display


def run(transport=None, main=None):
    """Top-level lvgl-variant entry point: open display, enter pump loop.

    Mirrors PH07's picolet_ui.run for the webview path but with the
    LvglDisplay and the _lvgl_pump coroutine.  If `transport` is None,
    constructs an InProcessTransport.pair() and wires both sides into
    the dispatcher (FR-LV-4).
    """
    from . import _loop
    if transport is None:
        from picolet._transport import InProcessTransport
        a, b = InProcessTransport.pair()
        # The user's @picolet.command handlers register against the
        # dispatcher; the dispatcher reads from one endpoint and
        # writes to it.  For a single-process app the second endpoint
        # is the "peer" that user-side lvgl event handlers will
        # picolet.invoke into.  PH11 wires the *first* endpoint into the
        # dispatcher and stores the second on picolet for the user to
        # access (see picolet_ui.__init__ for the convenience binding).
        transport = a
        try:
            import picolet as _picolet
            _picolet._inprocess_peer = b
        except ImportError:
            pass

    # The display opens here (not lazily) so the SDL2 window is up
    # before the pump's first lv.task_handler() call.
    LvglDisplay()
    return _loop.run(transport, main=main, pump=_loop._lvgl_pump)
