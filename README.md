# Picolet

Compile a Python program into a small self-contained native executable. Like PyInstaller, but the binary is hundreds of kilobytes rather than tens of megabytes, starts in milliseconds, and has no runtime Python dependency on the host.

## The shape of it

A Picolet binary is one file. Inside:

- A trimmed MicroPython interpreter (~410–650 KB depending on renderer).
- Your application's `.py` code, compiled to bytecode and frozen into the binary.
- Optional bundled assets (HTML, fonts, images, schemas) as a read-only ROM filesystem appended to the executable.
- Optionally: a GUI renderer (system webview or native LVGL) for HTML/JS or native UIs.

The result runs on any matching-OS/arch machine without installing Python.

## Three ways to use it

### 1. Bundle a single CLI script

The simplest case:

```bash
picolet build hello.py
./hello                # Linux  ≈ 647 KB
./hello.exe            # Windows ≈ 409 KB
```

`hello.py` becomes a self-contained executable. Sub-second startup. No `python` interpreter needed on the host machine. Roughly 20–50× smaller than the equivalent PyInstaller bundle, with no first-launch unpacking delay.

### 2. Manifest-driven build

For multi-module apps, drive the build from a MicroPython `manifest.py`:

```python
# manifest.py
require("argparse")                   # frozen micropython-lib module
require("typing")
freeze("./src", "main.py")            # your entry
freeze("./src/lib", "utils.py")       # additional modules
freeze("./src/lib", "data_model.py")
```

```bash
picolet build --manifest manifest.py
```

The manifest is the canonical MicroPython mechanism for declaring what's frozen into a build. Picolet uses it directly — everything declared is included.

Medium-term: romfs contents (HTML, fonts, etc.) declared in the same manifest, per upstream evolution.

### 3. GUI application

Add a `picolet.toml` to pick a renderer and bundle UI assets:

```toml
[app]
name = "my-app"
entry = "src/main.py"

[ui]
renderer = "webview"            # or "lvgl" for native
root = "ui"

[window]
title = "My App"
size = [900, 600]

[romfs]
include = ["ui", "assets"]
```

```bash
picolet build
```

For Vue or other JS framework frontends:

```toml
[ui.frontend]
framework = "vue"               # builds Vite output into romfs automatically
```

The IPC bridge is set up automatically:

```javascript
// JS side
await window.picolet.invoke('list_items', { filter: 'active' });
window.picolet.on('progress', e => bar.update(e.pct));
```

```python
# Python side
@picolet.command
async def list_items(args):
    return [...]
```

Four worked examples under `examples/` (`pydfu`, `notes`, `config-editor`, `dashboard`) demonstrate the patterns end-to-end, each with a distinct visual aesthetic.

## Caveats

Read these before adopting.

- **MicroPython, not CPython.** The standard library subset is smaller. Pure-Python code using `os`, `sys`, `json`, `re`, `time`, `asyncio`, `struct`, `io` etc. typically works. Code that depends on C extensions (numpy, pandas, Pillow, lxml, etc.) does not. Picolet cannot make a CPython library "just work" — it would have to be ported or replaced with a MicroPython equivalent.

