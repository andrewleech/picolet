"""conftest.py — pytest fixtures for the pydfu integration tests.

All tests run against the mock USB shim (PICOLET_PYDFU_MOCK=1) so no
physical DFU device is required. The harness propagates the env var to
the child process automatically via AppHarness.
"""
import pytest
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "pydfu"


@pytest.fixture
async def harness():
    h = AppHarness(
        str(BINARY),
        env={"PICOLET_PYDFU_MOCK": "1"},
    )
    await h.start()
    yield h
    await h.stop()
