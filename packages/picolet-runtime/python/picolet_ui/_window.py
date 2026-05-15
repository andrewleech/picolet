# picolet_ui._window — GTK 3 top-level window wrapper.
#
# PH07.  Reads [window] from /rom/picolet.toml (or uses defaults) and
# opens a single GtkWindow with title + size + resizable applied.
#
# Renderer-agnostic intent: PH11's LVGL renderer can reuse this same
# Window abstraction (SDL2 / LVGL substitute) by replacing the GTK
# specifics.

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


# Module-level singleton: gtk_init must be called exactly once per
# process.  Subsequent calls are no-ops but the flag here keeps us from
# accidentally racing on the libffi-side state.
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
        # FR-WV-3 verification — write a single line to stderr that the
        # gate-6 driver greps for.
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
