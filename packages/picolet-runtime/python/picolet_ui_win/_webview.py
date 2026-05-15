# picolet_ui_win._webview — WebView2 + WebviewTransport (PH10).
#
# Windows mirror of picolet_ui._webview.  Inbound JS->Python flows through
# the picolet_webview2 C overlay's ring buffer (drained per pump tick by
# _loop._win_pump).  Outbound Python->JS flows through ExecuteScript
# via the C overlay.
#
# Side-effect lifecycle (matches PH07's pattern):
#   * Module import: no COM, no LoadLibrary.
#   * First Webview() ctor: triggers loader extract + LoadLibraryW +
#     CoInitializeEx(STA) + CreateCoreWebView2Environment +
#     CreateCoreWebView2Controller + bridge-JS injection.

import json
import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False


_BRIDGE_PATH = "/rom/picolet/picolet-bridge.js"
_LOADER_DLL_PATH = "/rom/picolet/WebView2Loader.dll"

# Module-state: only one Webview per process (v1 is single-window) so
# the loader-DLL bytes / controller are cached at module scope.
_loader_loaded = False
_com_initialised = False
_env = None


def _ensure_loader_loaded():
    """One-time: extract WebView2Loader.dll from romfs and LoadLibraryW it.

    The DLL bytes live at /rom/picolet/WebView2Loader.dll in the romfs;
    `picolet build` copies them in from the picolet-runtime package data.
    A missing DLL produces a clear RuntimeError naming the bundling
    step — the runtime cannot proceed without the loader.
    """
    global _loader_loaded
    if _loader_loaded:
        return
    from . import _win_ffi
    try:
        with open(_LOADER_DLL_PATH, "rb") as fh:
            dll_bytes = fh.read()
    except OSError as e:
        raise RuntimeError(
            "picolet_ui_win: WebView2Loader.dll not found in romfs at {}. "
            "Run `picolet build --target windows-x64` to populate the loader "
            "DLL (it must be present in the app romfs at build time). "
            "Underlying error: {}".format(_LOADER_DLL_PATH, e)
        )
    handle = _win_ffi.picolet_wv2_load_loader_dll(dll_bytes, len(dll_bytes))
    if not handle:
        err = _win_ffi.picolet_wv2_last_error()
        raise RuntimeError(
            "picolet_ui_win: failed to load WebView2Loader.dll "
            "(HRESULT 0x{:08x}). The bundled loader may be corrupt; "
            "rebuild via `picolet build`.".format(err & 0xFFFFFFFF)
        )
    _loader_loaded = True


def _ensure_com_initialised():
    global _com_initialised
    if _com_initialised:
        return
    from . import _win_ffi
    rc = _win_ffi.picolet_wv2_init_com()
    if rc != 0:
        raise RuntimeError(
            "picolet_ui_win: CoInitializeEx failed (HRESULT 0x{:08x}). "
            "Some host process initialised COM as MTA before picolet ran; "
            "WebView2 requires STA.".format(rc & 0xFFFFFFFF)
        )
    _com_initialised = 1


