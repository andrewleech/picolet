"""conftest.py — pytest fixtures for notes integration tests.

Uses PICOLET_NOTES_DIR override to isolate each test in a temp directory.
No mock is needed for the storage layer — the real notes_store.py runs
against the temp directory so host FS state can be asserted directly.
"""
import pytest
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "notes"


@pytest.fixture
async def notes_dir(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    return d


@pytest.fixture
async def harness(notes_dir):
    h = AppHarness(
        str(BINARY),
        env={"PICOLET_NOTES_DIR": str(notes_dir)},
    )
    await h.start()
    yield h
    await h.stop()
