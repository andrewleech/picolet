/*
 * picolet_webview2.c — WebView2 COM dispatch + completion-handler vtables +
 * inbound JSON ring buffer, exposed as a flat C API to Python via libffi.
 *
 * PH10.  See picolet_webview2.h for the public API and AD2/AD3 in the
 * phase plan for the design rationale.
 *
 * License: MIT (picolet code).  Links against (dlopen at runtime):
 *   * WebView2Loader.dll — Microsoft WebView2 SDK License (permissive).
 *   * Edge WebView2 Runtime (system-installed) — Microsoft Edge Software
 *     License Terms; reached transitively via the loader, not redistributed.
 *
 * Compiles only when the windows-x64-webview variant is selected (the
 * variant's mpconfigvariant.mk adds this file to SRC_C and pulls in
 * -lole32 -loleaut32 -luser32 -lshell32).
 */

#ifndef _WIN32
/* Guard: the unix port should never compile this overlay (the variant
 * .mk only references it on the windows port).  But if a careless edit
 * adds it to a unix variant, the file degrades to a stub so the build
 * still passes — the symbols just return errors. */
#include <stdint.h>
#include <stddef.h>
#include "picolet_webview2.h"
int32_t picolet_wv2_last_error(void) { return -1; }
void *picolet_wv2_load_loader_dll(const uint8_t *b, size_t s) { (void)b; (void)s; return 0; }
int32_t picolet_wv2_init_com(void) { return -1; }
void *picolet_wv2_create_environment_blocking(int32_t t) { (void)t; return 0; }
void *picolet_wv2_create_controller_blocking(void *e, void *h, int32_t t) { (void)e; (void)h; (void)t; return 0; }
int32_t picolet_wv2_set_visible(void *c, int32_t v) { (void)c; (void)v; return -1; }
int32_t picolet_wv2_set_bounds(void *c, int32_t w, int32_t h) { (void)c; (void)w; (void)h; return -1; }
int32_t picolet_wv2_close_controller(void *c) { (void)c; return -1; }
int32_t picolet_wv2_add_script_to_execute_on_document_created(void *c, const char *j, int32_t t) { (void)c; (void)j; (void)t; return -1; }
int32_t picolet_wv2_navigate_to_string(void *c, const char *h) { (void)c; (void)h; return -1; }
int32_t picolet_wv2_execute_script(void *c, const char *j) { (void)c; (void)j; return -1; }
int32_t picolet_wv2_register_inbound_handler(void *c) { (void)c; return -1; }
char *picolet_wv2_poll_inbound(void) { return 0; }
void picolet_wv2_free_inbound(char *s) { (void)s; }
int32_t picolet_wv2_pump_messages(void) { return 0; }
#else  /* _WIN32 */

#include <windows.h>
#include <objbase.h>
#include <shlwapi.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "picolet_webview2.h"
#include "include/WebView2_min.h"

/* ----------------------------------------------------------------------
 * Per-call state — last error captured for Python to read.
 * ---------------------------------------------------------------------- */

static __thread HRESULT g_last_error = S_OK;

static void set_last(HRESULT hr) { g_last_error = hr; }

int32_t picolet_wv2_last_error(void) { return (int32_t)g_last_error; }

/* ----------------------------------------------------------------------
 * Loader-DLL extract path
 * ---------------------------------------------------------------------- */

static HMODULE g_loader_dll = NULL;
static PFN_CreateCoreWebView2EnvironmentWithOptions g_pfn_create_env = NULL;

static int build_loader_path(wchar_t *out, size_t outcap) {
    /* Use %LOCALAPPDATA%\picolet\<pid>\WebView2Loader.dll if available,
     * else fall back to %TEMP%\picolet-<pid>\WebView2Loader.dll. */
    wchar_t base[MAX_PATH];
    HRESULT hr = SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, base);
    if (FAILED(hr) || !*base) {
        DWORD n = GetTempPathW(MAX_PATH, base);
        if (n == 0 || n >= MAX_PATH) { return -1; }
    }
    DWORD pid = GetCurrentProcessId();
    int n = _snwprintf(out, outcap, L"%ls\\picolet\\%lu", base, (unsigned long)pid);
    if (n < 0 || (size_t)n >= outcap) { return -1; }
    /* Ensure the directory chain exists. */
    SHCreateDirectoryExW(NULL, out, NULL);
    int m = _snwprintf(out, outcap, L"%ls\\picolet\\%lu\\WebView2Loader.dll",
                       base, (unsigned long)pid);
    if (m < 0 || (size_t)m >= outcap) { return -1; }
    return 0;
}

