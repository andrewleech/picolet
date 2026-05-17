# _dfu_file.py — DfuSe file parser and CRC helpers.
#
# Pure-Python; no USB dependency. Safe to import on all platforms.
# Shared by pydfu_adapter.py (host-side) and _pydfu/pydfu.py (device-side).
#
# CRC32 — probe runtime zlib, fall back to vendored _crc32.

import struct

try:
    import zlib as _zlib
    if not hasattr(_zlib, "crc32"):
        raise ImportError
    def _crc32(data, value=0):
        return _zlib.crc32(data, value)
except (ImportError, AttributeError):
    from _crc32 import crc32 as _crc32  # type: ignore[no-redef]


def compute_crc(data):
    """DfuSe-compatible CRC32: bitwise complement of CRC of all bytes before suffix."""
    return 0xFFFFFFFF & -_crc32(data) - 1


def _named(values, names):
    return dict(zip(names.split(), values))


def _consume(fmt, data, names):
    size = struct.calcsize(fmt)
    return _named(struct.unpack(fmt, data[:size]), names), data[size:]


def _cstring(bs):
    return bs.decode("utf-8").split("\0", 1)[0]


def read_dfu_file(path):
    """Parse a DfuSe .dfu file; return list of element dicts.

    Each element dict has keys: num (int), addr (int), size (int), data (bytes).
    Raises ValueError on parse or CRC error.
    """
    with open(path, "rb") as f:
        data = f.read()

    crc = compute_crc(data[:-4])
    elements = []

    # DFU prefix: "DfuSe" signature, version, total size, target count
    prefix, data = _consume("<5sBIB", data, "signature version size targets")
    sig = prefix["signature"]
    if sig != b"DfuSe":
        raise ValueError("Not a DfuSe file (bad signature: {!r})".format(sig))

    for _target_idx in range(prefix["targets"]):
        img, data = _consume("<6sBI255s2I", data, "signature altsetting named name size elements")
        if img["named"]:
            img["name"] = _cstring(img["name"])
        else:
            img["name"] = ""
        target_size = img["size"]
        target_data = data[:target_size]
        data = data[target_size:]
        for elem_idx in range(img["elements"]):
            ep, target_data = _consume("<2I", target_data, "addr size")
            ep["num"] = elem_idx
            elem_size = ep["size"]
            ep["data"] = target_data[:elem_size]
            target_data = target_data[elem_size:]
            elements.append(ep)

    # DFU suffix: device, product, vendor, dfu version, "UFD", len=16, crc32
    suffix = _named(struct.unpack("<4H3sBI", data[:16]), "device product vendor dfu ufd len crc")
    if crc != suffix["crc"]:
        raise ValueError(
            "CRC mismatch: computed 0x{:08x}, file 0x{:08x}".format(crc, suffix["crc"])
        )
    return elements
