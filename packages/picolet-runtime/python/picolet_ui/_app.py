# picolet_ui._app — convenience factory wiring Window + Webview + Transport.
#
# PH07 / PH10.  A user's main.py typically does:
#
#     import picolet_ui
#     picolet_ui.run()        # auto-load config, open window, hand off
#
# This module implements that single entry point.  It reads
# /rom/picolet.toml's [ui] table for `root` and `index`, constructs the
# URI as file:///rom/<root>/<index>, opens a Window, embeds a Webview,
# constructs a WebviewTransport, and hands off to picolet.run with the
# pump task alongside.
#
# Both platforms read /rom/<root>/<index> through the MicroPython VFS
# and pass the HTML body to the webview backend (load_html on WebKit,
# NavigateToString on WebView2).  Neither WebKit nor WebView2 can
# resolve file:///rom/... directly because /rom is a VFS overlay
# inside the runtime process, not visible to the kernel.
#
# Linux asset loading (picolet:// URI scheme):
#   webkit_web_view_load_html supplies HTML content directly but needs a
#   base URI so relative sub-resource references (style.css, app.js,
#   images) can be resolved.  A synthetic file:// base URI does not work
#   because /rom is an in-process VFS overlay — the kernel has no such
#   path, so WebKit's sub-resource fetcher returns 404 for every relative
#   URL.
#
#   Instead, a custom "picolet" URI scheme is registered on the
#   WebKitWebContext before the view loads.  The scheme handler reads
#   sub-resources from /rom/<path> through the VFS and returns them via
#   webkit_uri_scheme_request_finish.  The HTML is loaded with
#   base_uri = "picolet:///<root>/" (three slashes: empty host, path =
#   /<root>/) so relative URLs become "picolet:///<root>/style.css" etc.,
#   and webkit_uri_scheme_request_get_path returns "/<root>/style.css",
#   which the scheme handler maps to "/rom/<root>/style.css".

import os
import sys


_DEFAULT_UI_ROOT = "ui"
_DEFAULT_UI_INDEX = "index.html"


# ---------------------------------------------------------------------------
# MIME type helper
# ---------------------------------------------------------------------------

_MIME_MAP = {
    ".html": "text/html",
    ".htm":  "text/html",
    ".css":  "text/css",
    ".js":   "application/javascript",
    ".json": "application/json",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
    ".otf":  "font/otf",
    ".txt":  "text/plain",
    ".xml":  "application/xml",
    ".webp": "image/webp",
}


def _mime_for_path(path):
    """Return MIME type based on file extension; default application/octet-stream."""
    dot = path.rfind(".")
    if dot >= 0:
        ext = path[dot:].lower()
        if ext in _MIME_MAP:
            return _MIME_MAP[ext]
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# picolet:// URI scheme registration (Linux / WebKitGTK)
# ---------------------------------------------------------------------------

# Stable reference: keeps the FFI callback alive for the process lifetime.
_scheme_callback = None


def _register_picolet_scheme(gtk_ffi):
    """Register the picolet:// URI scheme on the default WebKitWebContext.

    The scheme handler resolves "picolet://<path>" to "/rom/<path>" through
    the MicroPython VFS, reads the file bytes, and finishes the WebKit
    request with the data and a guessed MIME type.

    Must be called before any WebKitWebView is created.  WebKitGTK
    requires scheme registration before the first view is instantiated.

    Threading: the callback fires from within gtk_main_iteration_do on the
    same thread as the asyncio event loop (Option C design; sandbox
    disabled → single-process).  lock=False is correct for the same reason
    as the script-message-received closure.
    """
    global _scheme_callback
    import ffi

    if gtk_ffi.g_memory_input_stream_new_from_data is None:
        sys.stderr.write(
            "picolet_ui: g_memory_input_stream_new_from_data not available; "
            "sub-asset loading will not work\n"
        )
        return

    def _on_uri_request(request_p, user_data_p):
        """Serve a picolet:// sub-resource from /rom/<path>."""
        try:
            path_p = gtk_ffi.webkit_uri_scheme_request_get_path(request_p)
            path = gtk_ffi.ffi_string(path_p)   # e.g. "/ui/style.css"
            rom_path = "/rom" + path             # e.g. "/rom/ui/style.css"
            try:
                with open(rom_path, "rb") as fh:
                    data = fh.read()
            except OSError as e:
                sys.stderr.write(
                    "picolet_ui: picolet:// 404 {}: {}\n".format(rom_path, e)
                )
                # Respond with an empty body.  WebKit requires either
                # finish or finish_error; finish_error requires a non-NULL
                # GError which is expensive to construct via FFI.  An empty
                # 0-length stream with a neutral content-type is the
                # lowest-complexity correct finish for a missing asset.
                empty = b""
                stream = gtk_ffi.g_memory_input_stream_new_from_data(
                    empty, 0, 0
                )
                if stream:
                    gtk_ffi.webkit_uri_scheme_request_finish(
                        request_p, stream, 0, "application/octet-stream"
                    )
                    gtk_ffi.g_object_unref(stream)
                return
            mime = _mime_for_path(rom_path)
            stream = gtk_ffi.g_memory_input_stream_new_from_data(
                data, len(data), 0
            )
            if not stream:
                sys.stderr.write(
                    "picolet_ui: g_memory_input_stream_new_from_data returned NULL "
                    "for {}\n".format(rom_path)
                )
                return
            gtk_ffi.webkit_uri_scheme_request_finish(
                request_p, stream, len(data), mime
            )
            gtk_ffi.g_object_unref(stream)
        except BaseException as exc:
            sys.stderr.write(
                "picolet_ui: _on_uri_request raised: {}\n".format(exc)
            )

    # Build the libffi callback: void (*)(WebKitURISchemeRequest *, gpointer)
    cb = ffi.callback("v", _on_uri_request, "pp", lock=False)
    _scheme_callback = cb  # keep alive

    ctx = gtk_ffi.webkit_web_context_get_default()
    gtk_ffi.webkit_web_context_register_uri_scheme(
        ctx, "picolet", cb, 0, 0
    )


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
    """Return the picolet:// URI for the romfs-embedded index document (FR-WV-2).

    On Linux the picolet:// scheme is served by the URI scheme handler
    registered in Application.__init__.  On Windows NavigateToString is
    used and this function is not called.
    """
    cfg = _load_ui_config()
    return "picolet:///" + cfg["root"] + "/" + cfg["index"]


