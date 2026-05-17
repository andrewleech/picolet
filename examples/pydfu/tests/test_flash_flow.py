"""Test: full flash flow with mock USB."""
import asyncio
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio

DFU_FIXTURE = Path(__file__).parent / "fixtures" / "test.dfu"


async def test_flash_complete(harness):
    """Navigate to /flash, load DFU file, start flash, wait for done state."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    await page.goto("picolet:///ui/index.html#/flash")
    await page.wait_for_selector(".path-input", timeout=5000)
    await page.fill(".path-input", str(DFU_FIXTURE))
    await page.click(".btn-read-dfu")
    # Wait for the elements table to appear (read_dfu returned)
    await page.wait_for_selector(".dfu-elements-table", timeout=8000)
    # Need a selected device — inject one via evaluate.
    # The mock's device_id is "1:1"; we can invoke list_devices and select.
    await page.evaluate("""
        async () => {
            const devices = await window.picolet.invoke('list_devices');
            if (devices && devices.length > 0) {
                // Trigger flash with the first device
                window.__test_device_id = devices[0].bus + ':' + devices[0].addr;
            }
        }
    """)
    # Start flash via the Python IPC directly (the UI requires a selected device
    # from the provider context; use invoke directly in the test).
    flash_p = page.evaluate(
        "window.picolet.invoke('flash', {device_id: '1:1', dfu_path: '" + str(DFU_FIXTURE) + "'})"
    )
    # Wait for dfu:done event to be reflected
    await page.wait_for_selector(".flash-status-done", timeout=15000)
    text = await page.inner_text(".flash-status-done")
    assert "COMPLETE" in text.upper()


async def test_flash_error(harness):
    """Error sentinel path: .error.dfu suffix triggers dfu:error immediately."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    await page.goto("picolet:///ui/index.html#/flash")
    await page.wait_for_selector(".path-input", timeout=5000)
    error_path = str(DFU_FIXTURE.with_suffix("")) + ".error.dfu"
    await page.fill(".path-input", error_path)
    # Invoke flash directly (bypasses read_dfu; error sentinel fires before parse)
    await page.evaluate(
        "window.picolet.invoke('flash', {device_id: '1:1', dfu_path: '" + error_path + "'})"
    )
    await page.wait_for_selector(".flash-status-error", timeout=8000)
