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

## Examples

Four reference apps, each with a distinct visual direction. See
[`examples/`](examples/) for source and [`docs/examples.md`](docs/examples.md)
for the walkthrough.

| pydfu | notes |
|---|---|
| ![pydfu — industrial control panel](examples/pydfu/screenshots/device-list-populated.png) | ![notes — editorial refined](examples/notes/screenshots/list-populated.png) |

| config-editor | dashboard |
|---|---|
| ![config-editor — brutalist terminal](examples/config-editor/screenshots/edit-toml.png) | ![dashboard — data-dense dark UI](examples/dashboard/screenshots/full-dashboard.png) |

## Status

v1.1 examples complete. Four reference apps (pydfu, notes, config-editor,
dashboard) with screenshots, integration tests, and `picolet init` templates.
See [docs/examples.md](docs/examples.md) and [examples/](examples/).

Architecture decisions: [docs/architecture.md](docs/architecture.md).
Dependency tracking: [docs/sbom.md](docs/sbom.md).

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
