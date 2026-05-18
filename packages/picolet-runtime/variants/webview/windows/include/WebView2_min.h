/*
 * WebView2_min.h — minimal MinGW-friendly subset of the WebView2 SDK
 *
 * Hand-written declarations covering only the COM interfaces and
 * completion-handler shapes that picolet_webview2.c uses.  Full WebView2.h
 * from Microsoft's NuGet is ~30 K lines and uses MSVC-only annotations
 * that don't compile cleanly under dockcross/MinGW-w64-x86_64.
 *
 * The subset captured here is the canonical x64-windows COM ABI: each
 * interface starts with the three IUnknown methods (QueryInterface,
 * AddRef, Release) at vtable indices 0/1/2, followed by leaf methods
 * in declared order.  Vtable slots not used by picolet_webview2.c are
 * declared as `void *` to preserve interface layout (so the
 * function-pointer call we make through Navigate / NavigateToString /
 * ExecuteScript / AddScriptToExecuteOnDocumentCreated /
 * add_WebMessageReceived lands at the correct offset in Microsoft's
 * implementation).
 *
 * Method order is taken from the original ICoreWebView2 interface
 * declaration in WebView2 SDK 1.0.488 (the minimum NFR-9 runtime).
 * Microsoft does not add methods to ICoreWebView2 in later SDK
 * versions — additions go to ICoreWebView2_2, _3, etc., distinct
 * COM interfaces queried via QueryInterface.  Our vtable layout
 * therefore stays stable.
 *
 * Reference:
 *   Microsoft.Web.WebView2 NuGet package, build/native/include/WebView2.h
 *   (license: Microsoft WebView2 SDK License Terms; the COM ABI is not
 *   copyrightable, IIDs are factual ABI identifiers).
 *
 * License of this header: MIT (picolet code).
 */

#ifndef PICOLET_WEBVIEW2_MIN_H
#define PICOLET_WEBVIEW2_MIN_H

#include <windows.h>
#include <objbase.h>

#ifdef __cplusplus
extern "C" {
#endif

/* EventRegistrationToken — defined in EventToken.h on MSVC; MinGW
 * doesn't always ship it.  The shape is a single int64 cookie. */
#ifndef __EventToken_h__
#define __EventToken_h__
typedef struct EventRegistrationToken_struct {
    INT64 value;
} EventRegistrationToken;
#endif

/* Forward interface declarations.  Each interface struct's first member
 * is the vtable pointer (COM convention). */

typedef struct ICoreWebView2EnvironmentVtbl     ICoreWebView2EnvironmentVtbl;
typedef struct ICoreWebView2ControllerVtbl      ICoreWebView2ControllerVtbl;
typedef struct ICoreWebView2Vtbl                ICoreWebView2Vtbl;
typedef struct ICoreWebView2SettingsVtbl        ICoreWebView2SettingsVtbl;
typedef struct ICoreWebView2WebMessageReceivedEventArgsVtbl
    ICoreWebView2WebMessageReceivedEventArgsVtbl;

typedef struct ICoreWebView2Environment {
    ICoreWebView2EnvironmentVtbl *lpVtbl;
} ICoreWebView2Environment;
typedef struct ICoreWebView2Controller {
    ICoreWebView2ControllerVtbl *lpVtbl;
} ICoreWebView2Controller;
typedef struct ICoreWebView2 {
    ICoreWebView2Vtbl *lpVtbl;
} ICoreWebView2;
typedef struct ICoreWebView2Settings {
    ICoreWebView2SettingsVtbl *lpVtbl;
} ICoreWebView2Settings;
typedef struct ICoreWebView2WebMessageReceivedEventArgs {
    ICoreWebView2WebMessageReceivedEventArgsVtbl *lpVtbl;
} ICoreWebView2WebMessageReceivedEventArgs;

/* Completion-handler "interfaces".  Each Microsoft *CompletedHandler*
 * interface is QI/AddRef/Release/Invoke — we provide a generic
 * vtable struct per Invoke signature.  Multiple SDK interfaces share
 * the same shape so one of our handler vtable types covers them all. */

typedef struct PicoletWv2EnvCreatedHandler PicoletWv2EnvCreatedHandler;
typedef struct PicoletWv2EnvCreatedHandlerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2EnvCreatedHandler *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2EnvCreatedHandler *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2EnvCreatedHandler *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(PicoletWv2EnvCreatedHandler *,
                                        HRESULT errorCode,
                                        ICoreWebView2Environment *createdEnvironment);
} PicoletWv2EnvCreatedHandlerVtbl;
struct PicoletWv2EnvCreatedHandler {
    PicoletWv2EnvCreatedHandlerVtbl *lpVtbl;
};

