# manifest.py

The `manifest.py` file declares what MicroPython freezes into your Picolet
binary: which `.py` modules, which packages from `micropython-lib`, and
which user C modules. The format is the same as upstream MicroPython
uses for board manifests; `picolet build` invokes the standard manifest
processor under the hood.

The authoritative reference is the upstream MicroPython documentation:
**[Manifest files](https://docs.micropython.org/en/latest/reference/manifest.html)**.
This page is a Picolet-flavoured quick reference plus pointers for the
common cases.

## When do you need one

For a single-file CLI tool, you don't:

```bash
picolet build hello.py        # no manifest needed
```

For anything beyond one script — multiple modules, third-party packages
from `micropython-lib` or community sources, user C modules — a
manifest is how you declare the inputs.

## Getting started

Create `manifest.py` next to your `picolet.toml`:

```python
# manifest.py
metadata(
    description="My picolet app",
    version="0.1.0",
)

require("argparse")              # micropython-lib stdlib polyfill
require("pathlib")
require("dataclasses")

freeze("./src", "main.py")       # your entry point
freeze("./src", "utils.py")      # additional first-party modules
freeze("./src/sub", "lib.py")    # nested files OK
```

```bash
picolet build --manifest manifest.py
```

Or reference the manifest from `picolet.toml`:

```toml
[app]
name = "my-app"
entry = "src/main.py"
manifest = "manifest.py"
```

Then `picolet build` picks it up automatically — no `--manifest` flag
needed.

## Function reference

The manifest is a Python file evaluated in a sandbox that exposes these
functions. Full details at the upstream docs linked above; this is the
short version.

### `freeze(path, script=None, opt=0)`

Freeze `.py` files from `path` into the build. `script=None` freezes
every `.py` in the directory; pass a single filename to freeze just that
file.

```python
freeze("./src")                          # all .py in src/
freeze("./src", "main.py")               # only main.py
freeze("./src/lib", "helpers.py")        # specific nested file
```

`opt` is the bytecode optimisation level (0 = default, 3 = strip line
numbers and assertions for smaller `.mpy` output).

### `require(name, library=None)`

Pull a package by name from `micropython-lib` (or a registered custom
library — see `add_library` below). The package's `manifest.py` is
read, and its dependencies are resolved transitively.

```python
require("argparse")
require("dataclasses")
require("typing")
```

Available stdlib polyfills include: `argparse`, `pathlib`,
`dataclasses`, `typing`, `unittest`, `copy`, `decimal`, `fractions`,
`pprint`, `shutil`, `traceback`, `warnings`, `contextlib`, `abc`,
`enum`, `string`, `textwrap`, `urllib.parse`. See `docs/caveats.md`
for the full compatibility list.

### `package(package_path, files=None, base_path='.', opt=None)`

Freeze an entire package directory recursively (including `__init__.py`
and sub-packages). Restrict with `files=[...]` if you want only some
files.

```python
package("./src/mypkg")                   # everything under mypkg/
package("./src/mypkg", files=["a.py"])   # just a.py
```

### `module(module_path, base_path='.', opt=None)`

Freeze a single `.py` file as a top-level module. Equivalent to
`freeze(base_path, module_path)` but reads more naturally for one-off
modules.

```python
module("utils.py", base_path="./src")
```

### `include(manifest_path)`

Compose manifests. Useful for sharing common bits across apps or for
splitting a large manifest:

```python
include("../common/base-manifest.py")
include("./platform-specific.py")
```

### `c_module(path)`

Declare a user C module by absolute or `$(VAR)`-relative path. Each
module directory needs a `micropython.mk` (or `micropython.cmake`).

```python
c_module("$(BOARD_DIR)/drivers/sensor")
c_module("$(MPY_DIR)/../my-c-modules/cexample")
```

This is the modern alternative to the legacy `USER_C_MODULES=<dir>`
make-variable approach, which scans a single parent directory. With
`c_module()` each module can live anywhere. Backed by upstream PR
[micropython#18229](https://github.com/micropython/micropython/pull/18229),
included in Picolet's mbm composition.

### `add_library(library, library_path, prepend=False)`

Register a custom library directory so `require()` can find packages
there. Default order: registered libraries are appended (lower
priority than micropython-lib); pass `prepend=True` to override.

```python
add_library("my-lib", "/abs/path/to/my-lib")
require("widget", library="my-lib")
```

### `metadata(description=None, version=None, license=None, author=None)`

Declare metadata for the manifest. Picked up by `picolet`'s SBOM
emitter and surfaced in the output `.cdx.json`.

```python
metadata(
    description="My DFU flasher",
    version="0.2.1",
    license="MIT",
    author="Andrew Leech",
)
```

## Community packages

Beyond `micropython-lib`, the MicroPython community publishes packages
on independent indexes. **[checkmim.com/packages](https://checkmim.com/packages)**
is a searchable registry of MicroPython packages compatible with `mip`
(MicroPython's runtime package installer).

`mip` is for installing at runtime onto a live MicroPython filesystem;
Picolet builds happen at compile time and freeze packages into the
binary. To use a community package in a Picolet build:

### Option 1 — `require()` with a custom library

Clone the package source into a directory, register it with
`add_library`, then `require` it:

```python
# manifest.py
add_library("community", "./vendor/community-pkgs")
require("widget-toolkit", library="community")
```

The package needs its own `manifest.py` (or `package.json`) describing
its contents. Most checkmim-listed packages do.

### Option 2 — drop the source in tree and `freeze()` it

For packages that are pure-Python and small, just copy the source into
your app:

```python
freeze("./vendor/widget-toolkit")
```

You become responsible for licence attribution; declare it in your
SBOM input (see `docs/sbom.md`).

### Option 3 — fetch at build time

Run `mip install` (via `mpremote`) into a build-time staging directory,
then `freeze()` what landed there:

```bash
mpremote mip install --target ./vendor widget-toolkit
```

```python
freeze("./vendor/widget-toolkit")
```

This is reproducible if you check `./vendor/` into the repo; otherwise
the build depends on whatever the index has at build time.

## Worked example: a CLI tool with a third-party dep

```python
# manifest.py
metadata(version="1.0.0", license="MIT")

# stdlib polyfills
require("argparse")
require("dataclasses")

# community package (cloned into vendor/ by `mpremote mip install`)
add_library("vendor", "./vendor")
require("colorlog", library="vendor")

# our own sources
freeze("./src", "main.py")
package("./src/utils")
```

```toml
# picolet.toml
[app]
name = "my-tool"
entry = "src/main.py"
manifest = "manifest.py"
```

```bash
mpremote mip install --target ./vendor colorlog
picolet build
./target/linux-x64/my-tool --help
```

## Limits and gotchas

- `freeze()`/`require()` declare what's frozen at build time — once the
  binary is built, the contents are immutable. Runtime `mip.install`
  inside the running binary requires writable filesystem and network,
  neither of which a typical Picolet binary configures.
- Frozen modules use less RAM than file-system modules (their bytecode
  is in flash/ROM not heap), but they can't be reloaded — restart the
  app to pick up a code change. `picolet dev` automates this.
- Not all CPython libraries port cleanly to MicroPython. Check
  `docs/caveats.md` for compatibility limits before committing to a
  dep.

## See also

- [Upstream MicroPython manifest reference](https://docs.micropython.org/en/latest/reference/manifest.html)
- [micropython-lib README](https://github.com/micropython/micropython-lib/blob/master/README.md)
- [checkmim.com](https://checkmim.com) — community MicroPython package index
- [docs/caveats.md](caveats.md) — MicroPython vs CPython compatibility
- [docs/cli-reference.md](cli-reference.md) — `picolet build --manifest` options
