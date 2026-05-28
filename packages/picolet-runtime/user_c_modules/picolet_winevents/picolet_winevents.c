/*
 * picolet_winevents.c — Win32 event hook implementation.
 *
 * Design overview
 * ===============
 *
 *  * One picolet_winevents_ctx_t is allocated per HWND on first attach() and
 *    stashed via SetPropW(hwnd, L"PicoletWinEvents", ctx).  SetProp is used
 *    instead of GWLP_USERDATA because the variants already own that slot
 *    (webview keeps the ICoreWebView2Controller* there; lvgl/SDL2 owns its
 *    own per-window data).
 *
 *  * SetWindowSubclass installs subclass_proc with the ctx pointer as
 *    dwRefData.  The subclass executes BEFORE the original wndproc, so we
 *    can observe every message and consume it (return 0 without calling
 *    DefSubclassProc) when the subscription requests it.
 *
 *  * Events are pushed onto a fixed-size ring buffer (RING_CAP = 64).  Each
 *    entry inlines an extra-payload byte buffer (EXTRA_CAP = 512) for
 *    messages whose lParam dereferences data that becomes invalid after
 *    the wndproc returns (WM_DEVICECHANGE, WM_SETTINGCHANGE, WM_DROPFILES).
 *    The extra is captured to UTF-8 inside the wndproc while the original
 *    pointer is still live.
 *
 *  * poll_json() drains the ring into a malloc'd JSON byte string.  No
 *    micropython-side allocations; Python json.loads() handles parsing.
 *
 *  * The wndproc and the pump (which is what calls poll_json) both run on
 *    the same thread.  Ring head/tail are plain int — no atomics needed.
 *
 * License: MIT (picolet code).
 */

#include "picolet_winevents.h"

#include <windows.h>
#include <commctrl.h>      /* SetWindowSubclass */
#include <dbt.h>           /* DBT_DEVICEARRIVAL / DEV_BROADCAST_DEVICEINTERFACE_W */
#include <shellapi.h>      /* DragAcceptFiles / DragQueryFileW */
#include <wtsapi32.h>      /* WTSRegisterSessionNotification */
#include <powrprof.h>      /* RegisterPowerSettingNotification */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ---- Configuration ------------------------------------------------------ */

#define RING_CAP       64
#define EXTRA_CAP     512
#define MAX_SUBSCRIBE  32
#define SUBCLASS_ID     1

#define PROP_NAME L"PicoletWinEvents"

/* ---- Types -------------------------------------------------------------- */

typedef struct {
    uint32_t msg;
    uint64_t wparam;
    int64_t  lparam;
    uint32_t extra_len;          /* 0 if no payload */
    uint8_t  extra[EXTRA_CAP];   /* UTF-8 bytes; not NUL-terminated */
} ring_entry_t;

typedef struct {
    uint32_t msg;
    int      consume;
} sub_entry_t;

typedef struct picolet_winevents_ctx_s {
    ring_entry_t  ring[RING_CAP];
    int           ring_head;     /* write index */
    int           ring_tail;     /* read index */
    int           overflow;      /* dropped event counter (consumed by overflow_count) */

    sub_entry_t   subs[MAX_SUBSCRIBE];
    int           n_subs;

    /* Lifetime handles for the convenience watches; we keep them so
     * detach() can unregister cleanly. */
    HDEVNOTIFY    dev_notify;
    HPOWERNOTIFY  power_notify;
    int           session_registered;
    int           clipboard_listener;
} picolet_winevents_ctx_t;

/* ---- Last-error slot ---------------------------------------------------- */

static int32_t g_last_error = 0;

static inline void set_last(int32_t err) { g_last_error = err; }

int32_t picolet_winevents_last_error(void) { return g_last_error; }

/* ---- Context helpers ---------------------------------------------------- */

static picolet_winevents_ctx_t *get_ctx(HWND hwnd) {
    if (hwnd == NULL) return NULL;
    return (picolet_winevents_ctx_t *)GetPropW(hwnd, PROP_NAME);
}

