# picolet.system — cross-platform system-event façade.
#
# Lets picolet apps subscribe to OS-level events that aren't intrinsic to the
# UI renderer: device-arrival, power-state changes, close-veto, etc.
#
# The public API is platform-agnostic.  Per-platform backends:
#
#   sys.platform == "win32"  → picolet_ui._win_events  (production)
#   sys.platform == "darwin" → not yet implemented (raises at use time)
#   sys.platform == "linux"  → not yet implemented (raises at use time)
#
# Tests inject `set_backend(mock)` to drive the façade without a real OS
# window.  The backend protocol is documented at _BackendProtocol below.
#
# Typical usage
# -------------
#
#     import picolet_ui as ui
#     import picolet.system as sys_evt
#
#     window = ui.Window(title="App", size=(800, 600))
#
#     @sys_evt.on_close_request
#     async def confirm_quit():
#         if has_unsaved_changes():
#             return await ask_user("Discard changes?")
#         return True
#
#     @sys_evt.on_device_change(usb=True)
#     async def device_changed(ev):
#         if ev.arrived:
#             print("Plugged:", ev.device_path)
#
#     sys_evt.attach(window)         # hooks the OS window
#     sys_evt.start_pump()           # adds a task to the running event loop
#     ui.run(main=...)               # standard picolet entry
#
# The `attach(window)` call extracts the native handle from `window.handle`
# (or .nsview, .gdk_window on future ports) and gives it to the backend.

import sys
import asyncio


# ---------------------------------------------------------------------------
# Event dataclasses (lightweight — no @dataclass since MicroPython lacks it
# unless required; just plain classes with __slots__).
# ---------------------------------------------------------------------------


class _Ev:
    __slots__ = ()
    def __repr__(self):
        attrs = ", ".join("{}={!r}".format(k, getattr(self, k))
                          for k in self.__slots__)
        return "{}({})".format(type(self).__name__, attrs)


class CloseRequest(_Ev):
    """User requested window close (clicked X, alt-F4, etc.).

    Handler returns truthy to allow the close, falsy to veto.  When vetoed,
    the window stays open and the user can retry.  Default action when no
    handler is registered: allow.
    """
    __slots__ = ()


class DeviceChange(_Ev):
    __slots__ = ("arrived", "device_path", "wparam")
    def __init__(self, arrived, device_path, wparam=0):
        self.arrived = arrived
        self.device_path = device_path
        self.wparam = wparam


class PowerChange(_Ev):
    __slots__ = ("state",)
    def __init__(self, state):
        # state in {"suspend", "resume", "battery_low", "power_changed", "unknown"}
        self.state = state


class SessionChange(_Ev):
    __slots__ = ("state",)
    def __init__(self, state):
        # state in {"lock", "unlock", "logon", "logoff",
        #           "remote_connect", "remote_disconnect", "unknown"}
        self.state = state


class WindowStateChange(_Ev):
    __slots__ = ("state",)
    def __init__(self, state):
        # state in {"minimized", "maximized", "restored"}
        self.state = state


class DisplayChange(_Ev):
    __slots__ = ("width", "height", "bpp")
    def __init__(self, width, height, bpp):
        self.width = width
        self.height = height
        self.bpp = bpp


class ClipboardChange(_Ev):
    __slots__ = ()


class FilesDropped(_Ev):
    __slots__ = ("paths",)
    def __init__(self, paths):
        self.paths = paths


class SettingChange(_Ev):
    __slots__ = ("area",)
    def __init__(self, area):
        self.area = area


# ---------------------------------------------------------------------------
# Backend protocol (informal).  A backend exposes:
#
#   .attach(handle)
#   .detach()
#   .subscribe(event_key, consume=False)
#   .watch_devices(usb=False, hid=False, comport=False)
#   .watch_power()
#   .watch_session()
#   .watch_clipboard()
#   .accept_drop_files(enable)
#   .poll() -> iterable of platform-neutral _Ev instances
#
# event_key is a stringly-typed identifier: "close", "minimize", etc.  The
# backend maps this to the platform-native subscription.
# ---------------------------------------------------------------------------


_backend = None
_attached_handle = None

