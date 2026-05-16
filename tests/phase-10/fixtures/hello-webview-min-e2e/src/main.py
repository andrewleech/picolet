# PH10 e2e fixture: drives gates 8, 9, 10, 11 in a single WSL-interop run.
#
# Sentinel-token contract (driver greps for these on stdout):
#   PICOLET_PH10_BRIDGE_INJECT_OK  - window.picolet defined when first user
#                                  script runs (FR-WV-4)
#   PICOLET_PH10_INVOKE_OK         - invoke roundtrip
#   PICOLET_PH10_ERROR_OK          - error propagation
#   PICOLET_PH10_EVENT_OK          - picolet.emit -> picolet.on roundtrip
#
# JS sends results back via window.picolet.emit(...) which routes through
# the AD5-feature-detected channel (chrome.webview on Windows).

import sys
import asyncio
import picolet
import picolet_ui


@picolet.command
async def greet(args):
    name = args.get("name", "World")
    return "Hello, " + name


@picolet.command
async def fail_example(args):
    raise ValueError("this is an example error")


async def watcher():
    bridge_evt = asyncio.Event()
    invoke_evt = asyncio.Event()
    error_evt  = asyncio.Event()
    event_evt  = asyncio.Event()
    ready_evt  = asyncio.Event()

    def on_bridge_check(data):
        if data.get("present") is True:
            print("PICOLET_PH10_BRIDGE_INJECT_OK")
            bridge_evt.set()
        else:
            sys.stderr.write("hello-webview-min-e2e: bridge missing\n")

    def on_invoke_result(data):
        if data.get("value") == "Hello, World":
            print("PICOLET_PH10_INVOKE_OK")
            invoke_evt.set()
        else:
            sys.stderr.write(
                "hello-webview-min-e2e: unexpected invoke result: {}\n"
                .format(data)
            )

    def on_error_result(data):
        if (data.get("name") == "ValueError" and
                "this is an example error" in data.get("message", "")):
            print("PICOLET_PH10_ERROR_OK")
            error_evt.set()
        else:
            sys.stderr.write(
                "hello-webview-min-e2e: unexpected error result: {}\n"
                .format(data)
            )

    def on_event_echo(_data):
        print("PICOLET_PH10_EVENT_OK")
        event_evt.set()

    def on_page_ready(_data):
        ready_evt.set()

    picolet.on("bridge-check",  on_bridge_check)
    picolet.on("invoke-result", on_invoke_result)
    picolet.on("error-result",  on_error_result)
    picolet.on("event-echo",    on_event_echo)
    picolet.on("page-ready",    on_page_ready)

    for evt, label in [
        (bridge_evt, "bridge-check"),
        (invoke_evt, "invoke-result"),
        (error_evt,  "error-result"),
    ]:
        try:
            await asyncio.wait_for(evt.wait(), 25.0)
        except asyncio.TimeoutError:
            sys.stderr.write(
                "hello-webview-min-e2e: timed out on {}\n".format(label)
            )
            sys.exit(1)

    try:
        await asyncio.wait_for(ready_evt.wait(), 15.0)
    except asyncio.TimeoutError:
        sys.stderr.write("hello-webview-min-e2e: timed out on page-ready\n")
        sys.exit(1)

    await picolet.emit("server-push", {})

    try:
        await asyncio.wait_for(event_evt.wait(), 15.0)
    except asyncio.TimeoutError:
        sys.stderr.write("hello-webview-min-e2e: timed out on event-echo\n")
        sys.exit(1)

    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)


main()
