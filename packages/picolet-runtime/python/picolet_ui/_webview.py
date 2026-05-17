# picolet_ui._webview — webview wrapper + WebviewTransport.
#
# Cross-platform: the WebKitGTK 4.1 backend (PH07) and the WebView2
# backend (PH10) share one public surface.  Selection is by sys.platform
# at import time — the runtime variant only ships the relevant FFI
# module.
#
# Linux (sys.platform == 'linux'):
#   Embeds a WebKitWebView inside a picolet_ui.Window, loads a file://
#   URI, registers the "picolet" script-message handler.  Inbound
#   JS->Python flows through gtk_main_iteration_do (drained by
#   _loop._gtk_pump); outbound Python->JS through eval_js (webkit
#   evaluate_javascript).
#
# Windows (sys.platform == 'win32'):
#   Hosts a WebView2 controller inside a picolet_ui.Window.  Inbound
#   JS->Python flows through the picolet_webview2 C overlay's ring buffer
#   (drained per pump tick by _loop._win_pump); outbound Python->JS
#   through eval_js (ExecuteScript via the C overlay).
#
# Shared:
#   - Bridge JS (PH08) is injected at document-start.  The bundle is
#     read from /rom/picolet/picolet-bridge.js.
#   - WebviewTransport (PH06 Transport duck-type) is platform-agnostic;
#     it talks to whichever Webview backend via the uniform .eval_js
#     method.
#
# JS-side wire (both platforms):
#   Inbound:  postMessage(JSON.stringify(msg))
#             (window.webkit.messageHandlers.picolet on WebKit;
#              chrome.webview on WebView2)
#   Outbound: window.__picolet_recv(jsonString)

import json
import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False