def _ensure_environment():
    global _env
    if _env is not None:
        return _env
    from . import _win_ffi
    _ensure_loader_loaded()
    _ensure_com_initialised()
    # 30 s timeout — environment creation should be sub-second in
    # practice; the high ceiling tolerates first-run host warmups.
    env = _win_ffi.picolet_wv2_create_environment_blocking(30000)
    if not env:
        err = _win_ffi.picolet_wv2_last_error()
        if (err & 0xFFFFFFFF) == 0x80070002:  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
            raise RuntimeError(
                "Edge WebView2 Runtime not installed; install from "
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
        raise RuntimeError(
            "picolet_ui_win: CreateCoreWebView2Environment failed "
            "(HRESULT 0x{:08x})".format(err & 0xFFFFFFFF)
        )
    _env = env
    return env


class Webview:
    """A WebView2 controller hosted inside a picolet_ui_win.Window."""

    def __init__(self, window, transport=None):
        from . import _win_ffi
        env = _ensure_environment()
        ctrl = _win_ffi.picolet_wv2_create_controller_blocking(
            env, window.handle, 30000,
        )
        if not ctrl:
            err = _win_ffi.picolet_wv2_last_error()
            raise RuntimeError(
                "picolet_ui_win: CreateCoreWebView2Controller failed "
                "(HRESULT 0x{:08x})".format(err & 0xFFFFFFFF)
            )
        self._controller = ctrl
        self._window = window
        window.attach_controller(ctrl)

        # Inject the picolet-bridge-js IIFE at DocumentCreated so
        # window.picolet is alive before any user <script> tag runs
        # (FR-WV-4).  The bundle was placed at /rom/picolet/picolet-bridge.js
        # by `picolet build`'s _copy_bridge_js step.
        try:
            with open(_BRIDGE_PATH, "r") as fh:
                bridge_src = fh.read()
        except OSError:
            bridge_src = ""
        if bridge_src:
            rc = _win_ffi.picolet_wv2_add_script_to_execute_on_document_created(
                ctrl, bridge_src.encode("utf-8"), 10000,
            )
            if rc != 0:
                sys.stderr.write(
                    "picolet_ui_win: AddScriptToExecuteOnDocumentCreated "
                    "failed (HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
                )

        # Bind transport so the inbound handler has somewhere to deliver.
        self.transport = transport if transport is not None else WebviewTransport(self)
        if self.transport._webview is None:
            self.transport._webview = self

        # Register the persistent WebMessageReceived handler.
        rc = _win_ffi.picolet_wv2_register_inbound_handler(ctrl)
        if rc != 0:
            sys.stderr.write(
                "picolet_ui_win: register_inbound_handler failed "
                "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
            )

    def navigate_to_string(self, html):
        from . import _win_ffi
        rc = _win_ffi.picolet_wv2_navigate_to_string(
            self._controller, html.encode("utf-8"),
        )
        if rc != 0:
            sys.stderr.write(
                "picolet_ui_win: NavigateToString failed "
                "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
            )

    def execute_script(self, js):
        from . import _win_ffi
        rc = _win_ffi.picolet_wv2_execute_script(
            self._controller, js.encode("utf-8"),
        )
        if rc != 0:
            sys.stderr.write(
                "picolet_ui_win: ExecuteScript failed "
                "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
            )

    @property
    def controller(self):
        return self._controller

    def close(self):
        if self._controller is None:
            return
        from . import _win_ffi
        _win_ffi.picolet_wv2_close_controller(self._controller)
        self._controller = None


# ---------------------------------------------------------------------------
# WebviewTransport — duck-type-compatible with the dispatcher's Transport.
# ---------------------------------------------------------------------------


class WebviewTransport:
    """A picolet.Transport over WebView2's postMessage channel.

    Inbound: drained from the picolet_webview2 C overlay's ring buffer by
    _loop._win_pump on every pump tick.  Each raw JSON string is passed
    to _deliver_raw(), which parses + enqueues on _inbox and signals
    _evt; the next recv() returns the head.

    Outbound: ExecuteScript("window.__picolet_recv(json)") via the C overlay.
    """

    def __init__(self, webview=None):
        self._webview = webview
        self._inbox = []
        self._closed = False
        self._evt = None
        self._raw_hook = None
        self.recv_count = 0
        self.send_count = 0

    def _deliver_raw(self, json_str):
        if self._raw_hook is not None:
            try:
                self._raw_hook(json_str)
            except BaseException as e:
                sys.stderr.write(
                    "picolet_ui_win: _raw_hook raised: {}\n".format(e)
                )
        try:
            msg = json.loads(json_str)
        except (ValueError, Exception) as e:
            sys.stderr.write(
                "picolet_ui_win: malformed JSON from postMessage: {}\n".format(e)
            )
            return
        self._inbox.append(msg)
        evt = self._evt
        if evt is not None:
            evt.set()

    async def recv(self):
        while not self._closed:
            if self._inbox:
                self.recv_count += 1
                return self._inbox.pop(0)
            if not _HAVE_ASYNCIO:
                raise RuntimeError(
                    "WebviewTransport.recv requires asyncio"
                )
            self._evt = asyncio.Event()
            try:
                await self._evt.wait()
            finally:
                self._evt = None
        return None

    async def send(self, msg):
        if self._closed:
            return
        if self._webview is None:
            self._outbox_append(msg)
            return
        encoded = json.dumps(msg)
        js = "window.__picolet_recv(" + json.dumps(encoded) + ")"
        self._webview.execute_script(js)
        self._outbox_append(msg)
        self.send_count += 1

    async def close(self):
        self._closed = True
        evt = self._evt
        if evt is not None:
            evt.set()

    @property
    def closed(self):
        return self._closed

    def _outbox_append(self, msg):
        if not hasattr(self, "_outbox"):
            self._outbox = []
        self._outbox.append(msg)

    def drain_outbox(self):
        return list(getattr(self, "_outbox", []))
