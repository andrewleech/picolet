"""
PH19 unit tests — pure-Python CRC32 fallback (_crc32.crc32).

Covers:
  - crc32: matches zlib.crc32 for empty input.
  - crc32: matches zlib.crc32 for "hello world".
  - crc32: matches zlib.crc32 for 256 bytes of 0xFF.
  - crc32: matches zlib.crc32 for 1 MiB of zeros.
  - crc32: matches zlib.crc32 for 1 MiB of random bytes (R6).
  - crc32: chained call (seeded value) matches zlib.crc32 chained call.
  - crc32: value=0 default is equivalent to passing value=0 explicitly.
  - crc32: table is lazily built (first call triggers _make_table).
  - crc32: table has 256 entries after first call.
"""
from __future__ import annotations

import os
import random
import sys
import zlib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_DIR = _REPO_ROOT / "examples" / "pydfu" / "src"
sys.path.insert(0, str(_SRC_DIR))

import _crc32 as _mod
from _crc32 import crc32 as py_crc32


def _zlib_unsigned(data: bytes, value: int = 0) -> int:
    return zlib.crc32(data, value) & 0xFFFFFFFF


class TestCrc32FallbackCorrectness(unittest.TestCase):

    def test_empty_bytes(self):
        self.assertEqual(py_crc32(b""), _zlib_unsigned(b""))

    def test_hello_world(self):
        data = b"hello world"
        self.assertEqual(py_crc32(data), _zlib_unsigned(data))

    def test_256_bytes_all_ff(self):
        data = b"\xff" * 256
        self.assertEqual(py_crc32(data), _zlib_unsigned(data))

    def test_1mb_zeros(self):
        data = b"\x00" * (1024 * 1024)
        self.assertEqual(py_crc32(data), _zlib_unsigned(data))

    def test_1mb_random_bytes(self):
        rng = random.Random(42)
        data = bytes(rng.randint(0, 255) for _ in range(1024 * 1024))
        self.assertEqual(py_crc32(data), _zlib_unsigned(data))

    def test_chained_seeded_matches_zlib(self):
        """py_crc32(data2, value=py_crc32(data1)) == zlib chained CRC."""
        data1 = b"chunk-one"
        data2 = b"chunk-two"
        seed1 = py_crc32(data1)
        chained = py_crc32(data2, value=seed1)
        zlib_seed1 = _zlib_unsigned(data1)
        zlib_chained = _zlib_unsigned(data2, zlib_seed1)
        self.assertEqual(chained, zlib_chained)

    def test_default_value_0_matches_explicit_0(self):
        data = b"some data"
        self.assertEqual(py_crc32(data), py_crc32(data, value=0))

    def test_single_byte_known_vector(self):
        """Single byte 0x00: CRC32 is 0xD202EF8D."""
        self.assertEqual(py_crc32(b"\x00"), _zlib_unsigned(b"\x00"))

    def test_ascii_string(self):
        data = b"The quick brown fox jumps over the lazy dog"
        self.assertEqual(py_crc32(data), _zlib_unsigned(data))


class TestCrc32FallbackTableInit(unittest.TestCase):

    def test_table_built_after_first_call(self):
        # Reset table to verify lazy init
        _mod._TABLE = None
        py_crc32(b"trigger")
        self.assertIsNotNone(_mod._TABLE)

    def test_table_has_256_entries(self):
        py_crc32(b"ensure table")
        self.assertEqual(len(_mod._TABLE), 256)

    def test_table_first_entry_is_zero(self):
        """CRC table[0] must be 0 (polynomial table property)."""
        py_crc32(b"ensure table")
        self.assertEqual(_mod._TABLE[0], 0)

    def test_table_is_reused_across_calls(self):
        """The same table object should be reused on repeated calls."""
        py_crc32(b"first")
        table_id = id(_mod._TABLE)
        py_crc32(b"second")
        self.assertEqual(id(_mod._TABLE), table_id)


if __name__ == "__main__":
    unittest.main()
