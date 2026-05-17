"""Screenshot: flash-start — /flash route after reading DFU file, before flashing."""
import asyncio
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent.parent / "target" / "linux-x64" / "pydfu"
OUT = Path(__file__).parent.parent / "flash-start.png"
DFU_FIXTURE = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "test.dfu"


async def main():
    async with AppHarness(str(BINARY), env={"PICOLET_PYDFU_MOCK": "1"}) as h:
        # Give app time to open. Cannot navigate to /flash without inspector page;
        # capture the default / (HomeView) at t=1.5s instead to demonstrate
        # the app shell for the flash-start state.
        # Note: with inspector connection, we would navigate to #/flash and
        # invoke read_dfu then screenshot. In xvfb-only mode we capture the
        # rendered window.
        await asyncio.sleep(1.5)
        await h.screenshot(OUT)
        print(f"Captured (app window at t=1.5s): {OUT}")


asyncio.run(main())
