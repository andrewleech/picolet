/*
 * picolet_webview_mac.c — WKWebView backend via Objective-C runtime API.
 *
 * PH25.  All ObjC calls go through the public objc_msgSend ABI
 * (libobjc.A.dylib) — no static Objective-C++ linkage, no .m files.
 *
 * License: MIT (picolet code).
 *
 * References:
 *   WKWebView:              https://developer.apple.com/documentation/webkit/wkwebview
 *   WKUserContentController: https://developer.apple.com/documentation/webkit/wkusercontentcontroller
 *   objc_allocateClassPair: https://developer.apple.com/documentation/objectivec/1418559-objc_allocateclasspair
 *   WKURLSchemeHandler:     https://developer.apple.com/documentation/webkit/wkurlschemehandler
 *
 * Compiles only when __APPLE__ is defined.  A non-Apple build includes
 * only stub symbols that return error values so the link always succeeds
 * regardless of which C files are compiled.
 */

#ifndef __APPLE__

/* ----------------------------------------------------------------------- */
/* Non-Apple stub symbols.  Mirror the picolet_webview2.c unix guard pattern  */
/* so the build succeeds if this file is accidentally compiled on Linux.    */
/* ----------------------------------------------------------------------- */

#include <stddef.h>
#include <stdint.h>
#include "picolet_webview_mac.h"

int   picolet_wkwv_init(void)                                            { return -1; }
void *picolet_wkwv_create_window(const char *t, int w, int h)           { (void)t; (void)w; (void)h; return 0; }
int   picolet_wkwv_show_window(void *win, int v)                        { (void)win; (void)v; return -1; }
int   picolet_wkwv_destroy_window(void *win)                            { (void)win; return -1; }
void *picolet_wkwv_create_webview(void *win, int w, int h)              { (void)win; (void)w; (void)h; return 0; }
int   picolet_wkwv_load_html(void *wv, const char *h, const char *b)   { (void)wv; (void)h; (void)b; return -1; }
int   picolet_wkwv_load_url(void *wv, const char *u)                   { (void)wv; (void)u; return -1; }
int   picolet_wkwv_evaluate_js(void *wv, const char *js)               { (void)wv; (void)js; return -1; }
int   picolet_wkwv_register_message_handler(void)                      { return -1; }
char *picolet_wkwv_poll_inbound(void)                                   { return 0; }
void  picolet_wkwv_free_inbound(char *s)                                { (void)s; }
int   picolet_wkwv_register_scheme_handler(
          void (*cb)(const char *, void *, void *), void *ud)
      { (void)cb; (void)ud; return -1; }
int   picolet_wkwv_scheme_respond(void *t, const char *ct,
                                 const uint8_t *d, size_t l)
      { (void)t; (void)ct; (void)d; (void)l; return -1; }
int   picolet_wkwv_scheme_error(void *t)                               { (void)t; return -1; }
int   picolet_wkwv_pump_messages(double s)                             { (void)s; return 0; }
int   picolet_wkwv_take_snapshot(void *wv, uint8_t **ob, size_t *ol)  { (void)wv; (void)ob; (void)ol; return -1; }
int   picolet_wkwv_enable_inspector(int p)                             { (void)p; return -1; }
int   picolet_wkwv_pick_test_port(void)                                { return -1; }

#else  /* __APPLE__ */

/* ----------------------------------------------------------------------- */
/* macOS implementation                                                      */
/* ----------------------------------------------------------------------- */

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ObjC runtime — public C API, no .m required. */
#include <objc/objc.h>
#include <objc/runtime.h>
#include <objc/message.h>

/* CoreFoundation for CFRunLoopRunInMode and friends. */
#include <CoreFoundation/CoreFoundation.h>

/* dispatch_semaphore_t — used to block for async completions. */
#include <dispatch/dispatch.h>

/* BSD socket API for pick_test_port. */
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>

#include "picolet_webview_mac.h"

/* ----------------------------------------------------------------------- */
/* Visibility: export all picolet_wkwv_* symbols so ffi.open(None) resolves  */
/* them in the Mach-O executable.  All other symbols are hidden.            */
/* The mpconfigvariant.mk adds -fvisibility=hidden + -Wl,-export_dynamic.  */
/* ----------------------------------------------------------------------- */
#define PICOLET_API __attribute__((visibility("default")))

/* ----------------------------------------------------------------------- */
/* objc_msgSend cast helpers                                                 */
/*                                                                           */
/* The C standard prohibits calling a function through an incompatible      */
/* pointer type.  We cast objc_msgSend to the concrete prototype required   */
/* by each call site.  This is the only correct approach (used by PyObjC,   */
/* gnustep-make, and apple's own open-source ObjC bridge implementations).  */
/*                                                                           */
/* Struct-return calls:                                                      */
/*   arm64:  CGRect (16 bytes) fits in two x0/x1 registers — use the        */
/*           standard objc_msgSend signature.                                */
/*   x86_64: large structs are returned via a hidden first pointer arg —    */
/*           use objc_msgSend_stret.  CGRect is 32 bytes on x86_64.         */
/*                                                                           */
/* We wrap all CGRect-returning calls in picolet_wkwv_make_rect so neither    */
/* Python nor the pump code calls *_stret directly.                          */
/* ----------------------------------------------------------------------- */

