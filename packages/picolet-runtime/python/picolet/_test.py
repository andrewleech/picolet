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

    Calls lv.snapshot_take() for RGB888, extracts the pixel data via uctypes,
    encodes to PNG via the in-process picolet_lvgl_png_encode C symbol (linked
    into the lvgl runtime binary, exposed via libffi.ffi.open(None)).

    Returns:
        bytes: PNG-encoded image of the current screen state.

    Raises:
        RuntimeError: if the snapshot or PNG encoding fails.
    """
    scr = lv.screen_active()

    # lv.snapshot_take returns an lv_image_dsc_t (lv_img_dsc_t in LVGL 8).
    dsc = lv.snapshot_take(scr, lv.COLOR_FORMAT.RGB888)
    if dsc is None:
        raise RuntimeError("picolet._test.snapshot: lv.snapshot_take returned None")

    try:
        # Copy pixel data out before freeing the descriptor.
        # dsc.data is a pointer to the raw pixel bytes.
        # dsc.data_size is the byte count (width * height * 3 for RGB888).
        data_size = dsc.data_size
        if data_size == 0:
            raise RuntimeError("picolet._test.snapshot: snapshot data_size is 0")

        pixel_bytes = bytes(uctypes.bytes_at(dsc.data, data_size))

        # Retrieve width and height from the descriptor header.
        # lv_image_dsc_t: header.w, header.h accessible via .header.w etc.
        w = dsc.header.w
        h = dsc.header.h
    finally:
        lv.snapshot_free(dsc)

    # Encode to PNG via the C shim linked into the binary.
    # ffi.open(None) gives us the running process's symbol table.
    import ffi as _ffi
    try:
        _self = _ffi.open(None)
        _png_encode = _self.func("i", "picolet_lvgl_png_encode", "piipp")
        _png_free   = _self.func("v", "picolet_lvgl_png_free",   "p")
    except OSError as e:
        raise RuntimeError(
            "picolet._test.snapshot: cannot resolve picolet_lvgl_png_encode: {}".format(e)
        )

    # Prepare output pointer slots as 8-byte buffers (pointer + size_t).
    # We pass pointers to these buffers so the C function can fill them in.
    out_ptr_buf  = bytearray(8)   # void* (pointer to malloc'd output)
    out_size_buf = bytearray(8)   # size_t

    pixel_buf = bytearray(pixel_bytes)  # ensure a mutable buffer in heap

    rc = _png_encode(
        uctypes.addressof(pixel_buf),
        w, h,
        uctypes.addressof(out_ptr_buf),
        uctypes.addressof(out_size_buf),
    )
    if rc != 0:
        raise RuntimeError("picolet._test.snapshot: picolet_lvgl_png_encode failed (rc={})".format(rc))

    # Read back the pointer and size.
    # On x86-64 Linux: pointer is 8 bytes LE, size_t is 8 bytes LE.
    import struct
    out_ptr  = struct.unpack_from("<Q", out_ptr_buf,  0)[0]
    out_size = struct.unpack_from("<Q", out_size_buf, 0)[0]

    if out_ptr == 0 or out_size == 0:
        raise RuntimeError("picolet._test.snapshot: PNG encoder returned null/empty buffer")

    png_bytes = bytes(uctypes.bytes_at(out_ptr, out_size))
    _png_free(out_ptr)

    return png_bytes