- **`micropython-lib` fills most stdlib gaps.** Modules like `argparse`, `pathlib`, `dataclasses`, `typing`, `unittest` are available via [micropython-lib](https://github.com/micropython/micropython-lib) and are declared with `require("name")` in the manifest.

- **Custom C modules are straightforward.** A Picolet binary is a MicroPython build under the hood, so any C module written against the standard MicroPython API works. Drop a directory under `user_c_modules/` and reference it in the manifest. Once [upstream PR #18229](https://github.com/micropython/micropython/pull/18229) lands in the fork, `c_module("./path/to/mymodule")` can declare it directly in the manifest.

- **Python C-API extensions (CPython `.so`/`.pyd`) do NOT work.** MicroPython's runtime is binary-incompatible with CPython. Write a MicroPython C module instead.

- **Async semantics differ slightly.** MicroPython's `asyncio` is a subset of CPython's. Coroutines, tasks, queues, locks, `gather`, `wait_for` work; `asyncio.run` with policies, debug mode, the `loop.add_signal_handler` interface, and some niche APIs don't. Most app code is unaffected.

- **First-class platform support is Linux x64 and Windows x64.** macOS (Intel + Apple Silicon) is source-complete but requires GitHub Actions CI for native builds — local cross-compile from Linux is not supported. ARM Linux and mobile platforms are on the backlog.

See [docs/caveats.md](docs/caveats.md) for a full catalogue.

## Toolchain expectations

- The `picolet` CLI runs on Linux, macOS, or Windows (host).
- Building Windows binaries from Linux uses [`dockcross`](https://github.com/dockcross/dockcross); Docker is required for that path.
- Building macOS binaries uses GitHub Actions runners (`macos-13` Intel, `macos-14` Apple Silicon).
- Node.js + npm are required only when building Vue/React frontends.

## Getting started

Install the CLI. `uv tool install` is the recommended path (no separate
package manager needed if you already use `uv`); `pipx` is the fallback
for environments without `uv`. Plain `pip install` is deliberately not
listed — most modern distros (PEP 668) block it system-wide.

```bash
uv tool install picolet-cli                       # recommended
# or:
pipx install picolet-cli                          # fallback

picolet init my-app --template hello-cli          # see --list-templates for the full set
cd my-app
picolet dev                                        # live rebuild on file changes
picolet build                                      # produce target/<os-arch>/my-app[.exe]
```

Templates: `hello-cli`, `hello-webview`, `hello-lvgl`, `hello-vue`, `pydfu`, `notes`, `config-editor`, `dashboard`.

## Examples gallery

<table>
  <tr>
    <td align="center">
      <a href="examples/pydfu/">
        <img src="examples/pydfu/screenshots/device-list-populated.png" alt="pydfu screenshot" />
      </a>
      <br>
      <strong>pydfu</strong><br>
      DFU firmware flasher (industrial control-panel aesthetic)
    </td>
    <td align="center">
      <a href="examples/notes/">
        <img src="examples/notes/screenshots/list-populated.png" alt="notes screenshot" />
      </a>
      <br>
      <strong>notes</strong><br>
      Markdown notes (editorial / refined)
    </td>
  </tr>
  <tr>
    <td align="center">
      <a href="examples/config-editor/">
        <img src="examples/config-editor/screenshots/edit-toml.png" alt="config-editor screenshot" />
      </a>
      <br>
      <strong>config-editor</strong><br>
      Schema-driven TOML/YAML/JSON editor (brutalist terminal)
    </td>
    <td align="center">
      <a href="examples/dashboard/">
        <img src="examples/dashboard/screenshots/full-dashboard.png" alt="dashboard screenshot" />
      </a>
      <br>
      <strong>dashboard</strong><br>
      Live system metrics (data-dense)
    </td>
  </tr>
</table>

See [docs/examples.md](docs/examples.md) for a longer tour of each.

## Renderers

| Renderer | Backed by | Use case | Binary size (typical) |
|---|---|---|---|
| (none) | — | CLI tool, no UI | ~647 KB Linux / ~409 KB Windows |
| `webview` | WebKitGTK 4.1 (Linux), WebView2 (Windows), WKWebView (macOS) | HTML/CSS/JS frontend | ~710 KB Linux / ~525 KB Windows |
| `lvgl` | [`lv_binding_micropython`](https://github.com/lvgl/lv_binding_micropython) + SDL2 | Native widgets, zero system UI deps | ~1.84 MB Linux / ~2.05 MB Windows |

## Repository layout

```
picolet/
├── packages/
│   ├── picolet-cli/         # picolet init|dev|build|run|test
│   ├── picolet-runtime/     # the MicroPython runtime + variants + user_c_modules
│   ├── picolet-bridge-js/   # JS shim: window.picolet.invoke(...)
│   ├── picolet-templates/   # `picolet init --template <name>` starter packs
│   └── picolet-testing/     # AppHarness for autonomous test/screenshot
├── examples/              # four worked example apps
├── tests/                 # per-phase test suites
└── docs/                  # architecture, caveats, examples, CLI reference
```

## More

- [Architecture](docs/architecture.md) — design decisions
- [Caveats](docs/caveats.md) — MicroPython compatibility reference
- [Examples tour](docs/examples.md) — what each example app demonstrates
- [CLI reference](docs/cli-reference.md) — every `picolet` subcommand
- [Getting started](docs/getting-started.md) — step-by-step tutorial
- [SBOM](docs/sbom.md) — supply-chain documentation
- [History](docs/history/) — phase artefacts and version-specific specs/plans/audits

## License

MIT throughout. See [LICENSE](LICENSE).
