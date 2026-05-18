/*
 * picolet_webview_mac.h — public C surface for the macOS WKWebView backend.
 *
 * PH25.  All ObjC calls are made through the public objc_msgSend ABI
 * (libobjc.A.dylib) — no static ObjC++ linkage.
 *
 * Returned pointers are opaque handles to Objective-C objects; callers
 * must not dereference or retain them directly — use the functions below.
 *
 * License: MIT (picolet code).
 *
 * Ref: https://developer.apple.com/documentation/webkit/wkwebview
 */

#ifndef PICOLET_WEBVIEW_MAC_H
#define PICOLET_WEBVIEW_MAC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Lifecycle
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_init — initialise NSApplication.
 *
 * Calls [NSApplication sharedApplication] and sets the activation policy
 * to NSApplicationActivationPolicyRegular so the app appears in the Dock
 * and receives keyboard/mouse events normally.
 *
 * Must be called once before any window or webview is created.
 * Returns 0 on success, -1 on failure.
 */
int picolet_wkwv_init(void);

/* ---------------------------------------------------------------------------
 * Window
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_create_window — create an NSWindow.
 *
 * title    UTF-8 window title (NULL → "picolet").
 * w, h     Initial content-area size in pixels.
 *
 * Returns an opaque NSWindow* handle, or NULL on failure.
 */
void *picolet_wkwv_create_window(const char *title, int w, int h);

/*
 * picolet_wkwv_show_window — show (or hide) an NSWindow.
 *
 * visible  non-zero → makeKeyAndOrderFront, zero → orderOut.
 * Returns 0.
 */
int picolet_wkwv_show_window(void *window, int visible);

/*
 * picolet_wkwv_destroy_window — close and release an NSWindow.
 */
int picolet_wkwv_destroy_window(void *window);

/* ---------------------------------------------------------------------------
 * WKWebView
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_create_webview — create a WKWebView filling the window's
 * content view.
 *
 * window   NSWindow* returned by picolet_wkwv_create_window.
 * w, h     Initial pixel size (should match the window content area).
 *
 * Returns an opaque WKWebView* handle, or NULL on failure.
 *
 * Note: this function also creates the WKWebViewConfiguration and
 * WKUserContentController used by register_scheme_handler and
 * register_message_handler.  Both handlers must therefore be registered
 * before calling this function if they are to be active from page load.
 */
void *picolet_wkwv_create_webview(void *window, int w, int h);

/*
 * picolet_wkwv_load_html — load an HTML string into the webview.
 *
 * html      UTF-8 HTML source.
 * base_url  Base URL string (e.g. "picolet://ui/") or NULL.
 *
 * Returns 0 on success, -1 on failure.
 */
int picolet_wkwv_load_html(void *webview, const char *html, const char *base_url);

/*
 * picolet_wkwv_load_url — navigate the webview to a URL.
 *
 * url  UTF-8 URL string.
 *
 * Returns 0 on success, -1 on failure.
 */
int picolet_wkwv_load_url(void *webview, const char *url);

/*
 * picolet_wkwv_evaluate_js — execute JavaScript in the webview.
 *
 * js  NUL-terminated UTF-8 JavaScript source.  Completion is ignored.
 *
 * Returns 0 on success, -1 on failure.
 */
int picolet_wkwv_evaluate_js(void *webview, const char *js);

/* ---------------------------------------------------------------------------
 * JS → Python message bridge
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_register_message_handler — register the "picolet" WKScriptMessage
 * handler.
 *
 * This must be called before picolet_wkwv_create_webview so that the handler
 * is active in the WKWebViewConfiguration.  The class
 * PicoletScriptMessageHandler is created at runtime via objc_allocateClassPair
 * the first time this function is called; subsequent calls are no-ops.
 *
 * Returns 0 on success, -1 on failure.
 *
 * Ref: https://developer.apple.com/documentation/webkit/wkusercontentcontroller
 */
int picolet_wkwv_register_message_handler(void);

/*
 * picolet_wkwv_poll_inbound — dequeue the next JS→Python JSON message.
 *
 * Returns a malloc'd NUL-terminated UTF-8 string, or NULL if the queue is
 * empty.  The caller must pass the returned pointer to
 * picolet_wkwv_free_inbound when done.
 */
char *picolet_wkwv_poll_inbound(void);