void *picolet_wv2_load_loader_dll(const uint8_t *bytes, size_t size) {
    if (g_loader_dll != NULL) { return (void *)g_loader_dll; }
    if (bytes == NULL || size == 0) {
        set_last(E_INVALIDARG);
        return NULL;
    }

    wchar_t path[MAX_PATH * 2];
    if (build_loader_path(path, sizeof(path) / sizeof(path[0])) != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));
        return NULL;
    }

    /* Write the DLL bytes to disk.  We tolerate a pre-existing file
     * (idempotent unpack across process restarts is rare; the pid
     * subdir makes collisions effectively impossible). */
    HANDLE h = CreateFileW(path, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }
    DWORD wrote = 0;
    BOOL ok = WriteFile(h, bytes, (DWORD)size, &wrote, NULL);
    CloseHandle(h);
    if (!ok || wrote != size) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }

    HMODULE m = LoadLibraryW(path);
    if (m == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }
    g_loader_dll = m;
    g_pfn_create_env = (PFN_CreateCoreWebView2EnvironmentWithOptions)
        GetProcAddress(m, "CreateCoreWebView2EnvironmentWithOptions");
    if (g_pfn_create_env == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        FreeLibrary(m);
        g_loader_dll = NULL;
        return NULL;
    }
    set_last(S_OK);
    return (void *)m;
}

/* ----------------------------------------------------------------------
 * COM init
 * ---------------------------------------------------------------------- */

static int g_com_initialised = 0;

int32_t picolet_wv2_init_com(void) {
    if (g_com_initialised) { return 0; }
    HRESULT hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (hr == RPC_E_CHANGED_MODE) {
        /* Caller already set MTA — try joining as-is.  WebView2 requires
         * STA; if the host process is MTA we surface the error. */
        set_last(hr);
        return (int32_t)hr;
    }
    if (FAILED(hr)) {
        set_last(hr);
        return (int32_t)hr;
    }
    g_com_initialised = 1;
    set_last(S_OK);
    return 0;
}

/* ----------------------------------------------------------------------
 * Message-pump helper for blocking completions (AD3 two-phase pattern)
 * ---------------------------------------------------------------------- */

static int wait_with_pump(HANDLE event, DWORD timeout_ms) {
    DWORD start = GetTickCount();
    for (;;) {
        DWORD elapsed = GetTickCount() - start;
        if (elapsed >= timeout_ms) { return -1; }
        DWORD remaining = timeout_ms - elapsed;
        DWORD wait_rc = MsgWaitForMultipleObjects(1, &event, FALSE,
                                                  remaining, QS_ALLINPUT);
        if (wait_rc == WAIT_OBJECT_0) {
            /* The event signalled.  Drain any pending messages so the
             * completion's side effects (subsequent COM callbacks)
             * finish before we return. */
            MSG msg;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            return 0;
        } else if (wait_rc == WAIT_OBJECT_0 + 1) {
            /* Messages arrived.  Drain them and loop. */
            MSG msg;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            continue;
        } else if (wait_rc == WAIT_TIMEOUT) {
            return -1;
        } else {
            return -2;
        }
    }
}

/* ----------------------------------------------------------------------
 * Generic IUnknown methods for our caller-supplied handlers
 * ----------------------------------------------------------------------
 *
 * We use static singletons rather than ref-counted heap objects: each
 * handler is alive for the entire process lifetime (one-shot
 * completions block synchronously before returning; persistent
 * handlers stay registered until shutdown).  AddRef/Release return
 * 1 / 1 always (the runtime expects them to not crash; the actual
 * refcount is uninteresting for stack-allocated handlers).
 */

static HRESULT STDMETHODCALLTYPE handler_QI(void *self, REFIID riid, void **ppv) {
    (void)riid;
    if (ppv == NULL) { return E_POINTER; }
    *ppv = self;
    return S_OK;
}
static ULONG STDMETHODCALLTYPE handler_AddRef(void *self) { (void)self; return 1; }
static ULONG STDMETHODCALLTYPE handler_Release(void *self) { (void)self; return 1; }

/* ----------------------------------------------------------------------
 * Environment-created handler
 * ---------------------------------------------------------------------- */

typedef struct {
    PicoletWv2EnvCreatedHandlerVtbl vtbl;
    HANDLE event;
    HRESULT result;
    ICoreWebView2Environment *env;
} EnvHandlerCtx;