static int find_sub(picolet_winevents_ctx_t *ctx, uint32_t msg) {
    for (int i = 0; i < ctx->n_subs; i++) {
        if (ctx->subs[i].msg == msg) return i;
    }
    return -1;
}

/* ---- Ring buffer -------------------------------------------------------- */

/* Capture optional UTF-8 payload from lParam (out-of-band data that becomes
 * invalid once the wndproc returns).  Returns bytes written to entry->extra,
 * possibly 0. */
static uint32_t capture_extra(ring_entry_t *entry, UINT msg, WPARAM wp, LPARAM lp) {
    (void)wp;
    if (msg == WM_DEVICECHANGE && lp != 0) {
        DEV_BROADCAST_HDR *hdr = (DEV_BROADCAST_HDR *)lp;
        if (hdr->dbch_devicetype == DBT_DEVTYP_DEVICEINTERFACE) {
            DEV_BROADCAST_DEVICEINTERFACE_W *di =
                (DEV_BROADCAST_DEVICEINTERFACE_W *)lp;
            int n = WideCharToMultiByte(CP_UTF8, 0, di->dbcc_name, -1,
                                        (char *)entry->extra, EXTRA_CAP,
                                        NULL, NULL);
            if (n > 0) {
                /* WCTMB writes a trailing NUL; we report length without it. */
                return (uint32_t)(n - 1);
            }
        }
        return 0;
    }
    if (msg == WM_SETTINGCHANGE && lp != 0) {
        /* lParam is a LPCTSTR — the "area" name like "ImmersiveColorSet". */
        int n = WideCharToMultiByte(CP_UTF8, 0, (LPCWSTR)lp, -1,
                                    (char *)entry->extra, EXTRA_CAP,
                                    NULL, NULL);
        if (n > 0) return (uint32_t)(n - 1);
        return 0;
    }
    if (msg == WM_DROPFILES) {
        HDROP hdrop = (HDROP)wp;
        UINT count = DragQueryFileW(hdrop, 0xFFFFFFFF, NULL, 0);
        if (count == 0) return 0;
        /* Encode as newline-separated UTF-8 paths.  Bytes that wouldn't
         * fit are truncated; downstream code should treat extra as
         * advisory + check overflow_count(). */
        uint32_t pos = 0;
        for (UINT i = 0; i < count && pos < EXTRA_CAP; i++) {
            WCHAR wbuf[MAX_PATH];
            UINT got = DragQueryFileW(hdrop, i, wbuf, MAX_PATH);
            if (got == 0) continue;
            int n = WideCharToMultiByte(CP_UTF8, 0, wbuf, -1,
                                        (char *)entry->extra + pos,
                                        EXTRA_CAP - pos, NULL, NULL);
            if (n <= 0) break;
            pos += (uint32_t)(n - 1);
            if (i + 1 < count && pos < EXTRA_CAP) {
                entry->extra[pos++] = (uint8_t)'\n';
            }
        }
        return pos;
    }
    return 0;
}

static void ring_push(picolet_winevents_ctx_t *ctx, UINT msg, WPARAM wp, LPARAM lp) {
    int next = (ctx->ring_head + 1) % RING_CAP;
    if (next == ctx->ring_tail) {
        /* Full — drop oldest, advance tail, then push. */
        ctx->ring_tail = (ctx->ring_tail + 1) % RING_CAP;
        ctx->overflow++;
    }
    ring_entry_t *e = &ctx->ring[ctx->ring_head];
    e->msg = (uint32_t)msg;
    e->wparam = (uint64_t)wp;
    e->lparam = (int64_t)lp;
    e->extra_len = capture_extra(e, msg, wp, lp);
    ctx->ring_head = next;
}

/* ---- Subclass procedure ------------------------------------------------- */

static LRESULT CALLBACK subclass_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp,
                                       UINT_PTR id, DWORD_PTR ref) {
    (void)id;
    picolet_winevents_ctx_t *ctx = (picolet_winevents_ctx_t *)(uintptr_t)ref;
    if (ctx == NULL) {
        return DefSubclassProc(hwnd, msg, wp, lp);
    }

    int idx = find_sub(ctx, (uint32_t)msg);
    if (idx >= 0) {
        ring_push(ctx, msg, wp, lp);
        if (ctx->subs[idx].consume) {
            return 0;
        }
    }
    return DefSubclassProc(hwnd, msg, wp, lp);
}

