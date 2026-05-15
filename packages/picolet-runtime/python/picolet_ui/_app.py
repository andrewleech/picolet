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


class Application:
    """One-shot wiring of Window + Webview + WebviewTransport.

    Construct with no args to use the romfs config.  `.run(main=...)`
    enters the asyncio loop with the dispatcher and pump tasks.
    """

    def __init__(self, title=None, size=None, resizable=None,
                 root_uri=None):
        from ._window import Window
        from ._webview import Webview, WebviewTransport

        self.window = Window(title=title, size=size, resizable=resizable)
        self.transport = WebviewTransport()
        uri = root_uri if root_uri is not None else build_root_uri()
        self.webview = Webview(self.window, root_uri=uri,
                                transport=self.transport)
        self.window.show()

    def run(self, main=None):
        from ._loop import run as _run
        return _run(self.transport, main=main)


def run(main=None):
    """Top-level convenience: build an Application and run it."""
    app = Application()
    return app.run(main=main)
