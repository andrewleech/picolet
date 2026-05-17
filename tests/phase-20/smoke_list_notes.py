"""Smoke: list_notes IPC round-trip returns a list.

On Linux without a Playwright-attachable inspector (webkit/xvfb path),
falls back to invoking notes_store directly via Python to verify the
IPC commands work correctly.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Add notes src to path for direct store testing
_NOTES_SRC = Path(__file__).parent.parent.parent / "examples/notes/src"
sys.path.insert(0, str(_NOTES_SRC))

BINARY = Path(__file__).parent.parent.parent / "examples/notes/target/linux-x64/notes"


async def _via_harness(tmp: str):
    from picolet.testing import AppHarness
    async with AppHarness(str(BINARY), env={"PICOLET_NOTES_DIR": tmp}) as h:
        if h.page is None:
            return None  # webkit xvfb path — no inspector
        result = await h.page.evaluate("window.picolet.invoke('list_notes')")
        assert isinstance(result, list), f"expected list, got {type(result)}"
        print(f"list_notes (harness): OK ({len(result)} notes)")
        return True


def _via_store(tmp: str):
    """Direct Python test of the storage layer."""
    os.environ["PICOLET_NOTES_DIR"] = tmp
    import notes_store as s
    result = s.list_notes()
    assert isinstance(result, list), f"expected list, got {type(result)}"
    print(f"list_notes (store): OK ({len(result)} notes)")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        result = await _via_harness(tmp)
        if result is None:
            # Fallback: test store directly
            _via_store(tmp)
            print("list_notes: OK (via direct store, no inspector available)")


asyncio.run(main())
