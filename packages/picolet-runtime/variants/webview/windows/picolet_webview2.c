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
int32_t picolet_wv2_pick_test_port(void) { return -1; }
void *picolet_wv2_create_environment_blocking(const wchar_t *a, int32_t t) { (void)a; (void)t; return 0; }
void *picolet_wv2_create_controller_blocking(void *e, void *h, int32_t t) { (void)e; (void)h; (void)t; return 0; }
int32_t picolet_wv2_set_visible(void *c, int32_t v) { (void)c; (void)v; return -1; }
int32_t picolet_wv2_set_bounds(void *c, int32_t w, int32_t h) { (void)c; (void)w; (void)h; return -1; }
int32_t picolet_wv2_close_controller(void *c) { (void)c; return -1; }
int32_t picolet_wv2_add_script_to_execute_on_document_created(void *c, const char *j, int32_t t) { (void)c; (void)j; (void)t; return -1; }
int32_t picolet_wv2_navigate_to_string(void *c, const char *h) { (void)c; (void)h; return -1; }
int32_t picolet_wv2_navigate(void *c, const wchar_t *u) { (void)c; (void)u; return -1; }
int32_t picolet_wv2_execute_script(void *c, const char *j) { (void)c; (void)j; return -1; }
int32_t picolet_wv2_register_inbound_handler(void *c) { (void)c; return -1; }
char *picolet_wv2_poll_inbound(void) { return 0; }
void picolet_wv2_free_inbound(char *s) { (void)s; }
int32_t picolet_wv2_pump_messages(void) { return 0; }
void *picolet_wv2_create_window(const char *t, int32_t w, int32_t h, int32_t r) { (void)t; (void)w; (void)h; (void)r; return 0; }
int32_t picolet_wv2_show_window(void *hw, int32_t v) { (void)hw; (void)v; return -1; }
int32_t picolet_wv2_window_attach_controller(void *hw, void *c) { (void)hw; (void)c; return -1; }
int32_t picolet_wv2_destroy_window(void *hw) { (void)hw; return -1; }
#else  /* _WIN32 */

/* Winsock2 must come before windows.h to avoid winsock/winsock2 conflicts. */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <objbase.h>
#include <shlwapi.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "picolet_webview2.h"
#include "include/WebView2_min.h"

/* ----------------------------------------------------------------------
 * Inbound message size cap (S4).
 *
 * 1 MiB default, configurable at runtime via PICOLET_MAX_MESSAGE_BYTES.
 * Applied to the wide-string length before wide_to_utf8 allocates.
 * On overflow the message is freed and not enqueued; a diagnostic is
 * written via OutputDebugStringW.
 * ---------------------------------------------------------------------- */

#define PICOLET_DEFAULT_MAX_MESSAGE_BYTES (1024 * 1024)

static size_t g_max_message_bytes = 0;  /* 0 = not yet initialised */

static size_t get_max_message_bytes(void) {
    if (g_max_message_bytes != 0) {
        return g_max_message_bytes;
    }
    /* Read PICOLET_MAX_MESSAGE_BYTES env var; fall back to default. */
    wchar_t buf[32];
    DWORD n = GetEnvironmentVariableW(L"PICOLET_MAX_MESSAGE_BYTES", buf,
                                     (DWORD)(sizeof(buf) / sizeof(buf[0])));
    if (n > 0 && n < (DWORD)(sizeof(buf) / sizeof(buf[0]))) {
        size_t val = (size_t)_wtoi64(buf);
        if (val > 0) {
            g_max_message_bytes = val;
            return val;
        }
    }
    g_max_message_bytes = (size_t)PICOLET_DEFAULT_MAX_MESSAGE_BYTES;
    return g_max_message_bytes;
}

/* ----------------------------------------------------------------------
 * Per-call state — last error captured for Python to read.
 * ---------------------------------------------------------------------- */

static __thread HRESULT g_last_error = S_OK;

static void set_last(HRESULT hr) { g_last_error = hr; }

int32_t picolet_wv2_last_error(void) { return (int32_t)g_last_error; }

/* ----------------------------------------------------------------------
 * PH17 — pick a free loopback TCP port (FR-TEST-1, Windows/WebView2)
 * ---------------------------------------------------------------------- */