/* ---- Attach / detach ---------------------------------------------------- */

int32_t picolet_winevents_attach(void *hwnd_p) {
    HWND hwnd = (HWND)hwnd_p;
    if (hwnd == NULL) { set_last(E_INVALIDARG); return -1; }

    if (get_ctx(hwnd) != NULL) {
        /* Already attached. */
        set_last(0);
        return 0;
    }

    picolet_winevents_ctx_t *ctx = calloc(1, sizeof(*ctx));
    if (ctx == NULL) { set_last(E_OUTOFMEMORY); return -2; }

    if (!SetPropW(hwnd, PROP_NAME, (HANDLE)ctx)) {
        free(ctx);
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }

    if (!SetWindowSubclass(hwnd, subclass_proc, SUBCLASS_ID,
                            (DWORD_PTR)(uintptr_t)ctx)) {
        RemovePropW(hwnd, PROP_NAME);
        free(ctx);
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -4;
    }

    set_last(0);
    return 0;
}

int32_t picolet_winevents_detach(void *hwnd_p) {
    HWND hwnd = (HWND)hwnd_p;
    if (hwnd == NULL) { set_last(E_INVALIDARG); return -1; }
    picolet_winevents_ctx_t *ctx = get_ctx(hwnd);
    if (ctx == NULL) return 0;

    /* Tear down the convenience registrations. */
    if (ctx->dev_notify) {
        UnregisterDeviceNotification(ctx->dev_notify);
        ctx->dev_notify = NULL;
    }
    if (ctx->power_notify) {
        UnregisterPowerSettingNotification(ctx->power_notify);
        ctx->power_notify = NULL;
    }
    if (ctx->session_registered) {
        WTSUnRegisterSessionNotification(hwnd);
        ctx->session_registered = 0;
    }
    if (ctx->clipboard_listener) {
        RemoveClipboardFormatListener(hwnd);
        ctx->clipboard_listener = 0;
    }

    RemoveWindowSubclass(hwnd, subclass_proc, SUBCLASS_ID);
    RemovePropW(hwnd, PROP_NAME);
    free(ctx);
    set_last(0);
    return 0;
}

/* ---- Subscribe / unsubscribe -------------------------------------------- */

int32_t picolet_winevents_subscribe(void *hwnd_p, uint32_t msg, int32_t consume) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    int idx = find_sub(ctx, msg);
    if (idx >= 0) {
        ctx->subs[idx].consume = consume ? 1 : 0;
        set_last(0);
        return 0;
    }
    /* E_BOUNDS (0x8000000B) isn't in MinGW's winerror.h; use the WIN32
     * equivalent HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER). */
    if (ctx->n_subs >= MAX_SUBSCRIBE) {
        set_last(HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER));
        return -1;
    }
    ctx->subs[ctx->n_subs].msg = msg;
    ctx->subs[ctx->n_subs].consume = consume ? 1 : 0;
    ctx->n_subs++;
    set_last(0);
    return 0;
}

int32_t picolet_winevents_unsubscribe(void *hwnd_p, uint32_t msg) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    int idx = find_sub(ctx, msg);
    if (idx < 0) { set_last(0); return 0; }
    /* Compact: shift tail down. */
    for (int i = idx; i + 1 < ctx->n_subs; i++) {
        ctx->subs[i] = ctx->subs[i + 1];
    }
    ctx->n_subs--;
    set_last(0);
    return 0;
}

/* ---- Polling ------------------------------------------------------------ */

/* Append a string to a buffer, growing as needed.  Returns 0 on success. */
typedef struct {
    char    *buf;
    size_t   len;
    size_t   cap;
} sbuf_t;

static int sbuf_reserve(sbuf_t *s, size_t want) {
    if (s->len + want + 1 <= s->cap) return 0;
    size_t ncap = s->cap == 0 ? 256 : s->cap;
    while (ncap < s->len + want + 1) ncap *= 2;
    char *nb = realloc(s->buf, ncap);
    if (nb == NULL) return -1;
    s->buf = nb;
    s->cap = ncap;
    return 0;
}

