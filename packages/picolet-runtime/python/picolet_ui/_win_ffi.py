# picolet_ui._win_ffi — libffi bindings into Win32 + the in-process
# picolet_webview2 C overlay.
#
# PH10.  Pure libffi for user32.dll (Win32 windowing) + ffi.open(None)
# resolves the .exe's own exports for the picolet_wv2_* surface.  The
# overlay is statically linked into the runtime per AD2; symbols are
# exposed via -Wl,--export-all-symbols on the variant link line.
#
# Dynamic dependencies introduced (NFR-5 audit, PH13):
#
#   - user32.dll          system-installed; Win32 windowing API.
#   - kernel32.dll        system-installed; LoadLibraryW + GetModuleHandleW
#                         (consumed transitively by the overlay).
#   - ole32.dll           system-installed; CoInitializeEx (overlay).
#   - WebView2Loader.dll  bundled in app romfs, dlopen at runtime
#                         (overlay).
#
# All four are loaded dynamically; the runtime binary has no static
# import of WebView2Loader.dll (gate 7 confirms this).  NFR-9 is
# satisfied by Edge WebView2 Runtime presence on Windows 10 21H2+.
#
# FFI signature notation (modffi.c::char2ffi_type):
#
#   v = void               i = int32_t            I = uint32_t
#   l = int64_t            L = uint64_t           p = void *
#   s = char *             P = pointer            f = float / d = double
#   C = callback (closure)

import ffi


def _safe_open(soname_or_none):
    try:
        return ffi.open(soname_or_none)
    except OSError as e:
        raise ImportError(
            "picolet_ui: failed to open {!r}: {}".format(soname_or_none, e)
        )


# ---------------------------------------------------------------------------
# Open the running .exe (for picolet_wv2_*) and user32.dll (for window APIs)
# ---------------------------------------------------------------------------

# ffi.open(None) → GetModuleHandle(NULL) on the windows port — the
# running .exe.  All picolet_wv2_* symbols are statically linked into
# the .exe and exposed via -Wl,--export-all-symbols.  user32, ole32,
# kernel32 are reached transitively through the overlay (the overlay
# itself static-imports them).
self_exe = _safe_open(None)


# ---------------------------------------------------------------------------
# picolet_webview2 C overlay (statically linked into the .exe)
# ---------------------------------------------------------------------------

# int32_t picolet_wv2_last_error(void)
picolet_wv2_last_error = self_exe.func("i", "picolet_wv2_last_error", "")

# void *picolet_wv2_load_loader_dll(const uint8_t *bytes, size_t size)
# Python passes raw bytes via a uctypes-style pointer + length.
picolet_wv2_load_loader_dll = self_exe.func(
    "p", "picolet_wv2_load_loader_dll", "pL"
)

# int32_t picolet_wv2_init_com(void)
picolet_wv2_init_com = self_exe.func("i", "picolet_wv2_init_com", "")

# int32_t picolet_wv2_pick_test_port(void)
# PH17 (FR-TEST-1): pick a free 127.0.0.1 TCP port for CDP debugging.
picolet_wv2_pick_test_port = self_exe.func("i", "picolet_wv2_pick_test_port", "")

# void *picolet_wv2_create_environment_blocking(const wchar_t *extra_browser_args,
#                                              int32_t timeout_ms)
# PH17: extra_browser_args is a UTF-16 string or NULL (pass NULL for normal init).
# The 'p' param accepts a buffer pointer produced by extra_args.encode("utf-16-le")
# via uctypes; Python callers pass 0 (NULL) for the normal (no-test) path.
picolet_wv2_create_environment_blocking = self_exe.func(
    "p", "picolet_wv2_create_environment_blocking", "pi"
)

# void *picolet_wv2_create_controller_blocking(void *env, void *hwnd, int32_t timeout_ms)
picolet_wv2_create_controller_blocking = self_exe.func(
    "p", "picolet_wv2_create_controller_blocking", "ppi"
)

# int32_t picolet_wv2_set_visible(void *controller, int32_t visible)
picolet_wv2_set_visible = self_exe.func("i", "picolet_wv2_set_visible", "pi")

# int32_t picolet_wv2_set_bounds(void *controller, int32_t w, int32_t h)
picolet_wv2_set_bounds = self_exe.func("i", "picolet_wv2_set_bounds", "pii")

# int32_t picolet_wv2_close_controller(void *controller)
picolet_wv2_close_controller = self_exe.func(
    "i", "picolet_wv2_close_controller", "p"
)

# int32_t picolet_wv2_add_script_to_execute_on_document_created(
#     void *controller, const char *js_utf8, int32_t timeout_ms)
picolet_wv2_add_script_to_execute_on_document_created = self_exe.func(
    "i", "picolet_wv2_add_script_to_execute_on_document_created", "psi"
)

# int32_t picolet_wv2_navigate_to_string(void *controller, const char *html_utf8)
picolet_wv2_navigate_to_string = self_exe.func(
    "i", "picolet_wv2_navigate_to_string", "ps"
)

# int32_t picolet_wv2_navigate(void *controller, const wchar_t *url)
# url is a pointer to a UTF-16-LE buffer (pass buf from url.encode("utf-16-le")).
picolet_wv2_navigate = self_exe.func("i", "picolet_wv2_navigate", "pp")

# int32_t picolet_wv2_execute_script(void *controller, const char *js_utf8)
picolet_wv2_execute_script = self_exe.func(
    "i", "picolet_wv2_execute_script", "ps"
)

# int32_t picolet_wv2_register_inbound_handler(void *controller)
picolet_wv2_register_inbound_handler = self_exe.func(
    "i", "picolet_wv2_register_inbound_handler", "p"
)

# char *picolet_wv2_poll_inbound(void)
picolet_wv2_poll_inbound = self_exe.func("p", "picolet_wv2_poll_inbound", "")

# void picolet_wv2_free_inbound(char *s)
picolet_wv2_free_inbound = self_exe.func("v", "picolet_wv2_free_inbound", "p")

# int32_t picolet_wv2_pump_messages(void)
picolet_wv2_pump_messages = self_exe.func("i", "picolet_wv2_pump_messages", "")

# void *picolet_wv2_create_window(const char *title_utf8, int32_t w, int32_t h, int32_t resizable)
picolet_wv2_create_window = self_exe.func(
    "p", "picolet_wv2_create_window", "siii"
)

# int32_t picolet_wv2_show_window(void *hwnd, int32_t visible)
picolet_wv2_show_window = self_exe.func("i", "picolet_wv2_show_window", "pi")

# int32_t picolet_wv2_window_attach_controller(void *hwnd, void *controller)
picolet_wv2_window_attach_controller = self_exe.func(
    "i", "picolet_wv2_window_attach_controller", "pp"
)

# int32_t picolet_wv2_destroy_window(void *hwnd)
picolet_wv2_destroy_window = self_exe.func(
    "i", "picolet_wv2_destroy_window", "p"
)


# ---------------------------------------------------------------------------
# Helper: ffi_string for reading NUL-terminated C strings into Python str.
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
