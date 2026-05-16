# picolet_ui._gtk_ffi — libffi bindings into GTK 3 + WebKitGTK 4.1.
#
# PH07.  Pure-Python contact surface between the picolet runtime and the
# system-provided GUI stack.  No native modules; nothing is statically
# linked.  All four shared libraries are dlopen'd at runtime via
# `ffi.open` and the symbols below are bound via `ffi.func`.
#
# Dynamic dependencies introduced by this module (NFR-5 audit, PH13):
#
#   - libwebkit2gtk-4.1.so.0          LGPL-2.1+  dlopen (FR-WV-1)
#   - libgtk-3.so.0                   LGPL-2.1+  dlopen
#   - libgobject-2.0.so.0             LGPL-2.1+  dlopen
#   - libjavascriptcoregtk-4.1.so.0   LGPL-2.1+  dlopen
#   - libgio-2.0.so.0                 LGPL-2.1+  dlopen (g_memory_input_stream_*
#                                     for the picolet:// URI scheme handler; pulled
#                                     in transitively by libglib-2.0 / glib2)
#
# All five are dynamically linked at runtime; the runtime binary has no
# build-time dependency on the corresponding -dev packages.  NFR-8 is
# satisfied by `apt install libwebkit2gtk-4.1-0` (which pulls in all
# five transitively).
#
# FFI signature notation (modffi.c::char2ffi_type):
#
#   v = void               b = int8_t / char    B = uint8_t
#   c = int8_t (signed)    h = int16_t          H = uint16_t
#   i = int32_t            I = uint32_t
#   l = int64_t            L = uint64_t
#   f = float              d = double
#   p = void *             s = char *           P = pointer
#   C = callback (closure)
#
# Symbols are bound lazily-by-construction: ImportError at module-load
# time names the missing library plainly so a runtime user on a host
# without webkit2gtk-4.1-0 sees a clear message rather than a generic
# `OSError: cannot find shared object`.

import ffi


def _safe_open(soname):
    try:
        return ffi.open(soname)
    except OSError as e:
        raise ImportError(
            "picolet_ui: failed to open {!r}; ensure libwebkit2gtk-4.1-0 "
            "and its transitive deps are installed (apt install "
            "libwebkit2gtk-4.1-0): {}".format(soname, e)
        )


# ---------------------------------------------------------------------------
# Open the four shared libraries
# ---------------------------------------------------------------------------

# libgtk-3.so.0 carries gtk_*; the package is libgtk-3-0t64 on Ubuntu
# 24.04 and libgtk-3-0 on 22.04, but the SONAME is the same.
gtk = _safe_open("libgtk-3.so.0")

# libgobject-2.0.so.0 carries g_signal_connect_data, g_object_unref,
# g_free, g_main_context_*, etc.
gobject = _safe_open("libgobject-2.0.so.0")

# libwebkit2gtk-4.1.so.0 carries webkit_*.
webkit = _safe_open("libwebkit2gtk-4.1.so.0")

# libjavascriptcoregtk-4.1.so.0 carries jsc_value_*.
jsc = _safe_open("libjavascriptcoregtk-4.1.so.0")


# ---------------------------------------------------------------------------
# GTK 3 symbols
# ---------------------------------------------------------------------------

# gtk_init(int *argc, char ***argv) — both may be NULL.
gtk_init = gtk.func("v", "gtk_init", "pp")

# GtkWidget *gtk_window_new(GtkWindowType type)  — type 0 = TOPLEVEL.
gtk_window_new = gtk.func("p", "gtk_window_new", "i")

# void gtk_window_set_title(GtkWindow *, const gchar *title)
gtk_window_set_title = gtk.func("v", "gtk_window_set_title", "ps")

# void gtk_window_set_default_size(GtkWindow *, gint w, gint h)
gtk_window_set_default_size = gtk.func("v", "gtk_window_set_default_size", "pii")

# void gtk_window_set_resizable(GtkWindow *, gboolean resizable)
gtk_window_set_resizable = gtk.func("v", "gtk_window_set_resizable", "pi")

# void gtk_container_add(GtkContainer *, GtkWidget *)
gtk_container_add = gtk.func("v", "gtk_container_add", "pp")

# void gtk_widget_show_all(GtkWidget *)
gtk_widget_show_all = gtk.func("v", "gtk_widget_show_all", "p")

# void gtk_widget_destroy(GtkWidget *)
gtk_widget_destroy = gtk.func("v", "gtk_widget_destroy", "p")

# gboolean gtk_main_iteration_do(gboolean blocking)
gtk_main_iteration_do = gtk.func("i", "gtk_main_iteration_do", "i")

# gboolean gtk_events_pending(void)
gtk_events_pending = gtk.func("i", "gtk_events_pending", "")

# void gtk_main_quit(void)
gtk_main_quit = gtk.func("v", "gtk_main_quit", "")