/* CGPoint and CGSize are both { double x; double y } / { double w; double h }. */
typedef struct { double x; double y; } PICOLET_CGPoint;
typedef struct { double width; double height; } PICOLET_CGSize;
typedef struct { PICOLET_CGPoint origin; PICOLET_CGSize size; } PICOLET_CGRect;

/* Build a CGRect on the stack and return it — both arches can handle a     */
/* C-level return of a 32-byte struct through normal registers/stack.       */
static PICOLET_CGRect picolet_make_rect(double x, double y, double w, double h) {
    PICOLET_CGRect r;
    r.origin.x = x; r.origin.y = y;
    r.size.width = w; r.size.height = h;
    return r;
}

/* ----------------------------------------------------------------------- */
/* ObjC selector cache                                                       */
/*                                                                           */
/* sel_registerName is re-entrant and cheap after the first call (it        */
/* returns a cached SEL pointer).  We compute each selector once at first   */
/* use; assignment is idempotent and thread-safe on Darwin because SEL is   */
/* a pointer-sized value written atomically on all Apple architectures.     */
/* ----------------------------------------------------------------------- */

#define SEL_CACHED(name) \
    static SEL _sel_##name = 0; \
    if (!_sel_##name) _sel_##name = sel_registerName(#name); \
    SEL _s = _sel_##name; (void)_s

/* Convenience: look up a class and assert it's non-NULL. */
static Class objc_class(const char *name) {
    Class c = objc_getClass(name);
    if (!c) {
        fprintf(stderr, "picolet_webview_mac: class not found: %s\n", name);
    }
    return c;
}

/* ----------------------------------------------------------------------- */
/* NSString helpers                                                          */
/* ----------------------------------------------------------------------- */

/* Create an NSString from a UTF-8 C string. */
static id nsstring_from_utf8(const char *s) {
    if (!s) return 0;
    SEL_CACHED(stringWithUTF8String:);
    id cls = (id)objc_class("NSString");
    return ((id (*)(id, SEL, const char *))objc_msgSend)(cls, _s, s);
}

/* Copy an NSString to a malloc'd UTF-8 C string.  Caller must free(). */
static char *nsstring_to_utf8(id ns) {
    if (!ns) return NULL;
    SEL_CACHED(UTF8String);
    const char *cstr = ((const char *(*)(id, SEL))objc_msgSend)(ns, _s);
    if (!cstr) return NULL;
    return strdup(cstr);
}

/* ----------------------------------------------------------------------- */
/* NSData helpers                                                            */
/* ----------------------------------------------------------------------- */

/* Get bytes pointer and length from an NSData object. */
static const uint8_t *nsdata_bytes(id data) {
    if (!data) return NULL;
    SEL_CACHED(bytes);
    return ((const uint8_t *(*)(id, SEL))objc_msgSend)(data, _s);
}

static size_t nsdata_length(id data) {
    if (!data) return 0;
    SEL_CACHED(length);
    return (size_t)((NSUInteger (*)(id, SEL))objc_msgSend)(data, _s);
}

/* ----------------------------------------------------------------------- */
/* Inbound message ring buffer (JS → Python)                                 */
/*                                                                           */
/* Single-threaded model: ObjC message handler fires from inside            */
/* CFRunLoopRunInMode (called by picolet_wkwv_pump_messages), which itself    */
/* runs on the asyncio main thread.  No locking needed.                     */
/* ----------------------------------------------------------------------- */

#define PICOLET_WKWV_RING_SIZE 256

static char *g_ring[PICOLET_WKWV_RING_SIZE];
static int g_ring_head = 0;
static int g_ring_tail = 0;

static int ring_push(char *s) {
    int h = g_ring_head;
    int t = g_ring_tail;
    if (((h + 1) % PICOLET_WKWV_RING_SIZE) == t) {
        /* full — drop */
        return -1;
    }
    g_ring[h] = s;
    g_ring_head = (h + 1) % PICOLET_WKWV_RING_SIZE;
    return 0;
}

static char *ring_pop(void) {
    int h = g_ring_head;
    int t = g_ring_tail;
    if (h == t) return NULL;
    char *s = g_ring[t];
    g_ring_tail = (t + 1) % PICOLET_WKWV_RING_SIZE;
    return s;
}

/* ----------------------------------------------------------------------- */
/* PicoletScriptMessageHandler ObjC class                                      */
/*                                                                           */
/* Created once at runtime via objc_allocateClassPair.  The IMP below is    */
/* the implementation of -userContentController:didReceiveScriptMessage:.    */
/*                                                                           */
/* ObjC method calling convention (both arches):                            */
/*   void method(id self, SEL _cmd, id arg0, id arg1)                       */
/*                                                                           */
/* The IMP must match this signature exactly.                                */
/* ----------------------------------------------------------------------- */

static void picolet_script_message_handler_received(id self, SEL _cmd,
                                                   id controller, id message) {
    (void)self; (void)_cmd; (void)controller;
    /* Extract the body (NSString) from WKScriptMessage. */
    SEL sel_body = sel_registerName("body");
    id body = ((id (*)(id, SEL))objc_msgSend)(message, sel_body);
    if (!body) return;

    /* body may be an NSString or NSDictionary depending on what JS posted. */
    /* We expect a JSON string from our bridge.  If it's not an NSString,    */
    /* serialize via NSString description as fallback.                       */
    SEL sel_isa_str = sel_registerName("isKindOfClass:");
    Class cls_nsstr = objc_class("NSString");
    id is_string = (id)(intptr_t)((BOOL (*)(id, SEL, Class))objc_msgSend)(
        body, sel_isa_str, cls_nsstr);

    char *utf8 = NULL;
    if (is_string) {
        utf8 = nsstring_to_utf8(body);
    } else {
        /* Fall back to description (produces e.g. "{ key = value; }"). */
        SEL sel_desc = sel_registerName("description");
        id desc = ((id (*)(id, SEL))objc_msgSend)(body, sel_desc);
        utf8 = nsstring_to_utf8(desc);
    }
    if (!utf8) return;
    if (ring_push(utf8) != 0) {
        fprintf(stderr,
                "picolet_webview_mac: inbound ring buffer full; dropping message\n");
        free(utf8);
    }
}

static int g_message_handler_registered = 0;
/* WKUserContentController* cached so create_webview can attach the handler. */
static id g_user_content_controller = 0;
/* PicoletScriptMessageHandler instance — one global, lifetime = process. */
static id g_script_message_handler_obj = 0;

/* ----------------------------------------------------------------------- */
/* PicoletSchemeHandler ObjC class                                             */
/*                                                                           */
/* Implements WKURLSchemeHandler protocol.                                   */
/*                                                                           */
/* -webView:startURLSchemeTask:  is called for each picolet:// resource.       */
/* -webView:stopURLSchemeTask:   is called if the task is cancelled.         */
/*                                                                           */
/* The Python layer registers a C callback that is invoked with the URL     */
/* path and an opaque task pointer.  The callback calls                     */
/* picolet_wkwv_scheme_respond or picolet_wkwv_scheme_error to complete the    */
/* task.                                                                    */
/* ----------------------------------------------------------------------- */

typedef void (*SchemeHandlerCb)(const char *path, void *task_opaque,
                                void *user_data);

static SchemeHandlerCb g_scheme_cb = NULL;
static void *g_scheme_user_data = NULL;
static int g_scheme_handler_registered = 0;
/* WKURLSchemeHandler instance — one global, lifetime = process. */
static id g_scheme_handler_obj = 0;

static void picolet_scheme_start(id self, SEL _cmd, id webview, id task) {
    (void)self; (void)_cmd; (void)webview;
    if (!g_scheme_cb) {
        /* No Python callback registered — return a 404. */
        picolet_wkwv_scheme_error(task);
        return;
    }
    /* Extract URL path from the WKURLSchemeTask.
     * task.request.URL.path → NSString */
    SEL sel_request = sel_registerName("request");
    SEL sel_url     = sel_registerName("URL");
    SEL sel_path    = sel_registerName("path");

    id request = ((id (*)(id, SEL))objc_msgSend)(task, sel_request);
    id url      = ((id (*)(id, SEL))objc_msgSend)(request, sel_url);
    id path_ns  = ((id (*)(id, SEL))objc_msgSend)(url, sel_path);

    char *path_c = nsstring_to_utf8(path_ns);
    g_scheme_cb(path_c ? path_c : "/", (void *)task, g_scheme_user_data);
    if (path_c) free(path_c);
}

static void picolet_scheme_stop(id self, SEL _cmd, id webview, id task) {
    (void)self; (void)_cmd; (void)webview; (void)task;
    /* Nothing to do — the task is already done or will be ignored. */
}

/* ----------------------------------------------------------------------- */
/* Module-level cached ObjC object handles                                  */
/* ----------------------------------------------------------------------- */

static id g_app             = 0;  /* NSApplication sharedApplication */
static id g_wkwv_config     = 0;  /* WKWebViewConfiguration */
static id g_webview         = 0;  /* WKWebView */
static int g_inspector_port = 0;  /* port set by enable_inspector (0 = none) */

/* ----------------------------------------------------------------------- */
/* PICOLET_API symbol definitions                                              */
/* ----------------------------------------------------------------------- */

PICOLET_API int picolet_wkwv_init(void) {
    Class cls_app = objc_class("NSApplication");
    if (!cls_app) return -1;
    SEL sel_shared = sel_registerName("sharedApplication");
    g_app = ((id (*)(id, SEL))objc_msgSend)((id)cls_app, sel_shared);
    if (!g_app) return -1;

    /* NSApplicationActivationPolicyRegular = 0 */
    SEL sel_policy = sel_registerName("setActivationPolicy:");
    ((void (*)(id, SEL, NSInteger))objc_msgSend)(g_app, sel_policy, 0);

    /* finishLaunching so the application is ready to receive events. */
    SEL sel_finish = sel_registerName("finishLaunching");
    ((void (*)(id, SEL))objc_msgSend)(g_app, sel_finish);

    return 0;
}

PICOLET_API void *picolet_wkwv_create_window(const char *title, int w, int h) {
    Class cls_win = objc_class("NSWindow");
    if (!cls_win) return NULL;

    /* [[NSWindow alloc] initWithContentRect:styleMask:backing:defer:] */
    SEL sel_alloc = sel_registerName("alloc");
    id win = ((id (*)(id, SEL))objc_msgSend)((id)cls_win, sel_alloc);
    if (!win) return NULL;

    PICOLET_CGRect frame = picolet_make_rect(100.0, 100.0,
                                         (double)w, (double)h);

    /* styleMask: NSTitledWindowMask(1) | NSClosableWindowMask(2) |
     *            NSMiniaturizableWindowMask(4) | NSResizableWindowMask(8) */
    NSUInteger style = 1 | 2 | 4 | 8;

    SEL sel_init = sel_registerName("initWithContentRect:styleMask:backing:defer:");
    /* backing: NSBackingStoreBuffered = 2; defer: NO = 0 */
#if defined(__x86_64__)
    /* On x86_64, initWithContentRect:... takes a struct first argument by
     * hidden reference (stret ABI) via objc_msgSend_stret.              */
    PICOLET_CGRect result_ignored;
    ((void (*)(PICOLET_CGRect *, id, SEL, PICOLET_CGRect, NSUInteger, NSUInteger, BOOL))
        objc_msgSend_stret)(
        &result_ignored, win, sel_init,
        frame, style, (NSUInteger)2, (BOOL)0);
    /* On x86_64 objc_msgSend_stret with a pointer receiver-as-first arg
     * mutates `win` in place (the alloc'd object is initialised in-place
     * and the call returns via the hidden pointer, not via rax).  So win
     * is still the correct pointer after the call.                      */
#else
    /* arm64: CGRect ≤ 16B in registers — standard objc_msgSend.
     * NSWindow is > 16B but the return is the same `win` pointer here.
     * Use the id-returning variant; the real return is the same object. */
    win = ((id (*)(id, SEL, PICOLET_CGRect, NSUInteger, NSUInteger, BOOL))
               objc_msgSend)(win, sel_init,
                              frame, style, (NSUInteger)2, (BOOL)0);
#endif
    if (!win) return NULL;

    /* setTitle: */
    SEL sel_title = sel_registerName("setTitle:");
    id ns_title = nsstring_from_utf8(title ? title : "picolet");
    ((void (*)(id, SEL, id))objc_msgSend)(win, sel_title, ns_title);

    /* setReleasedWhenClosed:NO so we can reuse the pointer. */
    SEL sel_rwc = sel_registerName("setReleasedWhenClosed:");
    ((void (*)(id, SEL, BOOL))objc_msgSend)(win, sel_rwc, (BOOL)0);

    return (void *)win;
}

PICOLET_API int picolet_wkwv_show_window(void *window, int visible) {
    if (!window) return -1;
    id win = (id)window;
    if (visible) {
        SEL sel_front = sel_registerName("makeKeyAndOrderFront:");
        ((void (*)(id, SEL, id))objc_msgSend)(win, sel_front, (id)0);
        /* Activate the application so it comes to the front. */
        if (g_app) {
            SEL sel_activate = sel_registerName("activateIgnoringOtherApps:");
            ((void (*)(id, SEL, BOOL))objc_msgSend)(g_app, sel_activate, (BOOL)1);
        }
    } else {
        SEL sel_out = sel_registerName("orderOut:");
        ((void (*)(id, SEL, id))objc_msgSend)(win, sel_out, (id)0);
    }
    return 0;
}

PICOLET_API int picolet_wkwv_destroy_window(void *window) {
    if (!window) return 0;
    SEL sel_close = sel_registerName("close");
    ((void (*)(id, SEL))objc_msgSend)((id)window, sel_close);
    return 0;
}

PICOLET_API void *picolet_wkwv_create_webview(void *window, int w, int h) {
    /* ------------------------------------------------------------------
     * Build WKWebViewConfiguration
     * ------------------------------------------------------------------ */
    Class cls_cfg = objc_class("WKWebViewConfiguration");
    if (!cls_cfg) return NULL;

    SEL sel_alloc = sel_registerName("alloc");
    SEL sel_init  = sel_registerName("init");

    id cfg = ((id (*)(id, SEL))objc_msgSend)((id)cls_cfg, sel_alloc);
    cfg    = ((id (*)(id, SEL))objc_msgSend)(cfg, sel_init);
    if (!cfg) return NULL;
    g_wkwv_config = cfg;

    /* Attach the UserContentController. */
    Class cls_ucc = objc_class("WKUserContentController");
    if (!cls_ucc) return NULL;
    id ucc = ((id (*)(id, SEL))objc_msgSend)((id)cls_ucc, sel_alloc);
    ucc    = ((id (*)(id, SEL))objc_msgSend)(ucc, sel_init);
    if (!ucc) return NULL;
    g_user_content_controller = ucc;

    SEL sel_set_ucc = sel_registerName("setUserContentController:");
    ((void (*)(id, SEL, id))objc_msgSend)(cfg, sel_set_ucc, ucc);

    /* Register the "picolet" script message handler if requested. */
    if (g_message_handler_registered && g_script_message_handler_obj) {
        SEL sel_add_smh = sel_registerName("addScriptMessageHandler:name:");
        id name_ns = nsstring_from_utf8("picolet");
        ((void (*)(id, SEL, id, id))objc_msgSend)(
            ucc, sel_add_smh, g_script_message_handler_obj, name_ns);
    }

    /* Register the scheme handler if requested. */
    if (g_scheme_handler_registered && g_scheme_handler_obj) {
        SEL sel_set_sh = sel_registerName("setURLSchemeHandler:forURLScheme:");
        id scheme_ns = nsstring_from_utf8("picolet");
        ((void (*)(id, SEL, id, id))objc_msgSend)(
            cfg, sel_set_sh, g_scheme_handler_obj, scheme_ns);
    }

    /* Enable developer extras if inspector was requested. */
    if (g_inspector_port > 0) {
        SEL sel_prefs = sel_registerName("preferences");
        id prefs = ((id (*)(id, SEL))objc_msgSend)(cfg, sel_prefs);
        if (prefs) {
            /* Private key _developerExtrasEnabled — documented and stable
             * on macOS 10.14+.  The public API (setInspectorLevel:) is
             * available from macOS 13.3+; use private key for broader
             * compatibility per FR-WV-MAC-7.  */
            SEL sel_dev = sel_registerName("_setDeveloperExtrasEnabled:");
            ((void (*)(id, SEL, BOOL))objc_msgSend)(prefs, sel_dev, (BOOL)1);
        }
    }

    /* ------------------------------------------------------------------
     * Build WKWebView
     * ------------------------------------------------------------------ */
    Class cls_wv = objc_class("WKWebView");
    if (!cls_wv) return NULL;

    id wv = ((id (*)(id, SEL))objc_msgSend)((id)cls_wv, sel_alloc);
    if (!wv) return NULL;

    PICOLET_CGRect frame = picolet_make_rect(0.0, 0.0, (double)w, (double)h);

#if defined(__x86_64__)
    PICOLET_CGRect stret_out;
    SEL sel_init_frame = sel_registerName("initWithFrame:configuration:");
    ((void (*)(PICOLET_CGRect *, id, SEL, PICOLET_CGRect, id))
        objc_msgSend_stret)(
        &stret_out, wv, sel_init_frame, frame, cfg);
    /* wv is the correct pointer even after stret init (same object). */
#else
    SEL sel_init_frame = sel_registerName("initWithFrame:configuration:");
    wv = ((id (*)(id, SEL, PICOLET_CGRect, id))objc_msgSend)(
        wv, sel_init_frame, frame, cfg);
#endif
    if (!wv) return NULL;
    g_webview = wv;

    /* Add the webview to the window's content view. */
    if (window) {
        SEL sel_content = sel_registerName("contentView");
        id content_view = ((id (*)(id, SEL))objc_msgSend)((id)window, sel_content);
        if (content_view) {
            SEL sel_add_sub = sel_registerName("addSubview:");
            ((void (*)(id, SEL, id))objc_msgSend)(content_view, sel_add_sub, wv);
        }
    }

    return (void *)wv;
}

PICOLET_API int picolet_wkwv_load_html(void *webview, const char *html,
                                    const char *base_url) {
    if (!webview || !html) return -1;
    id html_ns = nsstring_from_utf8(html);
    id base_ns = base_url ? nsstring_from_utf8(base_url) : (id)0;

    /* Convert base_url string to NSURL if provided. */
    id nsurl = (id)0;
    if (base_ns) {
        Class cls_url = objc_class("NSURL");
        SEL sel_url_str = sel_registerName("URLWithString:");
        nsurl = ((id (*)(id, SEL, id))objc_msgSend)(
            (id)cls_url, sel_url_str, base_ns);
    }

    SEL sel_load = sel_registerName("loadHTMLString:baseURL:");
    ((id (*)(id, SEL, id, id))objc_msgSend)(
        (id)webview, sel_load, html_ns, nsurl);
    return 0;
}

PICOLET_API int picolet_wkwv_load_url(void *webview, const char *url) {
    if (!webview || !url) return -1;

    /* Build NSURLRequest from string. */
    Class cls_url = objc_class("NSURL");
    SEL sel_url_str = sel_registerName("URLWithString:");
    id nsurl = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)cls_url, sel_url_str, nsstring_from_utf8(url));
    if (!nsurl) return -1;

    Class cls_req = objc_class("NSURLRequest");
    SEL sel_req = sel_registerName("requestWithURL:");
    id req = ((id (*)(id, SEL, id))objc_msgSend)((id)cls_req, sel_req, nsurl);
    if (!req) return -1;

    SEL sel_load = sel_registerName("loadRequest:");
    ((id (*)(id, SEL, id))objc_msgSend)((id)webview, sel_load, req);
    return 0;
}

