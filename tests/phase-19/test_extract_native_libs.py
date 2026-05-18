"""
PH19 — smoke test for _extract_native_libs in pydfu main.py.

Verifies:
  - The function exists and is callable.
  - On non-Windows (Linux), returns None without raising.
  - The import order in main.py places the extraction and _usb attribute
    assignment before any pydfu_adapter import.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

MAIN_PY = Path(__file__).parent.parent.parent / "examples" / "pydfu" / "src" / "main.py"


class TestExtractNativeLibsExists(unittest.TestCase):

    def test_function_defined_in_main(self):
        """_extract_native_libs must be defined at module top in main.py."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)
        func_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn(
            "_extract_native_libs",
            func_names,
            "_extract_native_libs function not found in main.py",
        )

    def test_noop_on_non_windows(self):
        """On Linux the function must return None without side effects."""
        if sys.platform == "win32":
            self.skipTest("non-Windows only")

        # Execute only the function definition by compiling the source and
        # pulling the function object out without running module-level code.
        src = MAIN_PY.read_text()
        tree = ast.parse(src)

        # Extract just the function definition node.
        func_def = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_extract_native_libs":
                func_def = node
                break
        self.assertIsNotNone(func_def, "_extract_native_libs FunctionDef not found")

        # Compile and exec only that function definition; inject the stdlib
        # names the function uses so it runs without importing main.py fully.
        import os as _os
        module = ast.Module(body=[func_def], type_ignores=[])
        code = compile(module, str(MAIN_PY), "exec")
        ns: dict = {"sys": sys, "os": _os}
        exec(code, ns)  # noqa: S102

        fn = ns["_extract_native_libs"]
        result = fn()
        self.assertIsNone(result, "expected None on non-Windows, got {!r}".format(result))

    def test_import_order_extract_before_pydfu_adapter(self):
        """The _extract_native_libs call and _usb attr assignment must appear
        before 'import pydfu_adapter' in main.py (by source line number)."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)

        extract_call_line = None
        usb_attr_line = None
        pydfu_adapter_line = None

        for node in ast.walk(tree):
            # _native_lib_dir = _extract_native_libs()
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_native_lib_dir"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "_extract_native_libs"
            ):
                extract_call_line = node.lineno

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

        self.assertIsNotNone(extract_call_line, "_native_lib_dir = _extract_native_libs() call not found")
        self.assertIsNotNone(usb_attr_line, "_usb._native_lib_dir assignment not found")
        self.assertIsNotNone(pydfu_adapter_line, "import pydfu_adapter not found")

        self.assertLess(
            extract_call_line,
            pydfu_adapter_line,
            "_extract_native_libs() call (line {}) must precede 'import pydfu_adapter' (line {})".format(
                extract_call_line, pydfu_adapter_line
            ),
        )
        self.assertLess(
            usb_attr_line,
            pydfu_adapter_line,
            "_usb._native_lib_dir assignment (line {}) must precede 'import pydfu_adapter' (line {})".format(
                usb_attr_line, pydfu_adapter_line
            ),
        )


if __name__ == "__main__":
    unittest.main()
