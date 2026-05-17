"""Screenshot: flash-complete — app window (inspector path shows dfu:done state)."""
import asyncio
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent.parent / "target" / "linux-x64" / "pydfu"
OUT = Path(__file__).parent.parent / "flash-complete.png"


async def main():
    async with AppHarness(str(BINARY), env={"PICOLET_PYDFU_MOCK": "1"}) as h:
        await asyncio.sleep(1.5)
        await h.screenshot(OUT)
        print(f"Captured (app window): {OUT}")


asyncio.run(main())