PICOLET_API int picolet_wkwv_evaluate_js(void *webview, const char *js) {
    if (!webview || !js) return -1;
    id js_ns = nsstring_from_utf8(js);
    /* evaluateJavaScript:completionHandler: — pass NULL for the block. */
    SEL sel_eval = sel_registerName("evaluateJavaScript:completionHandler:");
    ((void (*)(id, SEL, id, id))objc_msgSend)(
        (id)webview, sel_eval, js_ns, (id)0);
    return 0;
}

PICOLET_API int picolet_wkwv_register_message_handler(void) {
    if (g_message_handler_registered) return 0;

    /* Allocate a new ObjC class PicoletScriptMessageHandler inheriting from
     * NSObject, implementing WKScriptMessageHandler protocol. */
    Class superclass = objc_class("NSObject");
    if (!superclass) return -1;

    Class cls = objc_allocateClassPair(superclass,
                                        "PicoletScriptMessageHandler", 0);
    if (!cls) {
        /* Class may already exist if this function was called previously. */
        cls = objc_getClass("PicoletScriptMessageHandler");
        if (!cls) return -1;
    }

    /* Add the -userContentController:didReceiveScriptMessage: method.
     * Type encoding: "v@:@@"
     *   v = void return
     *   @ = id self
     *   : = SEL _cmd
     *   @ = id controller
     *   @ = id message                                                   */
    class_addMethod(
        cls,
        sel_registerName("userContentController:didReceiveScriptMessage:"),
        (IMP)picolet_script_message_handler_received,
        "v@:@@");

    objc_registerClassPair(cls);

    /* Create a single global instance. */
    SEL sel_alloc = sel_registerName("alloc");
    SEL sel_init  = sel_registerName("init");
    id obj = ((id (*)(id, SEL))objc_msgSend)((id)cls, sel_alloc);
    obj    = ((id (*)(id, SEL))objc_msgSend)(obj, sel_init);
    if (!obj) return -1;

    g_script_message_handler_obj = obj;
    g_message_handler_registered = 1;
    return 0;
}

