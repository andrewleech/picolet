"""
PH19 unit tests — DFU file parser (pydfu_adapter.read_dfu_file).

Covers:
  - read_dfu_file: parses test fixture and returns correct element count.
  - read_dfu_file: element has correct addr, size, data keys.
  - read_dfu_file: addr matches the value encoded in the fixture (0x08000000).
  - read_dfu_file: size matches element data length (1024 bytes).
  - read_dfu_file: raises ValueError on bad DfuSe signature.
  - read_dfu_file: raises ValueError on CRC mismatch (last 4 bytes corrupted).
  - read_dfu_file: raises FileNotFoundError for nonexistent path.
  - read_dfu_file: round-trips a two-element DFU file correctly.
  - read_dfu_file: element data bytes match what was encoded.
  - compute_crc: output matches DfuSe CRC formula against known vectors.
  - compute_crc: empty data produces correct value.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import zlib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_DIR = _REPO_ROOT / "examples" / "pydfu" / "src"
_FIXTURE = _REPO_ROOT / "examples" / "pydfu" / "tests" / "fixtures" / "test.dfu"

sys.path.insert(0, str(_SRC_DIR))

# Import without PICOLET_PYDFU_MOCK so the real parser code path is exercised
# (read_dfu_file is pure Python; no USB needed).
import importlib

# Ensure a clean import without mock interference for this module.
if "pydfu_adapter" in sys.modules:
    del sys.modules["pydfu_adapter"]
if "pydfu_mock" in sys.modules:
    del sys.modules["pydfu_mock"]

_saved_mock_env = os.environ.pop("PICOLET_PYDFU_MOCK", None)
import pydfu_adapter as _adapter

if _saved_mock_env is not None:
    os.environ["PICOLET_PYDFU_MOCK"] = _saved_mock_env


def _build_dfu_file(elements: list[dict]) -> bytes:
    """Build a minimal valid DfuSe binary from a list of {addr, data} dicts."""
    elem_blocks = b""
    for e in elements:
        elem_blocks += struct.pack("<II", e["addr"], len(e["data"])) + e["data"]

    target_name = b"\x00" * 255
    img_prefix = struct.pack(
        "<6sBI255sII",
        b"Target",
        0,                      # altsetting
        0,                      # named = False
        target_name,
        len(elem_blocks),       # target size
        len(elements),          # element count
    )
    target_block = img_prefix + elem_blocks

    dfu_prefix = struct.pack("<5sBIB", b"DfuSe", 1, len(target_block), 1)
    body = dfu_prefix + target_block

    suffix_header = struct.pack(
        "<4H3sB",
        0x0000,  # device
        0xDF11,  # product
        0x0483,  # vendor
        0x011A,  # DFU spec version
        b"UFD",
        16,
    )
    crc_value = 0xFFFFFFFF & -zlib.crc32(body + suffix_header) - 1
    return body + suffix_header + struct.pack("<I", crc_value)


class TestReadDfuFileFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not _FIXTURE.exists():
            raise unittest.SkipTest(f"test.dfu fixture missing: {_FIXTURE}")
        cls.elements = _adapter.read_dfu_file(str(_FIXTURE))

    def test_returns_one_element(self):
        self.assertEqual(len(self.elements), 1)

    def test_element_has_addr_key(self):
        self.assertIn("addr", self.elements[0])

    def test_element_has_size_key(self):
        self.assertIn("size", self.elements[0])

    def test_element_has_data_key(self):
        self.assertIn("data", self.elements[0])

    def test_element_addr_is_stm32_flash_base(self):
        self.assertEqual(self.elements[0]["addr"], 0x08000000)

    def test_element_size_is_1024(self):
        self.assertEqual(self.elements[0]["size"], 1024)

    def test_element_data_length_matches_size(self):
        e = self.elements[0]
        self.assertEqual(len(e["data"]), e["size"])

    def test_element_data_is_zero_bytes(self):
        """The fixture was built with 1 KiB of 0x00 data."""
        self.assertEqual(self.elements[0]["data"], b"\x00" * 1024)


class TestReadDfuFileRoundTrip(unittest.TestCase):

    def test_single_element_round_trip(self):
        elem_data = b"\xAB\xCD" * 512  # 1024 bytes, non-null pattern
        dfu_bytes = _build_dfu_file([{"addr": 0x08010000, "data": elem_data}])
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(dfu_bytes)
            tmp = f.name
        try:
            elems = _adapter.read_dfu_file(tmp)
            self.assertEqual(len(elems), 1)
            self.assertEqual(elems[0]["addr"], 0x08010000)
            self.assertEqual(elems[0]["size"], 1024)
            self.assertEqual(elems[0]["data"], elem_data)
        finally:
            os.unlink(tmp)

    def test_two_element_round_trip(self):
        """A DFU file can contain multiple elements; all must be parsed."""
        e1 = {"addr": 0x08000000, "data": b"\x11" * 512}
        e2 = {"addr": 0x08004000, "data": b"\x22" * 256}
        dfu_bytes = _build_dfu_file([e1, e2])
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(dfu_bytes)
            tmp = f.name
        try:
            elems = _adapter.read_dfu_file(tmp)
            self.assertEqual(len(elems), 2)
            self.assertEqual(elems[0]["addr"], 0x08000000)
            self.assertEqual(elems[0]["data"], b"\x11" * 512)
            self.assertEqual(elems[1]["addr"], 0x08004000)
            self.assertEqual(elems[1]["data"], b"\x22" * 256)
        finally:
            os.unlink(tmp)

    def test_element_num_is_zero_indexed(self):
        e1 = {"addr": 0x08000000, "data": b"\xAA" * 64}
        e2 = {"addr": 0x08001000, "data": b"\xBB" * 64}
        dfu_bytes = _build_dfu_file([e1, e2])
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(dfu_bytes)
            tmp = f.name
        try:
            elems = _adapter.read_dfu_file(tmp)
            self.assertEqual(elems[0]["num"], 0)
            self.assertEqual(elems[1]["num"], 1)
        finally:
            os.unlink(tmp)


class TestReadDfuFileErrors(unittest.TestCase):

    def test_raises_value_error_on_bad_signature(self):
        bad = b"NotDfu" + b"\x00" * 200
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(bad)
            tmp = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                _adapter.read_dfu_file(tmp)
            self.assertIn("DfuSe", str(ctx.exception))
        finally:
            os.unlink(tmp)

    def test_raises_value_error_on_crc_mismatch(self):
        """Corrupting the stored CRC field must trigger ValueError."""
        with open(str(_FIXTURE), "rb") as f:
            data = f.read()
        # Zero out the last 4 bytes (CRC field)
        bad_data = data[:-4] + b"\x00\x00\x00\x00"
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(bad_data)
            tmp = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                _adapter.read_dfu_file(tmp)
            self.assertIn("CRC", str(ctx.exception))
        finally:
            os.unlink(tmp)

    def test_raises_on_corrupt_body_byte(self):
        """Flipping a byte in the element data must produce a CRC mismatch."""
        if not _FIXTURE.exists():
            self.skipTest("fixture missing")
        with open(str(_FIXTURE), "rb") as f:
            data = f.read()
        # Flip a byte well inside the element data region (offset ~50 is in body)
        corrupt = bytearray(data)
        corrupt[50] ^= 0xFF
        with tempfile.NamedTemporaryFile(suffix=".dfu", delete=False) as f:
            f.write(bytes(corrupt))
            tmp = f.name
        try:
            with self.assertRaises(ValueError):
                _adapter.read_dfu_file(tmp)
        finally:
            os.unlink(tmp)

    def test_raises_file_not_found_for_nonexistent_path(self):
        with self.assertRaises((FileNotFoundError, OSError)):
            _adapter.read_dfu_file("/nonexistent/path/firmware.dfu")


class TestComputeCrc(unittest.TestCase):

    def _expected(self, data: bytes) -> int:
        return 0xFFFFFFFF & -zlib.crc32(data) - 1

    def test_empty_data(self):
        self.assertEqual(_adapter.compute_crc(b""), self._expected(b""))

    def test_hello_world(self):
        data = b"hello world"
        self.assertEqual(_adapter.compute_crc(data), self._expected(data))

    def test_256_bytes_all_ff(self):
        data = b"\xff" * 256
        self.assertEqual(_adapter.compute_crc(data), self._expected(data))

    def test_1mb_zero_bytes(self):
        data = b"\x00" * (1024 * 1024)
        self.assertEqual(_adapter.compute_crc(data), self._expected(data))

    def test_fixture_file_crc_consistent_with_stored_value(self):
        """CRC computed over file-minus-last-4-bytes must match the stored suffix CRC."""
        if not _FIXTURE.exists():
            self.skipTest("fixture missing")
        with open(str(_FIXTURE), "rb") as f:
            data = f.read()
        stored_crc = struct.unpack("<I", data[-4:])[0]
        computed = _adapter.compute_crc(data[:-4])
        self.assertEqual(computed, stored_crc)


if __name__ == "__main__":
    unittest.main()
