# _crc32.py — pure-Python CRC32 fallback.
# Used when MicroPython's frozen runtime does not expose zlib.crc32.
# R6 in the phase plan. Algorithm: standard CRC-32 (ISO 3309 / ITU-T V.42).

_TABLE = None


def _make_table():
    global _TABLE
    _TABLE = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        _TABLE.append(crc)


def crc32(data, value=0):
    """Return the CRC32 checksum of data, optionally seeded with value."""
    if _TABLE is None:
        _make_table()
    crc = value ^ 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ _TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF
