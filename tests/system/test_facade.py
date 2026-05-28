"""Tests for the picolet.system cross-platform façade.

The façade is exercised through a MockBackend injected via set_backend().
This covers:
  - attach() handle extraction (Window-like and raw-int forms)
  - decorator registration triggering backend subscribe/watch calls
  - close-veto contract: truthy handler -> backend.destroy(), falsy -> not
  - device-class filter (usb=True only fires for USB device paths)
  - power / session / window-state / clipboard / files-dropped dispatch
  - handler-exception isolation
  - multiple handlers per slot
"""

import asyncio
import pytest

import picolet.system as syse


# ---------------------------------------------------------------------------
# Mock backend.
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self.attached_handle = None
        self.detached = False
        self.destroyed = 0
        self.subscribed = []
        self.watched_devices = []
        self.watched_power = 0
        self.watched_session = 0
        self.watched_clipboard = 0
        self.accept_drop_calls = []
        self._queue = []

    # ---- protocol ----
    def attach(self, hwnd):    self.attached_handle = hwnd
    def detach(self):          self.detached = True
    def destroy(self):         self.destroyed += 1

    def subscribe(self, key, consume=False):
        self.subscribed.append((key, bool(consume)))

    def watch_devices(self, **kw):
        self.watched_devices.append(kw)

    def watch_power(self):     self.watched_power += 1
    def watch_session(self):   self.watched_session += 1
    def watch_clipboard(self): self.watched_clipboard += 1
    def accept_drop_files(self, enable):
        self.accept_drop_calls.append(bool(enable))

    def poll(self):
        out, self._queue = self._queue, []
        return out

    # ---- test helpers ----
    def push(self, ev):
        self._queue.append(ev)


@pytest.fixture(autouse=True)
def _reset():
    syse._reset_for_test()
    yield
    syse._reset_for_test()


@pytest.fixture
def backend():
    b = MockBackend()
    syse.set_backend(b)
    return b


# ---------------------------------------------------------------------------
# attach()
# ---------------------------------------------------------------------------


def test_attach_with_window_like_obj(backend):
    class Win:
        handle = 0x1234

    syse.attach(Win())
    assert backend.attached_handle == 0x1234


def test_attach_with_raw_int(backend):
    syse.attach(0xABCD)
    assert backend.attached_handle == 0xABCD


def test_attach_rejects_object_without_handle(backend):
    with pytest.raises(TypeError):
        syse.attach(object())


# ---------------------------------------------------------------------------
# Decorator registration triggers backend subscribes.
# ---------------------------------------------------------------------------


def test_on_close_request_subscribes_consume(backend):
    @syse.on_close_request
    def _h():
        return True

    assert ("close", True) in backend.subscribed


def test_on_device_change_default_is_usb(backend):
    @syse.on_device_change
    def _h(ev):
        pass

    assert backend.watched_devices == [{"usb": True, "hid": False, "comport": False}]


def test_on_device_change_with_hid(backend):
    @syse.on_device_change(hid=True)
    def _h(ev):
        pass

    assert backend.watched_devices == [{"usb": False, "hid": True, "comport": False}]


def test_on_power_change_calls_watch_power(backend):
    @syse.on_power_change
    def _h(ev):
        pass

    assert backend.watched_power == 1


def test_on_clipboard_calls_watch_clipboard(backend):
    @syse.on_clipboard
    def _h(ev):
        pass

    assert backend.watched_clipboard == 1


def test_on_files_dropped_calls_accept_drop_files(backend):
    @syse.on_files_dropped
    def _h(ev):
        pass

    assert backend.accept_drop_calls == [True]


# ---------------------------------------------------------------------------
# Close-veto contract.
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_close_allow_calls_backend_destroy(backend):
    called = []

    @syse.on_close_request
    def _h():
        called.append(1)
        return True       # allow

    _run(syse._dispatch_for_test(syse.CloseRequest()))
    assert called == [1]
    assert backend.destroyed == 1


def test_close_veto_does_not_destroy(backend):
    @syse.on_close_request
    def _h():
        return False      # veto

    _run(syse._dispatch_for_test(syse.CloseRequest()))
    assert backend.destroyed == 0


def test_close_allow_via_async_handler(backend):
    @syse.on_close_request
    async def _h():
        await asyncio.sleep(0)
        return True

    _run(syse._dispatch_for_test(syse.CloseRequest()))
    assert backend.destroyed == 1


# ---------------------------------------------------------------------------
# Device dispatch + filter.
# ---------------------------------------------------------------------------


def test_device_change_handler_fires(backend):
    rcvd = []

    @syse.on_device_change(usb=True)
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.DeviceChange(
        arrived=True, device_path="\\\\?\\USB#VID_0403&PID_6001#A12345"
    )))
    assert len(rcvd) == 1
    assert rcvd[0].arrived is True
    assert "USB#" in rcvd[0].device_path


def test_device_change_usb_filter_rejects_hid(backend):
    rcvd = []

    @syse.on_device_change(usb=True)
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.DeviceChange(
        arrived=True, device_path="\\\\?\\HID#VID_0001&PID_0002"
    )))
    assert rcvd == []


def test_device_change_no_filter_accepts_all(backend):
    rcvd = []

    @syse.on_device_change(usb=False, hid=False, comport=False)
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.DeviceChange(
        arrived=True, device_path="anything"
    )))
    assert len(rcvd) == 1


# ---------------------------------------------------------------------------
# Other event types.
# ---------------------------------------------------------------------------


def test_power_change_dispatch(backend):
    rcvd = []

    @syse.on_power_change
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.PowerChange(state="suspend")))
    assert rcvd[0].state == "suspend"


def test_session_change_dispatch(backend):
    rcvd = []

    @syse.on_session_change
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.SessionChange(state="lock")))
    assert rcvd[0].state == "lock"


def test_window_state_dispatch(backend):
    rcvd = []

    @syse.on_window_state
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.WindowStateChange(state="minimized")))
    assert rcvd[0].state == "minimized"


def test_files_dropped_dispatch(backend):
    rcvd = []

    @syse.on_files_dropped
    def _h(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.FilesDropped(paths=["C:\\a", "C:\\b"])))
    assert rcvd[0].paths == ["C:\\a", "C:\\b"]


# ---------------------------------------------------------------------------
# Robustness.
# ---------------------------------------------------------------------------


def test_handler_exception_does_not_block_others(backend):
    rcvd = []

    @syse.on_power_change
    def _bad(ev):
        raise RuntimeError("kaboom")

    @syse.on_power_change
    def _good(ev):
        rcvd.append(ev)

    _run(syse._dispatch_for_test(syse.PowerChange(state="suspend")))
    assert len(rcvd) == 1


def test_multiple_handlers_per_slot(backend):
    rcvd = []

    @syse.on_clipboard
    def _h1(ev):
        rcvd.append("a")

    @syse.on_clipboard
    def _h2(ev):
        rcvd.append("b")

    _run(syse._dispatch_for_test(syse.ClipboardChange()))
    assert rcvd == ["a", "b"]


def test_no_handler_for_event_type_is_safe(backend):
    # No subscribers — dispatching should not raise.
    _run(syse._dispatch_for_test(syse.DisplayChange(width=1920, height=1080, bpp=32)))
