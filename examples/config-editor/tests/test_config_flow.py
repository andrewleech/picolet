"""Integration test: load TOML → modify → validate → save → diff confirmation."""
import asyncio
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


async def test_load_and_edit_toml(harness, config_dir):
    cfg_base, toml_file = config_dir
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")

    # Picker view — fill in path and load.
    await page.wait_for_selector(".picker-view", timeout=5000)
    await page.fill("input.file-path-input", str(toml_file))
    await page.click("button.btn-load")

    # Edit view — server.port field should be visible.
    await page.wait_for_selector(".edit-view", timeout=5000)
    port_input = page.locator("input[data-key='server.port']")
    await port_input.wait_for(state="visible", timeout=3000)
    old_val = await port_input.input_value()
    assert old_val == "8080"

    # Modify port.
    await port_input.fill("9090")


async def test_validate_passes(harness, config_dir):
    cfg_base, toml_file = config_dir
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")

    await page.wait_for_selector(".picker-view", timeout=5000)
    await page.fill("input.file-path-input", str(toml_file))
    await page.fill("input.schema-name-input", "test")
    await page.click("button.btn-load")
    await page.wait_for_selector(".edit-view", timeout=5000)
    await page.fill("input[data-key='server.port']", "9090")

    # Validate — no errors expected.
    await page.click("button.btn-validate")
    await asyncio.sleep(0.3)
    errors = page.locator(".field-error")
    count = await errors.count()
    assert count == 0, f"expected no validation errors, got {count}"


async def test_save_and_diff(harness, config_dir):
    cfg_base, toml_file = config_dir
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")

    await page.wait_for_selector(".picker-view", timeout=5000)
    await page.fill("input.file-path-input", str(toml_file))
    await page.click("button.btn-load")
    await page.wait_for_selector(".edit-view", timeout=5000)
    await page.fill("input[data-key='server.port']", "9090")
    await page.click("button.btn-save")

    # Diff view — should show the port change.
    await page.wait_for_selector(".diff-view", timeout=5000)
    diff_text = await page.locator("pre.diff-output").inner_text()
    assert "9090" in diff_text

    # FS: file now contains 9090.
    await asyncio.sleep(0.3)
    content = toml_file.read_text(encoding="utf-8")
    assert "9090" in content
    assert "8080" not in content


async def test_validate_fails_with_magenta_error(harness, config_dir):
    cfg_base, toml_file = config_dir
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")

    await page.wait_for_selector(".picker-view", timeout=5000)
    await page.fill("input.file-path-input", str(toml_file))
    await page.fill("input.schema-name-input", "test")
    await page.click("button.btn-load")
    await page.wait_for_selector(".edit-view", timeout=5000)

    # Set port to an invalid value (> 65535).
    await page.fill("input[data-key='server.port']", "99999")
    await page.click("button.btn-validate")
    await asyncio.sleep(0.3)

    errors = page.locator(".field-error")
    count = await errors.count()
    assert count > 0, "expected at least one validation error"
    # Verify the error has magenta colour via CSS.
    color = await errors.first.evaluate(
        "el => getComputedStyle(el).color"
    )
    # #ff5cd1 in RGB is rgb(255, 92, 209)
    assert "255" in color and "92" in color, f"expected magenta error colour, got {color!r}"
