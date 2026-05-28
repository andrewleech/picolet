# picolet_ui._win_events — libffi bindings into the in-process
# picolet_winevents user C module (compiled into the .exe by the windows
# webview/lvgl variants).
#
# Symbols are exported from the running .exe via -Wl,--export-all-symbols
# (webview) or per-symbol --undefined / --export-dynamic-symbol retains
# (lvgl).  ffi.open(None) -> GetModuleHandle(NULL) on the windows port
# resolves them.

import sys
import uctypes


def _open_self():
    if sys.platform != "win32":
        raise ImportError("picolet_ui._win_events is windows-only")
    import ffi
    try:
        return ffi.open(None)
    except OSError as e:
        raise ImportError(
            "picolet_ui._win_events: ffi.open(None) failed: {}".format(e)
        )


# Lazy: resolve symbols on first use so `import picolet_ui` stays cheap on
# variants where the winevents module is not linked in.
_self = None
_syms = {}


def _f(name, ret, args):
    global _self
    if _self is None:
        _self = _open_self()
    if name not in _syms:
        _syms[name] = _self.func(ret, name, args)
    return _syms[name]


# ---------------------------------------------------------------------------
# Public Win32 message ids — the subset most apps want.
# ---------------------------------------------------------------------------

WM_CLOSE              = 0x0010
WM_DESTROY            = 0x0002
WM_SIZE               = 0x0005
WM_MOVE               = 0x0003
WM_ACTIVATE           = 0x0006
WM_SETFOCUS           = 0x0007
WM_KILLFOCUS          = 0x0008
WM_SYSCOMMAND         = 0x0112
WM_QUERYENDSESSION    = 0x0011
WM_ENDSESSION         = 0x0016
WM_DEVICECHANGE       = 0x0219
WM_POWERBROADCAST     = 0x0218
WM_WTSSESSION_CHANGE  = 0x02B1
WM_DISPLAYCHANGE      = 0x007E
WM_DPICHANGED         = 0x02E0
WM_SETTINGCHANGE      = 0x001A
WM_THEMECHANGED       = 0x031A
WM_CLIPBOARDUPDATE    = 0x031D
WM_DROPFILES          = 0x0233

# WM_SIZE wParam values.
SIZE_RESTORED         = 0
SIZE_MINIMIZED        = 1
SIZE_MAXIMIZED        = 2

# WM_SYSCOMMAND wParam values.
SC_MINIMIZE           = 0xF020
SC_MAXIMIZE           = 0xF030
SC_CLOSE              = 0xF060

# DBT_* wParam values for WM_DEVICECHANGE.
DBT_DEVICEARRIVAL          = 0x8000
DBT_DEVICEREMOVECOMPLETE   = 0x8004
DBT_DEVICEQUERYREMOVE      = 0x8001
DBT_DEVICEQUERYREMOVEFAILED = 0x8002
DBT_DEVNODES_CHANGED       = 0x0007

# PBT_* wParam values for WM_POWERBROADCAST.
PBT_APMSUSPEND             = 0x0004
PBT_APMRESUMEAUTOMATIC     = 0x0012
PBT_APMRESUMESUSPEND       = 0x0007
PBT_POWERSETTINGCHANGE     = 0x8013

# Well-known device-interface class GUIDs as 16-byte little-endian buffers
# (the form uuid.UUID(...).bytes_le produces — and the form Win32 stores
# in DEV_BROADCAST_DEVICEINTERFACE.dbcc_classguid).
GUID_DEVINTERFACE_USB_DEVICE = (
    b"\x10\xbf\xdc\xa5\x30\x65\xd2\x11\x90\x1f\x00\xc0\x4f\xb9\x51\xed"
)
GUID_DEVINTERFACE_HID = (
    b"\xb2\x55\x1e\x4d\x6f\xf1\xcf\x11\x88\xcb\x00\x11\x11\x00\x00\x30"
)
GUID_DEVINTERFACE_COMPORT = (
    b"\xe0\xd1\xe0\x86\x89\x80\xd0\x11\x9c\xe4\x08\x00\x3e\x30\x1f\x73"
)


# ---------------------------------------------------------------------------
# Thin Python wrappers around the C surface.
# ---------------------------------------------------------------------------


def attach(hwnd):
    """Install the event subclass on `hwnd`.  Idempotent."""
    rc = _f("picolet_winevents_attach", "i", "p")(int(hwnd))
    if rc != 0:
        raise OSError("picolet_winevents_attach failed: {}".format(_last_error()))


def detach(hwnd):
    _f("picolet_winevents_detach", "i", "p")(int(hwnd))


def subscribe(hwnd, msg, consume=False):
    rc = _f("picolet_winevents_subscribe", "i", "pIi")(
        int(hwnd), int(msg), 1 if consume else 0
    )
    if rc != 0:
        raise OSError("picolet_winevents_subscribe({:#x}) failed: {}".format(
            msg, _last_error()
        ))


def unsubscribe(hwnd, msg):
    _f("picolet_winevents_unsubscribe", "i", "pI")(int(hwnd), int(msg))


def poll_json(hwnd):
    """Drain pending events.  Returns a list of dicts (possibly empty)."""
    ptr = _f("picolet_winevents_poll_json", "p", "p")(int(hwnd))
    if not ptr:
        return []
    try:
        # Read the malloc'd UTF-8 NUL-terminated buffer back as Python bytes.
        addr = int(ptr)
        out = bytearray()
        cap = 1024 * 1024  # 1 MiB safety cap — payloads are small
        for i in range(cap):
            b = uctypes.bytes_at(addr + i, 1)[0]
            if b == 0:
                break
            out.append(b)
        text = bytes(out).decode("utf-8")
    finally:
        _f("picolet_winevents_free", "v", "p")(int(ptr))
    import json
    return json.loads(text)


def overflow_count(hwnd):
    return _f("picolet_winevents_overflow_count", "i", "p")(int(hwnd))


def watch_device_interface(hwnd, guid_bytes_le):
    if not isinstance(guid_bytes_le, (bytes, bytearray)) or len(guid_bytes_le) != 16:
        raise ValueError("guid_bytes_le must be exactly 16 bytes")
    # uctypes.addressof on a bytes object yields the start address; ffi 'p'
    # accepts an int.
    addr = uctypes.addressof(guid_bytes_le)
    rc = _f("picolet_winevents_watch_device_interface", "i", "pp")(int(hwnd), int(addr))
    if rc != 0:
        raise OSError("watch_device_interface failed: {}".format(_last_error()))


def watch_power(hwnd):
    rc = _f("picolet_winevents_watch_power", "i", "p")(int(hwnd))
    if rc != 0:
        raise OSError("watch_power failed: {}".format(_last_error()))


def watch_session(hwnd):
    rc = _f("picolet_winevents_watch_session", "i", "p")(int(hwnd))
    if rc != 0:
        raise OSError("watch_session failed: {}".format(_last_error()))


def watch_clipboard(hwnd):
    rc = _f("picolet_winevents_watch_clipboard", "i", "p")(int(hwnd))
    if rc != 0:
        raise OSError("watch_clipboard failed: {}".format(_last_error()))


def accept_drop_files(hwnd, enable=True):
    rc = _f("picolet_winevents_accept_drop_files", "i", "pi")(
        int(hwnd), 1 if enable else 0
    )
    if rc != 0:
        raise OSError("accept_drop_files failed: {}".format(_last_error()))


def _last_error():
    try:
        return _f("picolet_winevents_last_error", "i", "")()
    except Exception:
        return -1
