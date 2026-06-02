# Caveats

A factual catalogue of what works and what doesn't in Picolet apps.

## MicroPython vs CPython

Picolet uses MicroPython, not CPython. The language core (functions,
classes, exceptions, comprehensions, generators, decorators, async/await)
is compatible, but a small number of CPython features are absent or
different:

- **No metaclasses** — `type(name, bases, ns)` works for simple cases;
  `__init_subclass__` and `__class_getitem__` are not supported.
- **`__slots__` is not enforced** — declared but has no effect on memory
  layout.
- **`f-string` expressions** — supported, but some edge cases around
  nested quotes differ from CPython 3.12+.
- **`match` / `case`** — not supported in MicroPython.
- **`__del__` finalizers** — called on garbage collection, but GC timing
  is non-deterministic; do not rely on them for resource cleanup.
- **`int` is arbitrary precision** — but operations are slower than
  CPython's for very large integers.
- **`str` is UTF-8 bytes internally** — most code is unaffected, but
  code that relies on `str` indexing returning single-character strings
  with specific Unicode scalar values may behave differently.

The upstream MicroPython project maintains a detailed differences
document: https://docs.micropython.org/en/latest/genrst/index.html

## Standard library subset

The following modules are built into every Picolet runtime and available
without any manifest declaration:

`sys`, `os`, `io`, `time`, `math`, `struct`, `json`, `re`, `asyncio`,
`gc`, `builtins`, `errno`, `socket` (where applicable), `select`,
`binascii`, `hashlib` (sha256 only), `random`, `collections`, `functools`,
`itertools`, `heapq`.

The following modules are available via
[micropython-lib](https://github.com/micropython/micropython-lib) and
must be declared with `require("name")` in the manifest (see
[docs/manifest.md](manifest.md) for the full syntax + community
package sources):

`argparse`, `pathlib`, `dataclasses`, `typing`, `unittest`, `copy`,
`decimal`, `fractions`, `pprint`, `shutil`, `traceback`, `warnings`,
`contextlib`, `abc`, `enum`, `string`, `textwrap`, `urllib.parse`.

Not available and not polyfillable without significant work:

`threading` (see Threading below), `multiprocessing`, `subprocess`,
`ctypes`, `inspect`, `ast`, `dis`, `importlib.util` (partial),
`logging` (partial — micropython-lib has a stub), `sqlite3`, `csv`,
`xml.*`, `html.parser`, `email.*`, `http.*`, `urllib.request`.

If you need one of these, check micropython-lib first. If it is not
there, you will need to either port the relevant code or find a
MicroPython-native alternative.

## C extension situation

**CPython C-API extensions (`.so` / `.pyd` files) do not work.** The
MicroPython runtime uses a different ABI and a different object model.
A CPython extension module cannot be loaded by MicroPython even if it
targets the same platform and architecture.

**MicroPython C modules do work.** Any C module written against the
standard MicroPython native module API (the `MP_DEFINE_CONST_*` macros
and `mp_obj_t` ABI) can be included in a Picolet binary. The process:

1. Write the C module following the MicroPython native module
   documentation: https://docs.micropython.org/en/latest/develop/natmod.html
2. Place the module directory anywhere accessible to the build.
3. Declare it in your manifest with `c_module("path/to/mymodule")`.

`c_module()` is already available in Picolet — it comes from
[upstream PR #18229](https://github.com/micropython/micropython/pull/18229),
which is composed into the
[andrewleech/micropython](https://github.com/andrewleech/micropython) fork
that Picolet builds against (the PR is still in review upstream).
A `--from-source` build is required to include custom C modules in your
own runtime; the prebuilt runtimes shipped on the GitHub Release only
contain modules baked in at release time.

## Runtime memory

MicroPython's heap is bounded. The default heap in a Picolet binary is
sized for typical application workloads but is smaller than what CPython
would provide on the same machine.

To add heap at runtime:

```python
import gc
gc.add_heap(bytearray(512 * 1024))   # add 512 KB
```

Call this early in `main.py`, before importing large modules. The total
usable heap is the initial heap plus any added via `gc.add_heap`.

Code that allocates large intermediate data structures (e.g. loading a
50 MB JSON file into a dict) will hit the heap limit before CPython
would. Structure your code to process data in chunks where possible.

## Threading

MicroPython's threading support is limited. The `_thread` module exists
but is a thin POSIX-thread wrapper without Python's GIL semantics, and
is not generally safe to use with MicroPython's garbage collector.

Most Picolet apps use `asyncio` instead:

- `asyncio.create_task`, `asyncio.gather`, `asyncio.wait_for` work.
- `asyncio.Queue`, `asyncio.Lock`, `asyncio.Event` work.
- `asyncio.run` works (MicroPython's version, which is a subset of
  CPython's).
- `loop.add_signal_handler`, `loop.run_in_executor`, and thread-executor
  integration are not available.

For CPU-bound work that genuinely needs threads, the recommended
approach is a native C module that manages its own POSIX threads outside
the MicroPython interpreter.

## Filesystem access

`os`, `os.path`, and `pathlib` (via micropython-lib) work. Specifics:

- `os.walk` is available via `require("os")` in micropython-lib.
- `tempfile` has a limited stub in micropython-lib (`tempfile.mkstemp`
  is absent; use `os.open` with `O_CREAT | O_EXCL` directly).
- `shutil` is in micropython-lib (`shutil.copy`, `shutil.copytree`,
  `shutil.rmtree` work; some edge-case options differ).
- The romfs (`/rom/`) is read-only. All writes must go to writable
  paths (`os.getcwd()`, the platform config dir, or `/tmp`).

## Platform-specific

**Linux:**
- The `webview` renderer requires WebKitGTK 4.1 (`libwebkitgtk-4.1.so`)
  to be installed on the host. On Ubuntu/Debian: `apt install libwebkit2gtk-4.1-0`.
- The `lvgl` renderer requires SDL2 (`libSDL2-2.0.so`). On Ubuntu/Debian:
  `apt install libsdl2-2.0-0`.
- The `cli` variant has no system dependencies beyond libc and libpthread.

**Windows:**
- The `webview` renderer uses the WebView2 runtime, which ships with
  Microsoft Edge. It is present on any Windows 10/11 machine with Edge
  installed (the majority of deployed systems). The Picolet binary
  includes `WebView2Loader.dll` in its romfs and extracts it on first
  run; no separate installer is required.
- The `lvgl` renderer bundles SDL2 statically; no system installation needed.

**macOS:**
- The `webview` renderer uses `WKWebView`, available on macOS 10.14+.
- The `lvgl` renderer requires SDL2. Install with `brew install sdl2`.
- pydfu-style USB access requires `brew install libusb`.
- macOS builds are produced via GitHub Actions (Intel: `macos-13`,
  Apple Silicon: `macos-14`). Local cross-compile from Linux is not
  supported.
