"""The built tui-pydfu example binary must launch and render a frame.

This is the gate every earlier phase verifier lacked: they confirmed
``picolet build`` produced a file and stopped there.  The frozen-mpy
compile step is syntax-only, so an entire class of MicroPython runtime
incompatibilities (nested classes, dict ordering, tuple.__new__,
function attributes, missing builtins) shipped inside binaries that had
never executed a single line.  This test boots the real artifact under
a PTY and asserts the actual first frame of the actual example app.

Run ``picolet build --target linux-x64`` in examples/tui-pydfu to
produce the binary; the test skips (loudly, with the path it wanted)
when the artifact is absent so unit-only CI laps stay green.

The ``test_harness_`` filename prefix matters: tests/mp/run.sh
excludes that prefix from the MicroPython gate — this file drives the
built binary from the outside via a CPython PTY and must not be
imported under MicroPython.

Windows note: the windows-x64 exe cannot be frame-verified from WSL —
interop hands it pipe stdio, never a Windows console, so the driver
correctly refuses with E_HANDLE.  ConPTY harness support is v0.2
(NFR-TUI-3); until then windows verification stops at "imports the
full framework and reaches driver enable".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.requires_binary,
]


def _find_example_binary() -> Path | None:
    if sys.platform == "win32":
        return None
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    candidate = (
        repo_root / "examples" / "tui-pydfu" / "target" / "linux-x64" / "tui-pydfu"
    )
    if candidate.exists() and os.access(candidate, os.X_OK):
        return candidate
    return None


_BINARY = _find_example_binary()
_NO_BINARY_REASON = (
    "tui-pydfu binary not found at examples/tui-pydfu/target/linux-x64/ — "
    "build it with `picolet build --target linux-x64` in examples/tui-pydfu"
)


@pytest.mark.skipif(_BINARY is None, reason=_NO_BINARY_REASON)
async def test_tui_pydfu_binary_renders_first_frame() -> None:
    """Boot the built example, read the first frame, check real content.

    PICOLET_PYDFU_MOCK=1 selects the mock DFU adapter so the test never
    touches USB.  The assertions pin the three landmarks of the v0.1
    layout: the device-list header, the mock STM32 device row, and the
    flash-view footer widgets.  Loose containment (not cell-exact)
    so cosmetic layout changes don't break the gate — its job is
    "the binary executes and composes a real frame", not pixel parity.
    """
    from picolet.testing import TuiHarness

    async with TuiHarness(
        str(_BINARY),
        env={"PICOLET_PYDFU_MOCK": "1"},
        timeout=10.0,
    ) as h:
        await h.wait_idle(timeout=5.0, quiet_ms=200)
        screen = "\n".join(
            h.cells_at(row, 0, 80) for row in range(24)
        )
        assert "Detected DFU devices:" in screen, (
            "device-list header missing from first frame:\n%s" % screen
        )
        assert "0483:DF11" in screen, (
            "mock STM32 DFU device row missing from first frame:\n%s" % screen
        )
        assert "Flash" in screen, (
            "flash-view footer missing from first frame:\n%s" % screen
        )
