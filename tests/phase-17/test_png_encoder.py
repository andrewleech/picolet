"""
PH17 unit tests — picolet_lvgl_png.c: PNG encoder via libz dlopen (D1 deviation).

These tests compile the encoder as a host-side shared library and call it
via ctypes to round-trip validate PNG output against Pillow.

The encoder is a C-only module normally linked into the MicroPython lvgl
runtime.  We build it as a .so at test time so we can call it directly
without needing the runtime binary.

Covers:
  - A 1x1 red pixel encodes to valid PNG bytes (magic + IHDR + IDAT + IEND).
  - A 4x4 checkerboard: Pillow opens the PNG and reads back the pixel values.
  - Dimensions (width, height) reported by Pillow match the input dimensions.
  - Width=1 height=1 (minimal case) produces > 0 bytes.
  - Width=320 height=240 (typical UI crop): Pillow validates the PNG.
  - Invalid args (null pointer equivalent, zero dimensions) return -1.
  - picolet_lvgl_png_free does not crash.

The .so is compiled once per test session via a session-scoped fixture.
If gcc or zlib is missing the test module is skipped.
"""
from __future__ import annotations

import ctypes
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_PNG_C_SRC = (
    _REPO_ROOT
    / "packages"
    / "picolet-runtime"
    / "user_c_modules"
    / "picolet_lvgl_test"
    / "picolet_lvgl_png.c"
)


# ---------------------------------------------------------------------------
# Session-level .so build
# ---------------------------------------------------------------------------

