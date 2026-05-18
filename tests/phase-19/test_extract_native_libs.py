"""
PH19 — verify pydfu main.py uses picolet.romfs_extract instead of the old
local _extract_native_libs() function.

Tests:
  - The old _extract_native_libs function is gone from main.py.
  - main.py imports extract_dir from picolet.romfs_extract.
  - The extract_dir call and the _usb._native_lib_dir assignment both
    precede 'import pydfu_adapter' (import-order invariant).
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_PICOLET_PYTHON = _REPO_ROOT / "packages" / "picolet-runtime" / "python"
if str(_PICOLET_PYTHON) not in sys.path:
    sys.path.insert(0, str(_PICOLET_PYTHON))

MAIN_PY = _REPO_ROOT / "examples" / "pydfu" / "src" / "main.py"


class TestPydfuUsesSharedExtract(unittest.TestCase):

    def test_old_function_removed(self):
        """_extract_native_libs must no longer be defined in main.py."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)
        func_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertNotIn(
            "_extract_native_libs",
            func_names,
            "_extract_native_libs function should have been removed from main.py",
        )

    def test_imports_extract_dir_from_romfs_extract(self):
        """main.py must import extract_dir from picolet.romfs_extract."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "picolet.romfs_extract":
                    names = [alias.name for alias in node.names]
                    if "extract_dir" in names:
                        found = True
                        break
        self.assertTrue(
            found,
            "main.py must contain 'from picolet.romfs_extract import extract_dir'",
        )

    def test_import_order_extract_before_pydfu_adapter(self):
        """The extract_dir call and _usb attr assignment must appear before
        'import pydfu_adapter' (by source line number)."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)

        extract_dir_import_line = None
        usb_attr_line = None
        pydfu_adapter_line = None

        for node in ast.walk(tree):
            # from picolet.romfs_extract import extract_dir
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "picolet.romfs_extract"
            ):
                extract_dir_import_line = node.lineno

            # _usb._native_lib_dir = _native_lib_dir
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and node.targets[0].attr == "_native_lib_dir"
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "_usb"
            ):
                usb_attr_line = node.lineno

            # import pydfu_adapter as dfu
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pydfu_adapter":
                        pydfu_adapter_line = node.lineno

        self.assertIsNotNone(
            extract_dir_import_line,
            "'from picolet.romfs_extract import extract_dir' not found in main.py",
        )
        self.assertIsNotNone(
            usb_attr_line,
            "_usb._native_lib_dir assignment not found in main.py",
        )
        self.assertIsNotNone(
            pydfu_adapter_line,
            "'import pydfu_adapter' not found in main.py",
        )

        self.assertLess(
            extract_dir_import_line,
            pydfu_adapter_line,
            "romfs_extract import (line {}) must precede 'import pydfu_adapter' (line {})".format(
                extract_dir_import_line, pydfu_adapter_line
            ),
        )
        self.assertLess(
            usb_attr_line,
            pydfu_adapter_line,
            "_usb._native_lib_dir assignment (line {}) must precede 'import pydfu_adapter' (line {})".format(
                usb_attr_line, pydfu_adapter_line
            ),
        )

    def test_noop_on_non_windows(self):
        """On Linux, extract_dir('/rom/src/_usb') returns the input unchanged."""
        if sys.platform == "win32":
            self.skipTest("non-Windows only")

        # romfs_extract checks sys.platform at call time, not at import time.
        import picolet.romfs_extract as rext
        result = rext.extract_dir("/rom/src/_usb", subdir="picolet_pydfu")
        self.assertEqual(
            result,
            "/rom/src/_usb",
            "extract_dir must return input unchanged on non-Windows",
        )


if __name__ == "__main__":
    unittest.main()
