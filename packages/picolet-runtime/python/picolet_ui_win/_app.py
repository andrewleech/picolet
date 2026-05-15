# picolet_ui_win._app — convenience factory wiring Window + Webview + Transport.
#
# Top-level entry point mirroring picolet_ui._app.  A user's main.py
# typically does:
#
#     import picolet_ui_win
#     picolet_ui_win.run()        # auto-load config, open window, hand off
#
# We load /rom/<ui.root>/<ui.index> via the MicroPython VFS and feed it
# to WebView2 via NavigateToString — WebView2 cannot resolve
# file:///rom/ paths because /rom is an in-process VFS overlay, not a
# kernel-visible filesystem (same constraint as WebKit on Linux).


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


def _read_rom_html(rom_path):
    with open(rom_path, "r") as fh:
        return fh.read()


class Application:
    """One-shot wiring of Window + Webview + WebviewTransport.

    Construct with no args to use the romfs config; `.run(main=...)`
    enters the asyncio loop with the dispatcher and pump tasks.
    """

    def __init__(self, title=None, size=None, resizable=None):
        from ._window import Window
        from ._webview import Webview, WebviewTransport

        self.window = Window(title=title, size=size, resizable=resizable)
        self.transport = WebviewTransport()
        self.webview = Webview(self.window, transport=self.transport)

        cfg = _load_ui_config()
        rom_doc = "/rom/" + cfg["root"] + "/" + cfg["index"]
        try:
            html = _read_rom_html(rom_doc)
        except OSError as e:
            import sys
            sys.stderr.write(
                "picolet_ui_win: failed to read {}: {}\n".format(rom_doc, e)
            )
            raise
        self.webview.navigate_to_string(html)
        self.window.show()

    def run(self, main=None):
        from ._loop import run as _run
        return _run(self.transport, main=main)


def run(main=None):
    app = Application()
    return app.run(main=main)
