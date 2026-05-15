/*
 * picolet_webview2.h — flat C API for WebView2 surfaced to Python via libffi.
 *
 * PH10.  Statically linked into picolet-runtime-windows-x64-webview.exe.
 * Python resolves each symbol via libffi.ffi.open(NULL)-equivalent
 * (GetModuleHandleW(NULL) wrapper) and calls them through ffi.func
 * declarations in picolet_ui_win/_win_ffi.py.
 *
 * Symbols are kept ABI-stable across patch releases: arg counts and
 * pointer-vs-int shapes do not change.  All blocking helpers pump the
 * Win32 message queue while waiting (STA affinity preserved per AD4).
 *
 * Return-code convention:
 *   * Pointer-returning APIs return NULL on failure; a follow-up call
 *     to picolet_wv2_last_error() retrieves the HRESULT.
 *   * Int-returning APIs return 0 on success, non-zero on failure
 *     (HRESULT value cast to int32_t).
 *
 * PH13 SBOM note — new dynamic dependencies introduced by this module:
 *   * WebView2Loader.dll — Microsoft WebView2 SDK License (permissive),
 *     bundled in app romfs by `picolet build`, dlopen at runtime.
 *   * Edge WebView2 Runtime (system-installed) — reached transitively
 *     via the loader; never redistributed.
 */

#ifndef PICOLET_WEBVIEW2_H
#define PICOLET_WEBVIEW2_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Last HRESULT (or Win32 error converted to HRESULT) from the previous
 * picolet_wv2_* call on the calling thread.  Read from Python after any
 * NULL/non-zero return to surface a useful error message. */
int32_t picolet_wv2_last_error(void);

/* Loader path: extract WebView2Loader.dll bytes (passed in from Python
 * via the romfs) to %LOCALAPPDATA%\picolet\<pid>\WebView2Loader.dll and
 * LoadLibraryW it.  Returns the HMODULE as an opaque pointer, or NULL
 * on failure.  Idempotent: subsequent calls return the cached handle. */
void *picolet_wv2_load_loader_dll(const uint8_t *bytes, size_t size);

/* Initialise COM (STA) on the calling thread.  Returns 0 on success,
 * non-zero on failure.  Idempotent: subsequent calls succeed without
 * re-initialising. */
int32_t picolet_wv2_init_com(void);

/* Create the WebView2 environment, blocking the calling thread (pumping
 * the message queue) until the async completion fires or `timeout_ms`
 * elapses.  Returns an opaque ICoreWebView2Environment * on success,
 * NULL on timeout/error (see picolet_wv2_last_error()). */
void *picolet_wv2_create_environment_blocking(int32_t timeout_ms);

/* Create the WebView2 controller parented to `hwnd`, blocking with the
 * same message-pump pattern.  Caches the underlying ICoreWebView2 on
 * the controller-wrapper for fast access from later calls. */
void *picolet_wv2_create_controller_blocking(void *env, void *hwnd, int32_t timeout_ms);

/* Show / hide the controller's child windows.  `visible` is 0 or 1. */
int32_t picolet_wv2_set_visible(void *controller, int32_t visible);

/* Resize the controller's bounds to (0, 0) — (width, height) in pixels. */
int32_t picolet_wv2_set_bounds(void *controller, int32_t width, int32_t height);

/* Close the controller.  Idempotent. */
int32_t picolet_wv2_close_controller(void *controller);

/* AddScriptToExecuteOnDocumentCreated: register `js_utf8` to run before
 * any user JS in newly navigated documents.  Used to inject the
 * picolet-bridge-js bundle (FR-WV-4).  Blocking until completion. */
int32_t picolet_wv2_add_script_to_execute_on_document_created(
    void *controller, const char *js_utf8, int32_t timeout_ms);

/* NavigateToString: load the HTML `html_utf8` into the controller's
 * WebView2 — the WebView2 equivalent of webkit_web_view_load_html.
 * Fire-and-forget; no completion callback. */
int32_t picolet_wv2_navigate_to_string(void *controller, const char *html_utf8);

/* ExecuteScript: run `js_utf8` in the page.  We don't need the result;
 * pass a do-nothing completion handler. */
int32_t picolet_wv2_execute_script(void *controller, const char *js_utf8);

/* Register the inbound `WebMessageReceived` handler.  Idempotent — only
 * the first call effects.  The handler pushes incoming JSON strings
 * (UTF-8) onto an internal ring buffer drained by picolet_wv2_poll_inbound. */
int32_t picolet_wv2_register_inbound_handler(void *controller);

/* Drain one inbound message from the ring buffer.  Returns a malloc'd
 * UTF-8 NUL-terminated string the caller must free with
 * picolet_wv2_free_inbound, or NULL if the buffer is empty. */
char *picolet_wv2_poll_inbound(void);

/* Free a string returned by picolet_wv2_poll_inbound. */
void picolet_wv2_free_inbound(char *s);

/* Single-step the Win32 message queue.  Returns the number of messages
 * dispatched.  Called from the Python pump task per tick. */
int32_t picolet_wv2_pump_messages(void);

/* Top-level window creation.
 *
 * The C overlay owns the window class registration, the WindowProc
 * callback (which forwards WM_SIZE to the attached controller via
 * put_Bounds and WM_DESTROY to PostQuitMessage), and the window
 * lifecycle.  Python sees a single opaque HWND handle.
 *
 * `title_utf8` is UTF-8; the overlay converts to UTF-16 for SetWindowTextW.
 * `width` / `height` are client-area pixels; 0 picks defaults.
 * `resizable` is 0 (fixed) or 1 (sizable).
 * Returns an HWND, or NULL on failure. */
void *picolet_wv2_create_window(const char *title_utf8,
                              int32_t width, int32_t height,
                              int32_t resizable);

/* Show or hide the window.  `visible` = 0 hides, 1 shows. */
int32_t picolet_wv2_show_window(void *hwnd, int32_t visible);

/* Associate a controller with this HWND so the WindowProc forwards
 * WM_SIZE events to it.  Must be called after both
 * picolet_wv2_create_window and picolet_wv2_create_controller_blocking. */
int32_t picolet_wv2_window_attach_controller(void *hwnd, void *controller);

/* Destroy the window.  Idempotent. */
int32_t picolet_wv2_destroy_window(void *hwnd);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_WEBVIEW2_H */