int32_t picolet_wv2_pick_test_port(void) {
    /* Bind a TCP socket to 127.0.0.1:0, read the assigned port, close.
     * The race window between close and the engine re-binding the port is
     * microseconds (no TIME_WAIT on a never-accepted listen socket).
     * The AppHarness retries on connect failure for up to 10 s (D4/F8). */
    WSADATA wsa;
    int need_cleanup = 0;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) == 0) {
        need_cleanup = 1;
    }

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) {
        if (need_cleanup) WSACleanup();
        return -1;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = 0;  /* let OS pick */
    addr.sin_addr.s_addr = htonl(0x7f000001);  /* 127.0.0.1 */

    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        closesocket(s);
        if (need_cleanup) WSACleanup();
        return -1;
    }

    struct sockaddr_in out;
    int outlen = sizeof(out);
    if (getsockname(s, (struct sockaddr *)&out, &outlen) != 0) {
        closesocket(s);
        if (need_cleanup) WSACleanup();
        return -1;
    }

    int port = (int)ntohs(out.sin_port);
    closesocket(s);
    /* Do NOT WSACleanup here — the caller still needs Winsock active when
     * it passes the port to WebView2 shortly after this call. */
    (void)need_cleanup;
    return (int32_t)port;
}

/* ----------------------------------------------------------------------
 * Loader-DLL extract path
 * ---------------------------------------------------------------------- */

static HMODULE g_loader_dll = NULL;
static PFN_CreateCoreWebView2EnvironmentWithOptions g_pfn_create_env = NULL;

static int build_loader_dir(wchar_t *out, size_t outcap) {
    /* %LOCALAPPDATA%\picolet\loader\ (shared across all picolet processes)
     * with a %TEMP%\picolet\loader\ fallback if SHGetFolderPathW fails.
     *
     * Shared path rather than per-pid: the unpacked DLL bytes are
     * identical across runs and across concurrent picolet processes, so a
     * single cached copy is sufficient and avoids the per-pid directory
     * cleanup problem (S8). */
    wchar_t base[MAX_PATH];
    HRESULT hr = SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, base);
    if (FAILED(hr) || !*base) {
        DWORD n = GetTempPathW(MAX_PATH, base);
        if (n == 0 || n >= MAX_PATH) { return -1; }
    }
    int n = _snwprintf(out, outcap, L"%ls\\picolet\\loader", base);
    if (n < 0 || (size_t)n >= outcap) { return -1; }
    return 0;
}

/* Open a handle to `path` without following reparse points.  Returns
 * INVALID_HANDLE_VALUE on failure (caller inspects GetLastError).  Used
 * to harden against symlink/junction-point swap attacks on the loader
 * file path (S7). */
static HANDLE open_no_reparse(LPCWSTR path, DWORD access, DWORD share,
                              DWORD disposition) {
    HANDLE h = CreateFileW(path, access, share, NULL, disposition,
                           FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                           NULL);
    if (h == INVALID_HANDLE_VALUE) { return h; }
    /* Reject any reparse-point handle.  A legitimate file we just
     * extracted will never have the REPARSE_POINT attribute set; if it
     * does, a hostile actor planted a symlink/junction here and we must
     * not LoadLibraryW it. */
    BY_HANDLE_FILE_INFORMATION info;
    if (!GetFileInformationByHandle(h, &info) ||
        (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        CloseHandle(h);
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    return h;
}

void *picolet_wv2_load_loader_dll(const uint8_t *bytes, size_t size) {
    if (g_loader_dll != NULL) { return (void *)g_loader_dll; }
    if (bytes == NULL || size == 0) {
        set_last(E_INVALIDARG);
        return NULL;
    }

    wchar_t dir[MAX_PATH * 2];
    if (build_loader_dir(dir, sizeof(dir) / sizeof(dir[0])) != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));
        return NULL;
    }
    /* Ensure the directory chain exists. */
    SHCreateDirectoryExW(NULL, dir, NULL);

    wchar_t path[MAX_PATH * 2];
    int m = _snwprintf(path, sizeof(path) / sizeof(path[0]),
                       L"%ls\\WebView2Loader.dll", dir);
    if (m < 0 || (size_t)m >= sizeof(path) / sizeof(path[0])) {
        set_last(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));
        return NULL;
    }

    /* CREATE_NEW: refuse to overwrite an existing file.  If another
     * picolet process (or a prior run) already extracted the loader, the
     * existing file is the same bytes — we use it instead of clobbering.
     * If the path resolves to a symlink/junction (S7), open_no_reparse
     * rejects with ERROR_ACCESS_DENIED. */
    HANDLE h = open_no_reparse(path, GENERIC_WRITE, 0, CREATE_NEW);
    if (h == INVALID_HANDLE_VALUE) {
        DWORD err = GetLastError();
        if (err == ERROR_FILE_EXISTS) {
            /* Pre-existing file from a prior run.  Skip the write and
             * fall through to LoadLibraryW after verifying the path
             * itself is not a reparse point. */
            HANDLE vh = open_no_reparse(path, GENERIC_READ,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                                        OPEN_EXISTING);
            if (vh == INVALID_HANDLE_VALUE) {
                set_last(HRESULT_FROM_WIN32(GetLastError()));
                return NULL;
            }
            CloseHandle(vh);
        } else {
            set_last(HRESULT_FROM_WIN32(err));
            return NULL;
        }
    } else {
        DWORD wrote = 0;
        BOOL ok = WriteFile(h, bytes, (DWORD)size, &wrote, NULL);
        DWORD werr = ok ? 0 : GetLastError();
        CloseHandle(h);
        if (!ok || wrote != size) {
            set_last(HRESULT_FROM_WIN32(werr ? werr : ERROR_WRITE_FAULT));
            return NULL;
        }
    }

    HMODULE mod = LoadLibraryW(path);
    if (mod == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }
    g_loader_dll = mod;
    g_pfn_create_env = (PFN_CreateCoreWebView2EnvironmentWithOptions)
        GetProcAddress(mod, "CreateCoreWebView2EnvironmentWithOptions");
    if (g_pfn_create_env == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        FreeLibrary(mod);
        g_loader_dll = NULL;
        return NULL;
    }
    set_last(S_OK);
    return (void *)mod;
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
 * One-shot blocking completion handlers (Env/Ctrl/AddScript) are heap-
 * allocated and ref-counted.  The blocking helper holds the caller's
 * ref until after the wait returns (even on timeout); WebView2's
 * eventual Invoke holds a second ref via add_X/CreateX callbacks and drops it
 * after writing its result.  This eliminates the UAF risk where a
 * timed-out helper returned and freed its stack frame while WebView2
 * still held the handler pointer (S1).
 *
 * Each Ctx struct has its IFace base first (so &ctx->base aliases &ctx
 * for the thunk casts) and a per-Ctx AddRef/Release pair that operates
 * on a `refcount` field via InterlockedIncrement / InterlockedDecrement.
 * The HANDLE `event` is owned by the refcount: whichever side (helper
 * or Invoke) calls the final Release closes it as part of cleanup.
 *
 * Persistent handlers (the WebMessageReceived sink and the do-nothing
 * ExecuteScript completion) keep their static-singleton lifetime — they
 * outlive every async call by construction.
 */

static HRESULT STDMETHODCALLTYPE handler_QI(void *self, REFIID riid, void **ppv) {
    (void)riid;
    if (ppv == NULL) { return E_POINTER; }
    *ppv = self;
    return S_OK;
}

/* No-op refcount for static-singleton handlers whose lifetime is the
 * whole process (the WebMessageReceived sink and the ExecuteScript
 * completion). */
static ULONG STDMETHODCALLTYPE handler_AddRef(void *self) { (void)self; return 1; }
static ULONG STDMETHODCALLTYPE handler_Release(void *self) { (void)self; return 1; }

/* Per-Ctx AddRef/Release for the heap-allocated one-shot handlers are
 * defined alongside each Ctx struct below.  The shared QI thunk above
 * is used by all of them. */

/* ----------------------------------------------------------------------
 * Environment-created handler
 * ---------------------------------------------------------------------- */

/* Layout: PicoletWv2EnvCreatedHandler base first so &ctx->base aliases &ctx
 * — that lets us recover the Ctx* in the AddRef/Release/Invoke thunks
 * via a simple cast.  refcount uses Interlocked* ops because WebView2's
 * Invoke may fire on a worker thread before delivering to our STA pump
 * (defensive — measured behaviour is STA-only, but the COM contract
 * doesn't promise that).
 *
 * `event` may be NULL after the blocking helper closes its handle on
 * timeout; the Invoke thunk guards against that. */
typedef struct {
    PicoletWv2EnvCreatedHandler base;
    PicoletWv2EnvCreatedHandlerVtbl vtbl;
    LONG refcount;
    HANDLE event;
    HRESULT result;
    ICoreWebView2Environment *env;
} EnvHandlerCtx;

