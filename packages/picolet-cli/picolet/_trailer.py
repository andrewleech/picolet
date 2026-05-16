"""
Picolet romfs trailer encoder/decoder (Python side of FR-BP-5).

The trailer is 24 bytes, little-endian, appended to the end of a picolet
app binary after the romfs payload:

  bytes  0..3   magic        b"PYLT"
  bytes  4..5   version      u16 = 1
  bytes  6..7   flags        u16 = 0  (reserved)
  bytes  8..15  payload_size u64 = N  (bytes of romfs payload)
  bytes 16..19  payload_crc32 u32     (zlib CRC32 of payload)
  bytes 20..23  pad          u32 = 0  (reserved)

The C struct in romfs_trailer.h uses the same layout.  CRC32 uses the
zlib polynomial (same as Python's zlib.crc32()).
"""

from __future__ import annotations

import struct
import zlib

TRAILER_MAGIC: bytes = b"PYLT"
TRAILER_VERSION: int = 1
TRAILER_FMT: str = "<4sHHQII"  # 24 bytes total
TRAILER_SIZE: int = struct.calcsize(TRAILER_FMT)

assert TRAILER_SIZE == 24, f"trailer size mismatch: {TRAILER_SIZE}"


def pack_trailer(payload: bytes) -> bytes:
    """Return the 24-byte trailer for the given romfs payload bytes.

    The CRC32 is computed over the payload bytes using zlib.crc32(), which
    uses the same polynomial as the C implementation in romfs_trailer.c.
    """
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return struct.pack(
        TRAILER_FMT,
        TRAILER_MAGIC,
        TRAILER_VERSION,
        0,              # flags
        len(payload),   # payload_size
        crc,            # payload_crc32
        0,              # pad
    )


def unpack_trailer(buf: bytes) -> tuple[bytes, int, int, int, int, int]:
    """Unpack the last 24 bytes of buf as a trailer.

    Returns a 6-tuple (magic, version, flags, payload_size, crc32, pad)
    matching TRAILER_FMT ``<4sHHQII``.  Does not validate the magic or
    CRC — callers must check.
    """
    return struct.unpack(TRAILER_FMT, buf[-TRAILER_SIZE:])