def _read_rom_html(rom_path):
    """Read /rom/<root>/<index> through the MicroPython VFS and return bytes."""
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

        self.window = Window(title=title, size=size, resizable=resizable)
        self.transport = WebviewTransport()

        # FR-VUE-2 / D1: PICOLET_DEV_URL is set by `picolet dev` when a non-vanilla
        # frontend (e.g. Vue) is active. Load from the Vite dev server URL
        # instead of the romfs picolet:// path. Behaviour is unchanged when
        # PICOLET_DEV_URL is unset (production and vanilla dev builds).
        _dev_url = os.getenv("PICOLET_DEV_URL")

        if sys.platform == "win32":
            # WebView2 cannot resolve file:///rom/ paths — read the HTML
            # via the VFS and hand it to NavigateToString.  When a dev URL
            # is set, inject a redirect page so the WebView2 navigates to
            # the Vite dev server (R3: no picolet_wv2_navigate export yet;
            # redirect HTML is the best-effort Windows dev path).
            self.webview = Webview(self.window, transport=self.transport)
            if _dev_url:
                redirect_html = (
                    "<!doctype html><html><head>"
                    "<meta http-equiv='refresh' content='0; url={}'>"
                    "</head><body></body></html>".format(_dev_url)
                )
                self.webview.navigate_to_string(redirect_html)
            else:
                cfg = _load_ui_config()
                rom_doc = "/rom/" + cfg["root"] + "/" + cfg["index"]
                try:
                    html = _read_rom_html(rom_doc)
                except OSError as e:
                    sys.stderr.write(
                        "picolet_ui: failed to read {}: {}\n".format(rom_doc, e)
                    )
                    raise
                self.webview.navigate_to_string(html)
        else:
            # WebKit on Linux: /rom is an in-process VFS overlay, not
            # visible to the kernel.  Read the index.html through Python
            # and inject via webkit_web_view_load_html.  Sub-resources
            # (CSS, JS, images) are served through the picolet:// URI
            # scheme registered below, which reads from /rom/<path>
            # through the same VFS.  The base URI is set to
            # "picolet://<root>/" so relative URLs resolve correctly.
            #
            # When PICOLET_DEV_URL is set, skip the romfs HTML load and
            # navigate directly to the dev server URL via load_uri.
            from . import _gtk_ffi
            if _dev_url:
                # Register picolet:// scheme anyway (scheme registration must
                # precede view creation on WebKitGTK).
                _register_picolet_scheme(_gtk_ffi)
                self.webview = Webview(
                    self.window, root_uri=None, transport=self.transport
                )
                _gtk_ffi.webkit_web_view_load_uri(self.webview.view, _dev_url)
            elif root_uri is None:
                cfg = _load_ui_config()
                # Register the picolet:// scheme before the view is created
                # (WebKitGTK requires scheme registration before any view).
                _register_picolet_scheme(_gtk_ffi)
                rom_doc = "/rom/" + cfg["root"] + "/" + cfg["index"]
                try:
                    html = _read_rom_html(rom_doc)
                except OSError as e:
                    sys.stderr.write(
                        "picolet_ui: failed to read {}: {}\n".format(rom_doc, e)
                    )
                    raise
                base_uri = "picolet:///" + cfg["root"] + "/"
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
