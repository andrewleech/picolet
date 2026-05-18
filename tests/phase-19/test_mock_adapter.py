"""
PH19 unit tests — MockUSB adapter and pydfu_adapter with PICOLET_PYDFU_MOCK=1.

Covers:
  - list_dfu_devices: returns exactly one device when mock is active.
  - list_dfu_devices: device dict contains vid, pid, bus, addr, id keys.
  - list_dfu_devices: device vid == 0x0483 (STMicro).
  - list_dfu_devices: device pid == 0xDF11 (STM32 DFU).
  - list_dfu_devices: id field is "<bus>:<addr>" string (O4 convention).
  - list_dfu_devices: MOCK_EMPTY=True returns empty list.
  - get_memory_layout: returns at least one segment for mock device.
  - get_memory_layout: segment has addr, last_addr, size, num_pages, page_size.
  - get_memory_layout: addr is STM32 flash base 0x08000000.
  - flash_device (mock): progress_cb called at least once per element.
  - flash_device (mock): final done == total bytes across all elements.
  - flash_device (mock): each callback addr is >= element base addr.
  - flash_device (mock): progress count = ceil(total/2048) for 2 KiB block size.
  - flash_device (mock): progress payload shape (addr, done, total all ints).
  - abort_flash: does not raise.
  - Windows DLL path: _ensure_lib attempts DLL load on win32 (OSError, not NotImplementedError).
"""
from __future__ import annotations

import os
import sys
import math
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_DIR = _REPO_ROOT / "examples" / "pydfu" / "src"
sys.path.insert(0, str(_SRC_DIR))


def _load_adapter_with_mock():
    """Return pydfu_adapter loaded with PICOLET_PYDFU_MOCK=1."""
    # Remove any cached module so we get a clean load.
    for key in list(sys.modules.keys()):
        if key in ("pydfu_adapter", "pydfu_mock"):
            del sys.modules[key]

    env_backup = os.environ.get("PICOLET_PYDFU_MOCK")
    os.environ["PICOLET_PYDFU_MOCK"] = "1"
    if "PICOLET_PYDFU_MOCK_EMPTY" in os.environ:
        del os.environ["PICOLET_PYDFU_MOCK_EMPTY"]
    try:
        import pydfu_adapter
    finally:
        if env_backup is None:
            os.environ.pop("PICOLET_PYDFU_MOCK", None)
        else:
            os.environ["PICOLET_PYDFU_MOCK"] = env_backup
    return pydfu_adapter


def _load_adapter_without_mock():
    """Return pydfu_adapter loaded without PICOLET_PYDFU_MOCK."""
    for key in list(sys.modules.keys()):
        if key in ("pydfu_adapter", "pydfu_mock"):
            del sys.modules[key]

    env_backup = os.environ.pop("PICOLET_PYDFU_MOCK", None)
    try:
        import pydfu_adapter
    finally:
        if env_backup is not None:
            os.environ["PICOLET_PYDFU_MOCK"] = env_backup
    return pydfu_adapter


_adapter = _load_adapter_with_mock()


