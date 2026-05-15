# PH08 gates 11 and 13: invoke round-trip and error propagation fixture.
#
# Registers two commands:
#   greet(args) -> "Hello, <name>"  (gate 11)
#   boom()      -> raises ValueError("bad input")  (gate 13)
#
# JS calls both via window.picolet.invoke() and posts results back via
# window.picolet.emit() / window.webkit.messageHandlers.picolet.postMessage().
# Python uses picolet.on() to receive those result events — the dispatcher
# routes incoming event messages to picolet.on() handlers without stealing
# them from user code.

import sys
import asyncio
import picolet
import picolet_ui


@picolet.command
async def greet(args):
    return "Hello, " + args["name"]


@picolet.command
async def boom(args):
    raise ValueError("bad input")


async def watcher():
    result_evt = asyncio.Event()
    err_evt = asyncio.Event()
    result_holder = [None]
    err_holder = [None]

    def on_result(data):
        sys.stderr.write("DEBUG on_result: " + str(data) + "\n")
        result_holder[0] = data
        result_evt.set()

    def on_err(data):
        sys.stderr.write("DEBUG on_err: " + str(data) + "\n")
        err_holder[0] = data
        err_evt.set()

    picolet.on("result", on_result)
    picolet.on("err", on_err)

    try:
        await asyncio.wait_for(result_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("invoke-roundtrip: timed out waiting for 'result' event\n")
        sys.exit(1)

    try:
        await asyncio.wait_for(err_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("invoke-roundtrip: timed out waiting for 'err' event\n")
        sys.exit(1)

    r = result_holder[0]
    if r.get("value") != "Hello, World":
        sys.stderr.write(
            "invoke-roundtrip: unexpected greet result: {}\n".format(r)
        )
        sys.exit(1)
    print("PICOLET_WV_INVOKE_OK")

    e = err_holder[0]
    if e.get("name") != "ValueError" or e.get("message") != "bad input":
        sys.stderr.write(
            "invoke-roundtrip: unexpected error result: {}\n".format(e)
        )
        sys.exit(1)
    print("PICOLET_WV_ERROR_OK")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)


main()
