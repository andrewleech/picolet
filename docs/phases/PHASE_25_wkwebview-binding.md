# PHASE 25 — WKWebView libffi binding

## Goal

Implement the macOS-specific C glue and Python FFI layer for WKWebView.
This is the architectural heart of macOS webview support. The result is a
`picolet_webview_mac.c` C file and a `_mac_ffi.py` Python module that
together provide the same surface as the Linux `_gtk_ffi.py` /
WebKitGTK binding and the Windows `picolet_webview2.c` / `_win_ffi.py`
binding.

## Prerequisites

- PH24 complete and green (macOS cli runtime builds on CI).
- Developer must have access to macOS hardware or iterate via CI.

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-WV-MAC-1 | macOS webview uses WKWebView; platform-conditional in Python |
| FR-WV-MAC-2 | `picolet_webview_mac.c` implements C surface |
| FR-WV-MAC-3 | All ObjC calls via `objc_msgSend` through `libobjc.dylib` |
| FR-WV-MAC-4 | Window opens, HTML loads, IPC bridge round-trips |
| FR-WV-MAC-5 | picolet:// URL scheme via `WKURLSchemeHandler` |
| FR-WV-MAC-6 | `window.picolet.invoke` and `window.picolet.on` work on macOS |
| FR-WV-MAC-7 | `PICOLET_TEST_MODE=1` enables inspector, announces port |
| FR-WV-MAC-8 | Screenshot via `takeSnapshotWithConfiguration:completionHandler:` |
| FR-TEST-MAC-1 | AppHarness autodetects macOS webview variant |
| FR-TEST-MAC-2 | WKRP WebSocket connection from AppHarness webkit path |
| FR-TEST-MAC-3 | macOS announces port in same format as Linux |
| FR-TEST-MAC-4 | macOS screenshot through AppHarness |
| NFR-MAC-7 | Only WebKit.framework; no third-party framework needed |
| NFR-MAC-8 | No GPL/AGPL static link |

## Dependencies

- PH24 (macOS cli runtime builds).
- Existing `_gtk_ffi.py` as the pattern for the Python FFI layer.
- Existing `picolet_webview2.c` as the pattern for the C glue structure.
- Existing `_app.py` platform dispatch (`sys.platform == "win32"` / else).

## Key research findings

### ObjC runtime via `objc_msgSend`

All ObjC calls in Cocoa go through `objc_msgSend(receiver, selector,
...args)`. The function is in `libobjc.A.dylib`. Python accesses it via:
```python
libobjc = ffi.open("/usr/lib/libobjc.A.dylib")
objc_msgSend = libobjc.func("p", "objc_msgSend", "pp...")
```

Selectors are registered via `sel_registerName(name)` (also in libobjc):
```python
sel_registerName = libobjc.func("p", "sel_registerName", "s")
```

Classes are looked up via `objc_getClass(name)` (in libobjc):
```python
objc_getClass = libobjc.func("p", "objc_getClass", "s")
```

The critical API surface needed:

| ObjC expression | C equivalent via objc_msgSend |
|---|---|
| `[NSApplication sharedApplication]` | `objc_msgSend(NSApplication, @"sharedApplication")` |
| `[NSWindow alloc]` | `objc_msgSend(NSWindow, @"alloc")` |
| `[NSWindow initWithContentRect:... styleMask:... backing:... defer:]` | `objc_msgSend(win, @"initWithContentRect:styleMask:backing:defer:", rect, style, 2, 0)` |
| `[WKWebViewConfiguration alloc] init` | double-dispatch |
| `[WKWebView initWithFrame:configuration:]` | rect + config |
| `[view evaluateJavaScript:completionHandler:]` | string + block/NULL |
| `[WKWebView takeSnapshotWithConfiguration:completionHandler:]` | macOS 10.13+ |

**Important**: `objc_msgSend` uses variadic arguments and has different
calling conventions depending on the return type:
- `objc_msgSend` — returns `id` (pointer).
- `objc_msgSend_fpret` — returns `float`/`double` (x86 only).
- `objc_msgSend_stret` — returns struct (deprecated on arm64, removed
  in arm64e; on arm64 all structs ≤ 16 bytes are returned in registers).

