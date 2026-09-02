"""Windows PE icon embedding.

picolet's Windows runtime binaries are downloaded pre-linked (see
build_cmd.py's icon-embedding step) and carry no icon resource. This module
patches RT_ICON / RT_GROUP_ICON entries into an already-linked exe's resource
directory so a per-app icon (declared via [app] icon in picolet.toml) shows
up in Explorer / the taskbar without requiring a Windows toolchain.

Existing resources (e.g. the runtime's VERSION_INFO) are preserved: they are
read back out via pefile and re-emitted, alongside the new icon entries, into
a freshly built resource section that is appended to the PE as a new
section. The old resource section (if any) is renamed to ".rsrc0" and left
in place, orphaned, rather than reused -- growing/relocating it in place
would need to shift every section after it. Any pre-existing RT_ICON/
RT_GROUP_ICON entries are dropped rather than kept alongside the new ones:
Explorer/LoadIcon resolve the lowest-numbered RT_GROUP_ICON, so a second icon
group added at a higher id would silently never be shown. Named
(non-numeric) resource entries, and resources with data directly under a
type or name directory (skipping the language level), are not round-tripped
-- the runtime this targets has neither, and supporting them would need
additional string-table layout this module doesn't implement; encountering
either raises rather than silently dropping data.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pefile

RT_ICON = 3
RT_GROUP_ICON = 14
_LANG_NEUTRAL = 0

# ICONDIRENTRY, as stored in a .ico file: 16 bytes, ending in a 4-byte file offset.
_ICONDIRENTRY = struct.Struct("<BBBBHHII")
# GRPICONDIRENTRY, as stored in an RT_GROUP_ICON resource: 14 bytes, ending in
# a 2-byte resource ID instead of a file offset.
_GRPICONDIRENTRY = struct.Struct("<BBBBHHIH")

_RES_DIR = struct.Struct("<IIHHHH")  # IMAGE_RESOURCE_DIRECTORY (16 bytes)
_RES_DIR_ENTRY = struct.Struct("<II")  # IMAGE_RESOURCE_DIRECTORY_ENTRY (8 bytes)
_RES_DATA_ENTRY = struct.Struct("<IIII")  # IMAGE_RESOURCE_DATA_ENTRY (16 bytes)
_HIGH_BIT = 0x80000000
_SECTION_HEADER_SIZE = 40

_IMAGE_SCN_CNT_INITIALIZED_DATA = 0x00000040
_IMAGE_SCN_MEM_READ = 0x40000000

# tree[type_id][name_id][lang_id] = (data, codepage). lang_id is the full,
# untruncated LANGID (primary | sublang << 10) -- pefile's convenience
# `.data.lang` attribute is only the primary language and collapses distinct
# sublanguages onto the same key, silently dropping all but one.
_ResourceTree = dict[int, dict[int, dict[int, tuple[bytes, int]]]]


def _align_up(value: int, align: int) -> int:
    return (value + align - 1) // align * align


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


def _extract_existing_resources(pe: pefile.PE) -> _ResourceTree:
    tree: _ResourceTree = {}
    res_dir = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if res_dir is None:
        return tree
    for type_entry in res_dir.entries:
        if type_entry.name is not None:
            raise ValueError(f"named resource type {type_entry.name!r} is not supported")
        type_dir = getattr(type_entry, "directory", None)
        if type_dir is None:
            raise ValueError(
                f"resource type {type_entry.struct.Id} has data directly under the "
                "type directory (no name level); unsupported"
            )
        name_tree = tree.setdefault(type_entry.struct.Id, {})
        for name_entry in type_dir.entries:
            if name_entry.name is not None:
                raise ValueError(f"named resource name {name_entry.name!r} is not supported")
            name_dir = getattr(name_entry, "directory", None)
            if name_dir is None:
                raise ValueError(
                    f"resource {type_entry.struct.Id}/{name_entry.struct.Id} has data "
                    "directly under the name directory (no language level); unsupported"
                )
            lang_tree = name_tree.setdefault(name_entry.struct.Id, {})
            for lang_entry in name_dir.entries:
                leaf = getattr(lang_entry, "data", None)
                if leaf is None:
                    raise ValueError(
                        f"resource {type_entry.struct.Id}/{name_entry.struct.Id} has a "
                        "language-level subdirectory instead of a leaf; unsupported"
                    )
                data_struct = leaf.struct
                raw = pe.get_data(data_struct.OffsetToData, data_struct.Size)
                lang_tree[lang_entry.struct.Id] = (bytes(raw), data_struct.CodePage)
    return tree


def _next_free_id(ids: Mapping[int, Any]) -> int:
    return max(ids, default=0) + 1


def _serialize_resources(tree: _ResourceTree, base_rva: int) -> bytes:
    types = sorted(tree)
    names_by_type = {t: sorted(tree[t]) for t in types}
    lang_dir_keys = [(t, n) for t in types for n in names_by_type[t]]
    langs_by_key = {k: sorted(tree[k[0]][k[1]]) for k in lang_dir_keys}
    leaf_keys = [(t, n, lang) for t, n in lang_dir_keys for lang in langs_by_key[(t, n)]]

    def dir_size(n_entries: int) -> int:
        return _RES_DIR.size + n_entries * _RES_DIR_ENTRY.size

    # Layout order: root type-dir, then one name-dir per type, then one
    # lang-dir per (type, name), then the data-entry array (one per leaf),
    # then the raw data blobs -- each in the same order as the keys above.
    off = 0
    root_off = off
    off += dir_size(len(types))
    name_dir_off = {}
    for t in types:
        name_dir_off[t] = off
        off += dir_size(len(names_by_type[t]))
    lang_dir_off = {}
    for k in lang_dir_keys:
        lang_dir_off[k] = off
        off += dir_size(len(langs_by_key[k]))
    data_entry_off = {}
    for k in leaf_keys:
        data_entry_off[k] = off
        off += _RES_DATA_ENTRY.size
    raw_data_off = {}
    for k in leaf_keys:
        raw_data_off[k] = off
        data_len = len(tree[k[0]][k[1]][k[2]][0])
        off += _align_up(data_len, 4)

    buf = bytearray(off)

    def write_dir(dir_off: int, entries: dict[int, tuple[bool, int]]) -> None:
        ids = sorted(entries)
        _RES_DIR.pack_into(buf, dir_off, 0, 0, 0, 0, 0, len(ids))
        p = dir_off + _RES_DIR.size
        for id_ in ids:
            is_subdir, target = entries[id_]
            _RES_DIR_ENTRY.pack_into(buf, p, id_, target | _HIGH_BIT if is_subdir else target)
            p += _RES_DIR_ENTRY.size

    write_dir(root_off, {t: (True, name_dir_off[t]) for t in types})
    for t in types:
        write_dir(name_dir_off[t], {n: (True, lang_dir_off[(t, n)]) for n in names_by_type[t]})
    for k in lang_dir_keys:
        write_dir(
            lang_dir_off[k], {lang: (False, data_entry_off[(*k, lang)]) for lang in langs_by_key[k]}
        )

    for k in leaf_keys:
        data, codepage = tree[k[0]][k[1]][k[2]]
        _RES_DATA_ENTRY.pack_into(
            buf, data_entry_off[k], raw_data_off[k] + base_rva, len(data), codepage, 0
        )
        buf[raw_data_off[k] : raw_data_off[k] + len(data)] = data

    return bytes(buf)


def _rename_section(buf: bytearray, section: pefile.SectionStructure, name: bytes) -> None:
    file_off = section.get_file_offset()
    buf[file_off : file_off + 8] = name[:8].ljust(8, b"\x00")


def _append_section(
    pe: pefile.PE,
    exe_bytes: bytes,
    name: bytes,
    data: bytes,
    rva: int,
    old_resource_section: pefile.SectionStructure | None,
) -> bytes:
    buf = bytearray(exe_bytes)

    last_section = pe.sections[-1]
    header_off = last_section.get_file_offset() + last_section.sizeof()
    header_limit = min(pe.OPTIONAL_HEADER.SizeOfHeaders, pe.sections[0].PointerToRawData)
    if header_off + _SECTION_HEADER_SIZE > header_limit:
        raise ValueError(
            "no room in the PE header for an additional section header "
            f"(header space ends at {header_limit}, needed up to offset "
            f"{header_off + _SECTION_HEADER_SIZE})"
        )
    if any(buf[header_off : header_off + _SECTION_HEADER_SIZE]):
        raise ValueError(
            f"PE header padding at offset {header_off} is not zero-filled; "
            "refusing to overwrite what looks like existing data"
        )

    if old_resource_section is not None:
        _rename_section(buf, old_resource_section, b".rsrc0")

    file_align = pe.OPTIONAL_HEADER.FileAlignment
    section_align = pe.OPTIONAL_HEADER.SectionAlignment
    raw_offset = _align_up(len(buf), file_align)
    raw_size = _align_up(len(data), file_align)

    header = struct.pack(
        "<8sIIIIIIHHI",
        name[:8].ljust(8, b"\x00"),
        len(data),  # VirtualSize
        rva,
        raw_size,
        raw_offset,
        0,
        0,
        0,
        0,
        _IMAGE_SCN_MEM_READ | _IMAGE_SCN_CNT_INITIALIZED_DATA,
    )
    buf[header_off : header_off + _SECTION_HEADER_SIZE] = header

    def patch(pe_struct, field: str, fmt: str, value: int) -> None:
        struct.pack_into(fmt, buf, pe_struct.get_field_absolute_offset(field), value)

    patch(pe.FILE_HEADER, "NumberOfSections", "<H", pe.FILE_HEADER.NumberOfSections + 1)
    patch(pe.OPTIONAL_HEADER, "SizeOfImage", "<I", _align_up(rva + len(data), section_align))
    patch(
        pe.OPTIONAL_HEADER,
        "SizeOfInitializedData",
        "<I",
        pe.OPTIONAL_HEADER.SizeOfInitializedData + raw_size,
    )

    res_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]
    ]
    patch(res_dir, "VirtualAddress", "<I", rva)
    patch(res_dir, "Size", "<I", len(data))

    if raw_offset > len(buf):
        buf.extend(b"\x00" * (raw_offset - len(buf)))
    buf.extend(data)
    buf.extend(b"\x00" * (raw_size - len(data)))

    # The checksum algorithm treats the checksum field itself as zero; recompute
    # after every other header/section edit above is in place.
    checksum = pefile.PE(data=bytes(buf), fast_load=True).generate_checksum()
    patch(pe.OPTIONAL_HEADER, "CheckSum", "<I", checksum)

    return bytes(buf)


def inject_icon(exe_bytes: bytes, icon_path: Path) -> bytes:
    """Return `exe_bytes` with `icon_path` embedded as its RT_ICON/RT_GROUP_ICON resources."""
    images = _read_ico(icon_path)

    pe = pefile.PE(data=exe_bytes, fast_load=True)

    security_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
    ]
    if security_dir.VirtualAddress or security_dir.Size:
        raise ValueError(
            "exe is Authenticode-signed; embedding an icon would invalidate the "
            "signature and is not supported"
        )

    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    warnings = pe.get_warnings()
    if warnings:
        raise ValueError(f"pefile could not fully parse the existing resource directory: {warnings}")

    old_resource_rva = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]
    ].VirtualAddress
    old_resource_section = pe.get_section_by_rva(old_resource_rva) if old_resource_rva else None

    tree = _extract_existing_resources(pe)
    # Any pre-existing icon is fully replaced, not merged alongside: Explorer/
    # LoadIcon resolve the lowest-numbered RT_GROUP_ICON, so a group added at
    # a higher id would silently never be shown.
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

    section_align = pe.OPTIONAL_HEADER.SectionAlignment
    base_rva = _align_up(
        max(s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData) for s in pe.sections),
        section_align,
    )

    section_data = _serialize_resources(tree, base_rva)
    return _append_section(pe, exe_bytes, b".rsrc", section_data, base_rva, old_resource_section)