typedef struct PicoletWv2CtrlCreatedHandler PicoletWv2CtrlCreatedHandler;
typedef struct PicoletWv2CtrlCreatedHandlerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2CtrlCreatedHandler *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2CtrlCreatedHandler *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2CtrlCreatedHandler *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(PicoletWv2CtrlCreatedHandler *,
                                        HRESULT errorCode,
                                        ICoreWebView2Controller *createdController);
} PicoletWv2CtrlCreatedHandlerVtbl;
struct PicoletWv2CtrlCreatedHandler {
    PicoletWv2CtrlCreatedHandlerVtbl *lpVtbl;
};

typedef struct PicoletWv2ExecuteScriptHandler PicoletWv2ExecuteScriptHandler;
typedef struct PicoletWv2ExecuteScriptHandlerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2ExecuteScriptHandler *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2ExecuteScriptHandler *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2ExecuteScriptHandler *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(PicoletWv2ExecuteScriptHandler *,
                                        HRESULT errorCode,
                                        LPCWSTR resultObjectAsJson);
} PicoletWv2ExecuteScriptHandlerVtbl;
struct PicoletWv2ExecuteScriptHandler {
    PicoletWv2ExecuteScriptHandlerVtbl *lpVtbl;
};

typedef struct PicoletWv2AddScriptHandler PicoletWv2AddScriptHandler;
typedef struct PicoletWv2AddScriptHandlerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2AddScriptHandler *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2AddScriptHandler *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2AddScriptHandler *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(PicoletWv2AddScriptHandler *,
                                        HRESULT errorCode,
                                        LPCWSTR id);
} PicoletWv2AddScriptHandlerVtbl;
struct PicoletWv2AddScriptHandler {
    PicoletWv2AddScriptHandlerVtbl *lpVtbl;
};

typedef struct PicoletWv2WebMessageHandler PicoletWv2WebMessageHandler;
typedef struct PicoletWv2WebMessageHandlerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2WebMessageHandler *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2WebMessageHandler *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2WebMessageHandler *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(PicoletWv2WebMessageHandler *,
                                        ICoreWebView2 *sender,
                                        ICoreWebView2WebMessageReceivedEventArgs *args);
} PicoletWv2WebMessageHandlerVtbl;
struct PicoletWv2WebMessageHandler {
    PicoletWv2WebMessageHandlerVtbl *lpVtbl;
};

/* ----- ICoreWebView2Environment ---------------------------------------
 *
 * Original interface (SDK 1.0.488).  Method order:
 *   0: QueryInterface
 *   1: AddRef
 *   2: Release
 *   3: CreateCoreWebView2Controller
 *   4: CreateWebResourceResponse
 *   5: get_BrowserVersionString
 *   6: add_NewBrowserVersionAvailable
 *   7: remove_NewBrowserVersionAvailable
 */
struct ICoreWebView2EnvironmentVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ICoreWebView2Environment *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ICoreWebView2Environment *);
    ULONG   (STDMETHODCALLTYPE *Release)(ICoreWebView2Environment *);
    HRESULT (STDMETHODCALLTYPE *CreateCoreWebView2Controller)(
        ICoreWebView2Environment *, HWND parentWindow,
        PicoletWv2CtrlCreatedHandler *handler);
    void *CreateWebResourceResponse;
    void *get_BrowserVersionString;
    void *add_NewBrowserVersionAvailable;
    void *remove_NewBrowserVersionAvailable;
};