For NSRect (a struct), on x86_64 use `objc_msgSend_stret`; on arm64
use `objc_msgSend`. This means the C glue must be arch-conditional or
the Python layer must handle the dispatch difference.

The cleanest approach (as used by pyobjc and other ObjC bridges) is to
have the C glue (`picolet_webview_mac.c`) expose a thin flat C API (like
`picolet_webview2.c`) that wraps the ObjC calls — Python never calls
`objc_msgSend` directly. This avoids the variadic/stret complexity in
Python. The Python layer only calls the flat C API.

### NSRunLoop vs asyncio event loop

WKWebView requires the Cocoa run loop (`NSRunLoop`) to be running for
its message delivery, page loads, and JS evaluation callbacks to fire.
The WebKitGTK equivalent is `gtk_main_iteration_do` (already called in
the existing Python pump loop).

On macOS the pump is:
```objc
[[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode
                         beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
```

This is the macOS equivalent of `gtk_main_iteration_do(0)`. It must be
called periodically from the asyncio pump task (same pattern as Linux).

Via the flat C API:
```c
int32_t picolet_wkwv_pump_messages(void) {
    /* Drain the run loop for up to 10ms. */
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, false);
    return 0;
}
```

### WKURLSchemeHandler for picolet:// URI scheme

WKWebView does not use WebKitGTK's `webkit_web_context_register_uri_scheme`.
Instead, the WKWebView API uses `WKURLSchemeHandler` (macOS 10.13+):

```objc
// Register before creating WKWebView
[config setURLSchemeHandler:handler forURLScheme:@"picolet"];
```

The handler must implement `webView:startURLSchemeTask:` and
`webView:stopURLSchemeTask:` selectors. This is an ObjC protocol; the
implementation requires either:
- A real ObjC class definition (compiled as `.m`) — incompatible with
  the pure-C no-ObjC-runtime approach.
- A lightweight class created at runtime using `objc_allocateClassPair`
  + `class_addMethod` — compatible with the C-only approach.

The recommended approach for this codebase is the second option. The
`picolet_webview_mac.c` file creates a custom ObjC class at runtime:
```c
Class cls = objc_allocateClassPair(
    objc_getClass("NSObject"),
    "PicoletSchemeHandler",
    0
);
class_addMethod(cls, sel_registerName("webView:startURLSchemeTask:"),
                (IMP)picolet_scheme_start, "v@:@@");
class_addMethod(cls, sel_registerName("webView:stopURLSchemeTask:"),
                (IMP)picolet_scheme_stop, "v@:@@");
objc_registerClassPair(cls);
```

This requires `<objc/runtime.h>` which is part of the macOS SDK (already
available on the GitHub Actions runners through Xcode command-line tools).

### WKWebView JS→Python bridge

WKWebView uses `WKScriptMessageHandler` for JS→native messaging:
```js
window.webkit.messageHandlers.picolet.postMessage(json)
```

On the C side, register a message handler class (same `objc_allocateClassPair`
pattern as above) implementing `userContentController:didReceiveScriptMessage:`.
The message body is a WKScriptMessage whose `body` property is an
NSString (or NSDictionary) — extract via the ObjC API and push into the
same ring buffer pattern as `picolet_webview2.c`.

### `PICOLET_TEST_MODE=1` on macOS

WKWebView's remote inspector is enabled via:
```objc
[webView configuration].preferences._developerExtrasEnabled = YES;
```
(private API — available on macOS 10.14+ but uses underscored selector).

Alternatively, the public API since macOS 13.3:
```objc
[[WKPreferences preferences] setInspectorLevel:WKInspectorLevelFull];
```
For v1.2, use the private `_developerExtrasEnabled` approach which is
documented and stable, with a note in the code that the public API is
preferred on macOS 13.3+.

Safari's Web Inspector connects via WebKit's WKRP (Web Inspector Remote
Protocol) over `com.apple.webinspector` Mach port or over a local TCP
socket announced by the `--inspector-web-socket-listen-address` internal
flag. The AppHarness webkit path already speaks the WKRP wire format
(it is identical to the WebKitGTK inspector JSON-RPC over WebSocket).

For the TCP path, set:
```objc
[[NSUserDefaults standardUserDefaults]
    setBool:YES
    forKey:@"WebInspectorServerEnabled"];
```
and the port via:
```objc
[[NSUserDefaults standardUserDefaults]
    setInteger:port
    forKey:@"WebInspectorPort"];
```

