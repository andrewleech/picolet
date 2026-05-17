# pydfu

DFU firmware flasher example app built with Picolet.

## USB backend

On Linux the app uses the system libusb-1.0 library (`libusb-1.0-0`) to enumerate
and flash DFU-mode USB devices. Install it with:

```
sudo apt install libusb-1.0-0
```

On Windows, real-USB support is not implemented in v1.1. Attempting to enumerate
or flash a device on Windows raises `NotImplementedError` (FR-EX-7).

## Mock mode

Set `PICOLET_PYDFU_MOCK=1` to replace the USB backend with a deterministic software
mock. The mock simulates one STM32 DFU device (VID 0x0483, PID 0xDF11) and a
synthetic flash operation. All tests and screenshot generation use the mock.

```
PICOLET_PYDFU_MOCK=1 ./target/linux-x64/pydfu
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
picolet build
```

The binary is written to `target/linux-x64/pydfu`.

## Tests

Unit tests (pure Python, no device required):

```
pytest tests/phase-19/
```

Integration smoke tests (requires the built binary):

```
bash tests/phase-19/run.sh --skip-slow
```