static ULONG STDMETHODCALLTYPE env_handler_AddRef(PicoletWv2EnvCreatedHandler *self) {
    EnvHandlerCtx *ctx = (EnvHandlerCtx *)self;
    return (ULONG)InterlockedIncrement(&ctx->refcount);
}
static ULONG STDMETHODCALLTYPE env_handler_Release(PicoletWv2EnvCreatedHandler *self) {
    EnvHandlerCtx *ctx = (EnvHandlerCtx *)self;
    LONG n = InterlockedDecrement(&ctx->refcount);
    if (n == 0) {
        /* Last ref drops the event handle too.  Whichever side (helper
         * or Invoke) releases last closes it — neither side races on
         * the close itself. */
        if (ctx->event != NULL) {
            CloseHandle(ctx->event);
            ctx->event = NULL;
        }
        free(ctx);
    }
    return (ULONG)n;
}

static HRESULT STDMETHODCALLTYPE env_handler_Invoke(
    PicoletWv2EnvCreatedHandler *self, HRESULT errorCode,
    ICoreWebView2Environment *createdEnvironment) {
    EnvHandlerCtx *ctx = (EnvHandlerCtx *)self;
    ctx->result = errorCode;
    ctx->env = createdEnvironment;
    if (createdEnvironment != NULL) {
        createdEnvironment->lpVtbl->AddRef(createdEnvironment);
    }
    /* Signal the helper.  ctx->event is guaranteed live because the
     * helper still holds its ref (no Release until after its wait).
     * If the helper's wait timed out before this Invoke fired, the
     * helper has already dropped its ref but the heap object lives
     * until our Release call below — including the event handle, which
     * is released by the last Release. */
    if (ctx->event != NULL) { SetEvent(ctx->event); }
    return S_OK;
}

/* ----------------------------------------------------------------------
 * PH17 — ICoreWebView2EnvironmentOptions vtable shim
 *
 * A minimal stack-allocated COM object used to pass AdditionalBrowserArguments
 * when PICOLET_TEST_MODE=1.  WebView2 calls the getters synchronously during
 * CreateCoreWebView2EnvironmentWithOptions and does NOT retain the pointer
 * past the call (R9), so stack allocation is safe.
 * ---------------------------------------------------------------------- */

