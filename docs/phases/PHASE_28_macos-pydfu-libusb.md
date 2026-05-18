# PHASE 28 — macOS pydfu (libusb dylib)

## Goal

Make the pydfu example app work on macOS. The change is small: add a
`sys.platform == "darwin"` branch to the libusb load path in
`_usb/core.py`, confirm `romfs_extract.py` handles macOS correctly, and
update the SBOM. No new C code; no new native modules.

## Prerequisites

- PH24 complete (macOS cli builds — establishes that the runtime runs
  on macOS with `ffi.open` working).
- PH19 complete (pydfu example app works on Linux and Windows).

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-EX-MAC-1 (pydfu) | pydfu builds and test suite passes on macOS |
| FR-EX-MAC-2 | `ffi.open("libusb-1.0.dylib")` loads libusb on macOS |
| FR-EX-MAC-3 | `romfs_extract.py` returns path unchanged on macOS |

## Dependencies

- PH19 (pydfu example app fully implemented on Linux/Windows).
- PH24 (macOS runtime with FFI available).

## Key research findings

### libusb on macOS

`brew install libusb` installs:
- `/usr/local/lib/libusb-1.0.dylib` (x64, Intel Homebrew)
- `/opt/homebrew/lib/libusb-1.0.dylib` (arm64, Apple Silicon Homebrew)

`dyld` searches the standard library paths. On macOS, `ffi.open("libusb-1.0.dylib")`
resolves via:
1. `DYLD_LIBRARY_PATH` (if set)
2. `DYLD_FALLBACK_LIBRARY_PATH` (default: `~/lib:/usr/local/lib:/usr/lib`)
3. The library's own `@rpath` / `@executable_path` relative path.

For Intel Homebrew, `/usr/local/lib` is in the default fallback — no
path needed. For Apple Silicon Homebrew, `/opt/homebrew/lib` is NOT in
the default fallback. The `ffi.open` call needs an explicit path on arm64.

Resolution strategy: Use the brew prefix:
```python
if sys.platform == "darwin":
    import subprocess
    try:
        prefix = subprocess.check_output(
            ["brew", "--prefix", "libusb"],
            text=True
        ).strip()
        libusb = ffi.open(prefix + "/lib/libusb-1.0.dylib")
    except Exception:
        # Fallback: try the plain name (works on Intel where /usr/local/lib is searched)
        libusb = ffi.open("libusb-1.0.dylib")
```

Wait — `subprocess` may not be available in MicroPython. A simpler
approach: try both known paths:
```python
if sys.platform == "darwin":
    for _path in [
        "/opt/homebrew/lib/libusb-1.0.dylib",   # arm64 Homebrew
        "/usr/local/lib/libusb-1.0.dylib",        # x64 Homebrew
        "libusb-1.0.dylib",                        # DYLD_LIBRARY_PATH fallback
    ]:
        try:
            libusb = ffi.open(_path)
            break
        except OSError:
            continue
    else:
        raise OSError("libusb-1.0 not found on macOS; run: brew install libusb")
```

This is idiomatic MicroPython (no subprocess) and handles both Homebrew
prefixes.

### `romfs_extract.py` on macOS

The current `extract_to_temp` function has:
```python
if sys.platform != "win32":
    return romfs_path
```

This already handles macOS correctly — it returns the romfs path unchanged
(same as Linux), because `ffi.open` on macOS can load dylibs by name via
dyld without extraction. No change needed to `romfs_extract.py`. This
satisfies FR-EX-MAC-3.

### pydfu Playwright tests on macOS

The pydfu test suite uses a mock USB shim (`@picolet.command` overrides
in `tests/conftest.py`) — it does not require real USB hardware. The
tests should pass on macOS CI without modification, as long as the
macOS webview variant (PH26) is available.

The test suite imports `AppHarness` and uses `browser="webkit"` (or
`"auto"` which resolves to `"webkit"` on macOS/Linux). The webkit path
connects to the WK inspector — all the work for this is in PH25.

## Files to modify

### `examples/pydfu/src/_usb/core.py`

Current platform dispatch:
```python
if sys.platform == "win32":
    ...
    libusb = ffi.open(_dll_path)
else:
    libusb = ffi.open("libusb-1.0.so.0")
```

New dispatch:
```python
if sys.platform == "win32":
    ...
    libusb = ffi.open(_dll_path)
elif sys.platform == "darwin":
    for _path in [
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "libusb-1.0.dylib",
    ]:
        try:
            libusb = ffi.open(_path)
            break
        except OSError:
            pass
    else:
        raise OSError(
            "libusb-1.0 not found; run: brew install libusb"
        )
else:
    libusb = ffi.open("libusb-1.0.so.0")
```

### `packages/picolet-runtime/sbom/runtime.toml`

The existing libusb entry targets `["linux-x64", "windows-x64"]`. Add
the macOS targets:
```toml
[[component]]
name = "libusb"
...
targets = ["linux-x64", "windows-x64", "macos-x64", "macos-arm64"]
variants = ["cli", "webview"]  # pydfu uses webview variant runtime
notes = "... macOS: installed via brew install libusb; loaded from /opt/homebrew/lib/ or /usr/local/lib/"
```

## Integration points

### CI setup for pydfu on macOS

When building or testing the pydfu example on macOS in CI:
```yaml
- name: Install libusb (macOS)
  if: startsWith(matrix.target, 'macos-')
  run: brew install libusb
```

This only applies to the pydfu example test job, not to the runtime
build itself (the runtime binary does not link libusb).

## Testing strategy

1. Run pydfu tests on `macos-x64` in CI with the macos-x64-webview binary:
   ```bash
   brew install libusb
   cd examples/pydfu && pytest tests/ \
     -k "not real_usb" \
     --binary ../../packages/picolet-runtime/build/picolet-runtime-macos-x64-webview
   ```
2. Verify `ffi.open` for libusb succeeds (the test harness logs the load
   path on startup).
3. Check that the mock-USB flash flow test passes end-to-end.

## Success criteria

- [ ] `_usb/core.py` loads `libusb-1.0.dylib` on both macOS x64 and arm64.
- [ ] The load-path fallback loop resolves to the Homebrew installation.
- [ ] pydfu Playwright tests pass on macOS (using mock USB shim).
- [ ] SBOM entry for libusb includes macOS targets.
- [ ] `romfs_extract.py` returns path unchanged on macOS (no extraction).

## Risks

1. **Homebrew path variability**: If a developer has a non-standard
   Homebrew prefix (unusual but possible), the hardcoded paths fail.
   The `"libusb-1.0.dylib"` bare-name fallback handles most cases via
   `DYLD_LIBRARY_PATH`. This is acceptable for v1.2 — the user can always
   set `DYLD_LIBRARY_PATH` as a workaround.

2. **macOS SIP and `DYLD_LIBRARY_PATH`**: System Integrity Protection
   strips `DYLD_LIBRARY_PATH` for binaries in `/usr/` locations. This
   does not affect picolet binaries (they're not in `/usr/`), so the
   fallback via `DYLD_LIBRARY_PATH` is safe.

## Model tier recommendation

planner `sonnet`, developer `sonnet`, sqe `sonnet`, tester `sonnet`.
This is a small code change (three lines of Python) plus CI wiring.
