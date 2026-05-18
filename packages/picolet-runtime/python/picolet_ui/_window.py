# picolet_ui._window — top-level window wrapper.
#
# Cross-platform: the GTK 3 backend (PH07), Win32 backend (PH10), and the
# NSWindow backend (PH25, macOS) share one public surface.  Selection is
# by sys.platform at import time — the runtime variant only ships the
# relevant FFI module.
#
# Linux (sys.platform == 'linux'):
#   GTK 3 top-level window.  PH07 introduced this; PH11's LVGL renderer
#   can reuse it (SDL2 / LVGL substitute) by replacing the GTK
#   specifics.
#
# Windows (sys.platform == 'win32'):
#   Win32 top-level window backed by the picolet_webview2 C overlay.
#   The C overlay encapsulates the WNDCLASSEXW registration and the
#   WindowProc callback (which handles WM_SIZE / WM_DESTROY) so Python
#   sees one HWND-shaped pointer.
#
# macOS (sys.platform == 'darwin'):
#   NSWindow created via the picolet_webview_mac C overlay.  The overlay
#   uses objc_msgSend (no .m files) and returns an opaque NSWindow*.
#
# Reading [window] from /rom/picolet.toml is identical on all platforms
# (same TOML subset, same defaults).

import sys


_DEFAULT_TITLE = "picolet"
_DEFAULT_W = 800
_DEFAULT_H = 600
_DEFAULT_RESIZABLE = True


def load_window_config(rom_path="/rom/picolet.toml"):
    """Load [window] from the romfs-embedded picolet.toml; defaults otherwise.

    Returns a dict with keys ``title``, ``size`` (2-list), ``resizable``.
    Missing keys are filled with defaults.
    """
    cfg = {
        "title": _DEFAULT_TITLE,
        "size": [_DEFAULT_W, _DEFAULT_H],
        "resizable": _DEFAULT_RESIZABLE,
    }
    try:
        with open(rom_path, "r") as fh:
            text = fh.read()
    except OSError:
        return cfg
    from ._toml import loads
    parsed = loads(text)
    window = parsed.get("window") or {}
    for k in cfg:
        if k in window:
            cfg[k] = window[k]
    # Normalise size — accept [w, h] or refuse to misbehave.
    sz = cfg["size"]
    if not (isinstance(sz, list) and len(sz) == 2 and
            isinstance(sz[0], int) and isinstance(sz[1], int)):
        cfg["size"] = [_DEFAULT_W, _DEFAULT_H]
    return cfg


if sys.platform == "darwin":

    # -----------------------------------------------------------------
    # macOS backend (NSWindow via picolet_webview_mac C overlay, PH25)
    # -----------------------------------------------------------------

    # Module-level flag: picolet_wkwv_init must be called exactly once.
    _mac_initialised = False


    def _ensure_mac_initialised():
        global _mac_initialised
        if _mac_initialised:
            return
        from . import _mac_ffi
        rc = _mac_ffi.picolet_wkwv_init()
        if rc != 0:
            raise RuntimeError("picolet_ui: picolet_wkwv_init failed")
        _mac_initialised = True


    class Window:
        """NSWindow created via the picolet_webview_mac C overlay."""

        def __init__(self, title=None, size=None, resizable=None, config=None):
            from . import _mac_ffi
            cfg = config if config is not None else load_window_config()
            self.title = title if title is not None else cfg["title"]
            self.size = list(size) if size is not None else list(cfg["size"])
            self.resizable = (
                resizable if resizable is not None else cfg["resizable"]
            )
            _ensure_mac_initialised()
            win = _mac_ffi.picolet_wkwv_create_window(
                self.title.encode("utf-8"),
                int(self.size[0]), int(self.size[1]),
            )
            if not win:
                raise RuntimeError(
                    "picolet_ui: picolet_wkwv_create_window returned NULL"
                )
            self._win = win
            sys.stderr.write(
                "window: title={} size={}x{} resizable={}\n".format(
                    self.title, self.size[0], self.size[1], self.resizable
                )
            )

        def show(self):
            from . import _mac_ffi
            _mac_ffi.picolet_wkwv_show_window(self._win, 1)

        def hide(self):
            from . import _mac_ffi
            _mac_ffi.picolet_wkwv_show_window(self._win, 0)

        def close(self):
            if self._win is None:
                return
            from . import _mac_ffi
            _mac_ffi.picolet_wkwv_destroy_window(self._win)
            self._win = None

        @property
        def handle(self):
            return self._win

        @property
        def width(self):
            return int(self.size[0])

        @property
        def height(self):
            return int(self.size[1])