static HRESULT STDMETHODCALLTYPE env_opts_QI(
    PicoletWv2EnvOptions *self, REFIID riid, void **ppv) {
    (void)self; (void)riid;
    *ppv = NULL;
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE env_opts_AddRef(PicoletWv2EnvOptions *self) {
    (void)self; return 1;
}
static ULONG STDMETHODCALLTYPE env_opts_Release(PicoletWv2EnvOptions *self) {
    (void)self; return 1;
}
static HRESULT STDMETHODCALLTYPE env_opts_get_AdditionalBrowserArguments(
    PicoletWv2EnvOptions *self, LPWSTR *out) {
    /* Return a copy the caller must CoTaskMemFree. */
    if (self->additional_args == NULL) { *out = NULL; return S_OK; }
    size_t len = wcslen(self->additional_args) + 1;
    LPWSTR buf = (LPWSTR)CoTaskMemAlloc(len * sizeof(WCHAR));
    if (buf == NULL) return E_OUTOFMEMORY;
    wmemcpy(buf, self->additional_args, len);
    *out = buf;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE env_opts_put_AdditionalBrowserArguments(
    PicoletWv2EnvOptions *self, LPCWSTR v) {
    self->additional_args = v; return S_OK;
}
static HRESULT STDMETHODCALLTYPE env_opts_stub_get_str(
    PicoletWv2EnvOptions *self, LPWSTR *out) {
    (void)self; if (out) *out = NULL; return S_OK;
}
static HRESULT STDMETHODCALLTYPE env_opts_stub_put_str(
    PicoletWv2EnvOptions *self, LPCWSTR v) {
    (void)self; (void)v; return S_OK;
}
static HRESULT STDMETHODCALLTYPE env_opts_get_sso(
    PicoletWv2EnvOptions *self, BOOL *out) {
    (void)self; if (out) *out = FALSE; return S_OK;
}
static HRESULT STDMETHODCALLTYPE env_opts_put_sso(
    PicoletWv2EnvOptions *self, BOOL v) {
    (void)self; (void)v; return S_OK;
}

static PicoletWv2EnvOptionsVtbl g_env_opts_vtbl = {
    env_opts_QI,
    env_opts_AddRef,
    env_opts_Release,
    env_opts_get_AdditionalBrowserArguments,
    env_opts_put_AdditionalBrowserArguments,
    env_opts_stub_get_str,  /* get_Language */
    env_opts_stub_put_str,  /* put_Language */
    env_opts_stub_get_str,  /* get_TargetCompatibleBrowserVersion */
    env_opts_stub_put_str,  /* put_TargetCompatibleBrowserVersion */
    env_opts_get_sso,
    env_opts_put_sso,
};

void *picolet_wv2_create_environment_blocking(const wchar_t *extra_browser_args,
                                             int32_t timeout_ms) {
    if (g_pfn_create_env == NULL) {
        set_last(HRESULT_FROM_WIN32(ERROR_INVALID_STATE));
        return NULL;
    }
    EnvHandlerCtx *ctx = (EnvHandlerCtx *)calloc(1, sizeof(*ctx));
    if (ctx == NULL) {
        set_last(E_OUTOFMEMORY);
        return NULL;
    }
    ctx->vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2EnvCreatedHandler *, REFIID, void **))handler_QI;
    ctx->vtbl.AddRef = env_handler_AddRef;
    ctx->vtbl.Release = env_handler_Release;
    ctx->vtbl.Invoke = env_handler_Invoke;
    ctx->base.lpVtbl = &ctx->vtbl;
    ctx->refcount = 1;  /* caller's ref */
    ctx->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx->event == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        env_handler_Release(&ctx->base);
        return NULL;
    }

    /* Build the environment options shim when extra_browser_args is provided
     * (PH17 PICOLET_TEST_MODE path).  Stack-allocated; lifetime is bounded by
     * this function frame (R9 — WebView2 does not retain the pointer). */
    PicoletWv2EnvOptions env_opts;
    PicoletWv2EnvOptions *opts_ptr = NULL;
    if (extra_browser_args != NULL) {
        memset(&env_opts, 0, sizeof(env_opts));
        env_opts.lpVtbl = &g_env_opts_vtbl;
        env_opts.additional_args = extra_browser_args;
        opts_ptr = &env_opts;
    }

    HRESULT hr = g_pfn_create_env(NULL, NULL, opts_ptr, &ctx->base);
    if (FAILED(hr)) {
        set_last(hr);
        env_handler_Release(&ctx->base);  /* closes event via refcount=0 cleanup */
        return NULL;
    }

    int wrc = wait_with_pump(ctx->event, (DWORD)timeout_ms);
    /* Snapshot result fields before dropping our ref.  If wait timed
     * out, WebView2 still holds a ref; its eventual Invoke writes into
     * ctx (still-live heap memory) and then Releases, decrementing the
     * refcount to 0 and freeing the context + closing the event.  No
     * UAF.  If wait succeeded, both refs may already be down to ours;
     * our Release drops the last ref and frees. */
    ICoreWebView2Environment *env = ctx->env;
    HRESULT result = ctx->result;
    env_handler_Release(&ctx->base);  /* drop caller's ref */

    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return NULL;
    }
    if (FAILED(result)) {
        set_last(result);
        return NULL;
    }
    set_last(S_OK);
    return (void *)env;
}

/* ----------------------------------------------------------------------
 * Controller-created handler + caching
 * ---------------------------------------------------------------------- */

typedef struct {
    PicoletWv2CtrlCreatedHandler base;
    PicoletWv2CtrlCreatedHandlerVtbl vtbl;
    LONG refcount;
    HANDLE event;
    HRESULT result;
    ICoreWebView2Controller *controller;
} CtrlHandlerCtx;

static ULONG STDMETHODCALLTYPE ctrl_handler_AddRef(PicoletWv2CtrlCreatedHandler *self) {
    CtrlHandlerCtx *ctx = (CtrlHandlerCtx *)self;
    return (ULONG)InterlockedIncrement(&ctx->refcount);
}
static ULONG STDMETHODCALLTYPE ctrl_handler_Release(PicoletWv2CtrlCreatedHandler *self) {
    CtrlHandlerCtx *ctx = (CtrlHandlerCtx *)self;
    LONG n = InterlockedDecrement(&ctx->refcount);
    if (n == 0) {
        if (ctx->event != NULL) {
            CloseHandle(ctx->event);
            ctx->event = NULL;
        }
        free(ctx);
    }
    return (ULONG)n;
}

