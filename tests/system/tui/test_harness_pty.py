"""Real picolet-tui binary spawned under TuiHarness (FR-TUI-60..63).

This is the ONLY test in the v0.1 suite that needs the actual compiled
binary.  Every other widget / dispatch / reactive test runs against the
frozen Python sources at packages/picolet-runtime/python/.  Here we
boot the runtime under a pty, send a couple of keystrokes, and read a
frame back — exercising the same wire surface CI's release gate runs.

Gating: the test skips unless the binary is present.  Build it with::

    cd /home/anl/picolet
    ./scripts/build-tui.sh        # or whatever the local build entry is

The path is the one Phase 5 lands at::

    packages/picolet-runtime/build/picolet-runtime-linux-x64-tui

If the binary is built but the test still skips, check ``os.access(...,
os.X_OK)`` on the path — the build artefact must be executable for
``UnixPty`` to ``execvp`` it.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# requires_binary is the gate the v0.1 spec uses for tests that need
# the compiled runtime artefact.  Registering it here (rather than in
# pyproject.toml's pytest config) keeps the gate co-located with the
# tests that consume it.
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.requires_binary,
]


def _find_tui_binary() -> Path | None:
    """Locate the picolet-runtime-linux-x64-tui binary.

    Returns the Path if found and executable; None otherwise.  The
    expected location is set by Phase 5 of the runtime build pipeline;
    we walk up from this test file to the repo root and look under
    packages/picolet-runtime/build/.
    """
    if sys.platform == "win32":
        # TuiHarness does not yet support ConPTY (Phase 8 / v0.2).
        return None
    here = Path(__file__).resolve()
    # tests/system/tui/test_harness_pty.py -> repo root is 4 levels up.
    repo_root = here.parents[3]
    candidate = repo_root / "packages" / "picolet-runtime" / "build" / "picolet-runtime-linux-x64-tui"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return candidate
    return None


# Evaluate once at collection time so the skip reason is informative.
_BINARY = _find_tui_binary()
_NO_BINARY_REASON = (
    "picolet-tui binary not found at packages/picolet-runtime/build/"
    "picolet-runtime-linux-x64-tui — build it with scripts/build-tui.sh"
)


@pytest.mark.skipif(_BINARY is None, reason=_NO_BINARY_REASON)
async def test_harness_spawns_and_reads_a_frame() -> None:
    """Spawn the real binary, give it a trivial script, read output.

    Uses ``-c`` to feed a one-liner so the test does not depend on a
    user script being present in the binary's frozen filesystem.  The
    script writes a known marker and exits; the harness reads the
    bytes through its parser and the cells assertion confirms the
    end-to-end pty -> reader -> parser -> grid path works.

    NB: a full TUI app would block on input; using ``print + exit``
    keeps the test deterministic and short (no SIGTERM dance, no
    risk of the harness deadlocking on a busy event loop).
    """
    # Defer the import: the picolet.testing namespace lives on the host
    # CLI path the conftest set up.  Importing inside the test keeps the
    # module load order explicit (conftest must run first).
    from picolet.testing import TuiHarness

    async with TuiHarness(
        str(_BINARY),
        args=["-c", "print('READY')"],
        timeout=5.0,
    ) as h:
        # The script prints READY\n and exits; wait_idle returns when
        # the byte stream goes quiet for 100 ms.  No DSR-6 round-trip
        # yet (Phase-7 scaffold).
        await h.wait_idle(timeout=3.0, quiet_ms=100)
        # The first five cells of row 0 must spell READY.  The parser
        # interprets a bare print() the same way a terminal would: the
        # bytes land at (0, 0) and advance one cell per character.
        assert h.cells_at(0, 0, 5) == "READY", (
            "expected 'READY' at row 0 col 0..4; got %r" % h.cells_at(0, 0, 5)
        )


@pytest.mark.skipif(_BINARY is None, reason=_NO_BINARY_REASON)
async def test_harness_send_does_not_crash_the_harness() -> None:
    """Sending a keystroke to a quiescent SUT must not throw inside the harness.

    The SUT in this test is a script that does NOT read stdin — it just
    prints and exits.  Sending bytes to its (already-closed) stdin via
    the pty is harmless from the harness's POV; the test pins that
    surface so a regression where ``send`` raises on a dead child does
    not silently break every widget smoke test.
    """
    from picolet.testing import TuiHarness

    async with TuiHarness(
        str(_BINARY),
        args=["-c", "print('alive')"],
        timeout=5.0,
    ) as h:
        await h.wait_idle(timeout=3.0, quiet_ms=100)
        # send() before SUT death: the pty buffer accepts the bytes; the
        # SUT has already exited so the bytes are dropped, but the
        # harness must not raise.  This is the regression guard.
        try:
            await h.send("ignored\n")
        except Exception as exc:
            pytest.fail(
                "TuiHarness.send raised on already-exited SUT: %r" % exc
            )
