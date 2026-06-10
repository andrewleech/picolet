# picolet_tui._tuiterm — libffi bindings into the in-process
# picolet_tuiterm user C module (compiled into the .exe by the tui
# variants, Unix and Windows).
#
# Symbol resolution mirrors picolet_ui._win_events: the C module is
# statically linked into the runtime .exe and exported via the variant
# Makefile (-Wl,--export-all-symbols on PE; -rdynamic on ELF).
# ffi.open(None) returns a handle whose .func() lookups resolve against
# the running process image — GetModuleHandle(NULL) on Windows,
# dlopen(NULL) on Unix.
#
# Unlike _win_events.py the bindings are resolved eagerly at import
# time, not on first use: tuiterm is the only path the TUI variant has
# to the terminal, so a missing symbol means the variant is wrong, not
# that the feature is optional.  Eager resolution turns that into a
# clear ImportError at the top-level `import picolet_tui` site instead
# of a confusing crash deep inside App.run_async().
#
# C surface contract (see docs/tui/research/04-terminal-handling.md §4
# and tui-v0.1-spec.md §3.3, FR-TUI-58): six functions plus the
# capabilities/resize-flag accessors.  The C module owns terminal
# state, the SIGWINCH atomic flag, and the colour-capability probe;
# the parser, key table, and mouse decoder all live in frozen Python.

import ffi
import uctypes


# ---------------------------------------------------------------------------
# Capability bitfield — returned by capabilities() once enable() has run.
# Matches the bit layout the C module emits; keep in sync with
# picolet_tuiterm.h PICOLET_TUITERM_CAP_*.
# ---------------------------------------------------------------------------

HAS_TRUECOLOR  = 1 << 0
HAS_256COLOR   = 1 << 1
NO_COLOR       = 1 << 2   # mono forced (NO_COLOR env, or not a tty)
HAS_MOUSE_SGR  = 1 << 3
HAS_BRAC_PASTE = 1 << 4
HAS_VT_INPUT   = 1 << 5   # Windows: ENABLE_VIRTUAL_TERMINAL_INPUT survived


# ---------------------------------------------------------------------------
# FFI binding — open the running process image and resolve the six entry
# points up front.  Any failure here turns into ImportError so a
# misconfigured variant (e.g. picolet_tui loaded into the cli variant
# during development) refuses to import instead of failing later.
# ---------------------------------------------------------------------------


def _open_self():
    try:
        return ffi.open(None)
    except OSError as e:
        raise ImportError(
            "picolet_tui._tuiterm: ffi.open(None) failed: {} "
            "(tuiterm is only available in the tui variant)".format(e)
        )


def _bind(handle, name, ret, args):
    try:
        return handle.func(ret, name, args)
    except (OSError, AttributeError) as e:
        raise ImportError(
            "picolet_tui._tuiterm: missing C symbol {!r}: {} "
            "(tuiterm is only available in the tui variant)".format(name, e)
        )


_self = _open_self()

# Signature strings follow ffi's letter encoding: i=int32, I=uint32,
# p=pointer/handle, v=void, l=long.  Return then argument list.
_enable          = _bind(_self, "picolet_tuiterm_enable",          "i", "")
_disable         = _bind(_self, "picolet_tuiterm_disable",         "v", "")
_size            = _bind(_self, "picolet_tuiterm_size",            "i", "pp")
_is_tty          = _bind(_self, "picolet_tuiterm_is_tty",          "i", "i")
_read_input      = _bind(_self, "picolet_tuiterm_read_input",      "i", "pii")
_write           = _bind(_self, "picolet_tuiterm_write",           "i", "pi")
_capabilities    = _bind(_self, "picolet_tuiterm_capabilities",    "i", "p")
_resize_pending  = _bind(_self, "picolet_tuiterm_resize_pending",  "i", "")
_last_errno      = _bind(_self, "picolet_tuiterm_last_error",      "i", "")


# ---------------------------------------------------------------------------
# Scratch buffers reused across calls.  Allocating two ints' worth of
# bytearray once at import keeps size()/read_input() allocation-free in
# the hot loop (FR-TUI-59 / NFR-TUI-4).
# ---------------------------------------------------------------------------

