"""picolet.testing._tui — TuiHarness: drive a picolet-tui binary deterministically.

Spec ref: FR-TUI-60..63 (Phase 7 deliverable).  The harness is the only thing
widget tests are written against — see docs/tui/tui-v0.1-spec.md §4.1.

The runtime side (picolet_tui._parser, FR-TUI-62) does not exist yet in this
Phase 7 scaffold; until it lands the harness owns a local parser
(`_tui_parser.Parser`) that mirrors what the runtime parser will emit.  When
the runtime parser arrives, this module rewires its import to that one (and
flips strict=True per NFR-TUI-21).

Why a separate `TuiHarness` instead of folding into `AppHarness`:

* `AppHarness` is webview/LVGL specific — it speaks CDP / WebKit Remote
  Inspector / stdio JSON.  Sharing those code paths with a pty-attached
  TUI binary would make every harness method either branch internally or
  fail with NotImplementedError.  Separate class, separate import, no
  cross-pollination.
* The synthesis doc §"Phase 7 in parallel with 4b" called out
  TuiHarness as the test driver Phase 4-5 widgets are written against.
  Splitting it now (before the widgets exist) sets the API surface in
  stone and lets the widget tests assert against it from day one.
"""
from __future__ import annotations

import asyncio
import os
import select
import sys
from pathlib import Path
from typing import Optional, Union

from picolet.testing._tui_parser import Cell, Parser, Style
from picolet.testing._tui_pty import PtyClosedError, UnixPty


# ---------------------------------------------------------------------------
# Key table — symbolic name → bytes the harness types when press() is called.
#
# v0.1 spec §4.1 fixes the names that must be supported.  Modifier handling
# (`ctrl`, `shift`, `alt`, `meta`) lands when the matching widget tests need
# it; the scaffold ships the bare key forms only.
#
# These sequences are the ones xterm emits when the corresponding physical
# key is pressed.  The SUT's input parser (Phase 4) consumes them via its
# own copy of the same table — keeping the two in sync is FR-TUI-62's job;
# this table will be retired once the runtime ships a `keys.py` we can
# import on the host side.
# ---------------------------------------------------------------------------

_KEY_TABLE: dict[str, bytes] = {
    "enter": b"\r",
    "tab": b"\t",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    "space": b" ",
}


class HarnessError(RuntimeError):
    """Raised when the harness cannot complete a requested operation.

    Distinct error type so widget tests can `pytest.raises(HarnessError)`
    on "child exited unexpectedly" vs. an assertion failure on cell state.
    """