PICOLET_API char *picolet_wkwv_poll_inbound(void) {
    return ring_pop();
}

PICOLET_API void picolet_wkwv_free_inbound(char *s) {
    if (s) free(s);
}

PICOLET_API int picolet_wkwv_register_scheme_handler(
    void (*cb)(const char *path, void *task_opaque, void *user_data),
    void *user_data)
{
    if (g_scheme_handler_registered) return 0;

    Class superclass = objc_class("NSObject");
    if (!superclass) return -1;

    Class cls = objc_allocateClassPair(superclass, "PicoletSchemeHandler", 0);
    if (!cls) {
        cls = objc_getClass("PicoletSchemeHandler");
        if (!cls) return -1;
    }

    /* -webView:startURLSchemeTask: — type "v@:@@" */
    class_addMethod(
        cls,
        sel_registerName("webView:startURLSchemeTask:"),
        (IMP)picolet_scheme_start,
        "v@:@@");

    /* -webView:stopURLSchemeTask: — type "v@:@@" */
    class_addMethod(
        cls,
        sel_registerName("webView:stopURLSchemeTask:"),
        (IMP)picolet_scheme_stop,
        "v@:@@");

    objc_registerClassPair(cls);

    SEL sel_alloc = sel_registerName("alloc");
    SEL sel_init  = sel_registerName("init");
    id obj = ((id (*)(id, SEL))objc_msgSend)((id)cls, sel_alloc);
    obj    = ((id (*)(id, SEL))objc_msgSend)(obj, sel_init);
    if (!obj) return -1;

    g_scheme_handler_obj = obj;
    g_scheme_cb = cb;
    g_scheme_user_data = user_data;
    g_scheme_handler_registered = 1;
    return 0;
}

