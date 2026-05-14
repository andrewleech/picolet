# Picolet v1 Specification

This document is the contract for what `picolet` v1 must do. Functional
requirements (FR) are numbered and traceable from implementation back
to spec. Non-functional requirements (NFR) constrain how those FRs are
realised. Out-of-scope items are explicit.

The architecture decisions in [architecture.md](architecture.md) and
the dependency policy in [sbom.md](sbom.md) are normative inputs.

## Targets

Picolet v1 supports two host targets:

- `linux-x64` — gcc, glibc 2.31+
- `windows-x64` — built via dockcross MinGW cross-compile from a
  Linux or WSL host

macOS, ARM, and 32-bit targets are out of scope for v1.

## Functional requirements

### CLI

| ID | Requirement |
|---|---|
| FR-CLI-1 | `picolet` is invokable from a shell with subcommands `init`, `build`, `run`, `dev`. |
| FR-CLI-2 | `picolet init <name>` scaffolds an app from a template; `--template <t>` selects between `hello-webview`, `hello-lvgl`, `hello-cli`. |
| FR-CLI-3 | `picolet build [--target T]` emits a single executable at `target/<target>/<app>[.exe]`. |
| FR-CLI-4 | `picolet build` with no `--target` builds for the host platform. |
| FR-CLI-5 | `picolet build --from-source` invokes the dockcross runtime build locally instead of downloading the pre-built artifact. |
| FR-CLI-6 | `picolet run` invokes `picolet build` if needed and executes the produced binary. |
| FR-CLI-7 | `picolet dev` watches the app directory and re-runs `build`+`run` on UI-asset or Python-source change. Live-reload of Python state is out of scope. |
| FR-CLI-8 | Invalid `picolet.toml` content is rejected with a structured error before any build work. |

### Runtime

| ID | Requirement |
|---|---|
| FR-RT-1 | Each runtime artifact is a single executable embedding MicroPython, the renderer modules for its variant, and romfs ioctl machinery. |
| FR-RT-2 | Three runtime variants per target: `webview`, `lvgl`, `cli`. |
| FR-RT-3 | The `cli` variant has no window, no webview, no LVGL. |
| FR-RT-4 | `gc.add_heap()` is available in every variant. |
| FR-RT-5 | The `ffi` module is available in every variant. |
| FR-RT-6 | Embedded romfs is auto-mounted at `/rom` and prepended to `sys.path`. |
| FR-RT-7 | `main.py` or `main.mpy` in frozen modules or under `/rom/` is executed at startup. |
| FR-RT-8 | `sys.argv` is populated from the host command line. |

### Webview renderer

| ID | Requirement |
|---|---|
| FR-WV-1 | On Linux the webview is WebKitGTK 4.1; on Windows it is WebView2 (Edge Chromium). |
| FR-WV-2 | The webview loads its root document from `/rom/<ui.root>/<index>`; default index is `index.html`. |
| FR-WV-3 | The window title and size come from `[window]` in `picolet.toml`. |
| FR-WV-4 | The `picolet-bridge-js` script is injected before any user frontend JS runs. |
| FR-WV-5 | The bridge exposes `window.picolet.invoke(cmd, args) → Promise<result>` and `window.picolet.on(event, handler) → unsubscribe`. |

### LVGL renderer

| ID | Requirement |
|---|---|
| FR-LV-1 | On both Linux and Windows the LVGL backend uses SDL2 for a desktop window. |
| FR-LV-2 | Display size comes from `[window]` in `picolet.toml`. |
| FR-LV-3 | `import lvgl as lv` works inside the app's frozen Python. |
| FR-LV-4 | `picolet.invoke` / `picolet.emit` work in the LVGL variant as Python-to-Python calls via the same dispatcher used by webview. |

### IPC

| ID | Requirement |
|---|---|
| FR-IPC-1 | `@picolet.command async def name(args): ...` registers a command on the Python side. |
| FR-IPC-2 | `await picolet.invoke(name, args)` from a peer returns the command's return value, or raises with the originating exception type and message preserved. |
| FR-IPC-3 | `picolet.emit(topic, data)` from Python pushes an event reachable by `picolet.on(topic, handler)` peers. |
| FR-IPC-4 | Messages are JSON; the wire format is documented in [architecture.md §IPC](architecture.md#ipc-wire-format). |
| FR-IPC-5 | `asyncio` is the Python-side scheduler. |

### Build pipeline

| ID | Requirement |
|---|---|
| FR-BP-1 | `picolet build` resolves the runtime variant from `[ui] renderer` (or absent `[ui]` → `cli`) and the target from `--target` or host. |
| FR-BP-2 | Pre-built runtimes are downloaded by tag from a configured release source and cached under `.picolet-cache/`. |
| FR-BP-3 | User `.py` sources under the entry's directory tree are compiled to `.mpy` via the bundled `mpy-cross`. |
| FR-BP-4 | A romfs image is built from `[romfs] include` directories plus the compiled `.mpy` set plus, for webview variants, the bridge-js bundle. |
| FR-BP-5 | The final binary is the runtime artifact with the romfs appended at the offset the runtime expects. |
| FR-BP-6 | The same inputs produce the same output bytes, modulo filesystem timestamps. |

### SBOM

| ID | Requirement |
|---|---|
| FR-SBOM-1 | Each runtime artifact and each `picolet build` output carries a sibling `<artifact>.cdx.json` in CycloneDX 1.5 format. |
| FR-SBOM-2 | The app SBOM is the union of: the runtime artifact's SBOM, the user's app `[dependencies]`, and the frozen micropython-lib modules pulled in by the manifest. |
| FR-SBOM-3 | `picolet build` consults `[sbom] allow_licences` and `[sbom] allow_dynamic` and either warns or fails per `[sbom] fail_unknown`. |

## Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | `picolet-runtime-{target}-cli` ≤ 1 MB. |
| NFR-2 | `picolet-runtime-{target}-webview` ≤ 2 MB (excluding system webview). |
| NFR-3 | `picolet-runtime-{target}-lvgl` ≤ 2 MB. |
| NFR-4 | The runtime requires no system Python on either target. |
| NFR-5 | No GPL or AGPL components are statically linked into any runtime variant. LGPL components reached dynamically are permitted and recorded in the SBOM. |
| NFR-6 | All Picolet-authored code is MIT-licensed. |
| NFR-7 | The CI matrix produces all six runtime artifacts (3 variants × 2 targets) plus their SBOMs in a single workflow run. |
| NFR-8 | Linux artifacts run on Ubuntu 22.04 with no extra packages beyond `webkit2gtk-4.1` (webview variant only). |
| NFR-9 | Windows artifacts run on Windows 10 21H2 and later with the Edge WebView2 Runtime present (system default on Windows 11). |

## Out of scope for v1

- macOS targets (deferred to v1.1).
- ARM targets.
- Native installer formats — handled by `picolet bundle` post-v1.
- Hot-reload of Python state during `picolet dev`.
- TypeScript code generation from registered commands.
- App icon / Win32 VERSIONINFO / Linux `.desktop` integration.
- Code signing.
- Auto-update.
