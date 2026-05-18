# picolet_ui._mac_ffi — libffi bindings into the macOS WKWebView C overlay.
#
# PH25.  Mirror of _win_ffi.py for the Darwin platform.
# ffi.open(None) on macOS resolves via dlopen(NULL, ...) — the running
# Mach-O binary.  All picolet_wkwv_* symbols are compiled into the binary
# with __attribute__((visibility("default"))) + -Wl,-export_dynamic so
# dlopen(NULL) can find them.
#
# FFI signature notation (modffi.c::char2ffi_type):
#
#   v = void               i = int32_t            I = uint32_t
#   l = int64_t            L = uint64_t           p = void *
#   s = char *             P = pointer            f = float / d = double
#   C = callback (closure)
#
# macOS WKWebView C API surface (picolet_webview_mac.h):
#
#   int  picolet_wkwv_init(void)
#   void *picolet_wkwv_create_window(const char *title, int w, int h)
#   int  picolet_wkwv_show_window(void *window, int visible)
#   int  picolet_wkwv_destroy_window(void *window)
#   void *picolet_wkwv_create_webview(void *window, int w, int h)
#   int  picolet_wkwv_load_html(void *webview, const char *html, const char *base_url)
#   int  picolet_wkwv_load_url(void *webview, const char *url)
#   int  picolet_wkwv_evaluate_js(void *webview, const char *js)
#   int  picolet_wkwv_register_message_handler(void)
#   char *picolet_wkwv_poll_inbound(void)
#   void  picolet_wkwv_free_inbound(char *s)
#   int  picolet_wkwv_register_scheme_handler(void (*cb)(...), void *user_data)
#   int  picolet_wkwv_scheme_respond(void *task, const char *ct,
#                                   const uint8_t *data, size_t len)
#   int  picolet_wkwv_scheme_error(void *task)
#   int  picolet_wkwv_pump_messages(double seconds)
#   int  picolet_wkwv_take_snapshot(void *webview, uint8_t **out_bytes, size_t *out_len)
#   int  picolet_wkwv_enable_inspector(int port)
#   int  picolet_wkwv_pick_test_port(void)

import ffi


def _safe_open(soname_or_none):
    try:
        return ffi.open(soname_or_none)
    except OSError as e:
        raise ImportError(
            "picolet_ui: failed to open {!r}: {}".format(soname_or_none, e)
        )


# ---------------------------------------------------------------------------
# Open the running Mach-O binary (for picolet_wkwv_* symbols).
# ffi.open(None) → dlopen(NULL) on Darwin — the main executable.
# All picolet_wkwv_* symbols carry __attribute__((visibility("default")))
# and the link line adds -Wl,-export_dynamic so dlsym finds them.
# ---------------------------------------------------------------------------

self_bin = _safe_open(None)


# ---------------------------------------------------------------------------
# picolet_webview_mac C overlay (compiled into the binary, __APPLE__ branch)
# ---------------------------------------------------------------------------

# int picolet_wkwv_init(void)
picolet_wkwv_init = self_bin.func("i", "picolet_wkwv_init", "")

# void *picolet_wkwv_create_window(const char *title, int w, int h)
picolet_wkwv_create_window = self_bin.func("p", "picolet_wkwv_create_window", "sii")

# int picolet_wkwv_show_window(void *window, int visible)
picolet_wkwv_show_window = self_bin.func("i", "picolet_wkwv_show_window", "pi")

# int picolet_wkwv_destroy_window(void *window)
picolet_wkwv_destroy_window = self_bin.func("i", "picolet_wkwv_destroy_window", "p")

# void *picolet_wkwv_create_webview(void *window, int w, int h)
picolet_wkwv_create_webview = self_bin.func("p", "picolet_wkwv_create_webview", "pii")

# int picolet_wkwv_load_html(void *webview, const char *html, const char *base_url)
# base_url may be NULL (pass 0 or None).
picolet_wkwv_load_html = self_bin.func("i", "picolet_wkwv_load_html", "pss")

