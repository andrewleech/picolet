# Picolet

A framework for building self-contained, single-binary applications in
MicroPython. Modeled on [Tauri](https://tauri.app/): a small native binary,
a system or embedded renderer, and a scripting language for the application
logic.

A Picolet binary contains a minimal MicroPython interpreter, the application's
Python code (frozen to bytecode), bundled assets (embedded as a romfs
image), and an optional GUI renderer — all linked into one executable.

## Renderers

Selected by the `[ui]` section of `picolet.toml`:

| Renderer | Backed by | Use case |
|---|---|---|
| `webview` | WebView2 (Win), WKWebView (macOS), WebKitGTK (Linux) | HTML/CSS/JS frontend |
| `lvgl`    | [`lv_binding_micropython`](https://github.com/lvgl/lv_binding_micropython) | No system dependency, true single binary |
| (omitted) | — | CLI tool, no UI |

## Repository layout

```
picolet/
├── packages/
│   ├── picolet-cli/         # `picolet init|dev|build|run`
│   ├── picolet-runtime/     # embedded MicroPython runtime (C+Python overlay)
│   ├── picolet-bridge-js/   # JS shim: window.picolet.invoke(...)
│   └── picolet-templates/   # starting templates for `picolet init`
├── examples/              # worked examples (added as renderers land)
├── docs/                  # architecture and reference
└── scripts/               # repo-wide tooling
```

## Status

Pre-alpha scaffolding. No releases. See
[docs/architecture.md](docs/architecture.md) for the v1 design decisions
and [docs/sbom.md](docs/sbom.md) for the dependency-tracking plan.

## Roadmap

| Version | Scope |
|---|---|
| v0.1 | webview renderer working on Linux, `hello-webview` example |
| v0.2 | webview on Windows and macOS |
| v0.3 | LVGL renderer across all three platforms |
| v0.4 | CLI runtime variant (no UI, smallest binary) |
| v0.5 | `picolet bundle` produces `.msi` / `.dmg` / `.AppImage` |
| v1.0 | stable CLI surface, IPC contract, runtime ABI, and SBOM emission |

## Licence

[MIT](LICENSE). Picolet ships static and dynamic third-party code into every
binary it builds; see [docs/sbom.md](docs/sbom.md) for the per-artifact
SBOM plan.
