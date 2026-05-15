# PH08 gate-12: Python emit -> JS on() -> JS postMessage echo.
#
# After the page signals it is ready, Python emits {event:"tick", data:{n:1}}.
# JS receives it via window.picolet.on("tick", ...) and posts an echo back.
# Python waits for the echo, asserts it, and prints PICOLET_WV_EVENT_OK.

import sys
import asyncio
import picolet
import picolet_ui


async def main_task(transport):
    # Step 1: wait for the page's "page-ready" postMessage.
    try:
        msg = await asyncio.wait_for(transport.recv(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("event-push: timed out waiting for page-ready\n")
        sys.exit(1)
    if not isinstance(msg, dict) or msg.get("event") != "page-ready":
        sys.stderr.write("event-push: expected page-ready, got: {}\n".format(msg))
        sys.exit(1)

    # Step 2: emit a tick event to JS (picolet.emit is async).
    await picolet.emit("tick", {"n": 1})

    # Step 3: wait for the echo back from JS.
    try:
        echo = await asyncio.wait_for(transport.recv(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("event-push: timed out waiting for tick-echo\n")
        sys.exit(1)

    if not isinstance(echo, dict) or echo.get("event") != "tick-echo":
        sys.stderr.write("event-push: unexpected echo: {}\n".format(echo))
        sys.exit(1)

    data = echo.get("data", {})
    if data.get("n") != 1:
        sys.stderr.write("event-push: wrong echo data: {}\n".format(data))
        sys.exit(1)

    print("PICOLET_WV_EVENT_OK")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=lambda: main_task(app.transport))


main()