PICOLET_API int picolet_wkwv_scheme_respond(void *task_opaque,
                                         const char *content_type,
                                         const uint8_t *data, size_t data_len)
{
    if (!task_opaque || !data || data_len == 0) return -1;
    id task = (id)task_opaque;

    /* Build NSHTTPURLResponse.
     * [[NSHTTPURLResponse alloc] initWithURL:statusCode:HTTPVersion:headerFields:] */
    Class cls_resp = objc_class("NSHTTPURLResponse");
    if (!cls_resp) return -1;

    /* We need the request URL to construct the response URL. */
    SEL sel_request = sel_registerName("request");
    SEL sel_url     = sel_registerName("URL");
    id request = ((id (*)(id, SEL))objc_msgSend)(task, sel_request);
    id url     = ((id (*)(id, SEL))objc_msgSend)(request, sel_url);

    id ct_ns = nsstring_from_utf8(content_type ? content_type
                                                : "application/octet-stream");

    /* Build NSDictionary with Content-Type header. */
    Class cls_dict = objc_class("NSDictionary");
    SEL sel_dict = sel_registerName("dictionaryWithObject:forKey:");
    id ct_key = nsstring_from_utf8("Content-Type");
    id headers = ((id (*)(id, SEL, id, id))objc_msgSend)(
        (id)cls_dict, sel_dict, ct_ns, ct_key);

    SEL sel_alloc = sel_registerName("alloc");
    id resp_obj = ((id (*)(id, SEL))objc_msgSend)((id)cls_resp, sel_alloc);

    SEL sel_init_resp = sel_registerName(
        "initWithURL:statusCode:HTTPVersion:headerFields:");
    id http_version = nsstring_from_utf8("HTTP/1.1");
    resp_obj = ((id (*)(id, SEL, id, NSInteger, id, id))objc_msgSend)(
        resp_obj, sel_init_resp,
        url, (NSInteger)200, http_version, headers);

    if (!resp_obj) return -1;

    /* [task didReceiveResponse:] */
    SEL sel_recv_resp = sel_registerName("didReceiveResponse:");
    ((void (*)(id, SEL, id))objc_msgSend)(task, sel_recv_resp, resp_obj);

    /* Build NSData from the byte buffer. */
    Class cls_data = objc_class("NSData");
    SEL sel_data = sel_registerName("dataWithBytes:length:");
    id nsdata = ((id (*)(id, SEL, const void *, NSUInteger))objc_msgSend)(
        (id)cls_data, sel_data, (const void *)data, (NSUInteger)data_len);
    if (!nsdata) return -1;

    /* [task didReceiveData:] */
    SEL sel_recv_data = sel_registerName("didReceiveData:");
    ((void (*)(id, SEL, id))objc_msgSend)(task, sel_recv_data, nsdata);

    /* [task didFinish] */
    SEL sel_finish = sel_registerName("didFinish");
    ((void (*)(id, SEL))objc_msgSend)(task, sel_finish);

    return 0;
}

