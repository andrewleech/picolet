"""Integration test — dashboard: verify metrics:tick events fire and DOM updates.

Requires:
  - Built binary at target/linux-x64/dashboard
  - Linux host (dashboard metrics only work on Linux)
  - PICOLET_TEST_MODE=1 (set by AppHarness)

FR-EX-5.
"""
import asyncio
import pytest

pytestmark = pytest.mark.asyncio


async def test_metrics_tick_fires(harness):
    """Wait for 2 consecutive metrics:tick events within 5 seconds."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    tick_count = await page.evaluate("""() => {
        return new Promise((resolve) => {
            let count = 0
            const unsub = window.picolet.on('metrics:tick', () => {
                count++
                if (count >= 2) {
                    unsub()
                    resolve(count)
                }
            })
            // 1Hz loop starts after 1s; allow 5s total.
            setTimeout(() => resolve(count), 5000)
        })
    }""")
    assert tick_count >= 2, f"expected >= 2 metrics:tick events, got {tick_count}"


async def test_cpu_widget_updates(harness):
    """Assert the CPU numeral DOM element is populated after first tick."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    # Wait for first tick to populate .cpu-value
    await page.wait_for_selector(".cpu-value", timeout=5000)
    val1 = await page.locator(".cpu-value").inner_text()

    # Wait for a second tick (1s interval; allow 3s)
    await asyncio.sleep(2.5)
    val2 = await page.locator(".cpu-value").inner_text()

    # Values should be present (not empty/dash) — DOM was populated.
    assert val1 not in ("", "--"), f"cpu-value was empty after first tick: {val1!r}"
    # val1 == val2 is permitted (CPU may not change). Only verify DOM is populated.
    assert val2 not in ("", "--"), f"cpu-value was empty after second tick: {val2!r}"


async def test_process_list_populated(harness):
    """Assert the top-procs table has at least one row."""
    page = harness.page
    if page is None:
        pytest.skip("no inspector page")

    await page.wait_for_selector(".proc-row", timeout=5000)
    rows = page.locator(".proc-row")
    count = await rows.count()
    assert count >= 1, f"expected at least 1 process row, got {count}"
