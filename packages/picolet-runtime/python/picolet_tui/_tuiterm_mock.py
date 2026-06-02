# picolet_tui._tuiterm_mock — CPython-host stand-in for the tuiterm
# C module.  Used by the unit-test suite and by Phase 7's AppHarness
# (which subclasses MockTuiterm to drive scripted scenarios).
#
# The surface matches picolet_tui._tuiterm exactly: enable / disable /
# size / is_tty / read_input / write / capabilities / resize_pending,
# plus the HAS_* / NO_COLOR bit constants.  Unlike the real module
# the mock holds all state on a MockTuiterm instance, and the
# module-level functions delegate to a process-wide default instance.
# Tests that want isolation construct their own MockTuiterm and call
# install() to swap it in for the duration of the test.
#
# Input model: read_input() drains from a pre-scripted bytes queue;
# tests push bytes with feed().  Output model: write() appends to a
# captured bytearray; tests read it back with output_bytes().
#
# The mock makes no real syscalls and never touches a tty — it is the
# inverse of _tuiterm.py.  This file is imported by tests under host
# CPython, so it uses only stdlib (no ffi, no uctypes).


HAS_TRUECOLOR  = 1 << 0
HAS_256COLOR   = 1 << 1
NO_COLOR       = 1 << 2
HAS_MOUSE_SGR  = 1 << 3
HAS_BRAC_PASTE = 1 << 4
HAS_VT_INPUT   = 1 << 5


class MockTuiterm:
    """In-memory replacement for the tuiterm C module.

    Construct one per test (or use the module-level default).  Drive
    input with feed(); inspect output with output_bytes().  Toggle
    capabilities() return value by passing `caps=` to __init__ or
    assigning to .caps.  Trigger a resize observation with
    set_size(cols, rows) — the next resize_pending() call returns
    True exactly once."""

    def __init__(self, *, cols=80, rows=24, caps=HAS_TRUECOLOR | HAS_MOUSE_SGR | HAS_BRAC_PASTE,
                 tty_fds=(0, 1, 2)):
        self._cols = cols
        self._rows = rows
        self.caps = caps
        # Set of fds that should report as ttys.  Tests that want
        # is_tty() to return False on stdin pass tty_fds=().
        self._tty_fds = set(tty_fds)
        # Input bytes pushed by feed(), drained by read_input().
        # Use a single bytearray to keep slice semantics simple; this
        # avoids the edge cases of chunking across deque entries.
        self._in_buf = bytearray()
        # Captured output, accumulated by write().
        self._out_buf = bytearray()
        # Lifecycle: track enable/disable balance so tests can assert
        # the framework restored the terminal exactly once on tear-down.
        self.enabled = False
        self.enable_calls = 0
        self.disable_calls = 0
        # Initial resize is unobserved; flips True on size change and
        # back to False on the next resize_pending() poll.
        self._resize_flag = False

    # -------- Test-side drivers ---------------------------------------

    def feed(self, data):
        """Append `data` to the pending-input buffer."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._in_buf.extend(data)

    def output_bytes(self):
        """Return a snapshot of the bytes written so far."""
        return bytes(self._out_buf)

    def clear_output(self):
        self._out_buf = bytearray()

    def set_size(self, cols, rows):
        """Change the reported terminal size and arm resize_pending()."""
        if (cols, rows) != (self._cols, self._rows):
            self._cols = cols
            self._rows = rows
            self._resize_flag = True

    def set_caps(self, caps):
        self.caps = caps

    # -------- tuiterm surface -----------------------------------------

    def enable(self):
        self.enable_calls += 1
        # Real tuiterm.enable() is idempotent and OS-failure raising;
        # mirror that by raising OSError only when explicitly armed.
        if getattr(self, "_enable_raises", None) is not None:
            raise self._enable_raises
        self.enabled = True

    def disable(self):
        self.disable_calls += 1
        self.enabled = False

    def size(self):
        return self._cols, self._rows

    def is_tty(self, fd):
        return int(fd) in self._tty_fds

    def read_input(self, timeout_ms):
        # The real tuiterm.read_input either returns the bytes pending
        # in the kernel buffer or blocks up to timeout_ms.  The mock is
        # always non-blocking — `timeout_ms` is ignored because tests
        # drive time explicitly via feed() / wait_idle().
        del timeout_ms
        if not self._in_buf:
            return b""
        out = bytes(self._in_buf)
        self._in_buf = bytearray()
        return out

    def write(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("write() requires bytes-like data")
        self._out_buf.extend(data)
        return len(data)

    def capabilities(self):
        return self.caps

    def resize_pending(self):
        # The real module clears the SIGWINCH atomic on read; match that.
        if self._resize_flag:
            self._resize_flag = False
            return True
        return False

    # -------- Test-side arming hooks ----------------------------------

    def arm_enable_failure(self, exc):
        """Make the next enable() call raise `exc`.  Pass None to disarm."""
        self._enable_raises = exc


# Module-level default instance + install() swap.  The framework's
# driver layer imports the module-level functions; tests that want to
# observe state replace the active instance via install().

_active = MockTuiterm()


def install(mock):
    """Swap the module-level active instance.  Returns the previous
    instance so the caller can restore it after the test."""
    global _active
    prev = _active
    _active = mock
    return prev


def active():
    """Return the currently-installed MockTuiterm instance."""
    return _active


def enable():
    _active.enable()


def disable():
    _active.disable()


def size():
    return _active.size()


def is_tty(fd):
    return _active.is_tty(fd)


def read_input(timeout_ms):
    return _active.read_input(timeout_ms)


def write(data):
    return _active.write(data)


def capabilities():
    return _active.capabilities()


def resize_pending():
    return _active.resize_pending()