static HRESULT STDMETHODCALLTYPE ctrl_handler_Invoke(
    PicoletWv2CtrlCreatedHandler *self, HRESULT errorCode,
    ICoreWebView2Controller *createdController) {
    CtrlHandlerCtx *ctx = (CtrlHandlerCtx *)self;
    ctx->result = errorCode;
    ctx->controller = createdController;
    if (createdController != NULL) {
        createdController->lpVtbl->AddRef(createdController);
    }
    if (ctx->event != NULL) { SetEvent(ctx->event); }
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
    CtrlHandlerCtx *ctx = (CtrlHandlerCtx *)calloc(1, sizeof(*ctx));
    if (ctx == NULL) {
        set_last(E_OUTOFMEMORY);
        return NULL;
    }
    ctx->vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2CtrlCreatedHandler *, REFIID, void **))handler_QI;
    ctx->vtbl.AddRef = ctrl_handler_AddRef;
    ctx->vtbl.Release = ctrl_handler_Release;
    ctx->vtbl.Invoke = ctrl_handler_Invoke;
    ctx->base.lpVtbl = &ctx->vtbl;
    ctx->refcount = 1;
    ctx->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx->event == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        ctrl_handler_Release(&ctx->base);
        return NULL;
    }

    ICoreWebView2Environment *e = (ICoreWebView2Environment *)env;
    HRESULT hr = e->lpVtbl->CreateCoreWebView2Controller(
        e, (HWND)hwnd, &ctx->base);
    if (FAILED(hr)) {
        set_last(hr);
        ctrl_handler_Release(&ctx->base);
        return NULL;
    }

    int wrc = wait_with_pump(ctx->event, (DWORD)timeout_ms);
    ICoreWebView2Controller *controller = ctx->controller;
    HRESULT result = ctx->result;
    ctrl_handler_Release(&ctx->base);  /* drop caller's ref */

    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return NULL;
    }
    if (FAILED(result)) {
        set_last(result);
        return NULL;
    }

    /* Pre-populate the get_CoreWebView2 cache so subsequent calls land
     * on the fast path. */
    (void)get_view(controller);

    set_last(S_OK);
    return (void *)controller;
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
    PicoletWv2AddScriptHandler base;
    PicoletWv2AddScriptHandlerVtbl vtbl;
    LONG refcount;
    HANDLE event;
    HRESULT result;
} AddScriptHandlerCtx;

static ULONG STDMETHODCALLTYPE add_script_AddRef(PicoletWv2AddScriptHandler *self) {
    AddScriptHandlerCtx *ctx = (AddScriptHandlerCtx *)self;
    return (ULONG)InterlockedIncrement(&ctx->refcount);
}
static ULONG STDMETHODCALLTYPE add_script_Release(PicoletWv2AddScriptHandler *self) {
    AddScriptHandlerCtx *ctx = (AddScriptHandlerCtx *)self;
    LONG n = InterlockedDecrement(&ctx->refcount);
    if (n == 0) {
        if (ctx->event != NULL) {
            CloseHandle(ctx->event);
            ctx->event = NULL;
        }
        free(ctx);
    }
    return (ULONG)n;
}

