# PH07 gate-5 fixture entry point.
#
# Standard webview-app shape: import picolet_ui and call run().  The
# runtime auto-loads /rom/picolet.toml for [window] and [ui], opens the
# window, embeds the webview, loads /rom/ui/index.html, and enters the
# dispatcher+pump asyncio loop.
#
# The index.html, on load, sets document.title='LOADED' and posts
# back to the host via window.webkit.messageHandlers.picolet.postMessage.
# The host watches for that postMessage and prints PICOLET_WV_SANITY_OK
# then exits — this lets the gate-5 harness run under xvfb-run with
# a short timeout.

import sys
import asyncio

import picolet_ui


async def loaded_watcher(transport):
    """Wait for the page-loaded postMessage; print success; exit."""
    try:
        msg = await asyncio.wait_for(transport.recv(), 5.0)
    except asyncio.TimeoutError:
        sys.stderr.write("hello-webview-min: timed out waiting for loaded\n")
        sys.exit(1)
    if not isinstance(msg, dict) or msg.get("event") != "loaded":
        sys.stderr.write(
            "hello-webview-min: unexpected postMessage: {}\n".format(msg)
        )
        sys.exit(1)
    print("PICOLET_WV_SANITY_OK title=LOADED")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    # The Application's transport is now wired and the window is shown.
    # Use the same _loop.run as picolet_ui.run() but with our watcher as
    # the "main" coroutine — when it returns, picolet exits.
    return app.run(main=lambda: loaded_watcher(app.transport))


main()
