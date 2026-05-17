"""Screenshot: device-list-empty — / route with PICOLET_PYDFU_MOCK_EMPTY=1."""
import asyncio
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent.parent / "target" / "linux-x64" / "pydfu"
OUT = Path(__file__).parent.parent / "device-list-empty.png"


async def main():
    async with AppHarness(
        str(BINARY),
        env={"PICOLET_PYDFU_MOCK": "1", "PICOLET_PYDFU_MOCK_EMPTY": "1"},
    ) as h:
        # Give the app time to open and the first poll to fire (returning empty).
        await asyncio.sleep(1.5)
        await h.screenshot(OUT)
        print(f"Captured: {OUT}")


asyncio.run(main())