static HRESULT STDMETHODCALLTYPE add_script_Invoke(
    PicoletWv2AddScriptHandler *self, HRESULT errorCode, LPCWSTR id) {
    (void)id;
    AddScriptHandlerCtx *ctx = (AddScriptHandlerCtx *)self;
    ctx->result = errorCode;
    if (ctx->event != NULL) { SetEvent(ctx->event); }
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

    AddScriptHandlerCtx *ctx = (AddScriptHandlerCtx *)calloc(1, sizeof(*ctx));
    if (ctx == NULL) {
        free(jsW);
        set_last(E_OUTOFMEMORY);
        return (int32_t)E_OUTOFMEMORY;
    }
    ctx->vtbl.QueryInterface = (HRESULT (STDMETHODCALLTYPE *)(PicoletWv2AddScriptHandler *, REFIID, void **))handler_QI;
    ctx->vtbl.AddRef = add_script_AddRef;
    ctx->vtbl.Release = add_script_Release;
    ctx->vtbl.Invoke = add_script_Invoke;
    ctx->base.lpVtbl = &ctx->vtbl;
    ctx->refcount = 1;
    ctx->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (ctx->event == NULL) {
        free(jsW);
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        HRESULT err = g_last_error;
        add_script_Release(&ctx->base);
        return (int32_t)err;
    }

    HRESULT hr = view->lpVtbl->AddScriptToExecuteOnDocumentCreated(
        view, jsW, &ctx->base);
    if (FAILED(hr)) {
        free(jsW);
        set_last(hr);
        add_script_Release(&ctx->base);
        return (int32_t)hr;
    }

    int wrc = wait_with_pump(ctx->event, (DWORD)timeout_ms);
    HRESULT result = ctx->result;
    add_script_Release(&ctx->base);  /* drop caller's ref */
    free(jsW);
    if (wrc != 0) {
        set_last(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
        return (int32_t)g_last_error;
    }
    set_last(result);
    return (int32_t)result;
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
 * Navigate — navigate the WebView2 to a URL.
 *
 * PH10 (A6 fix): exposes ICoreWebView2->Navigate so the Python side can
 * load a real URL directly instead of using a meta-refresh HTML redirect.
 * Used by picolet dev when PICOLET_DEV_URL is set (Windows path).
 * url is a NUL-terminated UTF-16 string.
 * ---------------------------------------------------------------------- */

int32_t picolet_wv2_navigate(void *controller, const wchar_t *url) {
    if (controller == NULL || url == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    ICoreWebView2 *view = get_view(controller);
    if (view == NULL) { return (int32_t)g_last_error; }
    HRESULT hr = view->lpVtbl->Navigate(view, url);
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
 * Threading model (single-threaded by construction):
 *   - The asyncio event loop runs on the STA thread that called
 *     CoInitializeEx(APARTMENTTHREADED).
 *   - WebView2's WebMessageReceived event is dispatched synchronously
 *     from inside our message pump (PeekMessageW + DispatchMessageW)
 *     in picolet_wv2_pump_messages, which is itself driven by the same
 *     asyncio loop.
 *   - Therefore ring_push (Invoke side) and ring_pop (asyncio-poll
 *     side) never execute concurrently.  No locks, atomics, or memory
 *     barriers are required.
 *
 * If a future variant moves the pump to a worker thread, switch to a
 * proper SPSC pattern (atomic-load the peer index with acquire
 * semantics; atomic-store the owned index with release semantics).
 */

#define PICOLET_WV2_RING_SIZE 256

static char *g_ring[PICOLET_WV2_RING_SIZE];
static LONG g_ring_head = 0;  /* next slot the producer writes */
static LONG g_ring_tail = 0;  /* next slot the consumer reads */

static int ring_push(char *s) {
    LONG h = g_ring_head;
    LONG t = g_ring_tail;
    if (((h + 1) % PICOLET_WV2_RING_SIZE) == t) {
        /* full — drop */
        return -1;
    }
    g_ring[h] = s;
    g_ring_head = (h + 1) % PICOLET_WV2_RING_SIZE;
    return 0;
}

static char *ring_pop(void) {
    LONG h = g_ring_head;
    LONG t = g_ring_tail;
    if (h == t) { return NULL; }
    char *s = g_ring[t];
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
    /* Size cap check (S4): guard before wide_to_utf8 allocates. */
    size_t wlen = wcslen(wjson);
    size_t max_bytes = get_max_message_bytes();
    /* UTF-8 is at most 3 bytes per wide char (BMP only; surrogates count
     * as 2 wide chars yielding one 4-byte sequence, so 3x is a valid
     * upper bound for BMP).  Reject if the worst-case UTF-8 size exceeds
     * the cap — this avoids allocating before we know the true size. */
    if (wlen > 0 && (wlen * 3) > max_bytes) {
        wchar_t dbg[128];
        _snwprintf(dbg, sizeof(dbg) / sizeof(dbg[0]),
                   L"picolet_webview2: inbound message too large (%zu wide chars"
                   L" * 3 > %zu limit); dropping\n",
                   wlen, max_bytes);
        OutputDebugStringW(dbg);
        CoTaskMemFree(wjson);
        return S_OK;
    }
    char *u = wide_to_utf8(wjson);
    /* CoTaskMemFree the WebView2-allocated wide string. */
    CoTaskMemFree(wjson);
    if (u == NULL) { return S_OK; }
    /* Secondary check on the actual UTF-8 byte count. */
    if (strlen(u) > max_bytes) {
        OutputDebugStringW(L"picolet_webview2: inbound message too large (UTF-8)"
                           L"; dropping\n");
        free(u);
        return S_OK;
    }
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

/* ----------------------------------------------------------------------
 * Top-level window
 * ----------------------------------------------------------------------
 *
 * The WindowProc owns three behaviours:
 *   * WM_SIZE   — resize the attached controller via put_Bounds.
 *   * WM_DESTROY — PostQuitMessage(0); also clears the attached
 *                  controller so subsequent posts don't dangle.
 *   * everything else — DefWindowProcW.
 *
 * We store the attached controller in the window's GWLP_USERDATA slot.
 */

#define PICOLET_WV2_WINDOW_CLASS L"PicoletWebView2Window"

static int g_class_registered = 0;

static LRESULT CALLBACK picolet_wv2_wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_SIZE: {
        ICoreWebView2Controller *ctrl =
            (ICoreWebView2Controller *)(uintptr_t)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
        if (ctrl != NULL) {
            RECT r;
            r.left = 0;
            r.top = 0;
            r.right = LOWORD(lp);
            r.bottom = HIWORD(lp);
            ctrl->lpVtbl->put_Bounds(ctrl, r);
        }
        return 0;
    }
    case WM_DESTROY:
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
}

static int register_window_class(void) {
    if (g_class_registered) { return 0; }
    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = picolet_wv2_wndproc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = PICOLET_WV2_WINDOW_CLASS;
    /* IDC_ARROW is defined as MAKEINTRESOURCE(32512), which on MSVC
     * decays to wide via the *W resource macros.  Under MinGW the
     * default IDC_ARROW expands to a CHAR* (32512), so we cast through
     * intptr_t to LPCWSTR to satisfy LoadCursorW's signature. */
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)(uintptr_t)32512);
    if (RegisterClassExW(&wc) == 0) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return -1;
    }
    g_class_registered = 1;
    return 0;
}

void *picolet_wv2_create_window(const char *title_utf8,
                              int32_t width, int32_t height,
                              int32_t resizable) {
    if (register_window_class() != 0) { return NULL; }
    wchar_t *titleW = utf8_to_wide(title_utf8 != NULL ? title_utf8 : "picolet");
    if (titleW == NULL) {
        set_last(E_OUTOFMEMORY);
        return NULL;
    }
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    if (resizable) {
        style |= WS_MAXIMIZEBOX | WS_THICKFRAME;
    }
    int w = (width  > 0) ? width  : 800;
    int h = (height > 0) ? height : 600;

    /* Compute outer window size from desired client area. */
    RECT rc = { 0, 0, w, h };
    AdjustWindowRectEx(&rc, style, FALSE, 0);
    int win_w = rc.right - rc.left;
    int win_h = rc.bottom - rc.top;

    HWND hwnd = CreateWindowExW(
        0, PICOLET_WV2_WINDOW_CLASS, titleW, style,
        CW_USEDEFAULT, CW_USEDEFAULT, win_w, win_h,
        NULL, NULL, GetModuleHandleW(NULL), NULL);
    free(titleW);
    if (hwnd == NULL) {
        set_last(HRESULT_FROM_WIN32(GetLastError()));
        return NULL;
    }
    set_last(S_OK);
    return (void *)hwnd;
}

int32_t picolet_wv2_show_window(void *hwnd, int32_t visible) {
    if (hwnd == NULL) { return -1; }
    ShowWindow((HWND)hwnd, visible ? SW_SHOW : SW_HIDE);
    if (visible) {
        UpdateWindow((HWND)hwnd);
    }
    return 0;
}

int32_t picolet_wv2_window_attach_controller(void *hwnd, void *controller) {
    if (hwnd == NULL || controller == NULL) {
        set_last(E_INVALIDARG);
        return (int32_t)E_INVALIDARG;
    }
    SetWindowLongPtrW((HWND)hwnd, GWLP_USERDATA, (LONG_PTR)(uintptr_t)controller);
    /* Apply the current client-size to the controller immediately so it
     * appears at the right bounds before WM_SIZE fires. */
    RECT cli;
    if (GetClientRect((HWND)hwnd, &cli)) {
        ICoreWebView2Controller *c = (ICoreWebView2Controller *)controller;
        c->lpVtbl->put_Bounds(c, cli);
    }
    return 0;
}

int32_t picolet_wv2_destroy_window(void *hwnd) {
    if (hwnd == NULL) { return 0; }
    DestroyWindow((HWND)hwnd);
    return 0;
}

#endif /* _WIN32 */