elif sys.platform == "win32":

    # -----------------------------------------------------------------
    # Windows backend
    # -----------------------------------------------------------------

    class Window:
        """Win32 top-level window backed by the picolet_webview2 C overlay.

        Construction does NOT initialise COM or load WebView2Loader.dll;
        those side effects happen at first Webview() construction.  Window
        creation itself is plain user32 — no WebView2 contact yet.
        """

        def __init__(self, title=None, size=None, resizable=None, config=None):
            from . import _win_ffi
            cfg = config if config is not None else load_window_config()
            self.title = title if title is not None else cfg["title"]
            self.size = list(size) if size is not None else list(cfg["size"])
            self.resizable = (
                resizable if resizable is not None else cfg["resizable"]
            )
            title_b = self.title.encode("utf-8")
            hwnd = _win_ffi.picolet_wv2_create_window(
                title_b, int(self.size[0]), int(self.size[1]),
                1 if self.resizable else 0,
            )
            if not hwnd:
                err = _win_ffi.picolet_wv2_last_error()
                raise RuntimeError(
                    "picolet_ui: picolet_wv2_create_window failed (HRESULT 0x{:08x})"
                    .format(err & 0xFFFFFFFF)
                )
            self._hwnd = hwnd
            # FR-WV-3 verification: the gate-6 driver greps for this line.
            sys.stderr.write(
                "window: title={} size={}x{} resizable={}\n".format(
                    self.title, self.size[0], self.size[1], self.resizable
                )
            )

        def attach_controller(self, controller_handle):
            """Bind a WebView2 controller pointer so WM_SIZE forwards to it."""
            from . import _win_ffi
            _win_ffi.picolet_wv2_window_attach_controller(
                self._hwnd, controller_handle
            )

        def show(self):
            from . import _win_ffi
            _win_ffi.picolet_wv2_show_window(self._hwnd, 1)

        def hide(self):
            from . import _win_ffi
            _win_ffi.picolet_wv2_show_window(self._hwnd, 0)

        def close(self):
            if self._hwnd is None:
                return
            from . import _win_ffi
            _win_ffi.picolet_wv2_destroy_window(self._hwnd)
            self._hwnd = None

        @property
        def handle(self):
            return self._hwnd

else:

    # -----------------------------------------------------------------
    # GTK 3 backend (linux / fallback)
    # -----------------------------------------------------------------

    # Module-level singleton: gtk_init must be called exactly once per
    # process.  Subsequent calls are no-ops but the flag here keeps us
    # from accidentally racing on the libffi-side state.
    _gtk_initialised = False


    def _ensure_gtk_initialised():
        global _gtk_initialised
        if _gtk_initialised:
            return
        from . import _gtk_ffi
        _gtk_ffi.gtk_init(0, 0)
        _gtk_initialised = True


    class Window:
        """A single GTK 3 top-level window.

        Construction does not call gtk_init; the first instance triggers
        gtk_init lazily.  This lets `import picolet_ui` succeed on a host
        without DISPLAY (gate 3) — only `Window()` instantiation requires X.

        The window is not shown until .show() is called.  PH07 expects
        .show() to happen after the WebKitWebView has been embedded and
        the URI has been set.
        """

        def __init__(self, title=None, size=None, resizable=None, config=None):
            cfg = config if config is not None else load_window_config()
            self.title = title if title is not None else cfg["title"]
            self.size = list(size) if size is not None else list(cfg["size"])
            self.resizable = (
                resizable if resizable is not None else cfg["resizable"]
            )
            _ensure_gtk_initialised()
            from . import _gtk_ffi
            # GTK_WINDOW_TOPLEVEL = 0 (GtkWindowType enum)
            self._win = _gtk_ffi.gtk_window_new(0)
            if not self._win:
                raise RuntimeError("picolet_ui: gtk_window_new returned NULL")
            _gtk_ffi.gtk_window_set_title(self._win, self.title)
            _gtk_ffi.gtk_window_set_default_size(
                self._win, int(self.size[0]), int(self.size[1])
            )
            _gtk_ffi.gtk_window_set_resizable(
                self._win, 1 if self.resizable else 0
            )
            # FR-WV-3 verification — write a single line to stderr that
            # the gate-6 driver greps for.
            sys.stderr.write(
                "window: title={} size={}x{} resizable={}\n".format(
                    self.title, self.size[0], self.size[1], self.resizable
                )
            )

        def add(self, widget):
            """Embed a GtkWidget * (e.g. a WebKitWebView) inside this window."""
            from . import _gtk_ffi
            _gtk_ffi.gtk_container_add(self._win, widget)

        def show(self):
            from . import _gtk_ffi
            _gtk_ffi.gtk_widget_show_all(self._win)

        def close(self):
            if self._win is None:
                return
            from . import _gtk_ffi
            _gtk_ffi.gtk_widget_destroy(self._win)
            self._win = None

        @property
        def handle(self):
            return self._win