_size_buf = bytearray(8)               # two int32: cols, rows
_size_addr = uctypes.addressof(_size_buf)
_size_cols_view = uctypes.struct(_size_addr,     {"v": uctypes.INT32}, uctypes.LITTLE_ENDIAN)
_size_rows_view = uctypes.struct(_size_addr + 4, {"v": uctypes.INT32}, uctypes.LITTLE_ENDIAN)

# read_input buffer.  64 bytes per FR-TUI-58 / research 04 §1; one
# read_input call drains at most one TTY chunk, the parser handles
# re-entry across chunk boundaries.
_READ_CAP = 64
_read_buf = bytearray(_READ_CAP)
_read_addr = uctypes.addressof(_read_buf)

# capabilities() out-param scratch — the C signature is
# int32_t picolet_tuiterm_capabilities(uint32_t *flags).
_caps_buf = bytearray(4)
_caps_addr = uctypes.addressof(_caps_buf)
_caps_view = uctypes.struct(_caps_addr, {"v": uctypes.UINT32}, uctypes.LITTLE_ENDIAN)


# ---------------------------------------------------------------------------
# Public Python wrappers.  Thin: no policy, no parsing, no decoding —
# all of that is frozen Python upstream of this module.
# ---------------------------------------------------------------------------


def enable():
    """Put the controlling terminal into raw mode and snapshot the
    original state.  Idempotent.  Raises OSError on failure (no tty,
    pre-1809 conhost, tcgetattr failure inside a chroot, ...)."""
    rc = _enable()
    if rc != 0:
        raise OSError(_last_errno(), "tuiterm.enable failed")


def disable():
    """Restore the terminal to its pre-enable state.  Idempotent;
    safe to call from a signal handler or atexit."""
    _disable()


def size():
    """Return (cols, rows).  Cheap; safe to call once per frame."""
    rc = _size(_size_addr, _size_addr + 4)
    if rc != 0:
        raise OSError(_last_errno(), "tuiterm.size failed")
    return _size_cols_view.v, _size_rows_view.v


def is_tty(fd):
    """True iff `fd` refers to a terminal."""
    return _is_tty(int(fd)) != 0


def read_input(timeout_ms):
    """Non-blocking read.  Returns immediately with b'' if no bytes are
    pending and `timeout_ms == 0`; otherwise waits up to `timeout_ms`
    ms for at least one byte.  Raises OSError on closed stdin."""
    # Argument order matches the C signature: (buf, cap, timeout_ms).
    n = _read_input(_read_addr, _READ_CAP, int(timeout_ms))
    if n < 0:
        raise OSError(_last_errno(), "tuiterm.read_input failed")
    if n == 0:
        return b""
    return bytes(_read_buf[:n])


def write(data):
    """Pass `data` straight to stdout.  Returns the number of bytes
    actually written.  Raises OSError on a write failure (e.g. the
    terminal disappeared)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("write() requires bytes-like data")
    n_in = len(data)
    if n_in == 0:
        return 0
    # uctypes.addressof needs a writeable buffer on some MP builds.
    # Promote bytes -> bytearray here so the C side gets a stable
    # address regardless of the input type.  Compositor output is
    # already assembled as a bytearray in the renderer, so the only
    # callers that hit this branch are diagnostic writes.
    if isinstance(data, bytes):
        data = bytearray(data)
    addr = uctypes.addressof(data)
    n = _write(addr, n_in)
    if n < 0:
        raise OSError(_last_errno(), "tuiterm.write failed")
    return n


def capabilities():
    """Return the capability bitfield assembled by enable().  Bits are
    HAS_TRUECOLOR | HAS_256COLOR | NO_COLOR | HAS_MOUSE_SGR |
    HAS_BRAC_PASTE | HAS_VT_INPUT.  Stable for the life of the
    process once enable() has succeeded; callers should not re-cache."""
    rc = _capabilities(_caps_addr)
    if rc != 0:
        raise OSError(_last_errno(), "tuiterm.capabilities failed")
    return _caps_view.v


def resize_pending():
    """True iff a SIGWINCH has fired since the last call (Unix) or the
    console buffer dimensions have changed since the last call
    (Windows).  Always clears the flag before returning."""
    return _resize_pending() != 0
