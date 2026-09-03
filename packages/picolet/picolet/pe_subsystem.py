"""Windows PE subsystem patching.

picolet's windows-x64 runtime is always linked as a console-subsystem
(IMAGE_SUBSYSTEM_WINDOWS_CUI) binary, so every build -- cli or webview --
flashes a console window on launch even though most apps never write to it.
`set_subsystem_gui` flips a downloaded/cached runtime copy to
IMAGE_SUBSYSTEM_WINDOWS_GUI so no console is allocated. It's driven by
[app] console = false in picolet.toml (build_cmd.py); the app must not
rely on inherited stdio in that mode -- there is no console for print()
or input() to reach, and stdin/stdout/stderr handles are simply absent.
"""

from __future__ import annotations

import struct

import pefile

IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
IMAGE_SUBSYSTEM_WINDOWS_CUI = 3


def set_subsystem_gui(exe_bytes: bytes) -> bytes:
    """Return `exe_bytes` with its PE subsystem flipped from CUI to GUI."""
    pe = pefile.PE(data=exe_bytes, fast_load=True)
    if pe.OPTIONAL_HEADER.Subsystem != IMAGE_SUBSYSTEM_WINDOWS_CUI:
        raise ValueError(
            f"expected IMAGE_SUBSYSTEM_WINDOWS_CUI ({IMAGE_SUBSYSTEM_WINDOWS_CUI}), "
            f"got {pe.OPTIONAL_HEADER.Subsystem}"
        )

    buf = bytearray(exe_bytes)
    off = pe.OPTIONAL_HEADER.get_field_absolute_offset("Subsystem")
    struct.pack_into("<H", buf, off, IMAGE_SUBSYSTEM_WINDOWS_GUI)

    # The checksum algorithm treats the checksum field itself as zero; recompute
    # after the subsystem edit above is in place.
    checksum = pefile.PE(data=bytes(buf), fast_load=True).generate_checksum()
    checksum_off = pe.OPTIONAL_HEADER.get_field_absolute_offset("CheckSum")
    struct.pack_into("<I", buf, checksum_off, checksum)

    return bytes(buf)
