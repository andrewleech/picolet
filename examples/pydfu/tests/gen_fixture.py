# /// script
# dependencies = []
# ///
"""Generate a minimal valid DfuSe test fixture (tests/fixtures/test.dfu).

This is a one-time script; the generated file is committed to the repo.
The DfuSe format: DFU Prefix + Image Prefix + Element Data + DFU Suffix.
Reference: ST DfuSe format spec (UM0391).
"""
import struct
import sys
import zlib
from pathlib import Path

OUTPUT = Path(__file__).parent / "fixtures" / "test.dfu"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

# --- Element data: 1 KiB of 0x00 bytes ---
ELEM_DATA = b"\x00" * 1024
ELEM_ADDR = 0x08000000

# --- Element prefix: <2I addr size ---
elem_prefix = struct.pack("<II", ELEM_ADDR, len(ELEM_DATA))
target_payload = elem_prefix + ELEM_DATA

# --- Image Prefix: "Target" + altsetting + named=0 + name(255 bytes) + size + elements=1 ---
target_name = b"\x00" * 255
img_prefix = struct.pack(
    "<6sBI255sII",
    b"Target",
    0,                        # altsetting
    0,                        # named = False
    target_name,              # 255 bytes of name
    len(target_payload),      # target image size
    1,                        # number of elements
)
target_block = img_prefix + target_payload

# --- DFU Prefix: "DfuSe" + version=1 + total_size + targets=1 ---
# total_size = len of everything AFTER the prefix (all targets)
dfu_prefix = struct.pack(
    "<5sBIB",
    b"DfuSe",
    1,                        # version
    len(target_block),        # size (total of all target blocks)
    1,                        # number of targets
)

body = dfu_prefix + target_block

# --- DFU Suffix: device=0 product=0xdf11 vendor=0x0483 dfu=0x011a "UFD" len=16 crc ---
# CRC in the DfuSe format covers ALL bytes of the file EXCEPT the last 4 bytes
# (the CRC field itself). So we build the suffix header first (12 bytes), append
# it to body, compute CRC over that, then append the 4-byte CRC field.
suffix_header = struct.pack(
    "<4H3sB",
    0x0000,   # device (firmware version)
    0xDF11,   # product
    0x0483,   # vendor (STMicro)
    0x011A,   # DFU spec version
    b"UFD",
    16,       # suffix length
)
crc_input = body + suffix_header  # everything except the trailing 4-byte CRC field
crc_value = 0xFFFFFFFF & -zlib.crc32(crc_input) - 1
dfu_suffix = suffix_header + struct.pack("<I", crc_value)

dfu_file = body + dfu_suffix

OUTPUT.write_bytes(dfu_file)
print(f"Written {len(dfu_file)} bytes to {OUTPUT}")

# Verify round-trip
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
try:
    import pydfu_adapter
    elems = pydfu_adapter.read_dfu_file(str(OUTPUT))
    assert len(elems) == 1
    assert elems[0]["addr"] == ELEM_ADDR
    assert elems[0]["size"] == len(ELEM_DATA)
    print(f"Verification OK: {len(elems)} element, addr=0x{elems[0]['addr']:08x}, size={elems[0]['size']}")
except Exception as e:
    print(f"Verification FAILED: {e}", file=sys.stderr)
    sys.exit(1)
