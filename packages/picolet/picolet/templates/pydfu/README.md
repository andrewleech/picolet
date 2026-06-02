# pydfu

DFU firmware flasher example app built with Picolet.

## USB backend

### Linux

The app uses the system libusb-1.0 library (`libusb-1.0-0`). Install it with:

```
sudo apt install libusb-1.0-0
```

The `ffi` module opens `libusb-1.0.so.0` at runtime.

### Windows

A vendored `libusb-1.0.dll` (x64, LGPL-2.1-or-later, v1.0.26) is bundled in the
app romfs at `/rom/src/_usb/libusb-1.0.dll`. No separate installation is needed.

On first run, `main.py` extracts the DLL from romfs to `%TEMP%\picolet_pydfu\libusb-1.0.dll`
before any USB import occurs. Windows `LoadLibrary` requires a real filesystem path and
cannot load from the MicroPython romfs VFS, so the extraction step is mandatory.
The extracted file is reused on subsequent runs (idempotent). Delete
`%TEMP%\picolet_pydfu\` to force re-extraction.

The same libusb API is used on both platforms — the DFU protocol implementation
is identical. No WinUSB driver is required; libusb handles driver selection.

### macOS

The app uses libusb-1.0 installed via Homebrew. Install it with:

```
brew install libusb
```

The `ffi` module searches for the dylib in the following order at runtime:
1. `/opt/homebrew/lib/libusb-1.0.dylib` — Apple Silicon (arm64) Homebrew prefix
2. `/usr/local/lib/libusb-1.0.dylib` — Intel (x64) Homebrew prefix
3. Bare name `libusb-1.0.dylib` — resolved via `DYLD_LIBRARY_PATH`

No DLL extraction step is needed on macOS; dyld resolves the dylib path directly
from the filesystem. If libusb is not found, the app exits with an error message
indicating `brew install libusb`.

## Mock mode

Set `PICOLET_PYDFU_MOCK=1` to replace the USB backend with a deterministic software
mock. The mock simulates one STM32 DFU device (VID 0x0483, PID 0xDF11) and a
synthetic flash operation. All tests and screenshot generation use the mock.

```
PICOLET_PYDFU_MOCK=1 ./target/linux-x64/pydfu          # Linux
PICOLET_PYDFU_MOCK=1 ./target/windows-x64/pydfu.exe    # Windows (or WSL interop)
```

Set `PICOLET_PYDFU_MOCK_EMPTY=1` (with `PICOLET_PYDFU_MOCK=1`) to simulate no devices
connected (tests the empty-state UI).

## Source layout

```
src/
  main.py            — @picolet.command handlers; wires IPC to adapter
  pydfu_adapter.py   — USB adapter: real-USB or mock depending on env
  pydfu_mock.py      — mock USB implementation
  _crc32.py          — pure-Python CRC32 fallback
  _usb/              — pyusb-compatible shim over libusb-1.0 (MicroPython ffi)
      __init__.py
      core.py        — Device/Configuration/Interface classes + find()
      control.py     — get_descriptor helper
      util.py        — claim_interface, get_string, dispose_resources
      libusb-1.0.dll — vendored Windows x64 DLL (LGPL-2.1-or-later, v1.0.26)
  _pydfu/            — DFU protocol implementation
      __init__.py
      pydfu.py       — enumerate, init, write_elements, exit_dfu
```

The `_usb/` and `_pydfu/` directories are app-internal (underscore prefix). They
are ported from the pydfu-win submodule (`micropython/tools/pydfu_app/lib/`) and
carry the OpenMV MIT licence. See the copyright header at the top of each file.

## Building

```
cd examples/pydfu
npm install
picolet build                        # linux-x64 (host auto-detected)
picolet build --target windows-x64   # cross-compile via dockcross
```

The binary is written to `target/<target>/pydfu` (Linux) or
`target/windows-x64/pydfu.exe` (Windows).

## Tests

Unit tests (pure Python, no device required):

```
pytest tests/phase-19/
```

Integration smoke tests (requires the built binary):

```
bash tests/phase-19/run.sh --skip-slow
```
