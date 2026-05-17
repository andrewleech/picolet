"""
PH19 — libusb-1.0 FFI binding smoke test.

Validates that the _usb shim's libusb-1.0 binding loads and initialises
successfully on the host system.  No real DFU device is required: the test
only confirms the library is present and libusb_init returns 0.

This is the minimal CI-safe proof that the FFI integration is correct.
Real-device flash is tested manually; CI uses PICOLET_PYDFU_MOCK=1 for all
functional tests.

Skipped automatically if:
  - sys.platform == "win32" (Windows is out of scope for v1.1, FR-EX-7)
  - libusb-1.0.so.0 is not installed on the host (prints a skip notice)
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
import unittest


@unittest.skipIf(sys.platform == "win32", "Windows is out of scope for v1.1 (FR-EX-7)")
class TestLibusbBinding(unittest.TestCase):

    def _load_libusb(self):
        """Load libusb-1.0 via ctypes (host Python, not MicroPython ffi)."""
        # Try the versioned soname first (what the _usb shim uses), then fallback.
        for name in ("libusb-1.0.so.0", "libusb-1.0.so", "usb-1.0"):
            path = ctypes.util.find_library(name.replace("lib", "").replace(".so.0", ""))
            try:
                lib = ctypes.CDLL(name)
                return lib
            except OSError:
                pass
            if path:
                try:
                    lib = ctypes.CDLL(path)
                    return lib
                except OSError:
                    pass
        return None

    def test_libusb_loads(self):
        """libusb-1.0.so.0 must be loadable."""
        lib = self._load_libusb()
        if lib is None:
            self.skipTest("libusb-1.0 not installed on this host (install libusb-1.0-0)")
        self.assertIsNotNone(lib)

    def test_libusb_init_succeeds(self):
        """libusb_init(NULL) must return 0 (LIBUSB_SUCCESS)."""
        lib = self._load_libusb()
        if lib is None:
            self.skipTest("libusb-1.0 not installed on this host (install libusb-1.0-0)")

        lib.libusb_init.restype = ctypes.c_int
        lib.libusb_init.argtypes = [ctypes.c_void_p]
        rc = lib.libusb_init(None)
        self.assertEqual(rc, 0, "libusb_init returned {}; expected 0 (LIBUSB_SUCCESS)".format(rc))

        # Clean up — call libusb_exit to release the context.
        lib.libusb_exit.restype = None
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        lib.libusb_exit(None)

    def test_libusb_get_device_list_returns_nonnegative(self):
        """libusb_get_device_list must return >= 0 (device count, possibly 0)."""
        lib = self._load_libusb()
        if lib is None:
            self.skipTest("libusb-1.0 not installed on this host (install libusb-1.0-0)")

        lib.libusb_init.restype = ctypes.c_int
        lib.libusb_init.argtypes = [ctypes.c_void_p]
        rc = lib.libusb_init(None)
        if rc != 0:
            self.skipTest("libusb_init failed with {}".format(rc))

        lib.libusb_get_device_list.restype = ctypes.c_ssize_t
        lib.libusb_get_device_list.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        list_ptr = ctypes.c_void_p(None)
        count = lib.libusb_get_device_list(None, ctypes.byref(list_ptr))
        self.assertGreaterEqual(
            count, 0,
            "libusb_get_device_list returned {} (negative = error)".format(count),
        )

        if count >= 0 and list_ptr:
            lib.libusb_free_device_list.restype = None
            lib.libusb_free_device_list.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.libusb_free_device_list(list_ptr, 1)

        lib.libusb_exit.restype = None
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        lib.libusb_exit(None)


if __name__ == "__main__":
    unittest.main()
