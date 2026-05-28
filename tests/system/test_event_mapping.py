"""Tests for picolet._system_win._map_event — the Win32 → picolet.system Ev
translation.

The C side emits JSON events of the form
    {"msg": int, "wp": int, "lp": int, "extra": "..."}

These tests feed synthetic dicts to WinBackend._map_event and verify the
mapping.  No FFI, no Windows binary required.
"""

import pytest

import picolet.system as syse
from picolet._system_win import WinBackend
from picolet_ui import _win_events as we


@pytest.fixture
def backend():
    b = WinBackend()
    # Bypass attach() so _hwnd is None — _map_event doesn't touch the HWND.
    return b


def test_wm_close_maps_to_close_request(backend):
    ev = backend._map_event({"msg": we.WM_CLOSE, "wp": 0, "lp": 0})
    assert isinstance(ev, syse.CloseRequest)


def test_wm_devicechange_arrival_with_path(backend):
    ev = backend._map_event({
        "msg": we.WM_DEVICECHANGE,
        "wp": we.DBT_DEVICEARRIVAL,
        "lp": 0,
        "extra": "\\\\?\\USB#VID_0403&PID_6001#A12345",
    })
    assert isinstance(ev, syse.DeviceChange)
    assert ev.arrived is True
    assert ev.device_path.endswith("A12345")


def test_wm_devicechange_remove(backend):
    ev = backend._map_event({
        "msg": we.WM_DEVICECHANGE,
        "wp": we.DBT_DEVICEREMOVECOMPLETE,
        "lp": 0,
        "extra": "\\\\?\\USB#VID_X",
    })
    assert isinstance(ev, syse.DeviceChange)
    assert ev.arrived is False


def test_wm_devicechange_devnodes_ignored(backend):
    ev = backend._map_event({
        "msg": we.WM_DEVICECHANGE,
        "wp": 0x0007,  # DBT_DEVNODES_CHANGED — too noisy to surface
        "lp": 0,
    })
    assert ev is None


def test_wm_powerbroadcast_suspend(backend):
    ev = backend._map_event({
        "msg": we.WM_POWERBROADCAST,
        "wp": we.PBT_APMSUSPEND,
        "lp": 0,
    })
    assert isinstance(ev, syse.PowerChange)
    assert ev.state == "suspend"


def test_wm_powerbroadcast_resume_variants(backend):
    for wp in (we.PBT_APMRESUMEAUTOMATIC, we.PBT_APMRESUMESUSPEND):
        ev = backend._map_event({"msg": we.WM_POWERBROADCAST, "wp": wp, "lp": 0})
        assert ev.state == "resume", "wp={:#x}".format(wp)


def test_wm_powerbroadcast_unknown_subcode(backend):
    ev = backend._map_event({"msg": we.WM_POWERBROADCAST, "wp": 0xdead, "lp": 0})
    assert ev.state == "unknown"


def test_wm_wtssession_change_lock_unlock(backend):
    lock = backend._map_event({"msg": we.WM_WTSSESSION_CHANGE, "wp": 7, "lp": 0})
    unlock = backend._map_event({"msg": we.WM_WTSSESSION_CHANGE, "wp": 8, "lp": 0})
    assert lock.state == "lock"
    assert unlock.state == "unlock"


def test_wm_size_states(backend):
    cases = [
        (we.SIZE_RESTORED,  "restored"),
        (we.SIZE_MINIMIZED, "minimized"),
        (we.SIZE_MAXIMIZED, "maximized"),
    ]
    for wp, expected in cases:
        ev = backend._map_event({"msg": we.WM_SIZE, "wp": wp, "lp": 0})
        assert ev.state == expected


def test_wm_displaychange_unpacks_dims(backend):
    # height << 16 | width
    lp = (1080 << 16) | 1920
    ev = backend._map_event({"msg": we.WM_DISPLAYCHANGE, "wp": 32, "lp": lp})
    assert ev.width == 1920
    assert ev.height == 1080
    assert ev.bpp == 32


def test_wm_settingchange_carries_area_name(backend):
    ev = backend._map_event({
        "msg": we.WM_SETTINGCHANGE,
        "wp": 0,
        "lp": 0,
        "extra": "ImmersiveColorSet",
    })
    assert ev.area == "ImmersiveColorSet"


def test_wm_clipboardupdate(backend):
    ev = backend._map_event({"msg": we.WM_CLIPBOARDUPDATE, "wp": 0, "lp": 0})
    assert isinstance(ev, syse.ClipboardChange)


def test_wm_dropfiles_splits_paths(backend):
    ev = backend._map_event({
        "msg": we.WM_DROPFILES,
        "wp": 0, "lp": 0,
        "extra": "C:\\a.txt\nC:\\b.txt\n",
    })
    assert ev.paths == ["C:\\a.txt", "C:\\b.txt"]


def test_unknown_message_returns_none(backend):
    ev = backend._map_event({"msg": 0xDEAD, "wp": 0, "lp": 0})
    assert ev is None


def test_guid_constants_are_16_bytes():
    """Sanity check: the well-known GUID byte buffers are exactly 16 bytes
    little-endian, as Win32 expects in DEV_BROADCAST_DEVICEINTERFACE."""
    assert len(we.GUID_DEVINTERFACE_USB_DEVICE) == 16
    assert len(we.GUID_DEVINTERFACE_HID) == 16
    assert len(we.GUID_DEVINTERFACE_COMPORT) == 16


def test_guid_usb_device_matches_known_value():
    """A5DCBF10-6530-11D2-901F-00C04FB951ED, encoded as bytes_le."""
    import uuid
    expected = uuid.UUID("a5dcbf10-6530-11d2-901f-00c04fb951ed").bytes_le
    assert bytes(we.GUID_DEVINTERFACE_USB_DEVICE) == expected