static HRESULT STDMETHODCALLTYPE env_handler_Invoke(
    PicoletWv2EnvCreatedHandler *self, HRESULT errorCode,
    ICoreWebView2Environment *createdEnvironment) {
    EnvHandlerCtx *ctx = (EnvHandlerCtx *)self;
    ctx->result = errorCode;
    ctx->env = createdEnvironment;
    if (createdEnvironment != NULL) {
        createdEnvironment->lpVtbl->AddRef(createdEnvironment);
    }
    SetEvent(ctx->event);
    return S_OK;
}

void *picolet_wv2_create_environment_blocking(int32_t timeout_ms) {
    if (g_pfn_create_env == NULL) {
        set_last(E_NOT_VALID_STATE);
        return NULL;
    }
    EnvHandlerCtx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2EnvCreatedHandler *, REFIID, void **))handler_QI;
    ctx.vtbl.AddRef = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2EnvCreatedHandler *))handler_AddRef;
    ctx.vtbl.Release = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2EnvCreatedHandler *))handler_Release;
    ctx.vtbl.Invoke = env_handler_Invoke;
    PicoletWv2EnvCreatedHandler handler;
    handler.lpVtbl = &ctx.vtbl;
    ctx.event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx.event == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }

    HRESULT hr = g_pfn_create_env(NULL, NULL, NULL, (PicoletWv2EnvCreatedHandler *)&ctx);
    if (FAILED(hr)) {
        set_last(hr);
        CloseHandle(ctx.event);
        return NULL;
    }

    int wrc = wait_with_pump(ctx.event, (DWORD)timeout_ms);
    CloseHandle(ctx.event);
    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return NULL;
    }
    if (FAILED(ctx.result)) {
        set_last(ctx.result);
        return NULL;
    }
    set_last(S_OK);
    return (void *)ctx.env;
}

/* ----------------------------------------------------------------------
 * Controller-created handler + caching
 * ---------------------------------------------------------------------- */

typedef struct {
    PicoletWv2CtrlCreatedHandlerVtbl vtbl;
    HANDLE event;
    HRESULT result;
    ICoreWebView2Controller *controller;
} CtrlHandlerCtx;

static HRESULT STDMETHODCALLTYPE ctrl_handler_Invoke(
    PicoletWv2CtrlCreatedHandler *self, HRESULT errorCode,
    ICoreWebView2Controller *createdController) {
    CtrlHandlerCtx *ctx = (CtrlHandlerCtx *)self;
    ctx->result = errorCode;
    ctx->controller = createdController;
    if (createdController != NULL) {
        createdController->lpVtbl->AddRef(createdController);
    }
    SetEvent(ctx->event);
    return S_OK;
}

/* We hold a global controller -> ICoreWebView2 cache so subsequent
 * picolet_wv2_navigate_to_string / picolet_wv2_execute_script /
 * picolet_wv2_add_script_to_execute_on_document_created /
 * picolet_wv2_register_inbound_handler don't pay the get_CoreWebView2
 * cost each time.  Single-controller assumption — v1 is single-window. */
static ICoreWebView2 *g_cached_view = NULL;
static ICoreWebView2Controller *g_cached_ctrl = NULL;

static ICoreWebView2 *get_view(void *controller) {
    if (g_cached_view != NULL && g_cached_ctrl == (ICoreWebView2Controller *)controller) {
        return g_cached_view;
    }
    ICoreWebView2Controller *ctrl = (ICoreWebView2Controller *)controller;
    ICoreWebView2 *view = NULL;
    HRESULT hr = ctrl->lpVtbl->get_CoreWebView2(ctrl, &view);
    if (FAILED(hr) || view == NULL) {
        set_last(hr);
        return NULL;
    }
    g_cached_view = view;
    g_cached_ctrl = ctrl;
    return view;
}

