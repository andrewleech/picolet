"""Screenshot: device-list-populated — / route with one mock device."""
import asyncio
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent.parent / "target" / "linux-x64" / "pydfu"
OUT = Path(__file__).parent.parent / "device-list-populated.png"


async def main():
    async with AppHarness(str(BINARY), env={"PICOLET_PYDFU_MOCK": "1"}) as h:
        # Give the app time to render the device list (first 500ms poll fires).
        await asyncio.sleep(1.5)
        await h.screenshot(OUT)
        print(f"Captured: {OUT}")


asyncio.run(main())