/* ----- ICoreWebView2Controller ----------------------------------------
 *
 * Original interface.  We use put_IsVisible, put_Bounds, get_CoreWebView2,
 * Close.  Method order:
 *   3: get_IsVisible
 *   4: put_IsVisible
 *   5: get_Bounds
 *   6: put_Bounds
 *   7: get_ZoomFactor
 *   8: put_ZoomFactor
 *   9: add_ZoomFactorChanged
 *  10: remove_ZoomFactorChanged
 *  11: SetBoundsAndZoomFactor
 *  12: MoveFocus
 *  13: add_MoveFocusRequested
 *  14: remove_MoveFocusRequested
 *  15: add_GotFocus
 *  16: remove_GotFocus
 *  17: add_LostFocus
 *  18: remove_LostFocus
 *  19: add_AcceleratorKeyPressed
 *  20: remove_AcceleratorKeyPressed
 *  21: get_ParentWindow
 *  22: put_ParentWindow
 *  23: NotifyParentWindowPositionChanged
 *  24: Close
 *  25: get_CoreWebView2
 */
struct ICoreWebView2ControllerVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ICoreWebView2Controller *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ICoreWebView2Controller *);
    ULONG   (STDMETHODCALLTYPE *Release)(ICoreWebView2Controller *);
    HRESULT (STDMETHODCALLTYPE *get_IsVisible)(ICoreWebView2Controller *, BOOL *isVisible);
    HRESULT (STDMETHODCALLTYPE *put_IsVisible)(ICoreWebView2Controller *, BOOL isVisible);
    HRESULT (STDMETHODCALLTYPE *get_Bounds)(ICoreWebView2Controller *, RECT *bounds);
    HRESULT (STDMETHODCALLTYPE *put_Bounds)(ICoreWebView2Controller *, RECT bounds);
    void *get_ZoomFactor;
    void *put_ZoomFactor;
    void *add_ZoomFactorChanged;
    void *remove_ZoomFactorChanged;
    void *SetBoundsAndZoomFactor;
    void *MoveFocus;
    void *add_MoveFocusRequested;
    void *remove_MoveFocusRequested;
    void *add_GotFocus;
    void *remove_GotFocus;
    void *add_LostFocus;
    void *remove_LostFocus;
    void *add_AcceleratorKeyPressed;
    void *remove_AcceleratorKeyPressed;
    void *get_ParentWindow;
    void *put_ParentWindow;
    void *NotifyParentWindowPositionChanged;
    HRESULT (STDMETHODCALLTYPE *Close)(ICoreWebView2Controller *);
    HRESULT (STDMETHODCALLTYPE *get_CoreWebView2)(ICoreWebView2Controller *, ICoreWebView2 **coreWebView2);
};

/* ----- ICoreWebView2 ---------------------------------------------------
 *
 * Original interface (SDK 1.0.488).  Microsoft does not extend this
 * interface in later SDKs (newer methods land on ICoreWebView2_2, _3,
 * etc.; queried separately).  Slot offsets stable:
 *   0: QueryInterface
 *   1: AddRef
 *   2: Release
 *   3: get_Settings
 *   4: get_Source
 *   5: Navigate
 *   6: NavigateToString
 *   7: add_NavigationStarting
 *   8: remove_NavigationStarting
 *   9: add_ContentLoading
 *  10: remove_ContentLoading
 *  11: add_SourceChanged
 *  12: remove_SourceChanged
 *  13: add_HistoryChanged
 *  14: remove_HistoryChanged
 *  15: add_NavigationCompleted
 *  16: remove_NavigationCompleted
 *  17: add_FrameNavigationStarting
 *  18: remove_FrameNavigationStarting
 *  19: add_FrameNavigationCompleted
 *  20: remove_FrameNavigationCompleted
 *  21: add_ScriptDialogOpening
 *  22: remove_ScriptDialogOpening
 *  23: add_PermissionRequested
 *  24: remove_PermissionRequested
 *  25: add_ProcessFailed
 *  26: remove_ProcessFailed
 *  27: AddScriptToExecuteOnDocumentCreated
 *  28: RemoveScriptToExecuteOnDocumentCreated
 *  29: ExecuteScript
 *  30: CapturePreview
 *  31: Reload
 *  32: PostWebMessageAsJson
 *  33: PostWebMessageAsString
 *  34: add_WebMessageReceived
 *  35: remove_WebMessageReceived
 *  (further methods unused by picolet)
 */
