# pydfu — DFU firmware flasher (picolet example).
#
# Registers five IPC commands consumed by the Vue frontend:
#   list_devices        -> list of DFU device dicts
#   read_dfu            -> parse a .dfu file, return elements list
#   get_memory_layout   -> memory segment list for a device
#   flash               -> start async flash task, emits dfu:progress / dfu:done / dfu:error
#   abort_flash         -> cancel the running flash task
#
# device_id convention: "<bus>:<addr>" string (e.g. "1:1"). O4 in phase plan.
#
# CLI mode (when argv contains a subcommand):
#   pydfu list               — enumerate DFU devices, print, exit
#   pydfu read <file.dfu>    — parse + describe a .dfu file, exit
#   pydfu flash <id> <file>  — flash without GUI (progress to stdout), exit
import asyncio
import os
import sys

from picolet.romfs_extract import extract_dir

# ---------------------------------------------------------------------------
# Windows DLL extraction — MUST run before any module that imports _usb.core,
# because core.py calls ffi.open at module load time and Windows LoadLibrary
# cannot read from the MicroPython romfs VFS.
# picolet.romfs_extract handles the platform check and idempotency; it is a
# no-op on non-Windows.
# ---------------------------------------------------------------------------

_native_lib_dir = extract_dir("/rom/src/_usb", subdir="picolet_pydfu")
if _native_lib_dir != "/rom/src/_usb":  # i.e. we're on Windows and extracted
    import _usb
    _usb._native_lib_dir = _native_lib_dir

# ---------------------------------------------------------------------------
# Pre-import argv scan: handle --mock before pydfu_adapter is imported so
# that the adapter's module-level mock initialisation sees the env var.
# ---------------------------------------------------------------------------
if "--mock" in sys.argv:
    os.putenv("PICOLET_PYDFU_MOCK", "1")

import picolet
import picolet_ui as ui
import pydfu_adapter as dfu


# ---------------------------------------------------------------------------
# CLI help strings
# ---------------------------------------------------------------------------

_TOP_HELP = """\
usage: pydfu [-h] [--mock] [{list,read,flash} ...]

pydfu — DFU firmware flasher (Picolet example).

Run without arguments to open the GUI:

    pydfu

Or use subcommands for headless operation:

    pydfu list                              # enumerate DFU devices
    pydfu read firmware.dfu                 # describe a .dfu file
    pydfu flash 1:5 firmware.dfu            # flash device 1:5

Environment variables:
    PICOLET_PYDFU_MOCK=1          use mock device set (no USB hardware needed)
    PICOLET_PYDFU_MOCK_EMPTY=1    mock with zero devices (for empty-list testing)
    PICOLET_TEST_MODE=1           enable the remote debug port (for picolet test)
    PICOLET_DEV_URL=<url>         load UI from URL instead of romfs (for picolet dev)

positional arguments:
  {list,read,flash}
    list        enumerate DFU devices
    read        parse and describe a .dfu file
    flash       flash a .dfu file to a device

options:
  -h, --help    show this help message and exit
  --mock        force mock device set (overrides PICOLET_PYDFU_MOCK)

For more, see https://github.com/andrewleech/picolet\
"""

_LIST_HELP = """\
usage: pydfu list [-h]

Enumerate USB DFU devices and print them to stdout.

Each line shows: <bus>:<addr>  VID:PID  Manufacturer  Product

options:
  -h, --help    show this help message and exit

Examples:
    pydfu list
    PICOLET_PYDFU_MOCK=1 pydfu list\
"""

_READ_HELP = """\
usage: pydfu read [-h] file.dfu

Parse a DFU file and describe its contents (targets, elements, CRC).

positional arguments:
  file.dfu      path to the .dfu file to inspect

options:
  -h, --help    show this help message and exit

Examples:
    pydfu read firmware.dfu
    pydfu read /path/to/stm32.dfu\
"""

_FLASH_HELP = """\
usage: pydfu flash [-h] device_id file.dfu

Flash a DFU file to a device. Progress is printed to stdout.

positional arguments:
  device_id     device to flash, as "<bus>:<addr>" (from pydfu list)
  file.dfu      path to the .dfu file to flash

options:
  -h, --help    show this help message and exit

Examples:
    pydfu flash 1:5 firmware.dfu
    PICOLET_PYDFU_MOCK=1 pydfu flash 1:1 firmware.dfu\
"""


# ---------------------------------------------------------------------------
# CLI subcommand implementations
# ---------------------------------------------------------------------------

def _cli_list():
    devices = dfu.list_dfu_devices()
    if not devices:
        print("No DFU devices found.")
        return 0
    for d in devices:
        print("{id}  {vid:04X}:{pid:04X}  {manufacturer}  {product}".format(**d))
    return 0


def _cli_read(args):
    if not args or args[0] in ("-h", "--help"):
        print(_READ_HELP)
        return 0
    path = args[0]
    try:
        elements = dfu.read_dfu_file(path)
    except Exception as e:
        print("error: {}".format(e), file=sys.stderr)
        return 1
    if not elements:
        print("No elements found in {}.".format(path))
        return 0
    print("DFU file: {}".format(path))
    print("{} element(s):".format(len(elements)))
    for i, elem in enumerate(elements):
        addr = elem.get("addr", elem.get("address", "?"))
        data = elem.get("data", b"")
        size = len(data) if data else elem.get("size", 0)
        if isinstance(addr, int):
            addr_str = "0x{:08X}".format(addr)
        else:
            addr_str = str(addr)
        print("  [{}] addr={} size={} bytes".format(i, addr_str, size))
    return 0