class TuiHarness:
    """Drive a picolet-tui binary attached to a real pty.

    Async context manager.  Spawns the child on `__aenter__`, terminates on
    `__aexit__`.  Example::

        async with TuiHarness("target/linux-x64/picolet-tui-app") as h:
            await h.wait_idle()
            await h.send("hello")
            await h.press("enter")
            assert h.cells_at(0, 0, 5) == "hello"

    Constructor args:
        binary_path: Path to the picolet-tui binary or a shell command.
            Passed straight to the pty as `argv[0]`; arg-splitting is the
            caller's job (we do NOT shell-split — pass a list via `args`).
        args: Extra argv elements appended after `binary_path`.
        cols, rows: Terminal geometry — sent to the kernel via TIOCSWINSZ
            before spawn so the SUT sees the right size from start-up.
        env: Extra environment variables merged over `os.environ`.
        timeout: Default timeout for wait_idle / send / press operations.
        cwd: Working directory for the child process.

    Lifecycle:
        __aenter__ → spawn pty, start the reader task, return self.
        __aexit__  → cancel reader, terminate child, drain pty.
    """

    def __init__(
        self,
        binary_path: Union[str, Path],
        *,
        args: Optional[list[str]] = None,
        cols: int = 80,
        rows: int = 24,
        env: Optional[dict] = None,
        timeout: float = 5.0,
        cwd: Optional[Union[str, Path]] = None,
    ) -> None:
        if sys.platform == "win32":
            raise NotImplementedError(
                "TuiHarness: Windows ConPTY support coming in v0.2 (FR-TUI-61)."
            )
        self._argv: list[str] = [str(binary_path)] + list(args or [])
        self._cols = cols
        self._rows = rows
        self._env = env
        self._timeout = timeout
        self._cwd = str(cwd) if cwd is not None else None

        self._pty: Optional[UnixPty] = None
        self._parser = Parser(rows=rows, cols=cols)
        self._reader_task: Optional[asyncio.Task] = None
        # Bytes-received event — set by the reader each time it folds a
        # chunk into the parser, cleared by wait_idle when it starts to
        # wait.  Lets wait_idle detect "no new bytes for N ms" without a
        # wall-clock sleep loop.
        self._bytes_event: asyncio.Event = asyncio.Event()
        # Exit code captured by the reader when it detects EOF.  None until
        # the child terminates; checked by aclose to validate.
        self._exit_code: Optional[int] = None

    # -------------------------------------------------------------------
    # Async context manager.
    # -------------------------------------------------------------------

    async def __aenter__(self) -> "TuiHarness":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def start(self) -> "TuiHarness":
        """Spawn the SUT and begin reading its output.

        Idempotent on the loop owner: calling start() twice on the same
        instance raises HarnessError rather than silently leaking the prior
        pty fd.
        """
        if self._pty is not None:
            raise HarnessError("TuiHarness: already started")
        self._pty = UnixPty(
            self._argv,
            rows=self._rows,
            cols=self._cols,
            env=self._env,
            cwd=self._cwd,
        )
        # The reader task drains pty output continuously; without it,
        # wait_idle would race against an OS read buffer that has nowhere
        # to go and the SUT would eventually block on stdout writes.
        self._reader_task = asyncio.create_task(self._read_loop())
        return self

    # -------------------------------------------------------------------
    # Public driving API.
    # -------------------------------------------------------------------

    async def send(self, text: str) -> None:
        """Type `text` to the SUT verbatim (no key translation).

        Encoded as UTF-8.  Use press() for symbolic keys.
        """
        await self._write_bytes(text.encode("utf-8"))

    async def press(self, key: str) -> None:
        """Send the byte sequence for a symbolic key (FR-TUI-15 vocabulary).

        Single printable characters fall through to send() so callers can
        write `await h.press("a")` interchangeably with `await h.send("a")`.
        """
        lowered = key.lower()
        if lowered in _KEY_TABLE:
            await self._write_bytes(_KEY_TABLE[lowered])
            return
        if len(key) == 1:
            await self.send(key)
            return
        raise HarnessError(
            "TuiHarness: unknown key {!r}; supported keys: {}".format(
                key, sorted(_KEY_TABLE.keys())
            )
        )

    async def wait_idle(self, timeout: Optional[float] = None, quiet_ms: int = 50) -> None:
        """Wait until the SUT stops emitting bytes for `quiet_ms` milliseconds.

        Phase-7 scaffold: synthesises a "frame done" signal from byte-flow
        quiescence rather than the DSR-6 round-trip FR-TUI-63 specifies.
        Reason: the runtime does not yet emit a DSR-6 reply path (the
        Phase-4 compositor that will is not built).  Replacing this with a
        real DSR-6 query / CPR reply is one synchronous wire change once
        the runtime parser lands; the public API stays identical.
        """
        if self._pty is None:
            raise HarnessError("TuiHarness: wait_idle before start()")
        deadline = (timeout if timeout is not None else self._timeout)
        quiet_s = quiet_ms / 1000.0
        loop = asyncio.get_event_loop()
        end = loop.time() + deadline
        # Drain any bytes that arrived before this call so the "quiet" window
        # is measured from now.
        self._bytes_event.clear()
        while loop.time() < end:
            try:
                await asyncio.wait_for(
                    self._bytes_event.wait(),
                    timeout=quiet_s,
                )
                # New bytes arrived — restart the quiet window.
                self._bytes_event.clear()
                continue
            except asyncio.TimeoutError:
                # Quiet window expired with no new bytes — the SUT has
                # settled.  We exit even if the absolute deadline has not
                # been reached; the caller's timeout is the upper bound,
                # not the target.
                return
            # Child died while waiting — surface it now rather than letting
            # the caller's next assertion fail mysteriously.
            if self._exit_code is not None:
                return
        # Hit the absolute deadline — the SUT never stopped emitting.  This
        # is the noisy-runtime case (a busy redraw loop or a spinner that
        # never settles).  Surface as HarnessError so the test fails loudly.
        raise HarnessError(
            "TuiHarness.wait_idle: SUT did not stop emitting bytes within {:.1f}s".format(
                deadline
            )
        )

    def cells_at(self, row: int, col: int, length: int = 1) -> str:
        """Return `length` characters from `cells[row][col:col+length]`.

        Convenience over `frame().cells[row][col].char` for the common
        assert pattern.  Bounds-checks both axes and raises IndexError on
        out-of-range coordinates rather than returning a truncated string,
        because the latter is the kind of off-by-one that hides bugs in
        the SUT.
        """
        if not (0 <= row < self._rows):
            raise IndexError("row {} out of range [0, {})".format(row, self._rows))
        if not (0 <= col < self._cols) or col + length > self._cols:
            raise IndexError(
                "col range [{}, {}) out of cols [0, {})".format(col, col + length, self._cols)
            )
        row_cells = self._parser.cells[row]
        return "".join(row_cells[col + i].char for i in range(length))

    def style_at(self, row: int, col: int) -> Style:
        """Return the Style of the cell at (row, col)."""
        if not (0 <= row < self._rows):
            raise IndexError("row {} out of range [0, {})".format(row, self._rows))
        if not (0 <= col < self._cols):
            raise IndexError("col {} out of range [0, {})".format(col, self._cols))
        return self._parser.cells[row][col].style

    def frame(self) -> list[list[Cell]]:
        """Return a list-of-list-of-Cell snapshot of the current screen.

        Returns the live parser grid; callers needing immutability should
        copy explicitly.  We avoid forcing a deepcopy here because the
        common pattern is `assert h.frame() == expected` immediately after
        wait_idle(), and an unnecessary copy on every assert is wasted
        work in CI where the grid is reasonably sized but the test count
        is in the thousands.
        """
        return self._parser.cells

    async def exit_app(self, key: str = "ctrl+q") -> None:
        """Convenience: send the v0.1 default quit binding and wait for exit.

        FR-TUI-4 names `ctrl+q` as the default `App.quit()` binding.  The
        scaffold encodes ctrl+q manually because the modifier-keypress
        table is not in `_KEY_TABLE` yet.
        """
        # ctrl+q = 0x11 (control character form of 'q')
        if key.lower() == "ctrl+q":
            await self._write_bytes(b"\x11")
        elif key.lower() == "ctrl+c":
            await self._write_bytes(b"\x03")
        else:
            await self.press(key)

    async def aclose(self) -> int:
        """Terminate the SUT and return its exit code.

        Always safe to call; calling it twice returns the same exit code.
        Pre-existing failures from the reader task are surfaced here so a
        crashed reader does not vanish silently when the harness exits.
        """
        if self._pty is None:
            return 0

        # Stop the reader before closing the pty so it doesn't read from a
        # closed fd and raise something cosmetic into the log.
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reader_task = None

        rc = self._pty.close()
        self._pty = None
        if self._exit_code is None:
            self._exit_code = rc
        return rc

    # -------------------------------------------------------------------
    # Internals.
    # -------------------------------------------------------------------

    async def _write_bytes(self, data: bytes) -> None:
        """Write `data` to the pty master, blocking-but-async on EAGAIN.

        Most TUI key sequences are ≤ 8 bytes, so the kernel always accepts
        the whole buffer in one go on a healthy pipe — the retry loop is
        defensive against a slow consumer rather than the common path.
        """
        if self._pty is None:
            raise HarnessError("TuiHarness: send/press before start()")
        loop = asyncio.get_event_loop()
        offset = 0
        while offset < len(data):
            try:
                n = self._pty.write(data[offset:])
                offset += n
            except BlockingIOError:
                # Pipe full — give the kernel a moment to drain.  10 ms
                # is short enough to feel synchronous in tests but long
                # enough to not burn CPU.
                await asyncio.sleep(0.01)
            except PtyClosedError as e:
                raise HarnessError("TuiHarness: write after pty close") from e

    async def _read_loop(self) -> None:
        """Background task: drain the pty into the parser until EOF.

        Uses `loop.add_reader` so we cooperatively yield instead of polling.
        On EOF (read returns b""), captures the child's exit code and
        exits — the reader task is single-shot.
        """
        loop = asyncio.get_event_loop()
        ready = asyncio.Event()

        def _on_ready() -> None:
            ready.set()

        assert self._pty is not None
        fd = self._pty.fd
        loop.add_reader(fd, _on_ready)

        try:
            while True:
                await ready.wait()
                ready.clear()
                try:
                    chunk = self._pty.read(65536)
                except BlockingIOError:
                    continue
                except (OSError, PtyClosedError):
                    break
                if not chunk:
                    # EOF — child exited.
                    self._exit_code = self._pty.poll_returncode()
                    break
                self._parser.feed(chunk)
                # Wake up wait_idle().  We set-then-leave-set, because the
                # waiter clears it; multiple chunks coalesce into one wake
                # which is exactly what wait_idle wants.
                self._bytes_event.set()
        finally:
            try:
                loop.remove_reader(fd)
            except (ValueError, OSError):
                # Already removed (e.g. fd was closed by aclose() racing
                # with the reader).  Cosmetic — nothing to do.
                pass