def _build_so() -> Path | None:
    """Compile picolet_lvgl_png.c to a shared library.  Returns path or None."""
    if not _PNG_C_SRC.exists():
        return None
    out = Path(tempfile.mkdtemp()) / "picolet_lvgl_png.so"
    result = subprocess.run(
        [
            "gcc",
            "-shared", "-fPIC", "-O2",
            "-o", str(out),
            str(_PNG_C_SRC),
            "-ldl",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return out


_SO_PATH: Path | None = None


def setUpModule():
    global _SO_PATH
    _SO_PATH = _build_so()


def _get_lib() -> ctypes.CDLL:
    if _SO_PATH is None:
        pytest.skip("picolet_lvgl_png.so could not be built (gcc or zlib missing)")
    return ctypes.CDLL(str(_SO_PATH))


# ---------------------------------------------------------------------------
# ctypes wrappers
# ---------------------------------------------------------------------------

def _encode(bgr888: bytes, width: int, height: int, stride: int | None = None) -> bytes | None:
    """Call picolet_lvgl_png_encode and return PNG bytes, or None on failure."""
    lib = _get_lib()

    lib.picolet_lvgl_png_encode.restype = ctypes.c_int32
    lib.picolet_lvgl_png_encode.argtypes = [
        ctypes.c_char_p,     # LVGL BGR byte order
        ctypes.c_int32,      # width
        ctypes.c_int32,      # height
        ctypes.c_int32,      # stride
        ctypes.POINTER(ctypes.c_void_p),  # out_bytes*
        ctypes.POINTER(ctypes.c_size_t),  # out_size*
    ]
    lib.picolet_lvgl_png_free.restype = None
    lib.picolet_lvgl_png_free.argtypes = [ctypes.c_void_p]

    out_ptr = ctypes.c_void_p(0)
    out_size = ctypes.c_size_t(0)
    buf = ctypes.create_string_buffer(bgr888)

    rc = lib.picolet_lvgl_png_encode(
        buf,
        ctypes.c_int32(width),
        ctypes.c_int32(height),
        ctypes.c_int32(width * 3 if stride is None else stride),
        ctypes.byref(out_ptr),
        ctypes.byref(out_size),
    )
    if rc != 0:
        return None

    size = out_size.value
    raw = (ctypes.c_uint8 * size).from_address(out_ptr.value)
    data = bytes(raw)
    lib.picolet_lvgl_png_free(out_ptr)
    return data


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _solid_rgb(r: int, g: int, b: int, w: int, h: int) -> bytes:
    return bytes([b, g, r] * (w * h))


def _checkerboard(w: int, h: int) -> bytes:
    data = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            idx = (y * w + x) * 3
            if (x + y) % 2 == 0:
                data[idx:idx+3] = [0, 0, 255]   # red in LVGL BGR byte order
            else:
                data[idx:idx+3] = [0, 255, 0]   # green
    return bytes(data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPngEncoderBasic(unittest.TestCase):

    def test_output_starts_with_png_magic(self):
        png = _encode(_solid_rgb(0, 128, 255, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(_PNG_MAGIC), "Output must begin with PNG magic bytes")

    def test_output_contains_ihdr_chunk(self):
        png = _encode(_solid_rgb(100, 100, 100, 3, 3), 3, 3)
        self.assertIsNotNone(png)
        self.assertIn(b"IHDR", png)

    def test_output_contains_idat_chunk(self):
        png = _encode(_solid_rgb(0, 0, 0, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        self.assertIn(b"IDAT", png)

    def test_output_ends_with_iend_chunk(self):
        png = _encode(_solid_rgb(200, 200, 200, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        self.assertTrue(png.endswith(b"IEND\xaeB`\x82"),
                        "PNG must end with valid IEND chunk")

    def test_zero_width_returns_failure(self):
        result = _encode(b"\x00" * 3, 0, 1)
        self.assertIsNone(result, "Width=0 must return failure (rc=-1)")

    def test_zero_height_returns_failure(self):
        result = _encode(b"\x00" * 3, 1, 0)
        self.assertIsNone(result, "Height=0 must return failure (rc=-1)")

    def test_negative_dimensions_return_failure(self):
        result = _encode(b"\x00" * 3, -1, -1)
        self.assertIsNone(result)


class TestPngEncoderPillowRoundtrip(unittest.TestCase):
    """Use Pillow to validate the output PNG and check pixel data."""

    def _open_png(self, data: bytes):
        """Open PNG bytes with Pillow, converting to RGB."""
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return img.convert("RGB")

    def test_1x1_red_roundtrip(self):
        png = _encode(_solid_rgb(255, 0, 0, 1, 1), 1, 1)
        self.assertIsNotNone(png)
        img = self._open_png(png)
        self.assertEqual(img.size, (1, 1))
        r, g, b = img.getpixel((0, 0))
        self.assertEqual(r, 255)
        self.assertEqual(g, 0)
        self.assertEqual(b, 0)

    def test_1x1_green_roundtrip(self):
        png = _encode(_solid_rgb(0, 255, 0, 1, 1), 1, 1)
        self.assertIsNotNone(png)
        img = self._open_png(png)
        r, g, b = img.getpixel((0, 0))
        self.assertEqual(g, 255)
        self.assertEqual(r, 0)

    def test_2x2_dimensions_correct(self):
        png = _encode(_solid_rgb(50, 100, 150, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        img = self._open_png(png)
        self.assertEqual(img.size, (2, 2))

    def test_checkerboard_4x4_dimensions(self):
        png = _encode(_checkerboard(4, 4), 4, 4)
        self.assertIsNotNone(png)
        img = self._open_png(png)
        self.assertEqual(img.size, (4, 4))

    def test_checkerboard_4x4_pixel_values(self):
        """Top-left pixel (0,0) should be red; (1,0) should be green."""
        png = _encode(_checkerboard(4, 4), 4, 4)
        self.assertIsNotNone(png)
        img = self._open_png(png)
        r, g, b = img.getpixel((0, 0))
        self.assertEqual((r, g, b), (255, 0, 0))
        r2, g2, b2 = img.getpixel((1, 0))
        self.assertEqual((r2, g2, b2), (0, 255, 0))

    def test_padded_bgr_rows_preserve_colours(self):
        png = _encode(b"\x00\x00\xff\xaa\xff\x00\x00\xbb", 1, 2, stride=4)
        img = self._open_png(png)
        self.assertEqual(img.size, (1, 2))
        self.assertEqual(img.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(img.getpixel((0, 1)), (0, 0, 255))

    def test_short_stride_returns_failure(self):
        self.assertIsNone(_encode(b"\x00" * 6, 2, 1, stride=5))

    def test_320x240_pillow_validates(self):
        """320x240 typical viewport: Pillow can open and verify the PNG."""
        png = _encode(_solid_rgb(128, 128, 128, 320, 240), 320, 240)
        self.assertIsNotNone(png)
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        img.verify()   # raises on corrupt PNG



class TestPngEncoderIhdrFields(unittest.TestCase):
    """Parse the IHDR chunk and check width/height/colour-type fields."""

    def _parse_ihdr(self, png: bytes) -> dict:
        """Extract IHDR fields from raw PNG bytes."""
        # PNG magic (8) + IHDR length (4) + IHDR type (4) + IHDR data (13)
        # IHDR data: width(4) + height(4) + bitdepth(1) + colourtype(1) + ...
        assert png[:8] == _PNG_MAGIC
        ihdr_data = png[16:16+13]   # skip magic(8) + length(4) + type(4)
        width  = struct.unpack(">I", ihdr_data[0:4])[0]
        height = struct.unpack(">I", ihdr_data[4:8])[0]
        bitdepth   = ihdr_data[8]
        colourtype = ihdr_data[9]
        return {"width": width, "height": height, "bitdepth": bitdepth,
                "colourtype": colourtype}

    def test_ihdr_width_matches_input(self):
        png = _encode(_solid_rgb(0, 0, 0, 7, 3), 7, 3)
        self.assertIsNotNone(png)
        ihdr = self._parse_ihdr(png)
        self.assertEqual(ihdr["width"], 7)

    def test_ihdr_height_matches_input(self):
        png = _encode(_solid_rgb(0, 0, 0, 7, 3), 7, 3)
        self.assertIsNotNone(png)
        ihdr = self._parse_ihdr(png)
        self.assertEqual(ihdr["height"], 3)

    def test_ihdr_bitdepth_is_8(self):
        png = _encode(_solid_rgb(0, 0, 0, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        ihdr = self._parse_ihdr(png)
        self.assertEqual(ihdr["bitdepth"], 8)

    def test_ihdr_colourtype_is_rgb(self):
        """Colour type 2 = RGB truecolour (PNG spec)."""
        png = _encode(_solid_rgb(0, 0, 0, 2, 2), 2, 2)
        self.assertIsNotNone(png)
        ihdr = self._parse_ihdr(png)
        self.assertEqual(ihdr["colourtype"], 2)


if __name__ == "__main__":
    unittest.main()