static int sbuf_putc(sbuf_t *s, char c) {
    if (sbuf_reserve(s, 1) < 0) return -1;
    s->buf[s->len++] = c;
    return 0;
}

static int sbuf_puts(sbuf_t *s, const char *p) {
    size_t n = strlen(p);
    if (sbuf_reserve(s, n) < 0) return -1;
    memcpy(s->buf + s->len, p, n);
    s->len += n;
    return 0;
}

static int sbuf_putd(sbuf_t *s, long long v) {
    char tmp[32];
    int n = snprintf(tmp, sizeof(tmp), "%lld", v);
    if (n < 0) return -1;
    return sbuf_puts(s, tmp);
}

static int sbuf_putu(sbuf_t *s, unsigned long long v) {
    char tmp[32];
    int n = snprintf(tmp, sizeof(tmp), "%llu", v);
    if (n < 0) return -1;
    return sbuf_puts(s, tmp);
}

/* Emit a JSON string literal, escaping per RFC 8259.  Input is UTF-8 bytes
 * not necessarily NUL-terminated. */
static int sbuf_putjsonstr(sbuf_t *s, const uint8_t *bytes, size_t n) {
    if (sbuf_putc(s, '"') < 0) return -1;
    for (size_t i = 0; i < n; i++) {
        uint8_t c = bytes[i];
        if (c == '"' || c == '\\') {
            if (sbuf_putc(s, '\\') < 0) return -1;
            if (sbuf_putc(s, (char)c) < 0) return -1;
        } else if (c == '\b') { if (sbuf_puts(s, "\\b") < 0) return -1; }
          else if (c == '\f') { if (sbuf_puts(s, "\\f") < 0) return -1; }
          else if (c == '\n') { if (sbuf_puts(s, "\\n") < 0) return -1; }
          else if (c == '\r') { if (sbuf_puts(s, "\\r") < 0) return -1; }
          else if (c == '\t') { if (sbuf_puts(s, "\\t") < 0) return -1; }
          else if (c < 0x20) {
            char tmp[8];
            int m = snprintf(tmp, sizeof(tmp), "\\u%04x", c);
            if (m < 0) return -1;
            if (sbuf_puts(s, tmp) < 0) return -1;
        } else {
            /* UTF-8 byte; pass through. */
            if (sbuf_putc(s, (char)c) < 0) return -1;
        }
    }
    return sbuf_putc(s, '"');
}

char *picolet_winevents_poll_json(void *hwnd_p) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return NULL; }
    if (ctx->ring_head == ctx->ring_tail) return NULL;

    sbuf_t s = {0};
    if (sbuf_putc(&s, '[') < 0) goto oom;

    int first = 1;
    while (ctx->ring_tail != ctx->ring_head) {
        ring_entry_t *e = &ctx->ring[ctx->ring_tail];
        if (!first) { if (sbuf_putc(&s, ',') < 0) goto oom; }
        first = 0;

        if (sbuf_puts(&s, "{\"msg\":") < 0) goto oom;
        if (sbuf_putu(&s, (unsigned long long)e->msg) < 0) goto oom;
        if (sbuf_puts(&s, ",\"wp\":") < 0) goto oom;
        if (sbuf_putu(&s, (unsigned long long)e->wparam) < 0) goto oom;
        if (sbuf_puts(&s, ",\"lp\":") < 0) goto oom;
        if (sbuf_putd(&s, (long long)e->lparam) < 0) goto oom;
        if (e->extra_len > 0) {
            if (sbuf_puts(&s, ",\"extra\":") < 0) goto oom;
            if (sbuf_putjsonstr(&s, e->extra, e->extra_len) < 0) goto oom;
        }
        if (sbuf_putc(&s, '}') < 0) goto oom;

        ctx->ring_tail = (ctx->ring_tail + 1) % RING_CAP;
    }

    if (sbuf_putc(&s, ']') < 0) goto oom;
    if (sbuf_putc(&s, '\0') < 0) goto oom;
    set_last(0);
    return s.buf;

