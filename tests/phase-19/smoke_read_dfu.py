"""smoke_read_dfu.py — Gate G smoke test.

Invokes the read_dfu command (pure Python, no USB) via direct module call
and asserts the returned elements have the expected keys.

Usage:
    python3 tests/phase-19/smoke_read_dfu.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DFU_FIXTURE = REPO_ROOT / "examples" / "pydfu" / "tests" / "fixtures" / "test.dfu"

sys.path.insert(0, str(REPO_ROOT / "examples" / "pydfu" / "src"))
import pydfu_adapter

elements = pydfu_adapter.read_dfu_file(str(DFU_FIXTURE))
assert isinstance(elements, list), f"expected list, got {type(elements)}"
assert len(elements) >= 1, "expected at least one element"
assert "addr" in elements[0], f"element missing 'addr' key: {elements[0].keys()}"
assert "size" in elements[0], f"element missing 'size' key: {elements[0].keys()}"
assert "data" in elements[0], f"element missing 'data' key: {elements[0].keys()}"
print(f"read_dfu: OK ({len(elements)} element(s), addr=0x{elements[0]['addr']:08x}, size={elements[0]['size']})")