# ---------------------------------------------------------------------------
# GObject / GLib symbols
# ---------------------------------------------------------------------------

# gulong g_signal_connect_data(gpointer instance, const gchar *signal,
#                              GCallback handler, gpointer data,
#                              GClosureNotify destroy_data, GConnectFlags flags)
g_signal_connect_data = gobject.func(
    "L", "g_signal_connect_data", "pspppI"
)

# void g_object_unref(gpointer)
g_object_unref = gobject.func("v", "g_object_unref", "p")

# void g_free(gpointer)
# libgobject-2.0 re-exports symbols from libglib-2.0 in some packagings; on
# Ubuntu it is in libglib-2.0.so.0.  Try gobject first, fall back to glib.
try:
    g_free = gobject.func("v", "g_free", "p")
except OSError:
    _glib = _safe_open("libglib-2.0.so.0")
    g_free = _glib.func("v", "g_free", "p")


# ---------------------------------------------------------------------------
# WebKitGTK 4.1 symbols
# ---------------------------------------------------------------------------

# GtkWidget *webkit_web_view_new(void)
webkit_web_view_new = webkit.func("p", "webkit_web_view_new", "")

# void webkit_web_view_load_uri(WebKitWebView *, const gchar *uri)
webkit_web_view_load_uri = webkit.func("v", "webkit_web_view_load_uri", "ps")

# void webkit_web_view_load_html(WebKitWebView *, const gchar *html,
#                                const gchar *base_uri)
# Used to inject HTML read from the MicroPython VFS romfs at /rom/.
# WebKit can't load file:///rom/... directly because /rom is an
# overlay inside the runtime process, not a real OS path.
webkit_web_view_load_html = webkit.func(
    "v", "webkit_web_view_load_html", "pss"
)

# WebKitUserContentManager *webkit_web_view_get_user_content_manager(WebKitWebView *)
webkit_web_view_get_user_content_manager = webkit.func(
    "p", "webkit_web_view_get_user_content_manager", "p"
)

# void webkit_web_view_evaluate_javascript(
#     WebKitWebView *,
#     const gchar *script,
#     gssize length,
#     const gchar *world_name,
#     const gchar *source_uri,
#     GCancellable *cancellable,
#     GAsyncReadyCallback callback,
#     gpointer user_data)
# We always pass length=-1 (NUL-terminated), world_name=NULL,
# source_uri=NULL, cancellable=NULL, callback=NULL, user_data=NULL.
# The signature uses 'l' for gssize (signed long).
webkit_web_view_evaluate_javascript = webkit.func(
    "v", "webkit_web_view_evaluate_javascript", "pslssppp"
)

# gboolean webkit_user_content_manager_register_script_message_handler(
#     WebKitUserContentManager *, const gchar *name, const gchar *world_name)
# Note: the 2.40+ signature has a world_name kwarg; the 2.36 signature
# has only (manager, name).  Try the 3-arg signature first; if it
# segfaults we fall back to 2-arg (deferred contingency, not exercised
# here).
webkit_user_content_manager_register_script_message_handler = webkit.func(
    "i", "webkit_user_content_manager_register_script_message_handler", "pss"
)

# void webkit_user_content_manager_add_script(
#     WebKitUserContentManager *, WebKitUserScript *)
webkit_user_content_manager_add_script = webkit.func(
    "v", "webkit_user_content_manager_add_script", "pp"
)

# WebKitUserScript *webkit_user_script_new(
#     const gchar *source,
#     WebKitUserContentInjectedFrames injected_frames,
#     WebKitUserScriptInjectionTime injection_time,
#     const gchar *const *allow_list,
#     const gchar *const *block_list)
# Frames: ALL_FRAMES=0, TOP_FRAME=1.  Time: START=0, END=1.
webkit_user_script_new = webkit.func(
    "p", "webkit_user_script_new", "siipp"
)

# WebKitWebContext *webkit_web_context_get_default(void)
webkit_web_context_get_default = webkit.func(
    "p", "webkit_web_context_get_default", ""
)

# void webkit_web_context_set_sandbox_enabled(WebKitWebContext *, gboolean)
# Available from WebKitGTK 2.26+.  Risk-3 mitigation: disable the
# bubblewrap sandbox for trusted file:// content the runtime bundled
# itself.
try:
    webkit_web_context_set_sandbox_enabled = webkit.func(
        "v", "webkit_web_context_set_sandbox_enabled", "pi"
    )
except OSError:
    webkit_web_context_set_sandbox_enabled = None

# void webkit_web_context_register_uri_scheme(
#     WebKitWebContext *, const gchar *scheme,
#     WebKitURISchemeRequestCallback callback,
#     gpointer user_data, GDestroyNotify user_data_destroy_func)
# Registers a custom URI scheme handler.  The callback signature is:
#   void (*)(WebKitURISchemeRequest *, gpointer user_data)
# FFI param string: "psppp"  (context, scheme-str, callback-closure, data, destroy)
webkit_web_context_register_uri_scheme = webkit.func(
    "v", "webkit_web_context_register_uri_scheme", "psppp"
)

