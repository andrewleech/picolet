# Getting started

This tutorial progresses from the simplest possible case (a single-file
CLI script) to a multi-module app, then to a GUI app with an IPC bridge.

## Prerequisites

- Python 3.11+
- Install the CLI:
  - **`uv tool install picolet-cli`** (recommended — fastest, isolated)
  - or **`pipx install picolet-cli`** (fallback)
  - Plain `pip install` is not recommended: most modern distros block
    system-wide pip per PEP 668.

For GUI work with Vue: Node.js 18+. For Windows cross-compilation from
Linux: Docker. Neither is needed for the first two sections.

---

## 1. Hello CLI

The simplest case: turn a Python script into a standalone binary.

```bash
picolet init hello --template hello-cli
cd hello
```

The scaffolded `src/main.py` looks roughly like:

```python
import sys

def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "world"
    print(f"hello, {name}")

main()
```

Build it:

```bash
picolet build
```

The output is `target/linux-x64/hello` (or `target/windows-x64/hello.exe`
if building for Windows). Run it:

```bash
./target/linux-x64/hello
hello, world

./target/linux-x64/hello picolet
hello, picolet
```

**Size comparison.** The binary is around 647 KB on Linux. A PyInstaller
bundle of the same script is typically 7–20 MB compressed and 50–80 MB
unpacked, with a multi-second first-launch extraction delay. A Picolet binary
needs no unpacking — it reads its own bytecode from the appended romfs
directly.

To target Windows from Linux:

```bash
picolet build --target windows-x64
# produces target/windows-x64/hello.exe (~409 KB)
# runs directly via WSL interop: ./target/windows-x64/hello.exe
```

---

## 2. Multi-module app via manifest

When your app grows past a single file, use MicroPython's manifest to
declare what gets frozen.

Add a `utils.py` next to `main.py`:

```python
# src/utils.py
def greet(name: str) -> str:
    return f"hello from utils, {name}"
```

Import it in `main.py`:

```python
import utils

def main():
    print(utils.greet("picolet"))

main()
```

The manifest approach (for larger projects) is a `manifest.py` that
lists what to freeze:

```python
# manifest.py
require("argparse")          # from micropython-lib
freeze("./src", "main.py")
freeze("./src", "utils.py")
```

```bash
picolet build --manifest manifest.py
```

For the simpler case — all `.py` files under `src/` — the default
`picolet.toml` scaffolded by `picolet init` already handles this: `picolet build`
without a manifest compiles every `.py` under the entry's directory and
produces a single binary.

For full manifest syntax — `package()`, `include()`, `add_library()`,
`c_module()`, custom indexes — and guidance on pulling community
packages from [checkmim.com/packages](https://checkmim.com/packages),
see **[docs/manifest.md](manifest.md)**.

---

## 3. Adding a webview GUI

```bash
picolet init my-app --template hello-webview
cd my-app
```

The scaffolded `picolet.toml`:

```toml
[app]
name = "my-app"
entry = "src/main.py"

[ui]
renderer = "webview"
root = "ui"

[window]
title = "My App"
size = [900, 600]
```

The Python entry point registers IPC commands and starts the app:

```python
import picolet
import picolet_ui as ui

@picolet.command
async def greet(args):
    name = args.get("name", "world")
    return {"message": f"hello, {name}"}

ui.run()
```

The JS side (in `ui/index.html` or a bundled frontend) calls Python via
the bridge:

```javascript
const result = await window.picolet.invoke('greet', { name: 'picolet' });
console.log(result.message);   // "hello, picolet"
```

Push events work in the other direction:

```python
# Python pushes an event to JS
picolet.emit("status:update", {"text": "ready"})
```

```javascript
window.picolet.on("status:update", e => {
    document.getElementById("status").textContent = e.text;
});
```

Start the dev loop — it rebuilds and relaunches on every source change:

```bash
picolet dev
```

For the `hello-webview` template (plain HTML, no build step), `picolet dev`
rebuilds the Python sources and relaunches the binary. Changes to the HTML
in `ui/` are also picked up.

---

## 4. A real example: notes

The `notes` example is the gentlest entry point to a full Picolet app. It
is a markdown notes app with list, create, edit, rename, delete, and
search — around 120 lines of Python and a Vue frontend.

```bash
picolet init my-notes --template notes
cd my-notes
picolet dev          # spawns Vite + builds Python, relaunches on change
```

**Structure:**

```
my-notes/
├── picolet.toml          # app config: renderer=webview, frontend=vue
├── src/
│   ├── main.py         # IPC command handlers
│   └── notes_store.py  # filesystem persistence (~/.config/notes/)
├── ui/                 # Vue 3 frontend source
│   └── src/
│       ├── App.vue
│       └── views/
│           ├── ListView.vue
│           └── EditView.vue
└── tests/
    └── test_notes.py   # AppHarness integration test
```

The Python layer is thin: each IPC command delegates to `notes_store`,
which reads and writes files in the platform config directory
(`~/.config/notes/` on Linux, `%APPDATA%\notes\` on Windows). The Vue
frontend handles routing, markdown rendering (via `marked`), and UI state.

A representative command:

```python
@picolet.command
async def save_note(args):
    slug = args.get("slug")
    body = args.get("body", "")
    return store.save_note(slug, body)
```

The Vue side calls it:

```javascript
const updated = await window.picolet.invoke('save_note', {
    slug: currentSlug,
    body: editorContent,
});
```

Build the release binary:

```bash
picolet build
# produces target/linux-x64/notes (~710 KB)
```

The binary includes the compiled Vue output (HTML, CSS, JS) in its romfs.
No web server, no Python installation, no Electron.

For more on each example app, see [docs/examples.md](examples.md).
