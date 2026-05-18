"""test_mac_ffi_imports.py — import-time smoke test for _mac_ffi.py (PH25).

Skips with a clear message on non-Darwin platforms (the Linux dev host
cannot resolve picolet_wkwv_* symbols from ffi.open(None)).

On a Darwin host with a picolet-runtime-macos-{x64,arm64}-webview binary,
run inside that binary so ffi.open(None) resolves the C overlay symbols.
"""

import sys
import unittest


@unittest.skipUnless(sys.platform == "darwin", "macOS-only (requires Darwin host)")
class TestMacFFIImports(unittest.TestCase):
    """Verify _mac_ffi module structure and symbol presence."""

    def test_module_importable(self):
        """_mac_ffi imports without raising ImportError."""
        try:
            from picolet_ui import _mac_ffi  # noqa: F401
        except ImportError as e:
            self.fail("_mac_ffi import raised ImportError: {}".format(e))

    def test_self_bin_present(self):
        """self_bin is set (ffi.open(None) succeeded)."""
        from picolet_ui import _mac_ffi
        self.assertIsNotNone(_mac_ffi.self_bin)

    def test_all_symbols_bound(self):
        """All expected picolet_wkwv_* symbols are bound as callable objects."""
        from picolet_ui import _mac_ffi
        expected = [
            "picolet_wkwv_init",
            "picolet_wkwv_create_window",
            "picolet_wkwv_show_window",
            "picolet_wkwv_destroy_window",
            "picolet_wkwv_create_webview",
            "picolet_wkwv_load_html",
            "picolet_wkwv_load_url",
            "picolet_wkwv_evaluate_js",
            "picolet_wkwv_register_message_handler",
            "picolet_wkwv_poll_inbound",
            "picolet_wkwv_free_inbound",
            "picolet_wkwv_register_scheme_handler",
            "picolet_wkwv_scheme_respond",
            "picolet_wkwv_scheme_error",
            "picolet_wkwv_pump_messages",
            "picolet_wkwv_take_snapshot",
            "picolet_wkwv_enable_inspector",
            "picolet_wkwv_pick_test_port",
        ]
        for name in expected:
            self.assertTrue(
                hasattr(_mac_ffi, name),
                "_mac_ffi missing symbol: {}".format(name),
            )
            sym = getattr(_mac_ffi, name)
            self.assertIsNotNone(sym, "{} is None".format(name))

    def test_ffi_string_helper_present(self):
        """ffi_string helper is exported."""
        from picolet_ui import _mac_ffi
        self.assertTrue(callable(_mac_ffi.ffi_string))


if __name__ == "__main__":
    unittest.main()
