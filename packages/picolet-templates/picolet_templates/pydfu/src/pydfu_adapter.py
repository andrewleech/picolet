# pydfu_adapter.py — USB adapter for the pydfu example app.
#
# Provides five functions consumed by main.py's @picolet.command handlers:
#   list_dfu_devices()          -> list of device dicts
#   read_dfu_file(path)         -> list of element dicts or raises ValueError
#   flash_device(id, elems, cb) -> None; calls cb(addr, done, total) per block
#   abort_flash()               -> None
#   get_memory_layout(id)       -> list of segment dicts
#
# The real USB path uses libusb-1.0 via the _usb and _pydfu modules (Linux only).
# When PICOLET_PYDFU_MOCK=1 is set, a MockUSB instance replaces the real path.
# When sys.platform == "win32" and not mocked, NotImplementedError is raised.
#
# DFU protocol reference: USB DFU spec v1.1, STM AN3156, STM UM0391 (DfuSe).
# Algorithm ported from pydfu-win/micropython/tools/pydfu_app/lib/pydfu.py (MIT).
# USB shim ported from pydfu-win/micropython/tools/pydfu_app/lib/usb/core.py (MIT).
#
# O1: Windows WinUSB is deferred; raises NotImplementedError per FR-EX-7 note.

import os
import sys

from _dfu_file import compute_crc, read_dfu_file  # noqa: F401 — re-exported for callers


# ---------------------------------------------------------------------------
# Mock shim — activated by PICOLET_PYDFU_MOCK=1
# ---------------------------------------------------------------------------

_mock = None


def _set_mock(obj):
    global _mock
    _mock = obj


if os.getenv("PICOLET_PYDFU_MOCK") == "1":
    from pydfu_mock import MockUSB
    _impl = MockUSB()
    # PICOLET_PYDFU_MOCK_EMPTY=1 makes the mock return zero devices.
    if os.getenv("PICOLET_PYDFU_MOCK_EMPTY") == "1":
        _impl.MOCK_EMPTY = True
    _set_mock(_impl)


# ---------------------------------------------------------------------------
# libusb guard — used only to validate platform before _usb module loads
# ---------------------------------------------------------------------------

_lib = None  # set after first successful _ensure_lib call (compat sentinel)


def _ensure_lib():
    """Raise NotImplementedError on Windows; ensure _usb module is available on Linux."""
    global _lib
    if _lib is not None:
        return _lib
    if sys.platform == "win32":
        raise NotImplementedError(
            "WinUSB support is post-v1.1 roadmap; see FR-EX-7 in v1.1-spec.md"
        )
    try:
        import _usb.core as _core
        _lib = _core
        return _lib
    except OSError as e:
        raise RuntimeError(
            "libusb-1.0 not found; install libusb-1.0-0: {}".format(e)
        )


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------

def list_dfu_devices():
    """Return list of DFU-mode USB devices as dicts.

    Each dict: {"bus", "addr", "vid", "pid", "manufacturer", "product", "id"}.
    "id" is the canonical "<bus>:<addr>" string used as device_id in other calls.
    """
    if _mock is not None:
        raw = _mock.list_dfu_devices()
        for d in raw:
            d.setdefault("id", "{}:{}".format(d["bus"], d["addr"]))
        return raw
    _ensure_lib()
    import _pydfu.pydfu as _dfu
    devices = _dfu.get_dfu_devices()
    result = []
    for dev in devices:
        # Try to read string descriptors; fall back to empty strings on failure.
        try:
            manufacturer = _dfu.get_string(dev, 1)
        except Exception:
            manufacturer = ""
        try:
            product = _dfu.get_string(dev, 2)
        except Exception:
            product = ""
        d = {
            "bus": dev.bus,
            "addr": dev.address,
            "vid": dev.idVendor,
            "pid": dev.idProduct,
            "manufacturer": manufacturer,
            "product": product,
        }
        d["id"] = "{}:{}".format(d["bus"], d["addr"])
        result.append(d)
    return result


def get_memory_layout(device_id):
    """Return memory layout segments for a device.

    Each segment dict: {"addr", "last_addr", "size", "num_pages", "page_size"}.
    Queries the DFU interface string descriptor on the real USB path.
    """
    if _mock is not None:
        return _mock.get_memory_layout(device_id)
    _ensure_lib()
    import _pydfu.pydfu as _dfu
    import _usb.core as _usb_core
    device = _find_device_by_id(device_id, _usb_core)
    if device is None:
        return []
    return _dfu.get_memory_layout(device)


def _find_device_by_id(device_id, usb_core):
    """Return the DFU Device object matching "<bus>:<addr>", or None.

    Only DFU-mode devices are enumerated (FilterDFU applied) to avoid
    opening config descriptors on unrelated USB peripherals.
    """
    try:
        bus_s, addr_s = device_id.split(":")
        target_bus = int(bus_s)
        target_addr = int(addr_s)
    except (ValueError, AttributeError):
        return None
    from _pydfu.pydfu import FilterDFU
    all_devices = usb_core.find(find_all=True, custom_match=FilterDFU())
    if not all_devices:
        return None
    for dev in all_devices:
        if dev.bus == target_bus and dev.address == target_addr:
            return dev
    return None


# ---------------------------------------------------------------------------
# Flash
# ---------------------------------------------------------------------------

def flash_device(device_id, elements, progress_cb):
    """Flash elements to the DFU device. Calls progress_cb(addr, done, total) per block.

    Intended to be called from a thread executor (run_in_executor) so that the
    asyncio event loop is not blocked during the USB control transfers.
    """
    if _mock is not None:
        return _mock.flash_device(device_id, elements, progress_cb)
    _ensure_lib()
    import _pydfu.pydfu as _dfu
    import _usb.core as _usb_core
    device = _find_device_by_id(device_id, _usb_core)
    if device is None:
        raise ValueError("DFU device not found: {}".format(device_id))
    _dfu.init_device(device)
    _dfu.write_elements(elements, mass_erase_used=False, progress=progress_cb)
    _dfu.exit_dfu()


def abort_flash():
    """Send DFU ABORT request."""
    if _mock is not None:
        return _mock.abort_flash()
    import _pydfu.pydfu as _dfu
    try:
        _dfu.abort_request()
    except Exception:
        pass