struct ICoreWebView2Vtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ICoreWebView2 *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ICoreWebView2 *);
    ULONG   (STDMETHODCALLTYPE *Release)(ICoreWebView2 *);
    HRESULT (STDMETHODCALLTYPE *get_Settings)(ICoreWebView2 *, ICoreWebView2Settings **Settings);
    void *get_Source;
    HRESULT (STDMETHODCALLTYPE *Navigate)(ICoreWebView2 *, LPCWSTR uri);
    HRESULT (STDMETHODCALLTYPE *NavigateToString)(ICoreWebView2 *, LPCWSTR htmlContent);
    void *add_NavigationStarting;
    void *remove_NavigationStarting;
    void *add_ContentLoading;
    void *remove_ContentLoading;
    void *add_SourceChanged;
    void *remove_SourceChanged;
    void *add_HistoryChanged;
    void *remove_HistoryChanged;
    void *add_NavigationCompleted;
    void *remove_NavigationCompleted;
    void *add_FrameNavigationStarting;
    void *remove_FrameNavigationStarting;
    void *add_FrameNavigationCompleted;
    void *remove_FrameNavigationCompleted;
    void *add_ScriptDialogOpening;
    void *remove_ScriptDialogOpening;
    void *add_PermissionRequested;
    void *remove_PermissionRequested;
    void *add_ProcessFailed;
    void *remove_ProcessFailed;
    HRESULT (STDMETHODCALLTYPE *AddScriptToExecuteOnDocumentCreated)(
        ICoreWebView2 *, LPCWSTR javaScript, PicoletWv2AddScriptHandler *handler);
    void *RemoveScriptToExecuteOnDocumentCreated;
    HRESULT (STDMETHODCALLTYPE *ExecuteScript)(
        ICoreWebView2 *, LPCWSTR javaScript, PicoletWv2ExecuteScriptHandler *handler);
    void *CapturePreview;
    void *Reload;
    void *PostWebMessageAsJson;
    void *PostWebMessageAsString;
    HRESULT (STDMETHODCALLTYPE *add_WebMessageReceived)(
        ICoreWebView2 *, PicoletWv2WebMessageHandler *handler, EventRegistrationToken *token);
    void *remove_WebMessageReceived;
};

/* ICoreWebView2Settings (just enough to put_IsScriptEnabled / put_IsWebMessageEnabled). */
struct ICoreWebView2SettingsVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ICoreWebView2Settings *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ICoreWebView2Settings *);
    ULONG   (STDMETHODCALLTYPE *Release)(ICoreWebView2Settings *);
    void *get_IsScriptEnabled;
    HRESULT (STDMETHODCALLTYPE *put_IsScriptEnabled)(ICoreWebView2Settings *, BOOL isScriptEnabled);
    void *get_IsWebMessageEnabled;
    HRESULT (STDMETHODCALLTYPE *put_IsWebMessageEnabled)(ICoreWebView2Settings *, BOOL isWebMessageEnabled);
};

/* ICoreWebView2WebMessageReceivedEventArgs — TryGetWebMessageAsString. */
struct ICoreWebView2WebMessageReceivedEventArgsVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(ICoreWebView2WebMessageReceivedEventArgs *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(ICoreWebView2WebMessageReceivedEventArgs *);
    ULONG   (STDMETHODCALLTYPE *Release)(ICoreWebView2WebMessageReceivedEventArgs *);
    void *get_Source;
    HRESULT (STDMETHODCALLTYPE *get_WebMessageAsJson)(
        ICoreWebView2WebMessageReceivedEventArgs *, LPWSTR *webMessageAsJson);
    HRESULT (STDMETHODCALLTYPE *TryGetWebMessageAsString)(
        ICoreWebView2WebMessageReceivedEventArgs *, LPWSTR *webMessageAsString);
};