if sys.platform == "win32":

    # -----------------------------------------------------------------
    # Windows backend (WebView2 via picolet_webview2 C overlay)
    # -----------------------------------------------------------------

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
                "picolet_ui: WebView2Loader.dll not found in romfs at {}. "
                "Run `picolet build --target windows-x64` to populate the loader "
                "DLL (it must be present in the app romfs at build time). "
                "Underlying error: {}".format(_LOADER_DLL_PATH, e)
            )
        handle = _win_ffi.picolet_wv2_load_loader_dll(dll_bytes, len(dll_bytes))
        if not handle:
            err = _win_ffi.picolet_wv2_last_error()
            raise RuntimeError(
                "picolet_ui: failed to load WebView2Loader.dll "
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
                "picolet_ui: CoInitializeEx failed (HRESULT 0x{:08x}). "
                "Some host process initialised COM as MTA before picolet ran; "
                "WebView2 requires STA.".format(rc & 0xFFFFFFFF)
            )
        _com_initialised = True


    def _ensure_environment(extra_browser_args=None):
        global _env
        if _env is not None:
            return _env
        from . import _win_ffi
        _ensure_loader_loaded()
        _ensure_com_initialised()
        # 30 s timeout — environment creation should be sub-second in
        # practice; the high ceiling tolerates first-run host warmups.
        #
        # PH17: extra_browser_args is a UTF-16LE buffer pointer (or NULL=0).
        # We pass it as the first argument; _win_ffi.func("p","..","pi") maps
        # it as a void* (pointer arg). NULL (0) = no extra args = normal path.
        if extra_browser_args is not None:
            import uctypes
            # extra_browser_args is a str; encode to UTF-16LE with NUL terminator.
            utf16 = extra_browser_args.encode("utf-16-le") + b"\x00\x00"
            buf = bytearray(utf16)
            env = _win_ffi.picolet_wv2_create_environment_blocking(
                uctypes.addressof(buf), 30000
            )
        else:
            env = _win_ffi.picolet_wv2_create_environment_blocking(0, 30000)
        if not env:
            err = _win_ffi.picolet_wv2_last_error()
            if (err & 0xFFFFFFFF) == 0x80070002:  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
                raise RuntimeError(
                    "Edge WebView2 Runtime not installed; install from "
                    "https://developer.microsoft.com/microsoft-edge/webview2/"
                )
            raise RuntimeError(
                "picolet_ui: CreateCoreWebView2Environment failed "
                "(HRESULT 0x{:08x})".format(err & 0xFFFFFFFF)
            )
        _env = env
        return env


    class Webview:
        """A WebView2 controller hosted inside a picolet_ui.Window."""

        def __init__(self, window, root_uri=None, transport=None):
            from . import _win_ffi
            # PH17 — PICOLET_TEST_MODE CDP port wiring (FR-TEST-1, Windows).
            import os as _os
            _test_browser_args = None
            _test_port_num = None
            if _os.getenv("PICOLET_TEST_MODE") == "1":
                port = _win_ffi.picolet_wv2_pick_test_port()
                if port > 0:
                    _test_port_num = port
                    _test_browser_args = (
                        "--remote-debugging-port={} "
                        "--remote-debugging-address=127.0.0.1".format(port)
                    )
                else:
                    sys.stderr.write(
                        "picolet_ui: PICOLET_TEST_MODE: picolet_wv2_pick_test_port() failed\n"
                    )
            env = _ensure_environment(extra_browser_args=_test_browser_args)
            ctrl = _win_ffi.picolet_wv2_create_controller_blocking(
                env, window.handle, 30000,
            )
            if not ctrl:
                err = _win_ffi.picolet_wv2_last_error()
                raise RuntimeError(
                    "picolet_ui: CreateCoreWebView2Controller failed "
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
                        "picolet_ui: AddScriptToExecuteOnDocumentCreated "
                        "failed (HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
                    )

            # Bind transport so the inbound handler has somewhere to deliver.
            self.transport = (
                transport if transport is not None else WebviewTransport(self)
            )
            if self.transport._webview is None:
                self.transport._webview = self

            # Register the persistent WebMessageReceived handler.
            rc = _win_ffi.picolet_wv2_register_inbound_handler(ctrl)
            if rc != 0:
                sys.stderr.write(
                    "picolet_ui: register_inbound_handler failed "
                    "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
                )

            # PH17 — announce the CDP debugging port on stderr (FR-TEST-1).
            if _test_port_num is not None:
                sys.stderr.write("picolet:test-port={}\n".format(_test_port_num))
                sys.stderr.flush()

        def navigate_to_string(self, html):
            from . import _win_ffi
            rc = _win_ffi.picolet_wv2_navigate_to_string(
                self._controller, html.encode("utf-8"),
            )
            if rc != 0:
                sys.stderr.write(
                    "picolet_ui: NavigateToString failed "
                    "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
                )

        def eval_js(self, js):
            """Run JS in the page.  Async-completion is ignored."""
            from . import _win_ffi
            rc = _win_ffi.picolet_wv2_execute_script(
                self._controller, js.encode("utf-8"),
            )
            if rc != 0:
                sys.stderr.write(
                    "picolet_ui: ExecuteScript failed "
                    "(HRESULT 0x{:08x})\n".format(rc & 0xFFFFFFFF)
                )

        # Legacy name kept for callers that imported picolet_ui_win directly.
        execute_script = eval_js

        @property
        def controller(self):
            return self._controller

        def close(self):
            if self._controller is None:
                return
            from . import _win_ffi
            _win_ffi.picolet_wv2_close_controller(self._controller)
            self._controller = None

