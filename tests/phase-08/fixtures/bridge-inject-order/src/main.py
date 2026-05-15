# PH08 gate-10: bridge injection order fixture.
#
# Waits for a postMessage from the page.  The page checks whether
# window.picolet is defined when its first <script> executes, and posts
# back {event:"ready"} if yes or {event:"missing"} if no.
# Prints PICOLET_WV_BRIDGE_INJECT_OK on success.
#
# NOTE: use picolet.on() to receive events from JS, not transport.recv()
# directly — calling transport.recv() in user code races with the
# dispatcher for inbox messages.

import sys
import asyncio
import picolet
import picolet_ui


async def watcher():
    evt = asyncio.Event()
    result_holder = [None]

    def on_event(topic):
        def handler(data):
            result_holder[0] = topic
            evt.set()
        return handler

    picolet.on("ready", on_event("ready"))
    picolet.on("missing", on_event("missing"))

    try:
        await asyncio.wait_for(evt.wait(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("bridge-inject-order: timed out waiting for message\n")
        sys.exit(1)

    if result_holder[0] == "ready":
        print("PICOLET_WV_BRIDGE_INJECT_OK")
        sys.exit(0)
    else:
        sys.stderr.write(
            "bridge-inject-order: expected 'ready', got: {}\n".format(result_holder[0])
        )
        sys.exit(1)


def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)


main()
