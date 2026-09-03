"""Shared PE resource-directory read/patch plumbing.

Both pe_icon.py (RT_ICON/RT_GROUP_ICON) and pe_version.py (RT_VERSION) need
the same operation: read a linked exe's full resource tree, replace one or
two entries, and write the merged tree back. This module owns that
round-trip; callers only build/mutate the tree dict.

Named (non-numeric) resource entries, and resources with data directly under
a type or name directory (skipping the language level), are not
round-tripped -- picolet's windows runtime has neither, and supporting them
would need additional string-table layout this module doesn't implement;
encountering either raises rather than silently dropping data.

The merged tree is always serialized fresh into a brand-new section
appended to the PE, rather than growing/relocating the original .rsrc
section in place (which would need to shift every section after it). The
old resource section (if any) is renamed to ".rsrc0" and left in place,
orphaned.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any

import pefile

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
ResourceTree = dict[int, dict[int, dict[int, tuple[bytes, int]]]]


def _align_up(value: int, align: int) -> int:
    return (value + align - 1) // align * align


def next_free_id(ids: Mapping[int, Any]) -> int:
    return max(ids, default=0) + 1


def _extract_existing_resources(pe: pefile.PE) -> ResourceTree:
    tree: ResourceTree = {}
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


def _serialize_resources(tree: ResourceTree, base_rva: int) -> bytes:
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


def load_resource_tree(
    exe_bytes: bytes,
) -> tuple[pefile.PE, ResourceTree, pefile.SectionStructure | None]:
    """Parse `exe_bytes`' resource directory into a mutable tree.

    Returns (pe, tree, old_resource_section) -- pass all three to
    `save_resource_tree` once `tree` has been mutated in place.
    """
    pe = pefile.PE(data=exe_bytes, fast_load=True)

    security_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
    ]
    if security_dir.VirtualAddress or security_dir.Size:
        raise ValueError(
            "exe is Authenticode-signed; patching resources would invalidate the "
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
    return pe, tree, old_resource_section


def save_resource_tree(
    pe: pefile.PE,
    exe_bytes: bytes,
    tree: ResourceTree,
    old_resource_section: pefile.SectionStructure | None,
) -> bytes:
    """Serialize `tree` and append it as a new .rsrc section of `exe_bytes`."""
    section_align = pe.OPTIONAL_HEADER.SectionAlignment
    base_rva = _align_up(
        max(s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData) for s in pe.sections),
        section_align,
    )
    section_data = _serialize_resources(tree, base_rva)
    return _append_section(pe, exe_bytes, b".rsrc", section_data, base_rva, old_resource_section)