else:

    # -----------------------------------------------------------------
    # GTK 3 / WebKitGTK 4.1 backend (linux)
    # -----------------------------------------------------------------

    # The libffi closure (ffi.callback) needs a stable reference for the
    # lifetime of the signal connection — if the Python wrapper is GC'd, the
    # closure trampoline crashes when GTK fires the signal next.  We keep
    # closures alive at module scope keyed by the transport's id.
    _active_callbacks = {}


    def _wait_for_inspector_port(port, gtk_ffi, timeout_ms=5000, poll_ms=50,
                                  drive_gtk=True):
        """Poll TCP 127.0.0.1:port until connect() succeeds or timeout.

        Drives GTK events between poll attempts so the WebKitGTK GMain-based
        networking thread can progress and actually bind the socket.

        Returns True if the port became reachable, False on timeout.

        All socket operations go through libc directly (MicroPython unix port
        disables the socket module).  Uses AF_INET SOCK_STREAM with
        connect-then-close — the inspector server will accept and immediately
        close; we only care that bind succeeded.
        """
        import uctypes

        AF_INET    = 2
        SOCK_STREAM = 1
        # SOCK_NONBLOCK (Linux) — prevents connect() from stalling the thread
        # in SYN_SENT while waiting for the remote to accept.  With O_NONBLOCK
        # set, connect() returns EINPROGRESS immediately (or 0 on instant
        # loopback success); we close the fd and pump GTK in the sleep below.
        # This is Linux-specific but the whole function is Linux-only.
        SOCK_NONBLOCK = 0x800  # O_NONBLOCK on Linux (also accepted as SOCK_* flag)

        # Open libc for socket syscalls.
        import ffi as _ffi
        _libc = None
        for _name in ("libc.so.6", "libc.so", "libc.so.0"):
            try:
                _libc = _ffi.open(_name)
                break
            except OSError:
                pass
        if _libc is None:
            return False

        try:
            sock_f    = _libc.func("i", "socket",  "iii")
            connect_f = _libc.func("i", "connect", "ipi")
            close_f   = _libc.func("i", "close",   "i")
        except OSError:
            return False

        # Build struct sockaddr_in for 127.0.0.1:<port> once.
        addr_buf = bytearray(16)
        addr_buf[0] = 2   # sin_family = AF_INET (LE uint16, low byte)
        addr_buf[1] = 0
        # sin_port in network byte order (big-endian)
        addr_buf[2] = (port >> 8) & 0xFF
        addr_buf[3] = port & 0xFF
        # sin_addr = 127.0.0.1 in network byte order
        addr_buf[4] = 0x7F
        addr_buf[5] = 0x00
        addr_buf[6] = 0x00
        addr_buf[7] = 0x01
        addr_ptr = uctypes.addressof(addr_buf)

        try:
            import utime
            _ticks_ms = utime.ticks_ms
            _ticks_diff = utime.ticks_diff
            _ticks_add = utime.ticks_add
            def _sleep_ms(ms):
                utime.sleep_ms(ms)
        except ImportError:
            import time as _time_mod
            _ticks_add = None
            def _ticks_ms():
                return int(_time_mod.monotonic() * 1000)
            def _ticks_diff(end, start):
                return end - start
            def _sleep_ms(ms):
                _time_mod.sleep(ms / 1000.0)

        # MicroPython's ticks_ms() wraps at ~30 bits; use ticks_add() to
        # compute the deadline safely.  CPython path uses plain addition
        # (monotonic() never wraps in practice).
        if _ticks_add is not None:
            deadline_ms = _ticks_add(_ticks_ms(), timeout_ms)
        else:
            deadline_ms = _ticks_ms() + timeout_ms

        while True:
            # SOCK_NONBLOCK: connect() returns EINPROGRESS immediately on a
            # not-yet-bound port rather than stalling the calling thread,
            # which would prevent GTK from being pumped below.  A return
            # value of 0 means the loopback connect completed synchronously
            # (port is bound and listening).
            fd = sock_f(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0)
            if fd >= 0:
                rc = connect_f(fd, addr_ptr, 16)
                close_f(fd)
                if rc == 0:
                    return True  # connected — port is bound

            # Drive GTK so WebKitGTK's GMain loop can make progress.
            if drive_gtk:
                try:
                    for _ in range(5):
                        gtk_ffi.gtk_main_iteration_do(0)
                except Exception:
                    pass

            remaining = _ticks_diff(deadline_ms, _ticks_ms())
            if remaining <= 0:
                return False

            # Sleep poll_ms (capped to remaining).
            sleep_ms = poll_ms if poll_ms < remaining else remaining
            _sleep_ms(sleep_ms)


    def _build_on_script_message(transport):
        """Build the (manager, js_result, user_data) signal handler.

        The libffi callback fires synchronously from inside
        gtk_main_iteration_do (Option C — same-thread pump).  Because we
        are single-threaded and the callback runs inside our own asyncio
        task, lock=False on the ffi.callback is correct: there is no
        foreign-thread re-entry and the GC/scheduler locks would only
        cause allocation failures inside the callback.

        Args arrive as Python ints (mp_int_t) per modffi.c:280.  In
        WebKitGTK 4.1 the signal delivers a WebKitJavascriptResult *
        (NOT a JSCValue * directly); we unwrap via
        webkit_javascript_result_get_js_value before
        jsc_value_to_string — empirically confirmed against 2.52 on
        Ubuntu 24.04, where jsc_value_to_string asserts JSC_IS_VALUE
        without the unwrap.
        """
        from . import _gtk_ffi

        def on_script_message(manager_p, js_result_p, user_data_p):
            try:
                if _gtk_ffi.webkit_javascript_result_get_js_value is not None:
                    value_p = _gtk_ffi.webkit_javascript_result_get_js_value(
                        js_result_p
                    )
                else:
                    value_p = js_result_p
                cstr = _gtk_ffi.jsc_value_to_string(value_p)
                if not cstr:
                    sys.stderr.write(
                        "picolet_ui: jsc_value_to_string returned NULL\n"
                    )
                    return 0
                try:
                    payload = _gtk_ffi.ffi_string(cstr)
                finally:
                    _gtk_ffi.g_free(cstr)
                transport._deliver_raw(payload)
            except BaseException as e:
                sys.stderr.write(
                    "picolet_ui: on_script_message raised: {}\n".format(e)
                )
            return 0  # rettype is "v" — modffi ignores the return for void

        return on_script_message


    class Webview:
        """A WebKitWebView embedded in a picolet_ui.Window.

        Construction:
            win = picolet_ui.Window()
            wv  = picolet_ui.Webview(win, root_uri="file:///rom/ui/index.html")
            win.show()

        The Webview registers a `"picolet"` script-message handler whose
        inbound JSON messages are buffered for the paired WebviewTransport.
        The picolet-bridge-js bundle is injected at DOCUMENT_START so
        window.picolet.{invoke, on, emit} are available to all user JS.
        """

        def __init__(self, window, root_uri=None, transport=None,
                     disable_sandbox=True):
            from . import _gtk_ffi
            self._window = window
            self._gtk_ffi = _gtk_ffi
            self._closures = []  # keep callback closures alive

            # PH17 — PICOLET_TEST_MODE inspector wiring (FR-TEST-1).
            # WEBKIT_INSPECTOR_SERVER must be set BEFORE webkit_web_view_new()
            # because WebKit reads it once at engine-init time (R1).  We do
            # the env-var setup here, before any view creation.
            self._test_port = None
            import os
            if os.getenv("PICOLET_TEST_MODE") == "1":
                try:
                    from ._test_port import pick_test_port
                    port = pick_test_port()
                    self._test_port = port
                    inspector_addr = "127.0.0.1:{}".format(port)
                    if _gtk_ffi.setenv is not None:
                        _gtk_ffi.setenv("WEBKIT_INSPECTOR_SERVER", inspector_addr, 1)
                    else:
                        os.environ["WEBKIT_INSPECTOR_SERVER"] = inspector_addr
                except Exception as exc:
                    sys.stderr.write(
                        "picolet_ui: PICOLET_TEST_MODE: failed to open inspector port: {}\n".format(exc)
                    )

            if disable_sandbox and _gtk_ffi.webkit_web_context_set_sandbox_enabled:
                # Risk-3 mitigation: trusted file:// content the runtime
                # bundled itself; sandbox costs us correctness on some
                # distros without buying security.
                ctx = _gtk_ffi.webkit_web_context_get_default()
                _gtk_ffi.webkit_web_context_set_sandbox_enabled(ctx, 0)

            self._view = _gtk_ffi.webkit_web_view_new()
            if not self._view:
                raise RuntimeError("picolet_ui: webkit_web_view_new returned NULL")
            window.add(self._view)

            self._manager = _gtk_ffi.webkit_web_view_get_user_content_manager(
                self._view
            )

            # Inject the picolet-bridge-js bundle at document-start so
            # window.picolet (invoke, on, emit) is available to user JS
            # before any <script> tags execute (FR-WV-4, PH08).
            # The bundle is copied into the romfs at build time by
            # picolet-cli's build_cmd.py _copy_bridge_js() step.
            _bridge_path = "/rom/picolet/picolet-bridge.js"
            try:
                with open(_bridge_path) as _f:
                    bridge_src = _f.read()
            except OSError:
                # Graceful degradation: window.picolet will be undefined.
                # This happens when running outside a built romfs (e.g.
                # during unit tests that import _webview directly).
                bridge_src = ""
            # WEBKIT_USER_CONTENT_INJECT_TOP_FRAME=1, WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START=0
            script = _gtk_ffi.webkit_user_script_new(bridge_src, 1, 0, 0, 0)
            _gtk_ffi.webkit_user_content_manager_add_script(
                self._manager, script
            )

            # Bind transport now so the script-message handler can deposit
            # into it.  A standalone Webview (no transport) is allowed for
            # the gate-8 callback probe; we use an inline list in that case.
            self.transport = transport if transport is not None else WebviewTransport(self)
            # Late-bind: if the caller passed a transport that wasn't yet
            # aware of us, attach now.
            if self.transport._webview is None:
                self.transport._webview = self

            # Build the libffi callback for "script-message-received::picolet".
            # The callback signature is:
            #   void (*)(WebKitUserContentManager *, WebKitJavascriptResult *, gpointer)
            # FFI param types: "ppp" → manager, js_result, user_data.
            #
            # lock=False is correct for Option C: the callback fires from
            # inside gtk_main_iteration_do which runs ON our asyncio thread
            # inside our pump task.  No threading involved; the scheduler
            # and GC locks would only be needed if a foreign thread were
            # re-entering Python.  lock=True triggers MemoryError on the
            # first allocation inside the callback when the heap is near
            # full — a real production hazard.
            import ffi
            cb = ffi.callback(
                "v",
                _build_on_script_message(self.transport),
                "ppp",
                lock=False,
            )
            # Keep the callback alive for the life of the Webview.
            self._closures.append(cb)
            _active_callbacks[id(self)] = self._closures

            # Register the handler name.  The (manager, name, world_name)
            # signature is the WebKitGTK 4.1+ shape; world_name=NULL targets
            # the default world.
            rc = _gtk_ffi.webkit_user_content_manager_register_script_message_handler(
                self._manager, "picolet", 0
            )
            if not rc:
                sys.stderr.write(
                    "picolet_ui: register_script_message_handler returned 0\n"
                )

            # Connect the signal.  The "::picolet" detail selects the handler
            # registered under that name.  Flags=0 (no swapped, no after).
            # modffi.c::ffifunc_call (line 520-522) recognises fficallback as
            # an FFI argument and passes p->func (the libffi closure address)
            # directly — no manual extraction needed.  Belt-and-suspenders:
            # also support older builds via cb.cfun() if direct passing fails.
            sig = "script-message-received::picolet"
            _gtk_ffi.g_signal_connect_data(
                self._manager, sig, cb, 0, 0, 0
            )

            # PH17 — enable developer extras after view creation, explicitly
            # open the inspector (which triggers the WEBKIT_INSPECTOR_SERVER
            # TCP bind), poll until the port accepts connections, then announce
            # the port on stderr (FR-TEST-1 race fix).
            #
            # Race: the env var alone does not bind the TCP socket.  WebKitGTK
            # only binds when webkit_web_inspector_show() is called on the view.
            # Without the explicit show() + TCP-poll guard, the port announcement
            # races ahead of the bind and consumers see ECONNREFUSED.
            if self._test_port is not None:
                try:
                    settings = _gtk_ffi.webkit_web_view_get_settings(self._view)
                    if settings:
                        _gtk_ffi.webkit_settings_set_enable_developer_extras(settings, 1)
                        if _gtk_ffi.webkit_settings_set_enable_write_console_messages_to_stdout is not None:
                            _gtk_ffi.webkit_settings_set_enable_write_console_messages_to_stdout(settings, 1)
                except Exception as exc:
                    sys.stderr.write(
                        "picolet_ui: PICOLET_TEST_MODE: settings configuration failed: {}\n".format(exc)
                    )

                # Explicitly open the inspector to trigger the TCP bind.
                _inspector_opened = False
                try:
                    if (_gtk_ffi.webkit_web_view_get_inspector is not None and
                            _gtk_ffi.webkit_web_inspector_show is not None):
                        inspector = _gtk_ffi.webkit_web_view_get_inspector(self._view)
                        if inspector:
                            _gtk_ffi.webkit_web_inspector_show(inspector)
                            _inspector_opened = True
                except Exception as exc:
                    sys.stderr.write(
                        "picolet_ui: PICOLET_TEST_MODE: inspector show failed: {}\n".format(exc)
                    )

                # If the inspector symbols are absent (WebKitGTK < 4.0), the
                # TCP poll will run without GTK pumping and is guaranteed to
                # timeout — the socket never binds.  Skip the announcement
                # entirely so AppHarness receives an honest timeout rather than
                # a port that immediately refuses connections.
                if not _inspector_opened:
                    sys.stderr.write(
                        "picolet_ui: PICOLET_TEST_MODE: WebKit inspector unavailable "
                        "(webkit_web_view_get_inspector or webkit_web_inspector_show "
                        "not found); skipping picolet:test-port announcement\n"
                    )
                    self._test_port = None

                if self._test_port is not None:
                    # TCP-poll: wait until the inspector port accepts connections
                    # (or 5 s timeout).  This is the only reliable "port is bound"
                    # signal available — WebKitGTK has no synchronous callback for
                    # inspector server ready.
                    #
                    # We call connect()/close() in a loop, driving GTK events between
                    # attempts so WebKitGTK's GMain-based networking can progress.
                    _port_ready = _wait_for_inspector_port(
                        self._test_port, _gtk_ffi,
                        timeout_ms=5000, poll_ms=50,
                        drive_gtk=True,
                    )
                    if not _port_ready:
                        sys.stderr.write(
                            "picolet_ui: PICOLET_TEST_MODE: inspector port {} not ready "
                            "after 5 s; announcing anyway\n".format(self._test_port)
                        )

                    sys.stderr.write("picolet:test-port={}\n".format(self._test_port))
                    sys.stderr.flush()

            if root_uri is not None:
                _gtk_ffi.webkit_web_view_load_uri(self._view, root_uri)

        def load_uri(self, uri):
            self._gtk_ffi.webkit_web_view_load_uri(self._view, uri)

        def eval_js(self, script):
            """Run JS in the page.  Async-completion is ignored (callback=NULL)."""
            self._gtk_ffi.webkit_web_view_evaluate_javascript(
                self._view, script, -1, 0, 0, 0, 0, 0
            )

        @property
        def view(self):
            return self._view

        @property
        def manager(self):
            return self._manager

        def close(self):
            _active_callbacks.pop(id(self), None)
            self._closures = []
            self._view = None
            self._manager = None


