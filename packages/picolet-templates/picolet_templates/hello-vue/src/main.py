# {{name}} — a minimal Vue picolet app.
import asyncio
import platform
import sys
import time

import picolet
import picolet_ui as ui


@picolet.command
async def ping(args):
    """Round-trip: JS sends ts, Python returns {pong: ts}."""
    ts = args.get("ts") if isinstance(args, dict) else None
    return {"pong": ts}


@picolet.command
async def get_info(args):
    """Return Python runtime platform/version info."""
    return {
        "platform": sys.platform,
        "python": "MicroPython",
        "uname": str(platform.uname()),
    }


async def _ticker():
    """Emit a ticker:tick event every second with the current Unix timestamp."""
    while True:
        await asyncio.sleep(1)
        picolet.emit("ticker:tick", {"ts": int(time.time())})


def main():
    app = ui.Application()
    return app.run(main=_ticker)


main()