/* ----- ICoreWebView2EnvironmentOptions ---------------------------------
 *
 * PH17 — needed to pass AdditionalBrowserArguments containing
 * --remote-debugging-port=<N> --remote-debugging-address=127.0.0.1 when
 * PICOLET_TEST_MODE=1 (FR-TEST-1, Windows/WebView2 path).
 *
 * The full ICoreWebView2EnvironmentOptions interface (SDK 1.0.488) has
 * these methods in declared order after QI/AddRef/Release:
 *   3: get_AdditionalBrowserArguments
 *   4: put_AdditionalBrowserArguments
 *   5: get_Language
 *   6: put_Language
 *   7: get_TargetCompatibleBrowserVersion
 *   8: put_TargetCompatibleBrowserVersion
 *   9: get_AllowSingleSignOnUsingOSPrimaryAccount
 *  10: put_AllowSingleSignOnUsingOSPrimaryAccount
 *
 * We only implement put_AdditionalBrowserArguments with real logic; the
 * other getters/setters are stub slot-holders so the vtable layout is
 * correct when WebView2 calls any of them.  WebView2 calls the getters
 * synchronously during CreateCoreWebView2EnvironmentWithOptions and
 * does not retain the options pointer (R9). */

typedef struct PicoletWv2EnvOptions PicoletWv2EnvOptions;
typedef struct PicoletWv2EnvOptionsVtbl {
    /* IUnknown */
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(PicoletWv2EnvOptions *, REFIID, void **);
    ULONG   (STDMETHODCALLTYPE *AddRef)(PicoletWv2EnvOptions *);
    ULONG   (STDMETHODCALLTYPE *Release)(PicoletWv2EnvOptions *);
    /* ICoreWebView2EnvironmentOptions */
    HRESULT (STDMETHODCALLTYPE *get_AdditionalBrowserArguments)(PicoletWv2EnvOptions *, LPWSTR *);
    HRESULT (STDMETHODCALLTYPE *put_AdditionalBrowserArguments)(PicoletWv2EnvOptions *, LPCWSTR);
    HRESULT (STDMETHODCALLTYPE *get_Language)(PicoletWv2EnvOptions *, LPWSTR *);
    HRESULT (STDMETHODCALLTYPE *put_Language)(PicoletWv2EnvOptions *, LPCWSTR);
    HRESULT (STDMETHODCALLTYPE *get_TargetCompatibleBrowserVersion)(PicoletWv2EnvOptions *, LPWSTR *);
    HRESULT (STDMETHODCALLTYPE *put_TargetCompatibleBrowserVersion)(PicoletWv2EnvOptions *, LPCWSTR);
    HRESULT (STDMETHODCALLTYPE *get_AllowSingleSignOnUsingOSPrimaryAccount)(PicoletWv2EnvOptions *, BOOL *);
    HRESULT (STDMETHODCALLTYPE *put_AllowSingleSignOnUsingOSPrimaryAccount)(PicoletWv2EnvOptions *, BOOL);
} PicoletWv2EnvOptionsVtbl;
struct PicoletWv2EnvOptions {
    PicoletWv2EnvOptionsVtbl *lpVtbl;
    /* AdditionalBrowserArguments string pointer; set by caller before
     * CreateCoreWebView2EnvironmentWithOptions is invoked. */
    LPCWSTR additional_args;
};

/* Loader DLL entry point — exported by WebView2Loader.dll. */
typedef HRESULT (STDMETHODCALLTYPE *PFN_CreateCoreWebView2EnvironmentWithOptions)(
    LPCWSTR browserExecutableFolder,
    LPCWSTR userDataFolder,
    PicoletWv2EnvOptions *environmentOptions,  /* NULL = defaults */
    PicoletWv2EnvCreatedHandler *environmentCreatedHandler);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_WEBVIEW2_MIN_H */
