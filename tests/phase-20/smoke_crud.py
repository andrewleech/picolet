"""Smoke: create → save → load → delete cycle with FS verification.

On Linux without a Playwright-attachable inspector (webkit/xvfb path),
falls back to invoking notes_store directly via Python to verify the
full CRUD cycle.

When no DISPLAY is available, the binary spawn would block on WebKitGTK
window creation, so AppHarness is skipped entirely.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_NOTES_SRC = Path(__file__).parent.parent.parent / "examples/notes/src"
sys.path.insert(0, str(_NOTES_SRC))

BINARY = Path(__file__).parent.parent.parent / "examples/notes/target/linux-x64/notes"


def _via_store(tmp: str):
    """Direct Python test of the storage layer."""
    os.environ["PICOLET_NOTES_DIR"] = tmp
    tmp_path = Path(tmp)
    import notes_store as s

    # Create
    note = s.create_note("Smoke Test")
    slug = note["slug"]
    assert slug, "create_note returned no slug"
    assert (tmp_path / f"{slug}.md").exists(), f"{slug}.md not found"
    # Save
    s.save_note(slug, "# Test")
    content = (tmp_path / f"{slug}.md").read_text()
    assert "# Test" in content, "body not saved"
    # Load
    loaded = s.load_note(slug)
    assert "# Test" in loaded["body"], "load_note body mismatch"
    # Delete
    s.delete_note(slug)
    assert not (tmp_path / f"{slug}.md").exists(), f"{slug}.md still exists"
    print("CRUD cycle (store): OK")


async def _via_harness(tmp: str):
    from picolet.testing import AppHarness
    tmp_path = Path(tmp)
    async with AppHarness(str(BINARY), env={"PICOLET_NOTES_DIR": tmp}) as h:
        if h.page is None:
            return None
        # Create
        note = await h.page.evaluate(
            "window.picolet.invoke('create_note', {title: 'Smoke Test'})"
        )
        slug = note["slug"]
        assert slug, "create_note returned no slug"
        assert (tmp_path / f"{slug}.md").exists(), f"{slug}.md not found"
        # Save
        await h.page.evaluate(
            f"window.picolet.invoke('save_note', {{slug: '{slug}', body: '# Test'}})"
        )
        content = (tmp_path / f"{slug}.md").read_text()
        assert "# Test" in content, "body not saved"
        # Delete
        await h.page.evaluate(
            f"window.picolet.invoke('delete_note', {{slug: '{slug}'}})"
        )
        assert not (tmp_path / f"{slug}.md").exists(), f"{slug}.md still exists"
        print("CRUD cycle (harness): OK")
        return True


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        if not os.environ.get("DISPLAY"):
            # No display: binary spawn would block on WebKitGTK window creation.
            print("NOTE: no DISPLAY; testing notes_store directly")
            _via_store(tmp)
            print("CRUD cycle: OK (via direct store, no display available)")
            return
        result = await _via_harness(tmp)
        if result is None:
            _via_store(tmp)
            print("CRUD cycle: OK (via direct store, no inspector available)")


asyncio.run(main())