# Handler registry.  Each slot stores a list of (callable, options) tuples.
_HANDLERS = {
    "close_request":   [],
    "device_change":   [],
    "power_change":    [],
    "session_change":  [],
    "window_state":    [],
    "display_change":  [],
    "clipboard":       [],
    "files_dropped":   [],
    "setting_change":  [],
}


def set_backend(backend):
    """Replace the active backend.  Tests use this to inject a mock.

    Production code does not call this directly — the default backend is
    selected automatically at import time based on sys.platform.
    """
    global _backend
    _backend = backend


def _default_backend():
    if sys.platform == "win32":
        from . import _system_win
        return _system_win.WinBackend()
    if sys.platform == "darwin":
        raise NotImplementedError("picolet.system: macOS backend not yet implemented")
    if sys.platform == "linux":
        raise NotImplementedError("picolet.system: linux backend not yet implemented")
    raise NotImplementedError(
        "picolet.system: no backend for sys.platform={!r}".format(sys.platform)
    )


def attach(window):
    """Attach the system-event hook to a picolet_ui Window-like object.

    The window's native handle (Windows: HWND via .handle) is given to the
    backend.  Idempotent; a second attach() on the same handle is a no-op.
    """
    global _backend, _attached_handle
    if _backend is None:
        _backend = _default_backend()
    handle = _extract_handle(window)
    _backend.attach(handle)
    _attached_handle = handle


def detach():
    global _backend, _attached_handle
    if _backend is not None and _attached_handle is not None:
        _backend.detach()
        _attached_handle = None


def _extract_handle(window):
    # Windows + LVGL/SDL: a .handle attribute returning an int.
    # We allow passing the raw int directly too, for tests and CLI variants.
    if isinstance(window, int):
        return window
    h = getattr(window, "handle", None)
    if h is None:
        raise TypeError(
            "picolet.system.attach: {!r} has no .handle attribute "
            "(pass a picolet_ui.Window or a raw native handle int)".format(window)
        )
    return int(h)


# ---------------------------------------------------------------------------
# Decorator-style subscribe API.
# ---------------------------------------------------------------------------


def _register(slot, func, **opts):
    _HANDLERS[slot].append((func, opts))
    return func


def on_close_request(func):
    """Register a coroutine (or sync fn) called when the user requests close.

    Return truthy to allow close, falsy to veto.  The backend subscribes
    with consume=True so the OS close is intercepted; on allow, we issue
    a window-level destroy through picolet_ui.
    """
    if _backend is not None:
        _backend.subscribe("close", consume=True)
    return _register("close_request", func)


def on_device_change(func=None, *, usb=False, hid=False, comport=False):
    """Register a handler for device-arrival/removal.

    Pass usb=True / hid=True / comport=True to register the corresponding
    device-interface class GUID.  At least one must be enabled.

    Can be used directly (``@on_device_change``) — defaults to usb=True —
    or with options (``@on_device_change(hid=True)``).
    """
    def _wrap(f):
        if _backend is not None:
            kw = {"usb": usb, "hid": hid, "comport": comport}
            if not (usb or hid or comport):
                kw["usb"] = True
            _backend.watch_devices(**kw)
        return _register("device_change", f, usb=usb, hid=hid, comport=comport)
    if func is not None and callable(func):
        if not (usb or hid or comport):
            usb = True
        return _wrap(func)
    return _wrap


def on_power_change(func):
    if _backend is not None:
        _backend.watch_power()
    return _register("power_change", func)


def on_session_change(func):
    if _backend is not None:
        _backend.watch_session()
    return _register("session_change", func)


def on_window_state(func):
    if _backend is not None:
        _backend.subscribe("window_state")
    return _register("window_state", func)


def on_display_change(func):
    if _backend is not None:
        _backend.subscribe("display_change")
    return _register("display_change", func)


def on_clipboard(func):
    if _backend is not None:
        _backend.watch_clipboard()
    return _register("clipboard", func)


def on_files_dropped(func):
    if _backend is not None:
        _backend.accept_drop_files(True)
    return _register("files_dropped", func)


def on_setting_change(func):
    if _backend is not None:
        _backend.subscribe("setting_change")
    return _register("setting_change", func)


