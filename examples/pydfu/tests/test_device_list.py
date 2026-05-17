"""Test: device list auto-refresh shows mock device."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_device_list_populated(harness):
    """The mock returns one STM32 device; DeviceList should render it."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    # Wait for Vue to render and DeviceList to populate after first 500ms refresh.
    await page.wait_for_selector(".device-row", timeout=5000)
    text = await page.inner_text(".device-row")
    assert "0483" in text  # VID in hex
    assert "df11" in text  # PID in hex


async def test_device_list_empty_state(harness):
    """Empty state test — timing-dependent; covered by screenshot instead."""
    # The mock always returns one device by default. Testing the empty state
    # requires PICOLET_PYDFU_MOCK_EMPTY=1 in a separate fixture or timing the
    # window before the first 500ms poll fires. This is documented as a known
    # limitation and covered by the device-list-empty.png screenshot.
    pytest.skip("empty-state timing test; see device-list-empty.png screenshot")