void *picolet_wv2_create_controller_blocking(void *env, void *hwnd, int32_t timeout_ms) {
    if (env == NULL) {
        set_last(E_INVALIDARG);
        return NULL;
    }
    CtrlHandlerCtx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2CtrlCreatedHandler *, REFIID, void **))handler_QI;
    ctx.vtbl.AddRef = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2CtrlCreatedHandler *))handler_AddRef;
    ctx.vtbl.Release = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2CtrlCreatedHandler *))handler_Release;
    ctx.vtbl.Invoke = ctrl_handler_Invoke;
    ctx.event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx.event == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }

    ICoreWebView2Environment *e = (ICoreWebView2Environment *)env;
    HRESULT hr = e->lpVtbl->CreateCoreWebView2Controller(
        e, (HWND)hwnd, (PicoletWv2CtrlCreatedHandler *)&ctx);
    if (FAILED(hr)) {
        set_last(hr);
        CloseHandle(ctx.event);
        return NULL;
    }

    int wrc = wait_with_pump(ctx.event, (DWORD)timeout_ms);
    CloseHandle(ctx.event);
    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return NULL;
    }
    if (FAILED(ctx.result)) {
        set_last(ctx.result);
        return NULL;
    }

    /* Pre-populate the get_CoreWebView2 cache so subsequent calls land
     * on the fast path. */
    (void)get_view(ctx.controller);

    set_last(S_OK);
    return (void *)ctx.controller;
}

int32_t picolet_wv2_set_visible(void *controller, int32_t visible) {
    if (controller == NULL) { set_last(E_INVALIDARG); return (int32_t)E_INVALIDARG; }
    ICoreWebView2Controller *c = (ICoreWebView2Controller *)controller;
    HRESULT hr = c->lpVtbl->put_IsVisible(c, visible ? TRUE : FALSE);
    set_last(hr);
    return (int32_t)hr;
}

int32_t picolet_wv2_set_bounds(void *controller, int32_t width, int32_t height) {
    if (controller == NULL) { set_last(E_INVALIDARG); return (int32_t)E_INVALIDARG; }
    ICoreWebView2Controller *c = (ICoreWebView2Controller *)controller;
    RECT r = { 0, 0, width, height };
    HRESULT hr = c->lpVtbl->put_Bounds(c, r);
    set_last(hr);
    return (int32_t)hr;
}

int32_t picolet_wv2_close_controller(void *controller) {
    if (controller == NULL) { return 0; }
    ICoreWebView2Controller *c = (ICoreWebView2Controller *)controller;
    HRESULT hr = c->lpVtbl->Close(c);
    if (c == g_cached_ctrl) {
        g_cached_view = NULL;
        g_cached_ctrl = NULL;
    }
    c->lpVtbl->Release(c);
    set_last(hr);
    return (int32_t)hr;
}

/* ----------------------------------------------------------------------
 * UTF-8 <-> UTF-16 helpers
 * ---------------------------------------------------------------------- */

static wchar_t *utf8_to_wide(const char *s) {
    if (s == NULL) { return NULL; }
    int len = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (len <= 0) { return NULL; }
    wchar_t *out = (wchar_t *)malloc(sizeof(wchar_t) * (size_t)len);
    if (out == NULL) { return NULL; }
    if (MultiByteToWideChar(CP_UTF8, 0, s, -1, out, len) <= 0) {
        free(out);
        return NULL;
    }
    return out;
}

static char *wide_to_utf8(LPCWSTR w) {
    if (w == NULL) { return NULL; }
    int len = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    if (len <= 0) { return NULL; }
    char *out = (char *)malloc((size_t)len);
    if (out == NULL) { return NULL; }
    if (WideCharToMultiByte(CP_UTF8, 0, w, -1, out, len, NULL, NULL) <= 0) {
        free(out);
        return NULL;
    }
    return out;
}

/* ----------------------------------------------------------------------
 * AddScriptToExecuteOnDocumentCreated — completion handler
 * ---------------------------------------------------------------------- */

typedef struct {
    PicoletWv2AddScriptHandlerVtbl vtbl;
    HANDLE event;
    HRESULT result;
} AddScriptHandlerCtx;

static HRESULT STDMETHODCALLTYPE add_script_Invoke(
    PicoletWv2AddScriptHandler *self, HRESULT errorCode, LPCWSTR id) {
    (void)id;
    AddScriptHandlerCtx *ctx = (AddScriptHandlerCtx *)self;
    ctx->result = errorCode;
    SetEvent(ctx->event);
    return S_OK;
}