PICOLET_API int picolet_wkwv_scheme_error(void *task_opaque) {
    if (!task_opaque) return -1;
    id task = (id)task_opaque;

    /* Build an NSError for "not found". */
    Class cls_err = objc_class("NSError");
    /* NSURLErrorDomain = "NSURLErrorDomain", code = -1100 (FileDoesNotExist) */
    id domain_ns = nsstring_from_utf8("NSURLErrorDomain");
    Class cls_dict = objc_class("NSDictionary");
    SEL sel_dict_new = sel_registerName("dictionary");
    id empty_dict = ((id (*)(id, SEL))objc_msgSend)((id)cls_dict, sel_dict_new);

    SEL sel_err_init = sel_registerName(
        "errorWithDomain:code:userInfo:");
    id err = ((id (*)(id, SEL, id, NSInteger, id))objc_msgSend)(
        (id)cls_err, sel_err_init,
        domain_ns, (NSInteger)-1100, empty_dict);

    SEL sel_fail = sel_registerName("didFailWithError:");
    ((void (*)(id, SEL, id))objc_msgSend)(task, sel_fail, err);
    return 0;
}

PICOLET_API int picolet_wkwv_pump_messages(double seconds) {
    /* Drain the Cocoa run loop for up to `seconds`.
     * Using kCFRunLoopDefaultMode ("kCFRunLoopDefaultMode" constant).
     * CFRunLoopRunInMode returns when either the timeout elapses or
     * an event is processed (returnAfterSourceHandled = false means
     * continue processing until timeout).                              */
    CFStringRef mode = kCFRunLoopDefaultMode;
    CFRunLoopRunInMode(mode, seconds > 0.0 ? seconds : 0.01, false);
    return 0;
}