/*
 * picolet_wkwv_free_inbound — free a message returned by picolet_wkwv_poll_inbound.
 */
void picolet_wkwv_free_inbound(char *s);

/* ---------------------------------------------------------------------------
 * picolet:// URL scheme handler
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_register_scheme_handler — register a WKURLSchemeHandler for the
 * "picolet" URL scheme.
 *
 * cb         C callback invoked for each request.  Signature:
 *               void cb(const char *path, void *user_data)
 *            where path is the URL path component (e.g. "/ui/index.html").
 *            The callback must call picolet_wkwv_scheme_respond (or
 *            picolet_wkwv_scheme_error) before returning to complete the task.
 *            task_cookie is an opaque value the callback must pass back.
 *            NOTE: in practice the Python layer uses picolet_wkwv_scheme_respond_for_task.
 *
 * user_data  Passed through to cb unchanged.
 *
 * This must be called before picolet_wkwv_create_webview.
 *
 * Returns 0 on success, -1 on failure.
 *
 * Ref: https://developer.apple.com/documentation/webkit/wkurlschemehandler
 */
int picolet_wkwv_register_scheme_handler(
    void (*cb)(const char *path, void *task_opaque, void *user_data),
    void *user_data);

/*
 * picolet_wkwv_scheme_respond — send a successful response for a URL scheme task.
 *
 * task_opaque   Opaque task pointer passed to the scheme handler callback.
 * content_type  MIME type string (e.g. "text/html; charset=utf-8").
 * data          Response body bytes.
 * data_len      Length of data.
 *
 * Returns 0 on success, -1 on failure.
 */
int picolet_wkwv_scheme_respond(void *task_opaque,
                               const char *content_type,
                               const uint8_t *data, size_t data_len);

/*
 * picolet_wkwv_scheme_error — send a 404 error response for a URL scheme task.
 */
int picolet_wkwv_scheme_error(void *task_opaque);

/* ---------------------------------------------------------------------------
 * Run loop
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_pump_messages — drain the Cocoa run loop for up to `seconds`.
 *
 * Called periodically from the asyncio event loop (same pattern as GTK's
 * gtk_main_iteration_do).  Internally calls CFRunLoopRunInMode.
 *
 * Returns 0.
 */
int picolet_wkwv_pump_messages(double seconds);

/* ---------------------------------------------------------------------------
 * Screenshot
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_take_snapshot — take a PNG screenshot of the webview.
 *
 * out_bytes  Pointer set to a malloc'd buffer containing PNG data.  Caller
 *            must free(*out_bytes).
 * out_len    Set to the byte length of *out_bytes.
 *
 * Blocks via a dispatch semaphore + CFRunLoopRunInMode loop until the
 * WKWebView completion handler fires (or 5 s timeout).
 *
 * Returns 0 on success, -1 on failure.
 *
 * Ref: https://developer.apple.com/documentation/webkit/wkwebview/2867262-takesnapshotwithconfiguration
 */
int picolet_wkwv_take_snapshot(void *webview, uint8_t **out_bytes, size_t *out_len);

/* ---------------------------------------------------------------------------
 * Inspector (PICOLET_TEST_MODE)
 * --------------------------------------------------------------------------*/

/*
 * picolet_wkwv_enable_inspector — enable the WKWebView remote inspector.
 *
 * Sets NSUserDefaults WebInspectorServerEnabled + WebInspectorPort and
 * enables developer extras in WKPreferences.
 *
 * port  TCP port to bind.  Pass 0 to request any free port (the actual
 *       bound port is returned via the return value on success; -1 on
 *       failure).
 *
 * This must be called before picolet_wkwv_create_webview.
 *
 * Note: WebInspectorPort NSUserDefaults support varies across macOS versions.
 * The key is documented as working on macOS 12+; on earlier versions the
 * inspector may use an OS-assigned port via Bonjour/WKRP.  See the v1.2
 * spec FR-WV-MAC-7 and the phase-25 risk notes.
 */
int picolet_wkwv_enable_inspector(int port);

/*
 * picolet_wkwv_pick_test_port — bind TCP 127.0.0.1:0, read the assigned
 * port, and close.  Returns the port number on success, -1 on failure.
 *
 * Same pattern as picolet_wv2_pick_test_port on Windows.
 */
int picolet_wkwv_pick_test_port(void);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_WEBVIEW_MAC_H */