int32_t picolet_wv2_add_script_to_execute_on_document_created(
    void *controller, const char *js_utf8, int32_t timeout_ms) {
    if (controller == NULL || js_utf8 == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    ICoreWebView2 *view = get_view(controller);
    if (view == NULL) {
        return (int32_t)g_last_error;
    }
    wchar_t *jsW = utf8_to_wide(js_utf8);
    if (jsW == NULL) {
        set_last(E_OUTOFMEMORY);
        return (int32_t)E_OUTOFMEMORY;
    }

    AddScriptHandlerCtx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2AddScriptHandler *, REFIID, void **))handler_QI;
    ctx.vtbl.AddRef = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2AddScriptHandler *))handler_AddRef;
    ctx.vtbl.Release = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2AddScriptHandler *))handler_Release;
    ctx.vtbl.Invoke = add_script_Invoke;
    ctx.event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx.event == NULL) {
        free(jsW);
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return (int32_t)g_last_error;
    }

    HRESULT hr = view->lpVtbl->AddScriptToExecuteOnDocumentCreated(
        view, jsW, (PicoletWv2AddScriptHandler *)&ctx);
    if (FAILED(hr)) {
        free(jsW);
        CloseHandle(ctx.event);
        set_last(hr);
        return (int32_t)hr;
    }

    int wrc = wait_with_pump(ctx.event, (DWORD)timeout_ms);
    CloseHandle(ctx.event);
    free(jsW);
    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return (int32_t)g_last_error;
    }
    set_last(ctx.result);
    return (int32_t)ctx.result;
}

/* ----------------------------------------------------------------------
 * NavigateToString
 * ---------------------------------------------------------------------- */

int32_t picolet_wv2_navigate_to_string(void *controller, const char *html_utf8) {
    if (controller == NULL || html_utf8 == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    ICoreWebView2 *view = get_view(controller);
    if (view == NULL) { return (int32_t)g_last_error; }
    wchar_t *htmlW = utf8_to_wide(html_utf8);
    if (htmlW == NULL) {
        set_last(E_OUTOFMEMORY);
        return (int32_t)E_OUTOFMEMORY;
    }
    HRESULT hr = view->lpVtbl->NavigateToString(view, htmlW);
    free(htmlW);
    set_last(hr);
    return (int32_t)hr;
}

/* ----------------------------------------------------------------------
 * ExecuteScript — completion handler is a do-nothing static singleton.
 * ---------------------------------------------------------------------- */

static HRESULT STDMETHODCALLTYPE execscript_noop_Invoke(
    PicoletWv2ExecuteScriptHandler *self, HRESULT errorCode, LPCWSTR result) {
    (void)self; (void)errorCode; (void)result;
    return S_OK;
}

static PicoletWv2ExecuteScriptHandlerVtbl g_execscript_noop_vtbl;
static PicoletWv2ExecuteScriptHandler g_execscript_noop_handler;
static int g_execscript_noop_initialised = 0;

static void init_execscript_noop(void) {
    if (g_execscript_noop_initialised) { return; }
    g_execscript_noop_vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2ExecuteScriptHandler *, REFIID, void **))handler_QI;
    g_execscript_noop_vtbl.AddRef = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2ExecuteScriptHandler *))handler_AddRef;
    g_execscript_noop_vtbl.Release = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2ExecuteScriptHandler *))handler_Release;
    g_execscript_noop_vtbl.Invoke = execscript_noop_Invoke;
    g_execscript_noop_handler.lpVtbl = &g_execscript_noop_vtbl;
    g_execscript_noop_initialised = 1;
}

