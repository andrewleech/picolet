# PH08 gates 11 and 13: invoke round-trip and error propagation fixture.
#
# Registers two commands:
#   greet(args) -> "Hello, <name>"  (gate 11)
#   boom()      -> raises ValueError("bad input")  (gate 13)
#
# Waits for two postMessage events posted by index.html after both
# invoke() calls complete, then prints the gate tokens and exits.

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


async def watcher(transport):
    results = {}
    # Collect up to 2 events with a combined timeout.
    deadline = asyncio.get_event_loop().time() + 10.0
    while len(results) < 2:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(transport.recv(), remaining)
        except asyncio.TimeoutError:
            break
        if not isinstance(msg, dict):
            continue
        ev = msg.get("event")
        if ev in ("result", "err"):
            results[ev] = msg.get("data")

    if "result" not in results:
        sys.stderr.write("invoke-roundtrip: did not receive 'result' event\n")
        sys.exit(1)
    if "err" not in results:
        sys.stderr.write("invoke-roundtrip: did not receive 'err' event\n")
        sys.exit(1)

    r = results["result"]
    if r.get("value") != "Hello, World":
        sys.stderr.write(
            "invoke-roundtrip: unexpected greet result: {}\n".format(r)
        )
        sys.exit(1)
    print("PICOLET_WV_INVOKE_OK")

    e = results["err"]
    if e.get("name") != "ValueError" or e.get("message") != "bad input":
        sys.stderr.write(
            "invoke-roundtrip: unexpected error result: {}\n".format(e)
        )
        sys.exit(1)
    print("PICOLET_WV_ERROR_OK")
    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=lambda: watcher(app.transport))


main()
