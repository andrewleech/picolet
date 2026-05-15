# PH09 e2e fixture: invoke, error-propagation, and Python-emit gates.
#
# Registers greet (success path), fail_example (error path), and a
# watcher coroutine that coordinates all three gates then calls sys.exit(0).
#
# Gate C: PICOLET_PH09_INVOKE_OK — greet("World") returns "Hello, World".
# Gate D: PICOLET_PH09_ERROR_OK  — fail_example raises ValueError with expected message.
# Gate E: PICOLET_PH09_EVENT_OK  — picolet.emit("server-push") reaches JS on() handler.
#
# Ordering for the event gate (mirrors PH08 event-push pattern):
#   JS registers on("server-push") BEFORE posting page-ready.
#   Python waits for page-ready BEFORE emitting server-push.

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
    invoke_evt = asyncio.Event()
    error_evt  = asyncio.Event()
    event_evt  = asyncio.Event()
    ready_evt  = asyncio.Event()

    def on_invoke_result(data):
        if data.get("value") == "Hello, World":
            print("PICOLET_PH09_INVOKE_OK")
            invoke_evt.set()
        else:
            sys.stderr.write("hello-webview-e2e: unexpected invoke result: {}\n".format(data))

    def on_error_result(data):
        if data.get("name") == "ValueError" and \
           "this is an example error" in data.get("message", ""):
            print("PICOLET_PH09_ERROR_OK")
            error_evt.set()
        else:
            sys.stderr.write("hello-webview-e2e: unexpected error result: {}\n".format(data))

    def on_event_echo(data):
        print("PICOLET_PH09_EVENT_OK")
        event_evt.set()

    def on_page_ready(data):
        ready_evt.set()

    picolet.on("invoke-result", on_invoke_result)
    picolet.on("error-result", on_error_result)
    picolet.on("event-echo", on_event_echo)
    picolet.on("page-ready", on_page_ready)

    # Gates C and D: wait for invoke and error results from JS.
    for evt, label in [
        (invoke_evt, "invoke-result"),
        (error_evt,  "error-result"),
    ]:
        try:
            await asyncio.wait_for(evt.wait(), 20.0)
        except asyncio.TimeoutError:
            sys.stderr.write("hello-webview-e2e: timed out on {}\n".format(label))
            sys.exit(1)

    # Gate E: wait for JS to register on("server-push") and signal readiness.
    try:
        await asyncio.wait_for(ready_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("hello-webview-e2e: timed out on page-ready\n")
        sys.exit(1)

    # Emit the server-push event now that JS handler is registered.
    await picolet.emit("server-push", {})

    try:
        await asyncio.wait_for(event_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("hello-webview-e2e: timed out on event-echo\n")
        sys.exit(1)

    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)


main()