def _cli_flash(args):
    if not args or args[0] in ("-h", "--help"):
        print(_FLASH_HELP)
        return 0
    if len(args) < 2:
        print("error: flash requires device_id and file.dfu", file=sys.stderr)
        print(_FLASH_HELP, file=sys.stderr)
        return 2
    device_id = args[0]
    path = args[1]

    try:
        elements = dfu.read_dfu_file(path)
    except Exception as e:
        print("error: reading {}: {}".format(path, e), file=sys.stderr)
        return 1

    if not elements:
        print("error: no elements in {}".format(path), file=sys.stderr)
        return 1

    written = [0]

    def _progress(addr, done, total):
        pct = (done * 100) // total if total else 0
        if isinstance(addr, int):
            addr_str = "0x{:08X}".format(addr)
        else:
            addr_str = str(addr)
        print("\rFlashing {}  {}/{}  {}%".format(addr_str, done, total, pct), end="")
        written[0] = done

    print("Flashing {} to device {} ...".format(path, device_id))
    try:
        dfu.flash_device(device_id, elements, _progress)
    except Exception as e:
        print("\nerror: {}".format(e), file=sys.stderr)
        return 1

    print("\nDone. {} bytes written.".format(written[0]))
    return 0


# ---------------------------------------------------------------------------
# CLI argv router
# ---------------------------------------------------------------------------

def _route_cli(argv):
    """Inspect argv and dispatch CLI subcommands. Returns exit code, or None for GUI."""
    # Strip argv[0] (program name); work with the rest.
    args = list(argv[1:]) if len(argv) > 1 else []

    # Remove --mock — already handled before import.
    args = [a for a in args if a != "--mock"]

    if not args:
        return None  # GUI mode

    first = args[0]

    if first in ("-h", "--help"):
        print(_TOP_HELP)
        return 0

    if first == "list":
        rest = args[1:]
        if rest and rest[0] in ("-h", "--help"):
            print(_LIST_HELP)
            return 0
        return _cli_list()

    if first == "read":
        return _cli_read(args[1:])

    if first == "flash":
        return _cli_flash(args[1:])

    # Unknown subcommand — print help, exit with error.
    print("error: unknown subcommand {!r}".format(first), file=sys.stderr)
    print("", file=sys.stderr)
    print(_TOP_HELP, file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# IPC command registration (GUI mode only)
# ---------------------------------------------------------------------------

_flash_task = None


@picolet.command
async def list_devices(args):
    """Enumerate USB DFU devices. Returns list of device dicts."""
    return dfu.list_dfu_devices()


@picolet.command
async def read_dfu(args):
    """Parse a .dfu file. Returns list of element dicts.

    args: {"path": "/path/to/firmware.dfu"} or a bare string path.
    """
    path = args.get("path") if isinstance(args, dict) else args
    if not path:
        return {"ok": False, "error": "no path provided"}
    return dfu.read_dfu_file(path)


@picolet.command
async def get_memory_layout(args):
    """Return memory layout segments for a device.

    args: {"device_id": "<bus>:<addr>"} or bare device_id string.
    """
    device_id = args.get("device_id") if isinstance(args, dict) else args
    return dfu.get_memory_layout(device_id)


@picolet.command
async def flash(args):
    """Start a DFU flash operation asynchronously.

    args: {"device_id": "<bus>:<addr>", "dfu_path": "/path/to/fw.dfu"}

    Returns immediately with {"ok": True, "status": "started"}.
    Progress events: dfu:progress {"addr", "done", "total", "pct"}
    Completion:      dfu:done    {"ok": True}
    Error:           dfu:error   {"message": str}

    Error sentinel: if dfu_path ends with ".error.dfu", a simulated error
    is emitted immediately (used by tests and mock flash error screenshots).
    """
    global _flash_task

    device_id = args.get("device_id") if isinstance(args, dict) else None
    dfu_path = args.get("dfu_path") if isinstance(args, dict) else None

    if not device_id or not dfu_path:
        return {"ok": False, "error": "device_id and dfu_path required"}

    # Error sentinel path — emit error immediately without reading file.
    if str(dfu_path).endswith(".error.dfu"):
        picolet.emit("dfu:error", {"message": "simulated flash error (sentinel path)"})
        return {"ok": False, "error": "simulated error"}

    try:
        elements = dfu.read_dfu_file(dfu_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not elements:
        return {"ok": False, "error": "no elements in dfu file"}

    async def _run():
        loop = asyncio.get_event_loop()

        def _progress(addr, done, total):
            pct = (done * 100) // total if total else 0
            loop.call_soon_threadsafe(
                loop.create_task,
                picolet.emit("dfu:progress", {
                    "addr": addr,
                    "done": done,
                    "total": total,
                    "pct": pct,
                }),
            )

        try:
            await loop.run_in_executor(None, dfu.flash_device, device_id, elements, _progress)
            await picolet.emit("dfu:done", {"ok": True})
        except asyncio.CancelledError:
            await picolet.emit("dfu:error", {"message": "flash cancelled"})
        except Exception as e:
            await picolet.emit("dfu:error", {"message": str(e)})

    _flash_task = asyncio.create_task(_run())
    return {"ok": True, "status": "started"}


@picolet.command
async def abort_flash(args):
    """Abort the running flash task."""
    global _flash_task
    if _flash_task is not None and not _flash_task.done():
        _flash_task.cancel()
    dfu.abort_flash()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    code = _route_cli(sys.argv)
    if code is not None:
        sys.exit(code)

    app = ui.Application()
    app.run()


main()