# ---------------------------------------------------------------------------
# Pump.  An asyncio task that drains backend.poll() and dispatches handlers.
# Apps either call start_pump() explicitly or rely on picolet_ui.run() to do it.
# ---------------------------------------------------------------------------

_pump_task = None
PUMP_INTERVAL_S = 0.05


async def _pump_loop():
    while True:
        if _backend is not None:
            try:
                events = _backend.poll()
            except Exception:
                events = ()
            for ev in events:
                await _dispatch(ev)
        await asyncio.sleep(PUMP_INTERVAL_S)


async def _dispatch(ev):
    slot = _slot_for(ev)
    if slot is None:
        return
    handlers = _HANDLERS.get(slot, ())
    for fn, opts in handlers:
        # Device handlers may opt into a single class; honour the filter.
        if slot == "device_change":
            if not _device_matches(ev, opts):
                continue
        try:
            # close_request handlers are zero-arg by contract (the event
            # carries no payload).  Every other slot receives the event.
            res = fn() if slot == "close_request" else fn(ev)
            if hasattr(res, "__await__"):
                res = await res
        except Exception as e:
            print("picolet.system: handler {!r} raised: {}".format(fn, e))
            continue
        if slot == "close_request":
            if res:
                # Allow close: ask backend to actually destroy.
                if _backend is not None:
                    _backend.destroy()
            # Falsy res = veto; the consumed WM_CLOSE already prevented it.
            return


def _slot_for(ev):
    if isinstance(ev, CloseRequest):     return "close_request"
    if isinstance(ev, DeviceChange):     return "device_change"
    if isinstance(ev, PowerChange):      return "power_change"
    if isinstance(ev, SessionChange):    return "session_change"
    if isinstance(ev, WindowStateChange):return "window_state"
    if isinstance(ev, DisplayChange):    return "display_change"
    if isinstance(ev, ClipboardChange):  return "clipboard"
    if isinstance(ev, FilesDropped):     return "files_dropped"
    if isinstance(ev, SettingChange):    return "setting_change"
    return None


def _device_matches(ev, opts):
    # Without a class GUID on the event we can't filter precisely; the
    # path prefix is a reasonable proxy.  USB paths look like
    # "\\?\USB#VID_XXXX&PID_XXXX#...".  HID paths start with "\\?\HID#".
    # Accept everything when no filter is set.
    path = (ev.device_path or "").upper()
    want_usb = opts.get("usb")
    want_hid = opts.get("hid")
    want_com = opts.get("comport")
    if not (want_usb or want_hid or want_com):
        return True
    if want_usb and "\\USB#" in path: return True
    if want_hid and "\\HID#" in path: return True
    if want_com and ("\\COM#" in path or "\\GUID_DEVINTERFACE_COMPORT" in path): return True
    return False


def start_pump():
    """Schedule the dispatch loop on the running asyncio event loop."""
    global _pump_task
    if _pump_task is None:
        _pump_task = asyncio.create_task(_pump_loop())
    return _pump_task


def stop_pump():
    global _pump_task
    if _pump_task is not None:
        _pump_task.cancel()
        _pump_task = None


# ---------------------------------------------------------------------------
# Test helpers (test code may construct mock events and dispatch directly).
# ---------------------------------------------------------------------------


async def _dispatch_for_test(ev):
    """Synchronous dispatch wrapper used by the unit tests."""
    await _dispatch(ev)


def _reset_for_test():
    """Clear all registered handlers and unset the backend.  Test-only."""
    global _backend, _pump_task, _attached_handle
    for k in _HANDLERS:
        _HANDLERS[k].clear()
    _backend = None
    _pump_task = None
    _attached_handle = None


__all__ = (
    "attach", "detach", "set_backend", "start_pump", "stop_pump",
    "on_close_request", "on_device_change", "on_power_change",
    "on_session_change", "on_window_state", "on_display_change",
    "on_clipboard", "on_files_dropped", "on_setting_change",
    "CloseRequest", "DeviceChange", "PowerChange", "SessionChange",
    "WindowStateChange", "DisplayChange", "ClipboardChange",
    "FilesDropped", "SettingChange",
)
