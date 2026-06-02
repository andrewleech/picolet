"""pytest conftest for tests/system/.

The tests in this dir need `import picolet.system` resolved against the
frozen-in-binary sources at packages/picolet-runtime/python/picolet/.
pyproject.toml's global pythonpath puts packages/picolet (the host CLI)
first, so by default `import picolet` finds the CLI's namespace — which
has no `.system` submodule.

This conftest prepends the runtime path AT MODULE LOAD (the moment
pytest enters this directory), and registers a session-scoped finalizer
that pops it again on test-session teardown.  That way running
tests/system and tests/phase-* in the same pytest invocation doesn't
leave sys.path permanently shadowed.

Also installs a minimal `ffi` and `uctypes` shim under sys.modules so
the _win_events module is importable on CPython — the tests never
actually call into the FFI surface; they inject a mock backend at the
picolet.system layer, which sits one level above the FFI binding.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_RUNTIME_PATH = str(_REPO_ROOT / "packages" / "picolet-runtime" / "python")

# Prepend so `import picolet` resolves to the frozen-runtime sources,
# not the CLI host package which lives at packages/picolet/picolet/.
if _RUNTIME_PATH not in sys.path:
    sys.path.insert(0, _RUNTIME_PATH)

# Evict any cached host-CLI `picolet` module that was imported before
# this conftest ran (e.g. via pyproject.toml's global pythonpath).
for _mod_name in list(sys.modules):
    if _mod_name == "picolet" or _mod_name.startswith("picolet."):
        # Don't evict picolet_cli / picolet_ui / picolet_tui — those are
        # different top-level names.
        if not _mod_name.startswith(("picolet_cli", "picolet_ui", "picolet_tui")):
            del sys.modules[_mod_name]


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
