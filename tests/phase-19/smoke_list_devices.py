"""smoke_list_devices.py — Gate F smoke test.

Invokes the list_devices command via AppHarness and asserts the mock
returns a list of device dicts.

Usage:
    PICOLET_PYDFU_MOCK=1 python3 tests/phase-19/smoke_list_devices.py <binary>
"""
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

# Allow running with an explicit binary argument or default location.
if len(sys.argv) > 1:
    BINARY = Path(sys.argv[1])
else:
    BINARY = REPO_ROOT / "examples" / "pydfu" / "target" / "linux-x64" / "pydfu"


async def main():
    from picolet.testing import AppHarness
    async with AppHarness(str(BINARY), env={"PICOLET_PYDFU_MOCK": "1"}) as h:
        page = h.page
        if page is None:
            # xvfb-only: test the Python side directly via PICOLET_PYDFU_MOCK path.
            print("NOTE: no inspector page; testing Python-side mock directly")
            sys.path.insert(0, str(REPO_ROOT / "examples" / "pydfu" / "src"))
            import os
            os.environ["PICOLET_PYDFU_MOCK"] = "1"
            import pydfu_adapter
            devices = pydfu_adapter.list_dfu_devices()
            assert isinstance(devices, list), f"expected list, got {type(devices)}"
            print(f"list_devices (direct): OK ({len(devices)} device(s))")
            return

        devices = await page.evaluate("window.picolet.invoke('list_devices')")
        assert isinstance(devices, list), f"expected list, got {type(devices)}"
        print(f"list_devices: OK ({len(devices)} device(s))")


asyncio.run(main())