These defaults must be set before the WKWebView is created. This is the
macOS equivalent of `WEBKIT_INSPECTOR_SERVER=127.0.0.1:<port>` on Linux.

After setting, announce `picolet:test-port=<N>` to stderr as usual.

### Screenshot API

`-[WKWebView takeSnapshotWithConfiguration:completionHandler:]` is
available since macOS 10.13. It returns a PNG-compatible `NSImage` via
the completion handler. The flat C API wraps this as:
```c
int32_t picolet_wkwv_snapshot(uint8_t **out_bytes, size_t *out_len);
```
The implementation runs the completion handler synchronously using
`CFRunLoopRunInMode` + a semaphore (same blocking pattern as
`picolet_wv2_create_environment_blocking` in `picolet_webview2.c`).

## Files to create

### `overlay/ports/unix/variants/picolet-webview/picolet_webview_mac.c`

New file, compiled only when `__APPLE__` is defined (guard at top).
When `!__APPLE__`, the file defines only the stub symbols (matching
the `picolet_webview2.c` unix stub pattern) so the build always succeeds.

Structure:
```c
#ifndef __APPLE__
/* Stub symbols for non-Darwin builds */
...
#else

#include <objc/objc.h>
#include <objc/runtime.h>
#include <objc/message.h>
#include <CoreFoundation/CoreFoundation.h>

/* Flat C API symbols exposed to Python via libffi:
   picolet_wkwv_init, picolet_wkwv_create_window, picolet_wkwv_create_webview,
   picolet_wkwv_load_url, picolet_wkwv_load_html, picolet_wkwv_execute_script,
   picolet_wkwv_register_scheme_handler, picolet_wkwv_register_message_handler,
   picolet_wkwv_poll_inbound, picolet_wkwv_free_inbound, picolet_wkwv_pump_messages,
   picolet_wkwv_snapshot, picolet_wkwv_pick_test_port,
   picolet_wkwv_enable_inspector, picolet_wkwv_show_window,
   picolet_wkwv_destroy_window
*/
...
#endif
```

Add to `overlay/ports/unix/variants/picolet-webview/mpconfigvariant.mk`:
```make
ifeq ($(UNAME_S),Darwin)
SRC_C += picolet_webview_mac.c
LDFLAGS_EXTRA += -framework WebKit -framework AppKit -framework Foundation
LDFLAGS_EXTRA += -framework CoreFoundation
else
SRC_C += picolet_webview_gtk.c
endif
```
(The existing Linux-only `picolet_webview_gtk.c` is conditionally excluded
on Darwin, included on Linux.)

### `packages/picolet-runtime/python/picolet_ui/_mac_ffi.py`

Python-side libffi bindings for the macOS flat C API. Pattern: identical
to `_win_ffi.py` but calls into the macOS binary's own exports via
`ffi.open(None)` (the running Mach-O binary, same as Windows uses
`ffi.open(None)` → `GetModuleHandle(NULL)`).

Key difference from Linux: on macOS `ffi.open(None)` returns the default
handle, which on Darwin resolves via `dlopen(NULL, ...)` → the main
executable. This is the same mechanism used on Linux where
`ffi.open(None)` resolves symbols from the running process.

```python
import ffi
self_bin = ffi.open(None)
picolet_wkwv_init = self_bin.func("i", "picolet_wkwv_init", "")
picolet_wkwv_create_window = self_bin.func("p", "picolet_wkwv_create_window", "sii")
# ... etc
```

### `packages/picolet-runtime/python/picolet_ui/_app.py` (modify)

The existing `if sys.platform == "win32": ... else: ...` block becomes a
three-way dispatch:
```python
if sys.platform == "win32":
    from . import _win_ffi as _platform_ffi
elif sys.platform == "darwin":
    from . import _mac_ffi as _platform_ffi
else:
    from . import _gtk_ffi as _platform_ffi
```

The macOS branch initialises the NSApplication and runs the NSRunLoop
pump identically to the GTK pump pattern on Linux.

### `packages/picolet-testing/picolet/testing/_harness.py` (modify)

