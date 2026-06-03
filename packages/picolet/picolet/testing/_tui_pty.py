"""picolet.testing._tui_pty — per-platform PTY wrapper for TuiHarness.

The harness needs a process attached to a real terminal so the
isatty()/ioctl() chain inside `tuiterm.enable()` succeeds (FR-TUI-10).
v0.1 ships the Unix side; Windows ConPTY support lands in v0.2 once
Phase 8 wires the picolet Windows variant (FR-TUI-61).

Why a dedicated wrapper module instead of inlining the pty.fork()+os.read()
in the harness:

* The harness is async-only.  Putting the blocking `os.read` behind a
  `select.poll` loop in its own object keeps the harness free of file-
  descriptor lifecycle bookkeeping and lets the Windows ConPTY path drop
  in as a sibling class with no harness changes.
* Unit-testable in isolation — feeding the wrapper a `cat`-like helper
  process is enough to exercise resize / read / write paths without the
  full Textual stack present.
"""
from __future__ import annotations

import os
import sys
from typing import Optional


class PtyClosedError(RuntimeError):
    """Raised when read/write is attempted after close() has been called.

    Distinct from BrokenPipeError because the harness uses this to drive
    its own state machine (terminated child vs. mis-sequenced call).
    """


class UnixPty:
    """Spawn `argv` attached to a freshly-allocated pty pair.

    `subprocess.Popen` does the heavy lifting; we hand it the slave end as
    stdin/stdout/stderr, then keep the master end ourselves for read/write.
    The slave fd is closed in the parent immediately after spawn — leaving
    it open would prevent us from seeing EOF when the child exits.

    Sized via `TIOCSWINSZ` so the SUT sees the requested geometry from its
    very first `tuiterm.size()` call (no SIGWINCH race at start-up).
    """

    def __init__(
        self,
        argv: list[str],
        *,
        rows: int = 24,
        cols: int = 80,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> None:
        if sys.platform == "win32":
            raise NotImplementedError(
                "TuiHarness: Windows ConPTY support coming in v0.2 (FR-TUI-61)."
            )
        import pty
        import subprocess
        import termios
        import fcntl
        import struct

        self._rows = rows
        self._cols = cols
        self._master_fd, slave_fd = pty.openpty()

        # Size the pty before spawn so the child sees the right geometry on
        # its first ioctl.  The Window-size struct is `(rows, cols, xpix,
        # ypix)`; xpix/ypix are unused by terminal apps so we leave them 0.
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            # ioctl can fail on some restricted environments (Docker without
            # tty privileges).  The child will run, just with whatever
            # geometry the kernel default supplies — log nothing, the
            # harness's wait_idle() will surface the symptom if it matters.
            pass

        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        # Force a sensible TERM so colour-capability probes inside the SUT
        # do not fall back to mono mode under CI shells that strip TERM.
        proc_env.setdefault("TERM", "xterm-256color")

        self._proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=proc_env,
            cwd=cwd,
            close_fds=True,
            # `start_new_session=True` divorces the child from our process
            # group so a `^C` in the harness's parent doesn't reach the
            # child via SIGINT — the harness owns shutdown via .close().
            start_new_session=True,
        )
        # Parent has no use for the slave fd; releasing it lets us see EOF
        # on the master when the child exits.
        os.close(slave_fd)

        # Switch the master end to non-blocking so the harness can poll
        # with a deadline rather than blocking on os.read.
        import fcntl as _fcntl
        flags = _fcntl.fcntl(self._master_fd, _fcntl.F_GETFL)
        _fcntl.fcntl(self._master_fd, _fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._closed = False

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def fd(self) -> int:
        """Master-end fd, suitable for `select.poll`."""
        if self._closed:
            raise PtyClosedError("UnixPty: master fd was already closed")
        return self._master_fd

    def write(self, data: bytes) -> int:
        """Write `data` to the child's stdin via the master fd.

        Returns the number of bytes accepted by the kernel; harness callers
        loop on short writes.  EAGAIN under O_NONBLOCK is surfaced as a
        BlockingIOError, which the harness retries inside its poll loop.
        """
        if self._closed:
            raise PtyClosedError("UnixPty: write after close")
        return os.write(self._master_fd, data)

    def read(self, n: int = 4096) -> bytes:
        """Read up to `n` bytes from the child's stdout.

        Returns `b""` on EOF (child exited and pty drained).  Raises
        BlockingIOError if no data is ready under O_NONBLOCK — the harness
        treats that as "go back and poll".
        """
        if self._closed:
            raise PtyClosedError("UnixPty: read after close")
        try:
            return os.read(self._master_fd, n)
        except OSError as e:
            # On Linux, read() against a pty whose slave has been closed
            # surfaces EIO rather than EOF (b"").  Normalise to b"" so the
            # caller has one termination signal to handle.
            import errno
            if e.errno == errno.EIO:
                return b""
            raise

    def resize(self, rows: int, cols: int) -> None:
        """Update the pty window size and signal SIGWINCH to the child."""
        if self._closed:
            raise PtyClosedError("UnixPty: resize after close")
        import termios
        import fcntl
        import struct
        import signal as _signal
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        self._rows = rows
        self._cols = cols
        # The kernel does not auto-deliver SIGWINCH on TIOCSWINSZ from the
        # master side in all cases (it does on Linux, but only when the
        # window dimensions actually changed); send it explicitly so the
        # SUT picks up the change regardless.
        try:
            self._proc.send_signal(_signal.SIGWINCH)
        except ProcessLookupError:
            # Race: child exited between our resize and the signal — fine,
            # there is no observer to notify.
            pass

    def poll_returncode(self) -> Optional[int]:
        """Return the child's exit code if it has exited, else None."""
        return self._proc.poll()

    def close(self, timeout: float = 3.0) -> int:
        """Tear the child down: SIGTERM → wait → SIGKILL → wait.

        Returns the child's exit code (or -signal if killed by signal).  The
        master fd is closed unconditionally so subsequent read/write raise
        cleanly even if the child somehow survived termination.
        """
        if self._closed:
            return self._proc.returncode if self._proc.returncode is not None else 0

        if self._proc.poll() is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=timeout)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    pass

        try:
            os.close(self._master_fd)
        except OSError:
            pass
        self._closed = True
        return self._proc.returncode if self._proc.returncode is not None else 0
