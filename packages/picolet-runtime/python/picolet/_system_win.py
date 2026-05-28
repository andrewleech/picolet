# picolet._system_win — Windows backend for picolet.system.
#
# Translates the cross-platform protocol described in picolet.system into the
# picolet_ui._win_events FFI surface, and converts raw WM_* events from the
# C ring buffer into picolet.system._Ev instances.

from picolet_ui import _win_events as we
from . import system as _system


def _dev_class_for(usb, hid, comport):
    rv = []
    if usb:     rv.append(we.GUID_DEVINTERFACE_USB_DEVICE)
    if hid:     rv.append(we.GUID_DEVINTERFACE_HID)
    if comport: rv.append(we.GUID_DEVINTERFACE_COMPORT)
    return rv


class WinBackend:
    def __init__(self):
        self._hwnd = None
        self._close_consumed = False

    # ---- Lifecycle ------------------------------------------------------

    def attach(self, hwnd):
        we.attach(hwnd)
        self._hwnd = hwnd

    def detach(self):
        if self._hwnd is not None:
            we.detach(self._hwnd)
            self._hwnd = None

    def destroy(self):
        """Called by the close_request dispatcher when the handler allows
        the close.  Issues a real DestroyWindow via the existing webview
        FFI; on LVGL the SDL shutdown path handles it."""
        if self._hwnd is None: return
        try:
            from picolet_ui import _win_ffi
            _win_ffi.picolet_wv2_destroy_window(self._hwnd)
        except Exception:
            # LVGL variant: PostMessageW(WM_DESTROY) directly is the fallback,
            # but lvgl-on-windows owns its own SDL window lifetime — apps
            # should call sys.exit() from the handler if they need an
            # immediate hard exit there.
            pass

    # ---- Subscriptions --------------------------------------------------

    _KEY_TO_MSG = {
        "close":          we.WM_CLOSE,
        "minimize":       we.WM_SYSCOMMAND,
        "window_state":   we.WM_SIZE,
        "display_change": we.WM_DISPLAYCHANGE,
        "setting_change": we.WM_SETTINGCHANGE,
    }

    def subscribe(self, event_key, consume=False):
        if self._hwnd is None: return
        msg = self._KEY_TO_MSG.get(event_key)
        if msg is None:
            raise ValueError("unknown event_key: {!r}".format(event_key))
        we.subscribe(self._hwnd, msg, consume=consume)
        if event_key == "close" and consume:
            self._close_consumed = True

    def watch_devices(self, usb=False, hid=False, comport=False):
        if self._hwnd is None: return
        for guid in _dev_class_for(usb, hid, comport):
            we.watch_device_interface(self._hwnd, guid)

    def watch_power(self):
        if self._hwnd is None: return
        we.watch_power(self._hwnd)

    def watch_session(self):
        if self._hwnd is None: return
        we.watch_session(self._hwnd)

    def watch_clipboard(self):
        if self._hwnd is None: return
        we.watch_clipboard(self._hwnd)

    def accept_drop_files(self, enable):
        if self._hwnd is None: return
        we.accept_drop_files(self._hwnd, enable)

    # ---- Polling --------------------------------------------------------

    def poll(self):
        if self._hwnd is None: return ()
        raw = we.poll_json(self._hwnd)
        for ev in raw:
            mapped = self._map_event(ev)
            if mapped is not None:
                yield mapped

    def _map_event(self, ev):
        msg = ev["msg"]
        wp = ev["wp"]
        lp = ev["lp"]
        extra = ev.get("extra", "")

        if msg == we.WM_CLOSE:
            return _system.CloseRequest()

        if msg == we.WM_DEVICECHANGE:
            if wp == we.DBT_DEVICEARRIVAL:
                return _system.DeviceChange(arrived=True, device_path=extra, wparam=wp)
            if wp == we.DBT_DEVICEREMOVECOMPLETE:
                return _system.DeviceChange(arrived=False, device_path=extra, wparam=wp)
            return None  # ignore the noisier DBT_DEVNODES_CHANGED chatter

        if msg == we.WM_POWERBROADCAST:
            mapping = {
                we.PBT_APMSUSPEND:         "suspend",
                we.PBT_APMRESUMEAUTOMATIC: "resume",
                we.PBT_APMRESUMESUSPEND:   "resume",
                we.PBT_POWERSETTINGCHANGE: "power_changed",
            }
            return _system.PowerChange(state=mapping.get(wp, "unknown"))

        if msg == we.WM_WTSSESSION_CHANGE:
            # wParam codes:  1 console-connect, 2 console-disconnect,
            #                3 remote-connect, 4 remote-disconnect,
            #                5 logon, 6 logoff, 7 lock, 8 unlock.
            mapping = {
                1: "logon", 2: "logoff",
                3: "remote_connect", 4: "remote_disconnect",
                5: "logon",  6: "logoff",
                7: "lock",   8: "unlock",
            }
            return _system.SessionChange(state=mapping.get(wp, "unknown"))

        if msg == we.WM_SIZE:
            mapping = {
                we.SIZE_RESTORED:  "restored",
                we.SIZE_MINIMIZED: "minimized",
                we.SIZE_MAXIMIZED: "maximized",
            }
            state = mapping.get(wp)
            if state is None: return None
            return _system.WindowStateChange(state=state)

        if msg == we.WM_DISPLAYCHANGE:
            # lParam packs height<<16 | width; wParam is the bpp.
            width = lp & 0xFFFF
            height = (lp >> 16) & 0xFFFF
            return _system.DisplayChange(width=width, height=height, bpp=wp)

        if msg == we.WM_SETTINGCHANGE:
            return _system.SettingChange(area=extra)

        if msg == we.WM_CLIPBOARDUPDATE:
            return _system.ClipboardChange()

        if msg == we.WM_DROPFILES:
            paths = [p for p in (extra or "").split("\n") if p]
            return _system.FilesDropped(paths=paths)

        return None