Update `_autodetect_browser`:
```python
def _autodetect_browser(binary, platform=sys.platform):
    name = Path(binary).name
    if "lvgl" in name:
        return "lvgl"
    if platform == "win32":
        return "chromium"
    return "webkit"  # covers both linux and darwin
```
No change needed — the `webkit` path already works for macOS since
WKRP wire format is identical to WebKitGTK's.

## Integration points

### `_app.py` — PICOLET_DEV_URL

The macOS branch must also handle `PICOLET_DEV_URL` (set by `picolet dev`
with Vue). Pattern is the same as the Linux path: call
`picolet_wkwv_load_url(window, dev_url)` instead of `picolet_wkwv_load_html`.

### `_app.py` — picolet:// scheme

`picolet_wkwv_register_scheme_handler` must be called before the WKWebView
is created (WKWebViewConfiguration is fixed at init time). The scheme
handler's C implementation reads `/rom/<path>` via a Python callback
(libffi closure) and returns the bytes — identical to the GTK scheme
handler pattern in `_gtk_ffi.py:_register_picolet_scheme`.

The libffi closure approach:
```python
def _on_scheme_request(task_ptr, user_data):
    # Extract URL path from WKURLSchemeTask via objc_msgSend
    # Read /rom/<path> from VFS
    # Call [task didReceiveResponse:] + [task didReceiveData:] + [task didFinish]
    ...
cb = ffi.callback("v", _on_scheme_request, "pp", lock=False)
_scheme_callback = cb  # keep alive
picolet_wkwv_register_scheme_handler(window, cb, 0)
```

### `sbom/runtime.toml`

Add macOS-specific dynamic dependencies:
```toml
[[component]]
name = "WebKit.framework"
version = "bundled-with-macos"
licence = "LicenseRef-Apple-System"
source_url = "https://developer.apple.com/documentation/webkit"
link_type = "dynamic"
targets = ["macos-x64", "macos-arm64"]
variants = ["webview"]
notes = "System framework; not redistributed. Reached via libobjc.A.dylib + objc_msgSend."
```

## Implementation guidance

### Minimising ObjC surface area

The C glue should be as thin as possible. The key selectors needed:

For window and app:
- `NSApplication.sharedApplication`, `.run`, `.finishLaunching`
- `NSWindow.alloc`, `.initWithContentRect:styleMask:backing:defer:`,
  `.setTitle:`, `.setContentView:`, `.makeKeyAndOrderFront:`,
  `.close`, `.contentView`
- `NSView.setFrame:`

For WKWebView (via WKWebViewConfiguration):
- `WKWebViewConfiguration.alloc.init`
- `WKWebViewConfiguration.userContentController`
- `WKUserContentController.addScriptMessageHandler:name:`
- `WKUserContentController.addUserScript:`
- `WKWebView.alloc`, `.initWithFrame:configuration:`
- `WKWebView.loadHTMLString:baseURL:`
- `WKWebView.loadRequest:` (for URL navigation)
- `WKWebView.evaluateJavaScript:completionHandler:`
- `WKWebView.takeSnapshotWithConfiguration:completionHandler:`

For scheme handler (class created at runtime):
- `WKURLSchemeTask.request` → NSURLRequest → `.URL` → `.path`
- `WKURLSchemeTask.didReceiveResponse:`
- `WKURLSchemeTask.didReceiveData:`
- `WKURLSchemeTask.didFinish`

### NSRect/CGRect on arm64 vs x86_64

On arm64, `CGRect` is returned in registers. `objc_msgSend` works
directly. On x86_64, `CGRect` is returned via `objc_msgSend_stret`.
The cleanest solution: wrap the frame/rect operations in the C glue
so Python never calls `objc_msgSend_stret` directly:
```c
void picolet_wkwv_set_frame(void *view, double x, double y, double w, double h) {
    CGRect r = {{x, y}, {w, h}};
    ((void (*)(id, SEL, CGRect))objc_msgSend)(view, sel_setFrame, r);
}
```

### Blocking JS evaluation on macOS

`evaluateJavaScript:completionHandler:` is async. The same blocking
pattern as `picolet_wv2_create_environment_blocking` in `picolet_webview2.c`
applies:
```c
dispatch_semaphore_t sem = dispatch_semaphore_create(0);
// Invoke with completion handler that signals sem
// Then: dispatch_semaphore_wait + CFRunLoopRunInMode loop
```

### `EXPORT_ALL_SYMBOLS` equivalent on macOS