int32_t picolet_wv2_execute_script(void *controller, const char *js_utf8) {
    if (controller == NULL || js_utf8 == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    ICoreWebView2 *view = get_view(controller);
    if (view == NULL) { return (int32_t)g_last_error; }
    wchar_t *jsW = utf8_to_wide(js_utf8);
    if (jsW == NULL) {
        set_last(E_OUTOFMEMORY);
        return (int32_t)E_OUTOFMEMORY;
    }
    init_execscript_noop();
    HRESULT hr = view->lpVtbl->ExecuteScript(view, jsW, &g_execscript_noop_handler);
    free(jsW);
    set_last(hr);
    return (int32_t)hr;
}

/* ----------------------------------------------------------------------
 * Inbound WebMessageReceived handler + ring buffer
 * ----------------------------------------------------------------------
 *
 * The handler runs synchronously on the STA pump thread.  It calls
 * TryGetWebMessageAsString to extract the JSON string the JS posted
 * via window.chrome.webview.postMessage, converts UTF-16 to UTF-8 in a
 * malloc'd buffer, and enqueues into a 256-slot SPSC ring.  Python
 * polls per pump tick.  On ring overflow we drop and log.
 */

#define PICOLET_WV2_RING_SIZE 256

static char *g_ring[PICOLET_WV2_RING_SIZE];
static volatile LONG g_ring_head = 0;  /* next slot the producer writes */
static volatile LONG g_ring_tail = 0;  /* next slot the consumer reads */

static int ring_push(char *s) {
    LONG h = g_ring_head;
    LONG t = g_ring_tail;
    if (((h + 1) % PICOLET_WV2_RING_SIZE) == t) {
        /* full — drop */
        return -1;
    }
    g_ring[h] = s;
    /* Memory ordering: the slot store must publish before the head
     * advances so the consumer sees the value.  MemoryBarrier()
     * suffices on x64. */
    MemoryBarrier();
    g_ring_head = (h + 1) % PICOLET_WV2_RING_SIZE;
    return 0;
}

static char *ring_pop(void) {
    LONG h = g_ring_head;
    LONG t = g_ring_tail;
    if (h == t) { return NULL; }
    char *s = g_ring[t];
    MemoryBarrier();
    g_ring_tail = (t + 1) % PICOLET_WV2_RING_SIZE;
    return s;
}

static HRESULT STDMETHODCALLTYPE inbound_Invoke(
    PicoletWv2WebMessageHandler *self, ICoreWebView2 *sender,
    ICoreWebView2WebMessageReceivedEventArgs *args) {
    (void)self; (void)sender;
    if (args == NULL) { return S_OK; }
    LPWSTR wjson = NULL;
    HRESULT hr = args->lpVtbl->TryGetWebMessageAsString(args, &wjson);
    if (FAILED(hr) || wjson == NULL) {
        /* Try get_WebMessageAsJson as fallback. */
        hr = args->lpVtbl->get_WebMessageAsJson(args, &wjson);
        if (FAILED(hr) || wjson == NULL) {
            fprintf(stderr,
                    "picolet_webview2: inbound message had no extractable payload (0x%08lx)\n",
                    (unsigned long)hr);
            return S_OK;
        }
    }
    char *u = wide_to_utf8(wjson);
    /* CoTaskMemFree the WebView2-allocated wide string. */
    CoTaskMemFree(wjson);
    if (u == NULL) { return S_OK; }
    if (ring_push(u) != 0) {
        fprintf(stderr, "picolet_webview2: inbound ring buffer full; dropping message\n");
        free(u);
    }
    return S_OK;
}

static PicoletWv2WebMessageHandlerVtbl g_inbound_vtbl;
static PicoletWv2WebMessageHandler g_inbound_handler;
static int g_inbound_registered = 0;

int32_t picolet_wv2_register_inbound_handler(void *controller) {
    if (g_inbound_registered) { return 0; }
    if (controller == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    ICoreWebView2 *view = get_view(controller);
    if (view == NULL) { return (int32_t)g_last_error; }

    g_inbound_vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2WebMessageHandler *, REFIID, void **))handler_QI;
    g_inbound_vtbl.AddRef = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2WebMessageHandler *))handler_AddRef;
    g_inbound_vtbl.Release = (ULONG (STDMETHODCALLTYPE *)(PicoletWv2WebMessageHandler *))handler_Release;
    g_inbound_vtbl.Invoke = inbound_Invoke;
    g_inbound_handler.lpVtbl = &g_inbound_vtbl;

    /* Ensure JS messages are enabled in the WebView2 settings. */
    ICoreWebView2Settings *settings = NULL;
    HRESULT hr = view->lpVtbl->get_Settings(view, &settings);
    if (SUCCEEDED(hr) && settings != NULL) {
        settings->lpVtbl->put_IsWebMessageEnabled(settings, TRUE);
        settings->lpVtbl->put_IsScriptEnabled(settings, TRUE);
        settings->lpVtbl->Release(settings);
    }

    EventRegistrationToken token;
    hr = view->lpVtbl->add_WebMessageReceived(view, &g_inbound_handler, &token);
    set_last(hr);
    if (SUCCEEDED(hr)) { g_inbound_registered = 1; }
    return (int32_t)hr;
}

char *picolet_wv2_poll_inbound(void) {
    return ring_pop();
}

void picolet_wv2_free_inbound(char *s) {
    if (s != NULL) { free(s); }
}

/* ----------------------------------------------------------------------
 * Message pump (called from the Python pump task per tick)
 * ---------------------------------------------------------------------- */

int32_t picolet_wv2_pump_messages(void) {
    int32_t n = 0;
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
        n++;
        if (n > 64) { break; }  /* cap per tick — matches PH07's pump cap */
    }
    return n;
}

#endif /* _WIN32 */