oom:
    free(s.buf);
    set_last(E_OUTOFMEMORY);
    return NULL;
}

void picolet_winevents_free(char *buf) { free(buf); }

int32_t picolet_winevents_overflow_count(void *hwnd_p) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) return 0;
    int n = ctx->overflow;
    ctx->overflow = 0;
    return n;
}

/* ---- Convenience registrations ------------------------------------------ */

int32_t picolet_winevents_watch_device_interface(void *hwnd_p,
                                                const uint8_t *guid16_bytes) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    if (guid16_bytes == NULL) { set_last(E_INVALIDARG); return -1; }

    DEV_BROADCAST_DEVICEINTERFACE_W filter;
    memset(&filter, 0, sizeof(filter));
    filter.dbcc_size = sizeof(filter);
    filter.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE;
    memcpy(&filter.dbcc_classguid, guid16_bytes, 16);

    HDEVNOTIFY n = RegisterDeviceNotificationW(
        (HWND)hwnd_p, &filter, DEVICE_NOTIFY_WINDOW_HANDLE);
    if (n == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }
    /* If a previous filter was set, we leak the old handle on purpose —
     * Win32 supports multiple filters on one HWND.  But we only remember
     * the most recent for clean detach.  Apps that need to attach many
     * filters should track them themselves and call Unregister at exit. */
    ctx->dev_notify = n;
    return picolet_winevents_subscribe(hwnd_p, WM_DEVICECHANGE, 0);
}

int32_t picolet_winevents_watch_power(void *hwnd_p) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    /* WM_POWERBROADCAST with PBT_APMSUSPEND/RESUME is delivered to top-level
     * windows without any explicit registration — RegisterPowerSettingNotification
     * is only needed for the PBT_POWERSETTINGCHANGE subcodes (battery state,
     * monitor on/off, etc.).  We register for the common battery/AC
     * subscription so consumers get a single WM_POWERBROADCAST stream. */
    GUID GUID_ACDC_POWER_SOURCE_ = {
        0x5d3e9a59, 0xe9D5, 0x4b00,
        {0xa6, 0xbd, 0xff, 0x34, 0xff, 0x51, 0x65, 0x48}
    };
    HPOWERNOTIFY n = RegisterPowerSettingNotification(
        (HANDLE)hwnd_p, &GUID_ACDC_POWER_SOURCE_, DEVICE_NOTIFY_WINDOW_HANDLE);
    if (n == NULL) {
        /* Continue anyway — WM_POWERBROADCAST baseline (SUSPEND/RESUME) is
         * still delivered without the setting-change registration. */
        set_last(HRESULT_FROM_WIN32(GetLastError()));
    } else {
        ctx->power_notify = n;
    }
    return picolet_winevents_subscribe(hwnd_p, WM_POWERBROADCAST, 0);
}

int32_t picolet_winevents_watch_session(void *hwnd_p) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    if (!WTSRegisterSessionNotification((HWND)hwnd_p, NOTIFY_FOR_THIS_SESSION)) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }
    ctx->session_registered = 1;
    return picolet_winevents_subscribe(hwnd_p, WM_WTSSESSION_CHANGE, 0);
}

int32_t picolet_winevents_watch_clipboard(void *hwnd_p) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    if (!AddClipboardFormatListener((HWND)hwnd_p)) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }
    ctx->clipboard_listener = 1;
    return picolet_winevents_subscribe(hwnd_p, WM_CLIPBOARDUPDATE, 0);
}

int32_t picolet_winevents_accept_drop_files(void *hwnd_p, int32_t enable) {
    picolet_winevents_ctx_t *ctx = get_ctx((HWND)hwnd_p);
    if (ctx == NULL) { set_last(E_HANDLE); return -2; }
    DragAcceptFiles((HWND)hwnd_p, enable ? TRUE : FALSE);
    if (enable) {
        return picolet_winevents_subscribe(hwnd_p, WM_DROPFILES, 0);
    }
    return picolet_winevents_unsubscribe(hwnd_p, WM_DROPFILES);
}