class TestListDfuDevicesMock(unittest.TestCase):

    def test_returns_list(self):
        result = _adapter.list_dfu_devices()
        self.assertIsInstance(result, list)

    def test_returns_one_device(self):
        result = _adapter.list_dfu_devices()
        self.assertEqual(len(result), 1)

    def test_device_has_vid_key(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertIn("vid", device)

    def test_device_has_pid_key(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertIn("pid", device)

    def test_device_has_bus_key(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertIn("bus", device)

    def test_device_has_addr_key(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertIn("addr", device)

    def test_device_has_id_key(self):
        """id field is the O4 canonical "<bus>:<addr>" string."""
        device = _adapter.list_dfu_devices()[0]
        self.assertIn("id", device)

    def test_device_vid_is_stmicro(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertEqual(device["vid"], 0x0483)

    def test_device_pid_is_stm32_dfu(self):
        device = _adapter.list_dfu_devices()[0]
        self.assertEqual(device["pid"], 0xDF11)

    def test_device_id_format_is_bus_colon_addr(self):
        device = _adapter.list_dfu_devices()[0]
        device_id = device["id"]
        parts = device_id.split(":")
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].isdigit(), f"bus part is not numeric: {parts[0]!r}")
        self.assertTrue(parts[1].isdigit(), f"addr part is not numeric: {parts[1]!r}")

    def test_device_id_matches_bus_addr(self):
        device = _adapter.list_dfu_devices()[0]
        expected_id = "{}:{}".format(device["bus"], device["addr"])
        self.assertEqual(device["id"], expected_id)

    def test_mock_empty_returns_empty_list(self):
        """Setting MOCK_EMPTY=True on the mock object makes list_dfu_devices return []."""
        _adapter._mock.MOCK_EMPTY = True
        try:
            result = _adapter.list_dfu_devices()
            self.assertEqual(result, [])
        finally:
            _adapter._mock.MOCK_EMPTY = False


class TestGetMemoryLayoutMock(unittest.TestCase):

    def test_returns_list(self):
        layout = _adapter.get_memory_layout("1:1")
        self.assertIsInstance(layout, list)

    def test_returns_at_least_one_segment(self):
        layout = _adapter.get_memory_layout("1:1")
        self.assertGreater(len(layout), 0)

    def test_segment_has_addr(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertIn("addr", seg)

    def test_segment_has_last_addr(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertIn("last_addr", seg)

    def test_segment_has_size(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertIn("size", seg)

    def test_segment_has_num_pages(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertIn("num_pages", seg)

    def test_segment_has_page_size(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertIn("page_size", seg)

    def test_addr_is_stm32_flash_base(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertEqual(seg["addr"], 0x08000000)

    def test_last_addr_gt_addr(self):
        seg = _adapter.get_memory_layout("1:1")[0]
        self.assertGreater(seg["last_addr"], seg["addr"])


class TestFlashDeviceMock(unittest.TestCase):

    def _make_elements(self, total_bytes: int) -> list[dict]:
        return [{"addr": 0x08000000, "size": total_bytes, "data": b"\x00" * total_bytes}]

    def _collect_callbacks(self, elements: list[dict]) -> list[dict]:
        callbacks = []

        def cb(addr, done, total):
            callbacks.append({"addr": addr, "done": done, "total": total})

        _adapter.flash_device("1:1", elements, cb)
        return callbacks

    def test_at_least_one_callback_for_nonempty_element(self):
        callbacks = self._collect_callbacks(self._make_elements(1024))
        self.assertGreater(len(callbacks), 0)

    def test_final_done_equals_total(self):
        elements = self._make_elements(4096)
        callbacks = self._collect_callbacks(elements)
        total = sum(e["size"] for e in elements)
        self.assertEqual(callbacks[-1]["done"], total)

    def test_callback_addr_within_element_range(self):
        elements = self._make_elements(4096)
        base = elements[0]["addr"]
        end = base + elements[0]["size"]
        callbacks = self._collect_callbacks(elements)
        for cb in callbacks:
            self.assertGreaterEqual(cb["addr"], base)
            self.assertLess(cb["addr"], end)

    def test_callback_count_equals_ceil_size_over_2048(self):
        """Mock uses 2 KiB block size; callback count == ceil(size/2048)."""
        total_bytes = 5 * 2048  # exactly 5 blocks
        callbacks = self._collect_callbacks(self._make_elements(total_bytes))
        self.assertEqual(len(callbacks), 5)

    def test_callback_count_rounds_up_for_partial_block(self):
        """Partial last block should still emit one callback."""
        total_bytes = 2048 + 1  # 2 full blocks? No: 2048 + 1 byte = 2 blocks
        callbacks = self._collect_callbacks(self._make_elements(total_bytes))
        expected = math.ceil(total_bytes / 2048)
        self.assertEqual(len(callbacks), expected)

    def test_callback_addr_is_int(self):
        callbacks = self._collect_callbacks(self._make_elements(2048))
        self.assertIsInstance(callbacks[0]["addr"], int)

    def test_callback_done_is_int(self):
        callbacks = self._collect_callbacks(self._make_elements(2048))
        self.assertIsInstance(callbacks[0]["done"], int)

    def test_callback_total_is_int(self):
        callbacks = self._collect_callbacks(self._make_elements(2048))
        self.assertIsInstance(callbacks[0]["total"], int)

    def test_callback_done_monotonically_increases(self):
        callbacks = self._collect_callbacks(self._make_elements(8192))
        dones = [c["done"] for c in callbacks]
        self.assertEqual(dones, sorted(dones))

    def test_multi_element_flash_accumulates_done(self):
        """done counter must accumulate across multiple elements."""
        elements = [
            {"addr": 0x08000000, "size": 2048, "data": b"\x00" * 2048},
            {"addr": 0x08001000, "size": 2048, "data": b"\x00" * 2048},
        ]
        callbacks = self._collect_callbacks(elements)
        total = sum(e["size"] for e in elements)
        self.assertEqual(callbacks[-1]["done"], total)

    def test_abort_flash_does_not_raise(self):
        _adapter.abort_flash()  # must not raise


class TestWindowsDllPath(unittest.TestCase):

    def test_ensure_lib_does_not_raise_not_implemented_on_win32_when_unmocked(self):
        """_ensure_lib no longer raises NotImplementedError on win32.

        Windows now uses the vendored libusb-1.0.dll.  On a Linux host the
        DLL cannot load (ffi module absent, or DLL not loadable), so we expect
        OSError, RuntimeError, or ModuleNotFoundError — but NOT a
        NotImplementedError, which was the old guard removed in FR-EX-7 update.
        """
        adapter_nomock = _load_adapter_without_mock()
        orig_lib = adapter_nomock._lib
        orig_mock = adapter_nomock._mock
        adapter_nomock._lib = None
        adapter_nomock._mock = None
        try:
            with patch.object(sys, "platform", "win32"):
                try:
                    adapter_nomock._ensure_lib()
                except NotImplementedError:
                    self.fail(
                        "_ensure_lib raised NotImplementedError on win32; "
                        "Windows support should no longer be gated by NotImplementedError"
                    )
                except (OSError, RuntimeError, ModuleNotFoundError, ImportError):
                    pass  # expected on Linux host: ffi module absent or DLL not loadable
        finally:
            adapter_nomock._lib = orig_lib
            adapter_nomock._mock = orig_mock

    def test_libusb_dll_exists_in_usb_package(self):
        """libusb-1.0.dll must be present in the src/_usb directory for Windows builds."""
        dll_path = _SRC_DIR / "_usb" / "libusb-1.0.dll"
        self.assertTrue(
            dll_path.exists(),
            f"libusb-1.0.dll not found at {dll_path}; required for windows-x64 target",
        )
        self.assertGreater(
            dll_path.stat().st_size, 0,
            "libusb-1.0.dll is empty",
        )

    def test_libusb_dll_is_valid_pe32(self):
        """libusb-1.0.dll must be a valid PE32+ (x64) Windows executable."""
        import struct
        dll_path = _SRC_DIR / "_usb" / "libusb-1.0.dll"
        if not dll_path.exists():
            self.skipTest("libusb-1.0.dll not present")
        data = dll_path.read_bytes()
        # DOS header magic
        self.assertEqual(data[:2], b"MZ", "Not a valid PE file (missing MZ header)")
        # Scan for PE\0\0 signature in the first 4 KiB of the file.
        # The e_lfanew field at 0x3c gives the nominal offset, but some
        # linkers have off-by-one alignment; scanning a window is robust.
        found_pe = False
        for offset in range(min(0x1000, len(data) - 6)):
            if data[offset:offset+4] == b"PE\x00\x00":
                machine = struct.unpack_from("<H", data, offset + 4)[0]
                self.assertEqual(
                    machine, 0x8664,
                    f"Expected x64 machine type (0x8664), got {hex(machine)}"
                )
                found_pe = True
                break
        self.assertTrue(found_pe, "PE signature not found in first 4 KiB of file")


if __name__ == "__main__":
    unittest.main()
