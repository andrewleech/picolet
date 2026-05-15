# picolet_ui._webview — WebKitGTK 4.1 webview + WebviewTransport.
#
# PH07: embeds a WebKitWebView inside a `picolet_ui.Window`, loads a
# file:// URI, registers the `"picolet"` script-message handler, and
# exposes a `WebviewTransport` that satisfies the PH06 transport
# duck-type contract.  The dispatcher consumes it unchanged.
#
# PH08: injects the picolet-bridge-js IIFE bundle at DOCUMENT_START
# (replacing the PH07 no-op stub).  The bundle installs
# window.picolet.{invoke, on, emit} and the internal window.__picolet_recv
# handler.  The bundle text is read from /rom/picolet/picolet-bridge.js
# inside the frozen runtime (copied there by picolet-cli build_cmd.py).
#
# JS-side wire:
#   Inbound (JS -> Python):  window.webkit.messageHandlers.picolet
#                              .postMessage(JSON.stringify(msg))
#   Outbound (Python -> JS): window.__picolet_recv(jsonString)

import json
import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False


# ---------------------------------------------------------------------------
# Inbound callback bookkeeping
# ---------------------------------------------------------------------------
#
# The libffi closure (ffi.callback) needs a stable reference for the
# lifetime of the signal connection — if the Python wrapper is GC'd, the
# closure trampoline crashes when GTK fires the signal next.  We keep
# closures alive at module scope keyed by the transport's id.

_active_callbacks = {}


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


# ---------------------------------------------------------------------------
# Webview
# ---------------------------------------------------------------------------


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
# WebviewTransport (PH06 Transport contract)
# ---------------------------------------------------------------------------


class WebviewTransport:
    """A picolet.Transport-compatible transport over WebKit's postMessage.

    Duck-type contract (from packages/picolet-runtime/python/picolet/_transport.py):

        async recv() -> dict | None
        async send(msg) -> None
        async close() -> None

    Inbound:
        JS calls window.webkit.messageHandlers.picolet.postMessage(json).
        The script-message-received signal fires our libffi closure,
        which decodes the JSC string and calls _deliver_raw(json_str).
        We parse it with json.loads and append to _inbox; the next
        recv() pops the head.

    Outbound:
        send(msg) JSON-encodes msg, wraps it in a JS expression that
        calls window.__picolet_recv(json), and queues the JS expression
        for execution by the next pump tick.  The actual
        evaluate_javascript call is performed synchronously inside
        send() — it returns immediately; WebKit dispatches the script
        to the renderer process.

    Concurrency:
        Same-thread per design D2; no locks needed.  send() does not
        await; recv() awaits on an asyncio.Event.
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

    # -------- inbound side (called from the FFI callback) --------

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
        except (ValueError, Exception) as e:
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
