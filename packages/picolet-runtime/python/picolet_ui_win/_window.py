# picolet_ui_win._window — Win32 top-level window (PH10).
#
# Mirrors picolet_ui._window's surface; the C overlay encapsulates the
# WNDCLASSEXW registration and the WindowProc callback (which handles
# WM_SIZE / WM_DESTROY) so Python sees one HWND-shaped pointer.
#
# Reading [window] from /rom/picolet.toml is identical to the Linux side
# (same TOML subset, same defaults).

import sys


_DEFAULT_TITLE = "picolet"
_DEFAULT_W = 800
_DEFAULT_H = 600
_DEFAULT_RESIZABLE = True


def load_window_config(rom_path="/rom/picolet.toml"):
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
    sz = cfg["size"]
    if not (isinstance(sz, list) and len(sz) == 2 and
            isinstance(sz[0], int) and isinstance(sz[1], int)):
        cfg["size"] = [_DEFAULT_W, _DEFAULT_H]
    return cfg


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
                "picolet_ui_win: picolet_wv2_create_window failed (HRESULT 0x{:08x})"
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
        _win_ffi.picolet_wv2_window_attach_controller(self._hwnd, controller_handle)

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
