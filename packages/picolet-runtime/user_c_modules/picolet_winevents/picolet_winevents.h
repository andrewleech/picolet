/*
 * picolet_winevents.h — generic Win32 event hook for the picolet runtime.
 *
 * Installs a SetWindowSubclass-based event tap on a picolet-owned HWND so
 * arbitrary WM_* messages can be observed (or vetoed) from Python.
 *
 * The C surface is variant-agnostic: it works against any HWND, regardless
 * of whether the host variant is `webview` (picolet_wv2_*) or `lvgl` (SDL2's
 * top-level window).  Python obtains the HWND from the active variant and
 * passes it in.
 *
 * Threading: the underlying wndproc executes on the thread that owns the
 * window's message queue — by construction the same thread that drives
 * the message pump (the Python thread).  Both producer and consumer of
 * the ring buffer therefore run on the same thread; no atomics needed.
 *
 * Symbol resolution: this file is compiled into the runtime .exe.  Python
 * reaches each symbol via `ffi.open(None).func(...)` — the variant Makefile
 * exports via either `-Wl,--export-all-symbols` (webview) or per-symbol
 * `-Wl,--undefined=` retains (lvgl).
 *
 * License: MIT (picolet code).
 */

#ifndef PICOLET_WINEVENTS_H
#define PICOLET_WINEVENTS_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Lifecycle ---------------------------------------------------------- */

/* Install the subclass on `hwnd`.  Idempotent; safe to call multiple times.
 * Returns 0 on success, negative on failure (sets last_error). */
int32_t picolet_winevents_attach(void *hwnd);

/* Remove the subclass and free any associated state.  Safe to call on a
 * never-attached HWND. */
int32_t picolet_winevents_detach(void *hwnd);

/* ---- Subscription ------------------------------------------------------- */

/* Subscribe to a WM_* message.  `consume != 0` means the subclass returns 0
 * for that message instead of calling DefSubclassProc — useful to veto
 * WM_CLOSE so Python can decide whether to actually destroy the window.
 *
 * Idempotent: subscribing to the same msg twice updates the consume flag.
 *
 * Returns 0 on success, -1 on table full, -2 on hwnd not attached. */
int32_t picolet_winevents_subscribe(void *hwnd, uint32_t msg, int32_t consume);

/* Remove a subscription.  Returns 0 even if msg was not subscribed. */
int32_t picolet_winevents_unsubscribe(void *hwnd, uint32_t msg);

/* ---- Polling ------------------------------------------------------------ */

/* Drain the ring into a malloc'd JSON-encoded byte string of the form:
 *
 *   [
 *     {"msg": 537, "wp": 7, "lp": 0, "extra": "\\\\?\\USB#VID..."},
 *     ...
 *   ]
 *
 * Returns NULL when the ring is empty.  The caller must release the buffer
 * with picolet_winevents_free().  `extra` is the optional UTF-8 payload
 * captured at fire time (e.g. the device-interface path).  When no extra
 * payload is associated with an event, the "extra" key is omitted.
 *
 * On out-of-memory or encoding failure, returns NULL and sets last_error. */
char *picolet_winevents_poll_json(void *hwnd);
void picolet_winevents_free(char *buf);

/* Number of events dropped due to ring overflow since the last call.
 * Reading also clears the counter. */
int32_t picolet_winevents_overflow_count(void *hwnd);

/* ---- Convenience registrations ------------------------------------------ */

/* Register for WM_DEVICECHANGE notifications for a device-interface class.
 * `guid16_bytes` is a 16-byte little-endian GUID buffer (the on-the-wire
 * form Python produces via uuid.UUID(...).bytes_le).
 *
 * The most common GUIDs:
 *   GUID_DEVINTERFACE_USB_DEVICE  A5DCBF10-6530-11D2-901F-00C04FB951ED
 *   GUID_DEVINTERFACE_HID         4D1E55B2-F16F-11CF-88CB-001111000030
 *   GUID_DEVINTERFACE_COMPORT     86E0D1E0-8089-11D0-9CE4-08003E301F73
 *
 * Returns 0 on success, negative on failure.  Also implicitly subscribes
 * to WM_DEVICECHANGE (without consume), so a separate subscribe() call is
 * not needed for the standard "observe device events" use case. */
int32_t picolet_winevents_watch_device_interface(void *hwnd,
                                                const uint8_t *guid16_bytes);

/* Register for system power-state notifications (WM_POWERBROADCAST). */
int32_t picolet_winevents_watch_power(void *hwnd);

/* Register for session notifications (WM_WTSSESSION_CHANGE) — lock,
 * unlock, RDP connect/disconnect. */
int32_t picolet_winevents_watch_session(void *hwnd);

/* Register the window as a clipboard format listener (WM_CLIPBOARDUPDATE). */
int32_t picolet_winevents_watch_clipboard(void *hwnd);

/* Enable WM_DROPFILES delivery (DragAcceptFiles).  `enable=0` disables. */
int32_t picolet_winevents_accept_drop_files(void *hwnd, int32_t enable);

/* ---- Errors ------------------------------------------------------------- */

/* Last HRESULT / GetLastError set by any of the above.  Cleared on any
 * successful call. */
int32_t picolet_winevents_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_WINEVENTS_H */
