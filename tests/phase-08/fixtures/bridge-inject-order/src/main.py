# PH08 gate-10: bridge injection order fixture.
#
# Waits for a postMessage from the page.  The page checks whether
# window.picolet is defined when its first <script> executes, and posts
# back {event:"ready"} if yes or {event:"missing"} if no.
# Prints PICOLET_WV_BRIDGE_INJECT_OK on success.

import sys
import asyncio
import picolet_ui


async def watcher(transport):
    try:
        msg = await asyncio.wait_for(transport.recv(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("bridge-inject-order: timed out waiting for message\n")
        sys.exit(1)
    if not isinstance(msg, dict):
        sys.stderr.write("bridge-inject-order: unexpected message type: {}\n".format(type(msg)))
        sys.exit(1)
    event = msg.get("event")
    if event == "ready":
        print("PICOLET_WV_BRIDGE_INJECT_OK")
        sys.exit(0)
    else:
        sys.stderr.write(
            "bridge-inject-order: expected event='ready', got: {}\n".format(msg)
        )
        sys.exit(1)


def main():
    app = picolet_ui.Application()
    return app.run(main=lambda: watcher(app.transport))


main()
