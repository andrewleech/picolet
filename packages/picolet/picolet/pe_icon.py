"""Windows PE icon embedding.

picolet's Windows runtime binaries are downloaded pre-linked (see
build_cmd.py's icon-embedding step) and carry no icon resource. This module
builds RT_ICON / RT_GROUP_ICON entries from a .ico file so a per-app icon
(declared via [app] icon in picolet.toml) shows up in Explorer / the
taskbar without requiring a Windows toolchain. See pe_resources.py for how
the resource tree itself is read back, merged, and written.

Any pre-existing RT_ICON/RT_GROUP_ICON entries are dropped rather than kept
alongside the new ones: Explorer/LoadIcon resolve the lowest-numbered
RT_GROUP_ICON, so a second icon group added at a higher id would silently
never be shown.
"""

from __future__ import annotations

import struct
from pathlib import Path

from picolet import pe_resources

RT_ICON = 3
RT_GROUP_ICON = 14
_LANG_NEUTRAL = 0

# ICONDIRENTRY, as stored in a .ico file: 16 bytes, ending in a 4-byte file offset.
_ICONDIRENTRY = struct.Struct("<BBBBHHII")
# GRPICONDIRENTRY, as stored in an RT_GROUP_ICON resource: 14 bytes, ending in
# a 2-byte resource ID instead of a file offset.
_GRPICONDIRENTRY = struct.Struct("<BBBBHHIH")


def _read_ico(icon_path: Path) -> list[dict]:
    data = icon_path.read_bytes()
    if len(data) < 6:
        raise ValueError(f"{icon_path}: not a valid .ico file")
    reserved, res_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or res_type != 1:
        raise ValueError(f"{icon_path}: not a valid .ico file")
    entries_end = 6 + count * _ICONDIRENTRY.size
    if entries_end > len(data):
        raise ValueError(f"{icon_path}: truncated ICONDIRENTRY table")
    images = []
    for i in range(count):
        width, height, color_count, _reserved2, planes, bit_count, bytes_in_res, image_offset = (
            _ICONDIRENTRY.unpack_from(data, 6 + i * _ICONDIRENTRY.size)
        )
        if bytes_in_res <= 0 or image_offset + bytes_in_res > len(data):
            raise ValueError(f"{icon_path}: image entry {i} points outside the file")
        images.append(
            {
                "width": width,
                "height": height,
                "color_count": color_count,
                "planes": planes,
                "bit_count": bit_count,
                "data": data[image_offset : image_offset + bytes_in_res],
            }
        )
    if not images:
        raise ValueError(f"{icon_path}: .ico file contains no images")
    return images


def _build_group_icon(entries: list[tuple[dict, int]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(entries))
    body = b"".join(
        _GRPICONDIRENTRY.pack(
            img["width"],
            img["height"],
            img["color_count"],
            0,
            img["planes"],
            img["bit_count"],
            len(img["data"]),
            icon_id,
        )
        for img, icon_id in entries
    )
    return header + body


def apply_icon(tree: pe_resources.ResourceTree, icon_path: Path) -> None:
    """Mutate `tree` in place to set RT_ICON/RT_GROUP_ICON from `icon_path`.

    Use this (rather than `inject_icon`) when composing multiple resource
    patches into a single load_resource_tree/save_resource_tree round trip
    -- see build_cmd.py, which combines this with pe_version's patch so an
    icon+version-info build appends one new section, not two.
    """
    images = _read_ico(icon_path)

    tree.pop(RT_ICON, None)
    tree.pop(RT_GROUP_ICON, None)

    icon_tree = tree.setdefault(RT_ICON, {})
    grp_entries = []
    for i, img in enumerate(images):
        icon_id = i + 1
        icon_tree[icon_id] = {_LANG_NEUTRAL: (img["data"], 0)}
        grp_entries.append((img, icon_id))

    group_data = _build_group_icon(grp_entries)
    tree.setdefault(RT_GROUP_ICON, {})[1] = {_LANG_NEUTRAL: (group_data, 0)}


def inject_icon(exe_bytes: bytes, icon_path: Path) -> bytes:
    """Return `exe_bytes` with `icon_path` embedded as its RT_ICON/RT_GROUP_ICON resources."""
    pe, tree, old_resource_section = pe_resources.load_resource_tree(exe_bytes)
    apply_icon(tree, icon_path)
    return pe_resources.save_resource_tree(pe, exe_bytes, tree, old_resource_section)