The Windows build uses `-Wl,--export-all-symbols` to expose C symbols
from the `.exe` to `ffi.open(None)`. On macOS, the linker flag is:
```
-Wl,-export_dynamic
```
Or better, use a `__attribute__((visibility("default")))` on each
exported symbol and `-fvisibility=hidden` globally (clean and precise).

Add to `mpconfigvariant.mk` for macOS webview:
```make
ifeq ($(UNAME_S),Darwin)
CFLAGS_EXTRA += -fvisibility=hidden
LDFLAGS_EXTRA += -Wl,-export_dynamic
endif
```

## Testing strategy

1. Build `macos-x64-webview` in CI after implementing PH25.
2. Run the hello-webview demo binary on macOS:
   ```bash
   xattr -d com.apple.quarantine picolet-runtime-macos-x64-webview
   ./picolet-runtime-macos-x64-webview
   ```
   Expected: window opens, HTML loads, no crash.
3. IPC round-trip test (from the existing hello-webview test suite):
   ```bash
   PICOLET_TEST_MODE=1 ./picolet-runtime-macos-x64-webview &
   # Parse picolet:test-port=N from stderr
   # Connect AppHarness webkit path to port N
   # Evaluate window.picolet.__ready__ === true
   ```
4. Screenshot test:
   ```python
   async with AppHarness("./picolet-runtime-macos-x64-webview") as h:
       png = await h.snapshot()
       assert len(png) > 1024
   ```
5. Verify `picolet-runtime-macos-x64-webview` binary size ≤ 2 MiB.

## Success criteria

- [ ] `picolet_webview_mac.c` compiles without warnings on both
      `macos-13` (x64) and `macos-14` (arm64).
- [ ] `_mac_ffi.py` imports without error when `ffi.open(None)` resolves
      all `picolet_wkwv_*` symbols.
- [ ] A window opens and the romfs HTML loads.
- [ ] `window.picolet.invoke('greet', {name: 'World'})` returns a result.
- [ ] `PICOLET_TEST_MODE=1` announces `picolet:test-port=<N>` on stderr.
- [ ] AppHarness webkit path connects to the WK inspector on macOS.
- [ ] Screenshot returns valid PNG bytes > 1 KB.
- [ ] Binary size ≤ 2 MiB.
- [ ] SBOM entry for `WebKit.framework` is present.

## Open questions requiring judgement

1. **NSApplication.run vs custom run loop**: `[NSApplication run]` enters
   the main event loop and blocks. The asyncio pump requires periodic
   control. Two patterns exist:
   a. Use `CFRunLoopRunInMode` from the asyncio pump task (non-blocking,
      requires NOT calling `[NSApplication run]`).
   b. Use `[NSApplication run]` on a separate thread and marshal IPC
      across threads.
   Option (a) is simpler and consistent with the GTK approach.
   Recommendation: use option (a). Flag for developer judgement if
   windowing behaviour is incorrect.

2. **WKWebView remote inspector port on macOS 12 vs 13**: The
   `NSUserDefaults` `WebInspectorPort` key may not work on all macOS
   versions. If it doesn't, the fallback is a Bonjour-based announce
   (WKRP over Bonjour). The tester should verify against the actual
   macOS version on the runner.

## Risks

1. **WKRP protocol differences from WebKitGTK inspector**: The existing
   `_webkit.py` was written against WebKitGTK's inspector JSON-RPC.
   WKWebView on macOS uses the same WKRP format (same codebase) but the
   endpoint discovery URL (`/json`) may differ. The `_discover_ws_url`
   fallback path in `_webkit.py` handles this gracefully.

2. **`objc_msgSend_stret` on x86_64**: The struct-return calling convention
   on Intel macOS is different from arm64. Incorrect use causes silent
   memory corruption. Mitigation: wrap all struct-returning calls in the
   C glue, never call `objc_msgSend_stret` from Python.

3. **NSApplication threading model**: Cocoa requires the main thread to
   drive the event loop. The asyncio event loop runs on the main thread,
   so this should be fine, but care is needed not to block the main thread
   in a tight spin.

## Model tier recommendation

planner `opus`, developer `opus` (ObjC runtime + libffi + macOS
window system is complex C/FFI work), sqe `sonnet`, tester `opus`.
