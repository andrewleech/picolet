# PH08 SQE fixture: JS window.picolet.emit -> Python picolet.on subscriber.
#
# JS page calls window.picolet.emit("user-action", {button:"submit"}).
# Python waits for the event via picolet.on() and asserts the payload.
# Prints PICOLET_WV_EMIT_OK on success.

import sys
import asyncio
import picolet
import picolet_ui


async def main_task():
    evt = asyncio.Event()
    holder = [None]

    def on_user_action(data):
        holder[0] = data
        evt.set()

    picolet.on("user-action", on_user_action)

    try:
        await asyncio.wait_for(evt.wait(), 8.0)
    except asyncio.TimeoutError:
        sys.stderr.write("js-emit-to-python: timed out waiting for user-action event\n")
        sys.exit(1)

    data = holder[0]
    if not isinstance(data, dict) or data.get("button") != "submit":
        sys.stderr.write(
            "js-emit-to-python: unexpected payload: {}\n".format(data)
        )
        sys.exit(1)

    print("PICOLET_WV_EMIT_OK")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=main_task)


main()