/* ----------------------------------------------------------------------- */
/* Screenshot                                                                */
/* ----------------------------------------------------------------------- */

/* Completion context for the snapshot callback. */
typedef struct {
    dispatch_semaphore_t sem;
    id image;          /* NSImage* received in the block */
    int error;
} SnapshotCtx;

/*
 * The snapshot completion block fires on the main queue.  Because we drive
 * it from picolet_wkwv_pump_messages (which runs on the main thread), we use
 * a semaphore + run-loop pump loop rather than dispatch_semaphore_wait
 * (which would deadlock the main thread).
 */

PICOLET_API int picolet_wkwv_take_snapshot(void *webview,
                                        uint8_t **out_bytes, size_t *out_len)
{
    if (!webview || !out_bytes || !out_len) return -1;

    SnapshotCtx ctx;
    ctx.sem   = dispatch_semaphore_create(0);
    ctx.image = (id)0;
    ctx.error = 0;

    if (!ctx.sem) return -1;

    /* WKSnapshotConfiguration — nil means default (full viewport). */
    id nil_cfg = (id)0;

    /* Build an ObjC block on the stack using the clang block literal ABI.
     * We use a libdispatch approach: capture ctx pointer into block.
     * Since we cannot easily declare a __block lambda in C, we use the
     * dispatch_block approach via dispatch_async to complete from main queue.
     *
     * Simpler alternative: use [WKWebView takeSnapshotWithConfiguration:NULL
     * completionHandler:^(NSImage *img, NSError *err) { ... }]
     * via the ObjC block runtime functions.
     *
     * The cleanest cross-language approach here is to use dispatch_semaphore
     * and the raw block trampolining via imp_implementationWithBlock — but
     * that requires a full ObjC block layout struct.
     *
     * For v1.2 we implement a simpler synchronous approach: use the
     * WebKit offscreen render via -[WKWebView _generateTestReport:] is
     * private and fragile.
     *
     * Practical approach: call takeSnapshotWithConfiguration:completionHandler:
     * using a pre-compiled block literal.  In pure C, blocks are structs with
     * a function pointer.  We declare the minimal required layout below.
     */

    /* Block literal structure (clang blocks ABI v1):
     *   void *isa;          // &_NSConcreteStackBlock
     *   int   flags;
     *   int   reserved;
     *   void (*invoke)(void *block, id image, id error);
     *   struct block_descriptor *descriptor;
     *   SnapshotCtx *ctx;   // captured variable
     */
    struct block_descriptor_snapshot {
        unsigned long int reserved;
        unsigned long int size;
    };

    typedef struct snapshot_block {
        void *isa;
        int   flags;
        int   reserved_field;
        void (*invoke)(struct snapshot_block *, id, id);
        struct block_descriptor_snapshot *descriptor;
        SnapshotCtx *ctx;
    } SnapshotBlock;

    /* Block invoke function: fires when the snapshot completes.
     * Signature: void(^)(NSImage *image, NSError *error)              */
    void snapshot_block_invoke(SnapshotBlock *b, id image, id error) {
        (void)error;
        b->ctx->image = image;
        b->ctx->error = (error && error != (id)0) ? 1 : 0;
        dispatch_semaphore_signal(b->ctx->sem);
    }

    static struct block_descriptor_snapshot desc = {
        0, sizeof(SnapshotBlock)
    };

    /* _NSConcreteStackBlock — resolved from libobjc at runtime.
     * The extern symbol is declared in <Block.h> but we access it
     * directly since we compile as pure C without Block.h.           */
    extern void _NSConcreteStackBlock;

    SnapshotBlock blk;
    blk.isa            = &_NSConcreteStackBlock;
    blk.flags          = 0;
    blk.reserved_field = 0;
    blk.invoke         = snapshot_block_invoke;
    blk.descriptor     = &desc;
    blk.ctx            = &ctx;

    SEL sel_snap = sel_registerName("takeSnapshotWithConfiguration:completionHandler:");
    ((void (*)(id, SEL, id, SnapshotBlock *))objc_msgSend)(
        (id)webview, sel_snap, nil_cfg, &blk);

    /* Pump the run loop until the semaphore signals or 5 s elapses. */
    int timeout = 0;
    for (int i = 0; i < 500; i++) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, false);
        if (dispatch_semaphore_wait(ctx.sem, DISPATCH_TIME_NOW) == 0) {
            timeout = 0;
            break;
        }
        timeout = 1;
    }
    dispatch_release(ctx.sem);

    if (timeout || ctx.error || !ctx.image) return -1;

    /* Convert NSImage → PNG bytes via NSBitmapImageRep.
     * [NSBitmapImageRep representationOfImageRepsInArray:
     *                   usingType:NSBitmapImageFileTypePNG
     *                   properties:nil]
     *
     * Simpler path: use CIImage → CGImage → NSBitmapImageRep.
     * Simplest for pure C: call -[NSImage TIFFRepresentation] and
     * convert, or use -[NSImage representationUsingType:properties:].
     * The latter is deprecated; use NSBitmapImageRep class method.   */

    /* Get the array of image representations. */
    SEL sel_reps = sel_registerName("representations");
    id reps = ((id (*)(id, SEL))objc_msgSend)(ctx.image, sel_reps);

    /* NSBitmapImageFileTypePNG = 4 */
    Class cls_bmpir = objc_class("NSBitmapImageRep");
    SEL sel_png = sel_registerName(
        "representationOfImageRepsInArray:usingType:properties:");

    /* properties = nil (pass 0 as NSDictionary*) */
    id png_data = ((id (*)(id, SEL, id, NSUInteger, id))objc_msgSend)(
        (id)cls_bmpir, sel_png, reps, (NSUInteger)4, (id)0);

    if (!png_data) return -1;

    size_t len = nsdata_length(png_data);
    const uint8_t *bytes = nsdata_bytes(png_data);
    if (!bytes || len == 0) return -1;

    uint8_t *copy = (uint8_t *)malloc(len);
    if (!copy) return -1;
    memcpy(copy, bytes, len);

    *out_bytes = copy;
    *out_len   = len;
    return 0;
}

