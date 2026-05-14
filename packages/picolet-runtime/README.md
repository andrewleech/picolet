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