# const gchar *webkit_uri_scheme_request_get_uri(WebKitURISchemeRequest *)
# Returns the full URI of the request (e.g. "picolet://ui/style.css").
webkit_uri_scheme_request_get_uri = webkit.func(
    "p", "webkit_uri_scheme_request_get_uri", "p"
)

# const gchar *webkit_uri_scheme_request_get_path(WebKitURISchemeRequest *)
# Returns the path component of the URI (e.g. "/ui/style.css").
webkit_uri_scheme_request_get_path = webkit.func(
    "p", "webkit_uri_scheme_request_get_path", "p"
)

# void webkit_uri_scheme_request_finish(
#     WebKitURISchemeRequest *, GInputStream *stream,
#     gint64 stream_length, const gchar *content_type)
# Finishes a URI scheme request by providing the response body.
# stream_length = -1 means the stream provides its own length signal.
webkit_uri_scheme_request_finish = webkit.func(
    "v", "webkit_uri_scheme_request_finish", "ppls"
)

# void webkit_uri_scheme_request_finish_error(
#     WebKitURISchemeRequest *, GError *error)
# Finishes a URI scheme request with an error (404, etc.).
webkit_uri_scheme_request_finish_error = webkit.func(
    "v", "webkit_uri_scheme_request_finish_error", "pp"
)

# GInputStream *g_memory_input_stream_new_from_data(
#     const void *data, gssize len, GDestroyNotify destroy)
# Creates an in-memory GInputStream backed by a data buffer.
# destroy = NULL because the Python bytes object owns the buffer for the
# duration of the call; the stream is consumed synchronously.
# libgio-2.0.so.0 carries this symbol.
try:
    _gio = _safe_open("libgio-2.0.so.0")
    g_memory_input_stream_new_from_data = _gio.func(
        "p", "g_memory_input_stream_new_from_data", "plp"
    )
except OSError:
    g_memory_input_stream_new_from_data = None


# ---------------------------------------------------------------------------
# JavaScriptCore symbols
# ---------------------------------------------------------------------------

# gchar *jsc_value_to_string(JSCValue *) — caller must g_free the result.
jsc_value_to_string = jsc.func("p", "jsc_value_to_string", "p")

# gboolean jsc_value_is_string(JSCValue *)
jsc_value_is_string = jsc.func("i", "jsc_value_is_string", "p")


# ---------------------------------------------------------------------------
# WebKit script-message accessor
# ---------------------------------------------------------------------------

# JSCValue *webkit_javascript_result_get_js_value(WebKitJavascriptResult *)
# WebKitGTK 4.1's "script-message-received" signal delivers a
# WebKitJavascriptResult * (NOT a JSCValue * directly) — confirmed
# empirically against 2.52 on Ubuntu 24.04: jsc_value_to_string asserts
# without the unwrap.  This function is present in 4.1; the lazy bind
# here keeps the contingency.
try:
    webkit_javascript_result_get_js_value = webkit.func(
        "p", "webkit_javascript_result_get_js_value", "p"
    )
except OSError:
    webkit_javascript_result_get_js_value = None

# WebKitJavascriptResult *webkit_javascript_result_ref(WebKitJavascriptResult *)
# void                    webkit_javascript_result_unref(WebKitJavascriptResult *)
# Used by the script-message callback to keep the result alive across a
# micropython.schedule() boundary (the callback runs under gc_lock and
# can't safely allocate; the real work runs on the next scheduler tick).
webkit_javascript_result_ref = webkit.func(
    "p", "webkit_javascript_result_ref", "p"
)
webkit_javascript_result_unref = webkit.func(
    "v", "webkit_javascript_result_unref", "p"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ffi_string(ptr):
    """Decode a NUL-terminated C string pointer into a Python str.

    Uses ffi.string-equivalent semantics via uctypes.
    """
    if not ptr:
        return ""
    import uctypes
    # Read until NUL.  Cap at 16 MiB to avoid runaway on a corrupt pointer.
    out = bytearray()
    addr = int(ptr)
    cap = 16 * 1024 * 1024
    for i in range(cap):
        b = uctypes.bytes_at(addr + i, 1)[0]
        if b == 0:
            break
        out.append(b)
    else:
        # Pointer with no NUL inside 16 MiB — treat as corrupt.
        raise ValueError("ffi_string: no NUL terminator within 16 MiB")
    try:
        return bytes(out).decode("utf-8")
    except UnicodeError:
        # Replace invalid bytes with U+FFFD.  Some browsers emit
        # surrogates from JSON.stringify on unpaired UTF-16 code points;
        # we surface the bytes-as-decoded for logging upstream.
        return bytes(out).decode("utf-8", "replace")
