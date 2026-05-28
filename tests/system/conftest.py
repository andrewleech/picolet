"""pytest conftest for tests/system/.

Adds packages/picolet-runtime/python/ to sys.path so the tests can import
picolet.system and picolet_ui._win_events without a frozen build.

Also installs a minimal `ffi` and `uctypes` shim under sys.modules so the
_win_events module is importable on CPython — the tests never actually
call into the FFI surface; they inject a mock backend at the picolet.system
layer, which sits one level above the FFI binding.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-runtime" / "python"))


# Stub `ffi` and `uctypes` so importing picolet_ui._win_events on CPython does
# not raise.  These modules are MicroPython-only at runtime.  The tests never
# invoke any of the FFI thunks — they substitute the WinBackend.

class _FakeFFI:
    def open(self, _name):
        class _Handle:
            def func(self, *_args, **_kw):
                raise RuntimeError("FFI call attempted on CPython test host")
        return _Handle()


class _FakeUctypes:
    @staticmethod
    def bytes_at(addr, n):
        raise RuntimeError("uctypes.bytes_at called on CPython test host")

    @staticmethod
    def addressof(obj):
        return id(obj)


if "ffi" not in sys.modules:
    sys.modules["ffi"] = _FakeFFI()
if "uctypes" not in sys.modules:
    sys.modules["uctypes"] = _FakeUctypes()
