# CLI reference

`picolet` is the build and development tool for Picolet apps. It requires
Python 3.11+ and is installed via pip or uv:

```bash
pip install picolet-cli
# or
uv tool install picolet-cli
```

---

## picolet init

Scaffold a new app directory from a starter template.

**Synopsis:**

```
picolet init [<name>] [--template TEMPLATE] [--output-dir DIR] [--list-templates]
```

**Arguments:**

| Argument | Description |
|---|---|
| `<name>` | App name, used as the project directory name. Omit when using `--list-templates`. |
| `--template TEMPLATE` | Template to use (default: `hello-cli`). |
| `--output-dir DIR` | Directory to create (default: `./<name>`). |
| `--list-templates` | Print available template names, one per line, and exit. |

**Available templates:**

| Name | Description |
|---|---|
| `hello-cli` | Minimal command-line app (no GUI) |
| `hello-webview` | Webview window with a plain HTML/JS page |
| `hello-lvgl` | LVGL display with a simple widget layout |
| `hello-vue` | Webview window with a Vue 3 frontend (requires Node) |
| `pydfu` | DFU firmware flashing tool (webview UI) |
| `notes` | Persistent notes app (webview UI) |
| `config-editor` | TOML config editor (webview UI) |
| `dashboard` | Live metrics dashboard (webview UI) |

**Examples:**

```bash
picolet init my-app
picolet init my-app --template hello-vue
picolet init pydfu-tool --template pydfu
picolet init --list-templates
```

The name is substituted for `{{name}}` in all template text files. Binary
files (images, fonts, compiled assets) are copied verbatim.

---

## picolet validate

Validate a `picolet.toml` file against the schema.

**Synopsis:**

```
picolet validate [<file>]
```

**Arguments:**

| Argument | Description |
|---|---|
| `<file>` | Path to the `picolet.toml` to validate (default: `./picolet.toml`). |

Exit 0 on success. Exit 1 and print structured errors on failure.
Warnings are printed to stderr but do not affect the exit code.

**Examples:**

```bash
picolet validate
picolet validate path/to/picolet.toml
```

---

## picolet build

Compile the app into a single self-contained binary.

**Synopsis:**

```
picolet build [--target TARGET] [--verbose] [--keep-staging]
            [--from-source] [--no-cache] [--allow-unverified-runtime]
            [--no-sbom]
```

Run from the app directory (the one containing `picolet.toml`). Output is
written to `target/<target>/<app-name>[.exe]`.

**Pipeline:**

1. Read and validate `picolet.toml`.
2. Resolve the runtime variant from `[ui]` (absent → `cli`).
3. Resolve the build target from `--target` or host auto-detection.
4. Locate the runtime artifact and `mpy-cross`; verify version match.
5. Compile `.py` sources → `.mpy` via `mpy-cross`.
6. Copy `[romfs]` include directories into staging.
7. Zero all file mtimes for reproducible output.
8. Build the romfs image with `mpremote`.
9. Append romfs + 24-byte trailer to the runtime binary.
10. Emit an SBOM sidecar `.cdx.json`.

**Arguments:**

| Argument | Description |
|---|---|
| `--target TARGET` | Build target (`linux-x64`, `windows-x64`). Default: host. |
| `--verbose`, `-v` | Print build steps to stderr. |
| `--keep-staging` | Preserve the staging directory after a successful build (debugging). |
| `--from-source` | Build the runtime locally using `build-runtime.sh` (requires Docker). |
| `--no-cache` | Skip the runtime artifact cache; always download fresh. |
| `--allow-unverified-runtime` | Run with a runtime binary that has no `.sha256` sidecar. |
| `--no-sbom` | Skip SBOM emission (no `.cdx.json` sidecar). |

**Examples:**

```bash
picolet build
picolet build --target windows-x64
picolet build --target linux-x64 --verbose
picolet build --from-source        # build runtime locally (requires Docker)
```

**Frontend builds:** when `[ui.frontend].framework` is `vue` (or any
non-`vanilla` value), `picolet build` automatically runs `npm install` then
the configured build command before compiling Python sources. Node.js ≥ 18
must be on `PATH`.