# ---------------------------------------------------------------------------
# WebviewTransport (PH06 Transport contract) — platform-agnostic.
# ---------------------------------------------------------------------------


class WebviewTransport:
    """A picolet.Transport-compatible transport over the webview postMessage channel.

    Duck-type contract (from packages/picolet-runtime/python/picolet/_transport.py):

        async recv() -> dict | None
        async send(msg) -> None
        async close() -> None

    Inbound (linux):
        JS calls window.webkit.messageHandlers.picolet.postMessage(json).
        The script-message-received signal fires our libffi closure,
        which decodes the JSC string and calls _deliver_raw(json_str).

    Inbound (windows):
        Drained from the picolet_webview2 C overlay's ring buffer by
        _loop._win_pump on every pump tick.  Each raw JSON string is
        passed to _deliver_raw().

    Both paths parse with json.loads and append to _inbox; the next
    recv() pops the head.

    Outbound:
        send(msg) JSON-encodes msg, wraps it in a JS expression that
        calls window.__picolet_recv(json), and hands the expression to
        the Webview backend via .eval_js (which dispatches via
        evaluate_javascript / ExecuteScript as appropriate).

    Concurrency:
        Same-thread per design D2 / AD4; no locks needed.  send() does
        not await; recv() awaits on an asyncio.Event.
    """

    def __init__(self, webview=None):
        self._webview = webview
        self._inbox = []
        self._closed = False
        self._evt = None  # asyncio.Event, lazily allocated
        # Test hook: a callable invoked with each raw JSON payload before
        # it's appended.  Lets the gate-8 probe assert message arrival
        # without driving the asyncio scheduler.
        self._raw_hook = None
        # Counter for diagnostics.
        self.recv_count = 0
        self.send_count = 0

    # -------- inbound side (called from the FFI callback / pump) --------

    def _deliver_raw(self, json_str):
        """Append a raw JSON string for the next recv().  Drops on parse error."""
        if self._raw_hook is not None:
            try:
                self._raw_hook(json_str)
            except BaseException as e:
                sys.stderr.write(
                    "picolet_ui: _raw_hook raised: {}\n".format(e)
                )
        try:
            msg = json.loads(json_str)
        except Exception as e:
            sys.stderr.write(
                "picolet_ui: malformed JSON from postMessage: {}\n".format(e)
            )
            return
        self._inbox.append(msg)
        evt = self._evt
        if evt is not None:
            evt.set()

    # -------- Transport contract --------

    async def recv(self):
        while not self._closed:
            if self._inbox:
                self.recv_count += 1
                return self._inbox.pop(0)
            if not _HAVE_ASYNCIO:
                raise RuntimeError("WebviewTransport.recv requires asyncio")
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
            # Standalone (test) mode — just record outbox.
            self._outbox_append(msg)
            return
        # Encode the message as a JSON string, then JSON-encode that string
        # so it becomes a quoted JS string literal.  window.__picolet_recv
        # expects a JSON string argument and calls JSON.parse() on it;
        # passing a JS object literal (the JSON without quotes) would cause
        # JSON.parse to receive "[object Object]" and silently fail.
        encoded = json.dumps(msg)
        js = "window.__picolet_recv(" + json.dumps(encoded) + ")"
        self._webview.eval_js(js)
        self._outbox_append(msg)
        self.send_count += 1

    async def close(self):
        self._closed = True
        evt = self._evt
        if evt is not None:
            evt.set()

    # -------- diagnostics --------

    @property
    def closed(self):
        return self._closed

    def _outbox_append(self, msg):
        # Outbox is allocated only when first accessed — keeps the
        # production case zero-overhead.
        if not hasattr(self, "_outbox"):
            self._outbox = []
        self._outbox.append(msg)

    def drain_outbox(self):
        return list(getattr(self, "_outbox", []))
