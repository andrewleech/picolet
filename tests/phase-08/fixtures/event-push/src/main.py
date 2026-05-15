# PH08 gate-12: Python emit -> JS on() -> JS postMessage echo.
#
# After the page signals readiness, Python emits {event:"tick", data:{n:1}}.
# JS receives it via window.picolet.on("tick", ...) and posts an echo back.
# Python waits for the echo via picolet.on("tick-echo", ...), asserts it,
# and prints PICOLET_WV_EVENT_OK.
#
# NOTE: use picolet.on() to receive events from JS, not transport.recv()
# directly — calling transport.recv() in user code races with the
# dispatcher for inbox messages.

import sys
import asyncio
import picolet
import picolet_ui


async def main_task():
    # Step 1: wait for the page's "page-ready" picolet.on event.
    ready_evt = asyncio.Event()

    def on_page_ready(data):
        ready_evt.set()

    picolet.on("page-ready", on_page_ready)

    try:
        await asyncio.wait_for(ready_evt.wait(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("event-push: timed out waiting for page-ready\n")
        sys.exit(1)

    # Step 2: emit a tick event to JS (picolet.emit is async).
    await picolet.emit("tick", {"n": 1})

    # Step 3: wait for the echo back from JS via picolet.on("tick-echo").
    echo_evt = asyncio.Event()
    echo_holder = [None]

    def on_tick_echo(data):
        echo_holder[0] = data
        echo_evt.set()

    picolet.on("tick-echo", on_tick_echo)

    try:
        await asyncio.wait_for(echo_evt.wait(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("event-push: timed out waiting for tick-echo\n")
        sys.exit(1)

    data = echo_holder[0]
    if data.get("n") != 1:
        sys.stderr.write("event-push: wrong echo data: {}\n".format(data))
        sys.exit(1)

    print("PICOLET_WV_EVENT_OK")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=main_task)


main()
