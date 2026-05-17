"""Integration test: create → edit → save → reopen → delete, with FS assertions."""
import asyncio
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


async def test_create_note_appears_on_fs(harness, notes_dir):
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    # List view — empty state
    await page.wait_for_selector(".note-list-empty", timeout=5000)
    # Create a note
    await page.click(".btn-new-note")
    # Navigated to edit route; editor pane visible
    await page.wait_for_selector(".editor-pane", timeout=5000)
    # Allow async IPC to complete
    await asyncio.sleep(0.3)
    md_files = list(notes_dir.glob("*.md"))
    assert len(md_files) == 1, f"expected 1 .md file, got {md_files}"


async def test_edit_and_save(harness, notes_dir):
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    # Create note
    await page.wait_for_selector(".note-list-empty", timeout=5000)
    await page.click(".btn-new-note")
    await page.wait_for_selector(".editor-pane", timeout=5000)
    # Type content — unsaved dot should appear
    textarea = page.locator("textarea.note-body")
    await textarea.fill("# My test note\n\nSome content.")
    dot = page.locator(".unsaved-dot")
    await dot.wait_for(state="visible", timeout=2000)
    # Ctrl+S to save — dot should disappear
    await page.keyboard.press("Control+s")
    await dot.wait_for(state="hidden", timeout=2000)
    # FS: file contains the typed body
    await asyncio.sleep(0.2)
    md = list(notes_dir.glob("*.md"))[0]
    content = md.read_text(encoding="utf-8")
    assert "My test note" in content
    assert "Some content." in content


async def test_reopen_shows_saved_body(harness, notes_dir):
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    # Create + save
    await page.wait_for_selector(".note-list-empty", timeout=5000)
    await page.click(".btn-new-note")
    await page.wait_for_selector(".editor-pane", timeout=5000)
    await page.locator("textarea.note-body").fill("Persisted body text.")
    await page.keyboard.press("Control+s")
    await asyncio.sleep(0.3)
    # Navigate to list
    await page.click("a.back-to-list")
    await page.wait_for_selector(".note-item", timeout=5000)
    # Click the note to reopen
    await page.click(".note-item")
    await page.wait_for_selector(".editor-pane", timeout=5000)
    await asyncio.sleep(0.3)
    body_val = await page.locator("textarea.note-body").input_value()
    assert "Persisted body text." in body_val


async def test_delete_note(harness, notes_dir):
    page = harness.page
    if page is None:
        pytest.skip("no inspector page (xvfb-only path)")
    # Create
    await page.wait_for_selector(".note-list-empty", timeout=5000)
    await page.click(".btn-new-note")
    await page.wait_for_selector(".editor-pane", timeout=5000)
    await asyncio.sleep(0.3)
    md_files = list(notes_dir.glob("*.md"))
    assert len(md_files) == 1
    slug_file = md_files[0]
    # Delete
    await page.click(".btn-delete-note")
    # Navigated back to list; file gone
    await page.wait_for_selector(".note-list-empty", timeout=5000)
    await asyncio.sleep(0.2)
    assert not slug_file.exists(), f"expected {slug_file} to be deleted"
