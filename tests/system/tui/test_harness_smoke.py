r"""Phase 7 TuiHarness smoke tests against shell-script stand-ins.

These run before the picolet-tui binary exists.  They drive trivial shell
commands attached to a pty and check that:

1. Printable bytes land in the parser's cell grid (the "ground" path).
2. Cursor positioning (`tput cup`) places subsequent characters at the
   right (row, col).
3. SGR attributes (`\e[1;32m`) bake into the Style stored alongside the
   character.

The shell commands here are NOT a picolet-tui app.  Once Phase 5 widgets
ship, the widget smoke tests replace these — they assert against the real
SUT, not `printf`.
"""
from __future__ import annotations

import shutil

import pytest

from picolet.testing import TuiHarness


pytestmark = pytest.mark.asyncio


def _have(*names: str) -> bool:
    """Skip when any required system command is missing from PATH."""
    return all(shutil.which(n) is not None for n in names)


@pytest.mark.skipif(not _have("sh", "printf"), reason="sh/printf required")
async def test_harness_reads_hello() -> None:
    """Smoke 1: printable run lands in the grid.

    Spawns `sh -c 'printf "hello\\r\\n"'` and asserts that "hello" appears
    at row 0, cols 0..4.  Exercises: pty spawn, reader task wiring,
    parser ground state, wait_idle quiescence detection.
    """
    async with TuiHarness(
        "sh",
        args=["-c", 'printf "hello\\r\\n"'],
        timeout=3.0,
    ) as h:
        await h.wait_idle(timeout=2.0, quiet_ms=100)
        assert h.cells_at(0, 0, 5) == "hello"


@pytest.mark.skipif(not _have("sh", "tput", "printf"), reason="sh/tput/printf required")
async def test_harness_cursor_positioning() -> None:
    """Smoke 2: CSI H places subsequent text correctly.

    `tput cup 0 0` emits the cursor-position sequence (typically
    `\\x1b[1;1H` — 1-indexed); the parser must translate to (0, 0)
    internally and the printable "ABC" that follows must land starting
    there.
    """
    async with TuiHarness(
        "sh",
        args=["-c", 'tput cup 0 0; printf "ABC"'],
        timeout=3.0,
    ) as h:
        await h.wait_idle(timeout=2.0, quiet_ms=100)
        assert h.cells_at(0, 0, 1) == "A"
        assert h.cells_at(0, 1, 1) == "B"
        assert h.cells_at(0, 2, 1) == "C"


@pytest.mark.skipif(not _have("sh", "printf"), reason="sh/printf required")
async def test_harness_sgr_style_bake() -> None:
    """Smoke 3: SGR state attaches to subsequent cells, resets on `m 0`.

    Emits `\\x1b[1;32mgreen\\x1b[0m` and asserts cells 0..4 carry (bold,
    green) in their Style.  Without the running-SGR-state tracking in
    the parser, this would always show default style.
    """
    async with TuiHarness(
        "sh",
        args=["-c", 'printf "\\033[1;32mgreen\\033[0m"'],
        timeout=3.0,
    ) as h:
        await h.wait_idle(timeout=2.0, quiet_ms=100)
        assert h.cells_at(0, 0, 5) == "green"
        for col in range(5):
            style = h.style_at(0, col)
            assert style.fg == "green", "cell {} fg was {!r}, expected 'green'".format(col, style.fg)
            assert style.bold is True, "cell {} bold was {!r}".format(col, style.bold)
        # After the `\e[0m` reset, the parser's running-SGR state must
        # have cleared — a subsequent printable would land with default
        # Style.  We don't write anything else, but check the running
        # state via the next cell over (cell 5) which is the blank fill.
        blank_style = h.style_at(0, 5)
        assert blank_style.fg is None
        assert blank_style.bold is False
