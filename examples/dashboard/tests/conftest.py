"""conftest.py — pytest fixtures for dashboard integration tests.

Requires the dashboard binary to be built at target/linux-x64/dashboard.
Build with: picolet build (from examples/dashboard/).
"""
from pathlib import Path
import pytest
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "dashboard"


@pytest.fixture
async def harness():
    h = AppHarness(str(BINARY))
    await h.start()
    yield h
    await h.stop()
