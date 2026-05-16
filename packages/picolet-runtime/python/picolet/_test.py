# picolet._test — LVGL test API (FR-TEST-2, PH17).
#
# This module is frozen into the LVGL variant and gated on PICOLET_TEST_MODE=1
# at import time.  It exposes three functions for autonomous test harnesses:
#
#   tap(x, y)      — synthesise a pointer-press + release at (x, y).
#   press(key)     — synthesise a keypad-press + release for the given key code.
#   snapshot()     — capture the active screen to PNG bytes.
#
# Usage from the AppHarness LVGL path (FR-TEST-6) or directly:
#
#   PICOLET_TEST_MODE=1 picolet-lvgl -c "
#   import picolet._test as t
#   png = t.snapshot()
#   open('/tmp/snap.png', 'wb').write(png)
#   "
#
# The module must NOT be imported when PICOLET_TEST_MODE is not set — the guard
# raises ImportError so that user apps can use conditional import patterns:
#
#   import os
#   if os.getenv('PICOLET_TEST_MODE'):
#       from picolet import _test as picolet_test
#
# LVGL binding note (F2): lv.snapshot_take, lv.indev_create, etc. are all
# exposed by the generated lv_mpy_example.c.  LV_USE_SNAPSHOT=1 is already on
# in lv_conf.h:1030.  No lv_conf.h change is required.
#
# Thread-safety: all LVGL calls must run on the asyncio/LVGL event thread.
# tap/press/snapshot are designed to be called from within one asyncio tick,
# not from a foreign thread.

import os as _os

if _os.getenv("PICOLET_TEST_MODE") != "1":
    raise ImportError(
        "picolet._test is gated on PICOLET_TEST_MODE=1; "
        "set the environment variable before starting the runtime"
    )

import lvgl as lv
import uctypes

# ---------------------------------------------------------------------------
# Synthetic input devices (lazily initialised on first tap/press call)
# ---------------------------------------------------------------------------

# Pre-allocated event ring buffer: 32 entries × 4 ints each
# Layout per entry: [type, x_or_key, state, _pad]
# type: 0=pointer, 1=keypad
# state: 0=released, 1=pressed
_RING_CAP = 32
_ring = [None] * _RING_CAP  # list of (type, x, y_or_key, state)

_ring_read  = 0
_ring_write = 0

_ptr_indev = None   # lv.indev_t for pointer (POINTER type)
_key_indev = None   # lv.indev_t for keypad (KEYPAD type)


def _ring_push(entry):
    """Push one event tuple; silently drops on overflow (ring full)."""
    global _ring_write, _ring_read
    nxt = (_ring_write + 1) % _RING_CAP
    if nxt == _ring_read:
        # Ring is full — drop oldest to make room.
        _ring_read = (_ring_read + 1) % _RING_CAP
    _ring[_ring_write] = entry
    _ring_write = nxt


def _ring_pop():
    """Pop one event tuple, or None if empty."""
    global _ring_read
    if _ring_read == _ring_write:
        return None
    entry = _ring[_ring_read]
    _ring[_ring_read] = None
    _ring_read = (_ring_read + 1) % _RING_CAP
    return entry


def _ptr_read_cb(indev_drv, data):
    """Pointer read_cb: drain one event from the ring into data."""
    event = None
    for _ in range(_RING_CAP):
        e = _ring_pop()
        if e is None:
            break
        if e[0] == 0:  # pointer event
            event = e
            break
        # Not a pointer event — put it back (approximate: push to front)
        _ring_push(e)
        break

    if event is None:
        data.state = lv.INDEV_STATE.RELEASED
        return False

    _, x, y, state = event
    data.point.x = x
    data.point.y = y
    data.state = lv.INDEV_STATE.PRESSED if state else lv.INDEV_STATE.RELEASED
    # If more events are pending, return True so LVGL polls again this tick.
    has_more = (_ring_read != _ring_write and _ring[_ring_read] is not None
                and _ring[_ring_read][0] == 0)
    return has_more


def _key_read_cb(indev_drv, data):
    """Keypad read_cb: drain one event from the ring into data."""
    event = None
    for _ in range(_RING_CAP):
        e = _ring_pop()
        if e is None:
            break
        if e[0] == 1:  # key event
            event = e
            break
        _ring_push(e)
        break

    if event is None:
        data.state = lv.INDEV_STATE.RELEASED
        return False

    _, key, _, state = event
    data.key = key
    data.state = lv.INDEV_STATE.PRESSED if state else lv.INDEV_STATE.RELEASED
    return False


def _ensure_indevs():
    """Lazily create the two synthetic indev devices."""
    global _ptr_indev, _key_indev

    if _ptr_indev is None:
        ptr = lv.indev_create()
        ptr.set_type(lv.INDEV_TYPE.POINTER)
        ptr.set_read_cb(_ptr_read_cb)
        _ptr_indev = ptr

    if _key_indev is None:
        key = lv.indev_create()
        key.set_type(lv.INDEV_TYPE.KEYPAD)
        key.set_read_cb(_key_read_cb)
        _key_indev = key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tap(x, y):
    """Synthesise a pointer-press at (x, y) followed by a release.

    The events are drained during the next lv.task_handler() call.
    Callers should await at least one asyncio tick after tap() to ensure
    the events are processed.

    Args:
        x: horizontal coordinate in pixels.
        y: vertical coordinate in pixels.
    """
    _ensure_indevs()
    _ring_push((0, int(x), int(y), 1))   # press
    _ring_push((0, int(x), int(y), 0))   # release