---

## picolet run

Build (if needed) and execute the produced binary.

**Synopsis:**

```
picolet run [--target TARGET] [--verbose] [--no-build] [-- arg1 arg2 ...]
```

The binary is rebuilt if it does not exist or if any source file (`src/`,
`ui/`, `picolet.toml`) is newer than the binary. Pass `--no-build` to skip
the freshness check and run whatever binary is already present.

Arguments after `--` are forwarded verbatim to the child binary.

**Arguments:**

| Argument | Description |
|---|---|
| `--target TARGET` | Build target. Default: host. |
| `--verbose`, `-v` | Print build steps to stderr. |
| `--no-build` | Skip build freshness check; run the existing binary directly. |
| `-- [args]` | Arguments forwarded to the binary. The `--` separator is required. |

**Examples:**

```bash
picolet run
picolet run --no-build
picolet run -- --port 8080
picolet run --verbose -- --some-arg-for-the-binary
```

---

## picolet dev

Watch for source changes and rebuild + relaunch the app automatically.

**Synopsis:**

```
picolet dev [--target TARGET] [--verbose]
```

Watches `src/`, `ui/`, and `picolet.toml`. On change, rebuilds the app and
relaunches it. Uses filesystem polling at 500 ms intervals with a 500 ms
debounce — no external dependencies required.

For Vue apps, `picolet dev` additionally spawns a Vite dev server so the
browser window loads from the live Vite HMR server rather than from romfs.

Press CTRL-C to stop.

**Arguments:**

| Argument | Description |
|---|---|
| `--target TARGET` | Build target. Default: host. |
| `--verbose`, `-v` | Print watch and build steps to stderr. |

**Examples:**

```bash
picolet dev
picolet dev --verbose
picolet dev --target linux-x64
```

---

## picolet test

Launch the app in test mode and drive it via the inspector port.

**Synopsis:**

```
picolet test [BINARY] [--target TARGET] [--no-build]
           [--browser {webkit,chromium,auto}]
           [--screenshot PATH] [--run SCRIPT_PY]
           [--timeout SECONDS] [--verbose]
           [-- arg1 arg2 ...]
```

Spawns the binary with `PICOLET_TEST_MODE=1`, waits for
`picolet:test-port=<N>` on stderr, then operates in one of three modes:

| Mode | Description |
|---|---|
| bare (no flags) | Connect, print connection info, then exit. |
| `--screenshot PATH` | Attach via AppHarness, wait for window ready, capture PNG, exit. |
| `--run SCRIPT_PY` | Execute the script with `harness` pre-bound to AppHarness. Exit code mirrors the script's. |

On Linux without `$DISPLAY`, Xvfb is started automatically if available.

**Arguments:**

| Argument | Description |
|---|---|
| `BINARY` | Path to the binary. Resolved from `picolet.toml` if omitted. |
| `--target TARGET` | Build target. Default: host. |
| `--no-build` | Skip build freshness check; use the existing binary. |
| `--browser BROWSER` | Debug driver: `webkit` (Linux), `chromium` (Windows), `auto` (default). |
| `--screenshot PATH` | Capture a screenshot to PATH (PNG), then exit. |
| `--run SCRIPT_PY` | Execute SCRIPT_PY with `harness` pre-bound in globals. |
| `--timeout SECONDS` | Seconds to wait for the port announcement (default: 10). |
| `--verbose`, `-v` | Print extra diagnostic information. |
| `-- [args]` | Extra arguments forwarded to the binary. |

**Examples:**

```bash
picolet test
picolet test --screenshot home.png
picolet test --screenshot home.png ./target/linux-x64/my-app
picolet test --run tests/test_flow.py
picolet test --run tests/test_flow.py -- --some-arg-for-the-binary
picolet test --browser webkit --timeout 30 --verbose
```

`--screenshot` and `--run` require `picolet-testing` to be installed:

```bash
pip install picolet-testing
```

---

## picolet help

Print help for a subcommand.

**Synopsis:**

```
picolet help [<command>]
```

`picolet help build` is equivalent to `picolet build --help`.
`picolet help` with no argument prints top-level help.