/* ----------------------------------------------------------------------- */
/* Inspector                                                                 */
/* ----------------------------------------------------------------------- */

PICOLET_API int picolet_wkwv_enable_inspector(int port) {
    /* Set NSUserDefaults keys before WKWebView is created.
     *
     * WebInspectorServerEnabled + WebInspectorPort are the NSUserDefaults
     * keys that enable the WKRP (WebKit Remote Protocol) TCP listener.
     * They must be set before any WKWebView is created.
     *
     * These keys are documented at:
     * https://webkit.org/blog/1587/programmatic-access-to-the-web-inspector/
     *
     * Note: reliable TCP-port control via WebInspectorPort may not work
     * on all macOS versions (see FR-WV-MAC-7 risk notes in PHASE_25 spec).
     * If port = 0, we just enable the inspector without specifying a port.
     */
    Class cls_ud = objc_class("NSUserDefaults");
    SEL sel_std = sel_registerName("standardUserDefaults");
    id ud = ((id (*)(id, SEL))objc_msgSend)((id)cls_ud, sel_std);
    if (!ud) return -1;

    /* setBool:YES forKey:@"WebInspectorServerEnabled" */
    SEL sel_set_bool = sel_registerName("setBool:forKey:");
    id key_enabled = nsstring_from_utf8("WebInspectorServerEnabled");
    ((void (*)(id, SEL, BOOL, id))objc_msgSend)(
        ud, sel_set_bool, (BOOL)1, key_enabled);

    if (port > 0) {
        /* setInteger:port forKey:@"WebInspectorPort" */
        SEL sel_set_int = sel_registerName("setInteger:forKey:");
        id key_port = nsstring_from_utf8("WebInspectorPort");
        ((void (*)(id, SEL, NSInteger, id))objc_msgSend)(
            ud, sel_set_int, (NSInteger)port, key_port);
        g_inspector_port = port;
    } else {
        g_inspector_port = 1;  /* non-zero = enabled, port chosen by OS */
    }

    return port;
}

PICOLET_API int picolet_wkwv_pick_test_port(void) {
    /* Bind 127.0.0.1:0, read the ephemeral port, close.
     * Same pattern as picolet_wv2_pick_test_port on Windows (see PH10). */
    int s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0) return -1;

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = 0;
    addr.sin_addr.s_addr = htonl(0x7f000001);  /* 127.0.0.1 */

    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(s);
        return -1;
    }

    struct sockaddr_in out;
    socklen_t outlen = sizeof(out);
    if (getsockname(s, (struct sockaddr *)&out, &outlen) != 0) {
        close(s);
        return -1;
    }

    int port = (int)ntohs(out.sin_port);
    close(s);
    return port;
}

#endif /* __APPLE__ */
