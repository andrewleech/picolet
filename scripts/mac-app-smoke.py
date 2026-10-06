"""Exercise a packaged example app through its native WKWebView and IPC bridge."""

import asyncio
import json
import os
import struct
import sys
import time

import ffi
import uctypes


_APP_CHECKS = {
    "notes": (
        ".note-list",
        "list_notes",
        None,
    ),
    "pydfu": (
        ".device-list",
        "list_devices",
        None,
    ),
    "config-editor": (
        ".picker-view",
        "list_schemas",
        None,
    ),
    "dashboard": (
        ".grid-cpu",
        "get_history",
        "__ready__",
    ),
}


async def _wait_for_result(result_box, deadline, webview, script):
    while result_box[0] is None:
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            raise RuntimeError("frontend did not complete its bridge round-trip before timeout")
        webview.eval_js(script)
        await asyncio.sleep(0.05)
    report = result_box[0]
    if not report.get("ready"):
        raise RuntimeError("frontend did not report document and app readiness")
    if "error" in report:
        raise RuntimeError("{} bridge command failed: {}".format(report["command"], report["error"]))

    value = report.get("result")
    app = report["app"]
    if app in ("notes", "pydfu", "config-editor") and not isinstance(value, list):
        raise RuntimeError("{} returned an unexpected command result".format(report["command"]))
    if app == "pydfu" and not any(
        device.get("vid") == 0x0483 and device.get("pid") == 0xDF11
        and device.get("id") == "1:1" for device in value
    ):
        raise RuntimeError("mock DFU backend did not enumerate its STM32 DFU target")
    if app == "dashboard" and (
        not isinstance(value, dict) or not isinstance(value.get("history"), list)
    ):
        raise RuntimeError("dashboard returned an unexpected history result")
    return report


def _script(app, selector, command, ready_flag):
    app_json = json.dumps(app)
    selector_json = json.dumps(selector)
    command_json = json.dumps(command)
    ready_js = "window.picolet.__ready__ === true" if ready_flag else "true"
    args_js = "null"
    return """(function() {
      if (window.__picoletMacAppSmoke) return;
      window.__picoletMacAppSmoke = true;
      function check() {
        if (document.readyState !== 'complete' || !window.picolet ||
            !document.querySelector(%s) || !(%s)) {
          window.setTimeout(check, 50);
          return;
        }
        window.picolet.invoke(%s, %s).then(function(result) {
          document.fonts.ready.then(function() {
            // Animation callbacks precede paint; the second frame crosses a paint boundary.
            window.requestAnimationFrame(function() {
              window.requestAnimationFrame(function() {
                window.webkit.messageHandlers.picolet.postMessage(JSON.stringify({
                  event: 'mac-app-smoke', data: {
                    app: %s, command: %s, ready: true, result: result
                  }
                }));
              });
            });
          });
        }, function(error) {
          window.webkit.messageHandlers.picolet.postMessage(JSON.stringify({
            event: 'mac-app-smoke', data: {
              app: %s, command: %s, ready: true,
              error: String(error && error.message || error)
            }
          }));
        });
      }
      check();
    })();""" % (
        selector_json,
        ready_js,
        command_json,
        args_js,
        app_json,
        command_json,
        app_json,
        command_json,
    )


def _snapshot(webview, path):
    from picolet_ui import _mac_ffi

    out = bytearray(8)
    size = bytearray(8)
    rc = _mac_ffi.picolet_wkwv_take_snapshot(
        webview.handle, uctypes.addressof(out), uctypes.addressof(size)
    )
    if rc != 0:
        raise RuntimeError("Native WKWebView snapshot failed: {}".format(rc))
    pointer = struct.unpack("<Q", out)[0]
    length = struct.unpack("<Q", size)[0]
    if not pointer:
        raise RuntimeError("Native WKWebView snapshot returned a null buffer")
    free = ffi.open(None).func("v", "free", "p")
    try:
        png = uctypes.bytearray_at(pointer, length)
        if bytes(png[:8]) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Native WKWebView snapshot is not a PNG")
        with open(path, "wb") as output:
            output.write(png)
    finally:
        free(pointer)


def install(app, output_path):
    if sys.platform != "darwin":
        raise RuntimeError("This probe requires the native Darwin webview runtime")
    selector, command, ready_flag = _APP_CHECKS[app]
    if app == "pydfu":
        os.putenv("PICOLET_PYDFU_MOCK", "1")

    from picolet_ui import _loop
    original_run = _loop.run
    result_box = [None]

    deadline = time.ticks_add(time.ticks_ms(), 30000)

    def run_with_observer(transport, main=None, pump=None):
        previous_hook = transport._raw_hook

        def observe_raw(raw):
            if previous_hook is not None:
                previous_hook(raw)
            try:
                message = json.loads(raw)
                if message.get("event") == "mac-app-smoke":
                    result_box[0] = message.get("data")
            except Exception:
                pass

        transport._raw_hook = observe_raw
        webview = transport._webview
        if webview is None:
            raise RuntimeError("Application did not bind its real WebviewTransport")
        script = _script(app, selector, command, ready_flag)

        async def observe_main():
            existing_task = None
            if main is not None:
                existing = main() if callable(main) else main
                existing_task = asyncio.create_task(existing)
            try:
                report = await _wait_for_result(result_box, deadline, webview, script)
                _snapshot(webview, output_path)
                print("{}: {} returned {}; captured {}".format(
                    report["app"], report["command"],
                    json.dumps(report.get("result"), separators=(",", ":")), output_path
                ))
            finally:
                if existing_task is not None:
                    existing_task.cancel()
                    try:
                        await existing_task
                    except asyncio.CancelledError:
                        pass

        return original_run(transport, main=observe_main, pump=pump)

    _loop.run = run_with_observer
