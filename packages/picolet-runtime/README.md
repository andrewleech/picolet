# picolet-runtime

The MicroPython runtime that ships embedded in every Picolet application.
This package owns the C build, the renderer modules, and the frozen
Python manifests.

## Layout

```
picolet-runtime/
├── micropython/              # submodule (added during repo init)
├── mbm.toml                  # upstream-bound feature branches
├── overlay/                  # downstream-only files, re-applied each rebase
│   ├── ports/
│   │   ├── windows/variants/{picolet-webview,picolet-lvgl,picolet-cli}/
│   │   ├── unix/variants/{picolet-webview,picolet-lvgl,picolet-cli}/
│   │   └── macos-picolet/      # added when macOS lands
│   └── modules/
│       ├── picolet_webview/    # FFI: WebView2 / WKWebView / WebKitGTK
│       ├── picolet_ipc/        # JSON message router
│       └── picolet_window/     # native window mgmt
├── manifests/
│   ├── manifest_core.py
│   ├── manifest_webview.py
│   ├── manifest_lvgl.py
│   └── manifest_cli.py
└── scripts/
    ├── rebuild-integration.sh    # rebase submodule + re-apply overlay
    └── build-runtime.sh          # cross-compile via dockcross
```

## Integration branch

The `micropython/` submodule tracks an integration branch composed by
[`mbm`](https://gitlab.com/alelec/micropython-branch-manager) from the
PRs listed in `mbm.toml`. After each rebase,
`scripts/rebuild-integration.sh` re-applies the downstream-only `overlay/`
tree on top.

The seven feature PRs that feed pydfu-win are the starting set:

| PR | What |
|---|---|
| #38 | `lib/micropython-lib`: pyusb Windows support |
| #39 | `py/mkrules.mk`: honour `PROG=` on mingw |
| #40 | `py/mkrules.mk`: `MICROPY_MODULE_FROZEN_STR` conditional on compiler |
| #41 | `py/modgc`: `gc.add_heap()` for runtime heap expansion |
| #42 | `ports/windows`: FFI module support with libffi |
| #43 | `ports/unix,windows`: embedded romfs with auto-mount |
| #44 | `ports/windows`: variant overrides |

Renderer-specific PRs (LVGL bindings, webview bridge plumbing) get added
to `mbm.toml` as they're written.

## Webview variant (linux-x64, PH07)

The `picolet-runtime-linux-x64-webview` variant adds a WebKitGTK 4.1
webview renderer. It is dynamically linked against WebKit at runtime
via libffi — the runtime binary itself has no build-time dependency on
the webkit2gtk-4.1-dev headers and no static link of any LGPL component
(NFR-5).

### Runtime user requirements

Install the WebKit binary package on the target host:

```
sudo apt install libwebkit2gtk-4.1-0
```

This package transitively pulls in `libgtk-3-0`, `libgobject-2.0-0`,
and `libjavascriptcoregtk-4.1-0` — the four shared libraries the
runtime `dlopen`s. No other packages are required (NFR-8).

### Running headless (CI, server, no DISPLAY)

```
sudo apt install xvfb
xvfb-run -a -s "-screen 0 800x600x24" ./your-picolet-webview-app
```

`xvfb` is a test-only dependency; production deployment does not need
it. `xvfb-run -a` auto-picks a free display number.

### Building the runtime

```
./packages/picolet-runtime/scripts/build-runtime.sh \
    --target linux-x64 --variant webview
```

Produces `packages/picolet-runtime/build/picolet-runtime-linux-x64-webview`,
size budget NFR-2 (≤ 2 MiB). The build container installs
`libwebkit2gtk-4.1-0` and `xvfb` at image build time so the test
harness can exercise the rendering path; the runtime binary itself is
the same on every host that has libwebkit2gtk-4.1-0.

### Building an app for the webview variant

The user app's `picolet.toml` declares the renderer:

```
[ui]
renderer = "webview"
root = "ui"

[window]
title = "Hello"
size = [800, 600]

[romfs]
include = ["ui"]
```

`picolet build --target linux-x64` then resolves the webview runtime
automatically and emits a self-contained binary with the romfs (UI
assets + a sanitised `picolet.toml` for the runtime to read [window] and
[ui] from at startup).

## Webview variant (Windows)

PH10.  Mirrors the linux-x64 webview variant; the renderer is
Microsoft Edge WebView2 instead of WebKitGTK 4.1.

```
./packages/picolet-runtime/scripts/build-runtime.sh \
    --target windows-x64 --variant webview
```

Produces `packages/picolet-runtime/build/picolet-runtime-windows-x64-webview.exe`,
size budget NFR-2 (≤ 2 MiB; ~660 KB at PH10).

### Host requirements (NFR-9)

The Edge WebView2 Runtime must be installed on the host running the
.exe.  It ships by default on Windows 11; Windows 10 21H2+ installs
it via Microsoft Update.  Hosts without the runtime get a clear
error at first window creation:

```
Edge WebView2 Runtime not installed; install from
https://developer.microsoft.com/microsoft-edge/webview2/
```

### WebView2Loader.dll bundling

The runtime calls into `WebView2Loader.dll` via `LoadLibraryW` at
startup; the DLL is not in System32 so it must be bundled by the
app.  `picolet build --target windows-x64` (with `[ui] renderer="webview"`)
copies `WebView2Loader.x64.dll` from the runtime package's
`overlay/.../redist/` directory into the app romfs at
`/rom/picolet/WebView2Loader.dll`.  At runtime, the C overlay extracts
the bytes to `%LOCALAPPDATA%\picolet\<pid>\WebView2Loader.dll` and
`LoadLibraryW`s from there (parallel to the bridge JS distribution).

If the DLL is missing at app build time, `picolet build` errors with
the SDK NuGet fetch recipe.  Override with
`PICOLET_WEBVIEW2_LOADER_DLL=<path>` for CI.

### License footprint

`WebView2Loader.dll` is redistributable under the Microsoft WebView2
SDK License Terms.  The Edge WebView2 Runtime engine itself is
system-installed and never redistributed by picolet.  NFR-5's no-static-
GPL/LGPL constraint is satisfied: the loader is dynamically loaded
and the Microsoft license is permissive.  PH13's SBOM will record
both as runtime dependencies.
