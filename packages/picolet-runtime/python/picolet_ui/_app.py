# picolet_ui._app — convenience factory wiring Window + Webview + Transport.
#
# PH07.  A user's main.py typically does:
#
#     import picolet_ui
#     picolet_ui.run()        # auto-load config, open window, hand off
#
# This module implements that single entry point.  It reads
# /rom/picolet.toml's [ui] table for `root` and `index`, constructs the
# URI as file:///rom/<root>/<index>, opens a Window, embeds a Webview,
# constructs a WebviewTransport, and hands off to picolet.run with the
# pump task alongside.

import sys


_DEFAULT_UI_ROOT = "ui"
_DEFAULT_UI_INDEX = "index.html"


def _load_ui_config(rom_path="/rom/picolet.toml"):
    cfg = {"root": _DEFAULT_UI_ROOT, "index": _DEFAULT_UI_INDEX}
    try:
        with open(rom_path, "r") as fh:
            text = fh.read()
    except OSError:
        return cfg
    from ._toml import loads
    parsed = loads(text)
    ui = parsed.get("ui") or {}
    if "root" in ui:
        cfg["root"] = ui["root"]
    if "index" in ui:
        cfg["index"] = ui["index"]
    return cfg


def build_root_uri():
    """Return the file:// URI for the romfs-embedded index document (FR-WV-2)."""
    cfg = _load_ui_config()
    return "file:///rom/" + cfg["root"] + "/" + cfg["index"]


def _read_rom_html(rom_path):
    """Read /rom/<root>/<index> through the MicroPython VFS and return bytes.

    WebKit cannot load file:///rom/... directly because /rom is a VFS
    overlay inside the runtime process, not visible to the kernel.
    We read the document via Python and pass it to
    webkit_web_view_load_html with a synthetic base URI so relative
    asset references (CSS, JS, images) resolve through Python-side
    interception layers PH08+ may add.
    """
    with open(rom_path, "r") as fh:
        return fh.read()


class Application:
    """One-shot wiring of Window + Webview + WebviewTransport.

    Construct with no args to use the romfs config.  `.run(main=...)`
    enters the asyncio loop with the dispatcher and pump tasks.
    """

    def __init__(self, title=None, size=None, resizable=None,
                 root_uri=None):
        from ._window import Window
        from ._webview import Webview, WebviewTransport
        from . import _gtk_ffi

        self.window = Window(title=title, size=size, resizable=resizable)
        self.transport = WebviewTransport()
        if root_uri is None:
            # Default: read the romfs-embedded index.html and inject via
            # webkit_web_view_load_html.  The base_uri tells WebKit how
            # to resolve relative URLs in the page; we synthesise one
            # under file:// so document.location / window.location have
            # plausible values for the JS shim.
            cfg = _load_ui_config()
            rom_doc = "/rom/" + cfg["root"] + "/" + cfg["index"]
            try:
                html = _read_rom_html(rom_doc)
            except OSError as e:
                import sys
                sys.stderr.write(
                    "picolet_ui: failed to read {}: {}\n".format(rom_doc, e)
                )
                raise
            base_uri = "file:///picolet/" + cfg["root"] + "/"
            self.webview = Webview(
                self.window, root_uri=None, transport=self.transport
            )
            _gtk_ffi.webkit_web_view_load_html(
                self.webview.view, html, base_uri
            )
        else:
            self.webview = Webview(
                self.window, root_uri=root_uri, transport=self.transport
            )
        self.window.show()

    def run(self, main=None):
        from ._loop import run as _run
        return _run(self.transport, main=main)


def run(main=None):
    """Top-level convenience: build an Application and run it."""
    app = Application()
    return app.run(main=main)
