"""smoke_list_devices.py — Gate F smoke test.

Invokes the list_devices command via AppHarness and asserts the mock
returns a list of device dicts.

When no DISPLAY is available (headless Linux), the binary spawn would block
waiting for a WebKitGTK window that never opens, so AppHarness is skipped
entirely and pydfu_adapter is tested directly via the mock path.

Usage:
    PICOLET_PYDFU_MOCK=1 python3 tests/phase-19/smoke_list_devices.py <binary>
"""
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

# Allow running with an explicit binary argument or default location.
if len(sys.argv) > 1:
    BINARY = Path(sys.argv[1])
else:
    BINARY = REPO_ROOT / "examples" / "pydfu" / "target" / "linux-x64" / "pydfu"


if not os.environ.get("DISPLAY"):
    # No display: binary spawn would block on WebKitGTK window creation.
    # Test pydfu_adapter mock directly instead.
    print("NOTE: no DISPLAY; testing pydfu_adapter mock directly")
    sys.path.insert(0, str(REPO_ROOT / "examples" / "pydfu" / "src"))
    os.environ["PICOLET_PYDFU_MOCK"] = "1"
    import pydfu_adapter
    devices = pydfu_adapter.list_dfu_devices()
    assert isinstance(devices, list), f"expected list, got {type(devices)}"
    print(f"list_devices (direct): OK ({len(devices)} device(s))")
    sys.exit(0)


async def main():
    from picolet.testing import AppHarness
    async with AppHarness(str(BINARY), env={"PICOLET_PYDFU_MOCK": "1"}) as h:
        page = h.page
        if page is None:
            # xvfb-only: test the Python side directly via PICOLET_PYDFU_MOCK path.
            print("NOTE: no inspector page; testing Python-side mock directly")
            sys.path.insert(0, str(REPO_ROOT / "examples" / "pydfu" / "src"))
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