def press(key):
    """Synthesise a key-press for the given LVGL key code followed by release.

    Args:
        key: LVGL key code (e.g. lv.KEY.ENTER, lv.KEY.UP, or a char code).
    """
    _ensure_indevs()
    _ring_push((1, int(key), 0, 1))  # pressed
    _ring_push((1, int(key), 0, 0))  # released


def snapshot():
    """Capture the active LVGL screen to PNG bytes.

    Calls lv.snapshot_take() for RGB888, then passes the pixel data pointer
    directly to picolet_lvgl_png_encode (a C symbol linked into the binary and
    exposed via ffi.open(None)).  The pixel buffer is NOT copied into Python
    heap (1.44 MB for 800x600 would exhaust MicroPython's allocator); instead
    the C encoder receives the raw pointer from the lv_draw_buf_t.

    Returns:
        bytes: PNG-encoded image of the current screen state.

    Raises:
        RuntimeError: if the snapshot or PNG encoding fails.
    """
    import ffi as _ffi
    import struct

    # Resolve the C encoder once (fast path reuses module-level cache via
    # closure; if import is cached this is just an attribute lookup).
    try:
        _self = _ffi.open(None)
        _png_encode = _self.func("i", "picolet_lvgl_png_encode", "piipp")
        _png_free   = _self.func("v", "picolet_lvgl_png_free",   "p")
    except OSError as e:
        raise RuntimeError(
            "picolet._test.snapshot: cannot resolve picolet_lvgl_png_encode: {}".format(e)
        )

    scr = lv.screen_active()

    # Run task_handler to flush any pending draw operations before capture.
    lv.task_handler()

    # lv.snapshot_take returns an lv_draw_buf_t.
    dsc = lv.snapshot_take(scr, lv.COLOR_FORMAT.RGB888)
    if dsc is None:
        raise RuntimeError("picolet._test.snapshot: lv.snapshot_take returned None")

    try:
        data_size = dsc.data_size
        if data_size == 0:
            raise RuntimeError("picolet._test.snapshot: snapshot data_size is 0")

        w = dsc.header.w
        h = dsc.header.h

        # dsc.data is a C_Array (uint8_t*).  memoryview() yields the 8-byte
        # pointer value (on x86-64); unpack it to get the actual pixel address.
        # Avoid bytes(uctypes.bytes_at(addr, data_size)) — that would allocate
        # width*height*3 bytes in the MicroPython heap (> 1 MiB for 800x600),
        # which would trigger a MemoryError.  Pass the raw pointer to the C
        # encoder instead and only copy the much-smaller PNG output.
        data_ptr_bytes = bytes(memoryview(dsc.data))
        data_ptr = struct.unpack_from("<Q", data_ptr_bytes, 0)[0]
        if data_ptr == 0:
            raise RuntimeError("picolet._test.snapshot: dsc.data pointer is NULL")

        # Encode to PNG.  Output buffers: 8-byte void* + 8-byte size_t.
        out_ptr_buf  = bytearray(8)
        out_size_buf = bytearray(8)

        rc = _png_encode(
            data_ptr,       # raw pixel pointer (not copied to Python heap)
            w, h,
            uctypes.addressof(out_ptr_buf),
            uctypes.addressof(out_size_buf),
        )
        if rc != 0:
            raise RuntimeError(
                "picolet._test.snapshot: picolet_lvgl_png_encode failed (rc={})".format(rc)
            )

        out_ptr  = struct.unpack_from("<Q", out_ptr_buf,  0)[0]
        out_size = struct.unpack_from("<Q", out_size_buf, 0)[0]

        if out_ptr == 0 or out_size == 0:
            raise RuntimeError(
                "picolet._test.snapshot: PNG encoder returned null/empty buffer"
            )

        png_bytes = bytes(uctypes.bytes_at(out_ptr, out_size))
        _png_free(out_ptr)
        return png_bytes
    finally:
        # lv.snapshot_free() only accepts lv_image_dsc_t*, but snapshot_take()
        # in LVGL 9 returns lv_draw_buf_t*.  Use the draw buffer's own
        # destroy() method to free the allocation.
        try:
            dsc.destroy()
        except Exception:
            pass

    return png_bytes


# ---------------------------------------------------------------------------
# IPC dispatcher registration (FR-TEST-6, BUG-B fix).
#
# Register @picolet.command handlers so the AppHarness can drive this module
# via the stdio transport (StdioTransport + Dispatcher).  The harness sends
# JSON requests {"id": N, "cmd": "__test__.tap", "args": {"x": X, "y": Y}}
# and expects {"id": N, "ok": true, "result": null}.
#
# Registration is gated on PICOLET_TEST_MODE=1 (already enforced at module
# import — the ImportError guard at the top of this file ensures we only
# reach here when the env var is set).
# ---------------------------------------------------------------------------

import picolet as _picolet


@_picolet.command("__test__.tap")
async def _cmd_tap(args):
    """IPC handler: synthesise a pointer tap at (x, y)."""
    tap(int(args["x"]), int(args["y"]))
    return None


@_picolet.command("__test__.press")
async def _cmd_press(args):
    """IPC handler: synthesise a key press for the given key code."""
    press(int(args["key"]))
    return None


@_picolet.command("__test__.snapshot")
async def _cmd_snapshot(args):
    """IPC handler: capture the LVGL screen to PNG, return base64-encoded bytes."""
    import ubinascii as _b64
    png = snapshot()
    return _b64.b2a_base64(png).decode("ascii").strip()


@_picolet.command("__test__.ping")
async def _cmd_ping(args):
    """IPC handler: handshake probe.  Returns 'pong' so the harness can detect readiness."""
    return "pong"
