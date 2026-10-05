"""Exercise native WKWebView message delivery and PNG capture on MicroPython."""

import json
import struct
import sys
import time

import ffi
import uctypes
from picolet_ui import _mac_ffi
from picolet_ui._webview import Webview, WebviewTransport
from picolet_ui._window import Window


def main():
    if sys.platform != "darwin":
        raise RuntimeError("This probe requires the native Darwin webview runtime")
    if len(sys.argv) != 2:
        raise RuntimeError("Pass the output PNG path")

    messages = []
    transport = WebviewTransport()
    transport._raw_hook = messages.append
    window = Window(title="Native WKWebView smoke", size=[320, 240], resizable=False)
    view = Webview(window, transport=transport)
    view.load_html(
        "<!doctype html><html><body style='margin:0;background:rgb(51,102,153)'>"
        "<script>window.webkit.messageHandlers.picolet.postMessage("
        "JSON.stringify({event:'native-smoke',data:{colour:'#336699'}}));</script>"
        "</body></html>"
    )
    window.show()

    deadline = time.ticks_add(time.ticks_ms(), 10000)
    while not messages and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        _mac_ffi.picolet_wkwv_pump_messages(0.01)
        pointer = _mac_ffi.picolet_wkwv_poll_inbound()
        if pointer:
            try:
                message = _mac_ffi.ffi_string(pointer)
            finally:
                _mac_ffi.picolet_wkwv_free_inbound(pointer)
            transport._deliver_raw(message)
    if not messages:
        raise RuntimeError("WKWebView did not deliver the page's script message")
    expected = {"event": "native-smoke", "data": {"colour": "#336699"}}
    if json.loads(messages[0]) != expected:
        raise RuntimeError("WKWebView delivered an unexpected script message")

    out = bytearray(8)
    size = bytearray(8)
    rc = _mac_ffi.picolet_wkwv_take_snapshot(
        view.handle, uctypes.addressof(out), uctypes.addressof(size)
    )
    if rc != 0:
        raise RuntimeError(f"Native WKWebView snapshot failed: {rc}")
    pointer = struct.unpack("<Q", out)[0]
    length = struct.unpack("<Q", size)[0]
    if not pointer:
        raise RuntimeError("Native WKWebView snapshot returned a null buffer")
    free = ffi.open(None).func("v", "free", "p")
    try:
        png = uctypes.bytearray_at(pointer, length)
        if bytes(png[:8]) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Native WKWebView snapshot is not a PNG")
        with open(sys.argv[1], "wb") as output:
            output.write(png)
    finally:
        free(pointer)
    print("Native WKWebView delivered its script message and captured PNG")


main()