# int picolet_wkwv_load_url(void *webview, const char *url)
picolet_wkwv_load_url = self_bin.func("i", "picolet_wkwv_load_url", "ps")

# int picolet_wkwv_evaluate_js(void *webview, const char *js)
picolet_wkwv_evaluate_js = self_bin.func("i", "picolet_wkwv_evaluate_js", "ps")

# int picolet_wkwv_register_message_handler(void)
picolet_wkwv_register_message_handler = self_bin.func(
    "i", "picolet_wkwv_register_message_handler", ""
)

# char *picolet_wkwv_poll_inbound(void)
# Returns a malloc'd NUL-terminated string, or NULL if the ring is empty.
# Caller must pass the pointer to picolet_wkwv_free_inbound when done.
picolet_wkwv_poll_inbound = self_bin.func("p", "picolet_wkwv_poll_inbound", "")

# void picolet_wkwv_free_inbound(char *s)
picolet_wkwv_free_inbound = self_bin.func("v", "picolet_wkwv_free_inbound", "p")

# int picolet_wkwv_register_scheme_handler(
#     void (*cb)(const char *path, void *task_opaque, void *user_data),
#     void *user_data)
# cb is a libffi closure with signature "v" "ppp" (three void* args, void return).
picolet_wkwv_register_scheme_handler = self_bin.func(
    "i", "picolet_wkwv_register_scheme_handler", "pp"
)

# int picolet_wkwv_scheme_respond(void *task_opaque, const char *content_type,
#                                const uint8_t *data, size_t data_len)
# data is a pointer to a Python bytes/bytearray buffer (p), data_len is L (uint64).
picolet_wkwv_scheme_respond = self_bin.func(
    "i", "picolet_wkwv_scheme_respond", "pspL"
)

# int picolet_wkwv_scheme_error(void *task_opaque)
picolet_wkwv_scheme_error = self_bin.func("i", "picolet_wkwv_scheme_error", "p")

# int picolet_wkwv_pump_messages(double seconds)
picolet_wkwv_pump_messages = self_bin.func("i", "picolet_wkwv_pump_messages", "d")

# int picolet_wkwv_take_snapshot(void *webview, uint8_t **out_bytes, size_t *out_len)
# out_bytes and out_len are output pointers — caller passes uctypes buffers.
picolet_wkwv_take_snapshot = self_bin.func("i", "picolet_wkwv_take_snapshot", "ppp")

# int picolet_wkwv_enable_inspector(int port)
# port = 0 to let the OS pick; returns the actual port (or -1 on failure).
picolet_wkwv_enable_inspector = self_bin.func("i", "picolet_wkwv_enable_inspector", "i")

# int picolet_wkwv_pick_test_port(void)
# Binds 127.0.0.1:0, reads the ephemeral port, closes.  Returns port or -1.
picolet_wkwv_pick_test_port = self_bin.func("i", "picolet_wkwv_pick_test_port", "")


# ---------------------------------------------------------------------------
# Helper: ffi_string — decode a NUL-terminated C string pointer to Python str.
# Matches the pattern in _win_ffi.py.
# ---------------------------------------------------------------------------


def ffi_string(ptr):
    """Decode a malloc'd UTF-8 NUL-terminated C string pointer."""
    if not ptr:
        return ""
    import uctypes
    out = bytearray()
    addr = int(ptr)
    cap = 4 * 1024 * 1024  # 4 MiB cap — webview JSON messages stay small
    for i in range(cap):
        b = uctypes.bytes_at(addr + i, 1)[0]
        if b == 0:
            break
        out.append(b)
    else:
        raise ValueError("ffi_string: no NUL terminator within 4 MiB")
    try:
        return bytes(out).decode("utf-8")
    except UnicodeError:
        return bytes(out).decode("utf-8", "replace")
