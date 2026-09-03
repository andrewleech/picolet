"""Windows PE VERSION_INFO (RT_VERSION) patching.

picolet's windows-x64 runtime ships a generic VERSION_INFO resource
(FileDescription "Picolet Runtime", ProductName "Picolet", version
1.0.0.0 -- see ports/windows/micropython.rc) that shows up verbatim in
Explorer's Properties > Details tab for every app built with picolet,
regardless of what the app actually is. This module replaces that
resource's fixed-version fields and string table with values derived from
[app] in picolet.toml. See pe_resources.py for how the resource tree
itself is read back, merged, and written.

The existing resource's language/codepage is reused so Explorer's language
fallback doesn't need to do anything unexpected; if the runtime somehow has
none, EN-US/Unicode (0x0409, 1200) is assumed.
"""

from __future__ import annotations

import re
import struct

from picolet import pe_resources

RT_VERSION = 16
_DEFAULT_LANG = 0x0409
_DEFAULT_CODEPAGE = 1200

_VERSION_DWORDS_RE = re.compile(r"\d+")

_STRING_FIELDS = (
    "CompanyName",
    "FileDescription",
    "FileVersion",
    "InternalName",
    "LegalCopyright",
    "OriginalFilename",
    "ProductName",
    "ProductVersion",
)


def parse_file_version(version: str) -> tuple[int, int]:
    """Split a version string into the (MS, LS) DWORD pair VS_FIXEDFILEINFO wants.

    Windows FILEVERSION/PRODUCTVERSION are exactly 4 16-bit integers. Only
    the leading run of dot-separated numbers is used -- any dev/local suffix
    (e.g. "1.2.3.dev4+gabc123") is dropped for this binary field but kept in
    full in the string-table FileVersion/ProductVersion values.
    """
    parts = _VERSION_DWORDS_RE.findall(version.split("+", 1)[0])
    nums = [int(p) & 0xFFFF for p in parts[:4]]
    nums += [0] * (4 - len(nums))
    major, minor, build, revision = nums
    return (major << 16) | minor, (build << 16) | revision


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def _build_wstring(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\x00\x00"


def _build_var(key: str, value_type: int, value: bytes) -> bytes:
    key_bytes = _build_wstring(key)
    header_len = 6 + len(key_bytes)
    body_off = _pad4(header_len)
    total = _pad4(body_off + len(value))
    buf = bytearray(total)
    struct.pack_into("<HHH", buf, 0, total, len(value), value_type)
    buf[6 : 6 + len(key_bytes)] = key_bytes
    buf[body_off : body_off + len(value)] = value
    return bytes(buf)


def _build_string(key: str, value: str) -> bytes:
    key_bytes = _build_wstring(key)
    value_bytes = _build_wstring(value)
    value_len_words = len(value_bytes) // 2
    header_len = 6 + len(key_bytes)
    value_off = _pad4(header_len)
    total = _pad4(value_off + len(value_bytes))
    buf = bytearray(total)
    struct.pack_into("<HHH", buf, 0, total, value_len_words, 1)
    buf[6 : 6 + len(key_bytes)] = key_bytes
    buf[value_off : value_off + len(value_bytes)] = value_bytes
    return bytes(buf)


def _build_block(key: str, value_type: int, value: bytes, children: bytes) -> bytes:
    key_bytes = _build_wstring(key)
    header_len = 6 + len(key_bytes)
    children_off = _pad4(header_len + len(value))
    total = children_off + len(children)
    buf = bytearray(total)
    struct.pack_into("<HHH", buf, 0, total, len(value), value_type)
    buf[6 : 6 + len(key_bytes)] = key_bytes
    if value:
        buf[header_len : header_len + len(value)] = value
    buf[children_off : children_off + len(children)] = children
    return bytes(buf)


def _build_string_table(lang: int, codepage: int, fields: dict[str, str]) -> bytes:
    strings = b"".join(_build_string(key, value) for key, value in fields.items() if value)
    lang_hex = "{:04x}{:04x}".format(lang, codepage)
    return _build_block(lang_hex, 0, b"", strings)


def _build_var_file_info(lang: int, codepage: int) -> bytes:
    translation = struct.pack("<HH", lang, codepage)
    var = _build_var("Translation", 0, translation)
    return _build_block("VarFileInfo", 0, b"", var)


def build_version_info(
    fields: dict[str, str],
    file_version: str,
    product_version: str,
    lang: int = _DEFAULT_LANG,
    codepage: int = _DEFAULT_CODEPAGE,
) -> bytes:
    """Build a full VS_VERSIONINFO resource blob.

    `fields` may set any of CompanyName/FileDescription/InternalName/
    LegalCopyright/OriginalFilename/ProductName -- absent or empty ones are
    omitted from the string table rather than written blank.
    """
    string_fields = dict(fields)
    string_fields["FileVersion"] = file_version
    string_fields["ProductVersion"] = product_version
    string_fields = {k: v for k, v in string_fields.items() if k in _STRING_FIELDS and v}

    string_table = _build_string_table(lang, codepage, string_fields)
    string_file_info = _build_block("StringFileInfo", 0, b"", string_table)
    var_file_info = _build_var_file_info(lang, codepage)
    children = string_file_info + var_file_info

    file_ms, file_ls = parse_file_version(file_version)
    product_ms, product_ls = parse_file_version(product_version)
    fixed_file_info = struct.pack(
        "<12I",
        0xFEEF04BD,  # dwSignature
        0x00010000,  # dwStrucVersion
        file_ms, file_ls,
        product_ms, product_ls,
        0x3F,  # dwFileFlagsMask
        0,  # dwFileFlags
        0x40004,  # dwFileOS: VOS_NT_WINDOWS32
        1,  # dwFileType: VFT_APP
        0,  # dwFileSubtype
        0,  # dwFileDateMS
    ) + struct.pack("<I", 0)  # dwFileDateLS

    key_bytes = _build_wstring("VS_VERSION_INFO")
    header_len = 6 + len(key_bytes)
    value_off = _pad4(header_len)
    children_off = _pad4(value_off + len(fixed_file_info))
    total = children_off + len(children)
    buf = bytearray(total)
    struct.pack_into("<HHH", buf, 0, total, len(fixed_file_info), 0)
    buf[6 : 6 + len(key_bytes)] = key_bytes
    buf[value_off : value_off + len(fixed_file_info)] = fixed_file_info
    buf[children_off : children_off + len(children)] = children
    return bytes(buf)


def apply_version_info(
    tree: pe_resources.ResourceTree,
    fields: dict[str, str],
    file_version: str,
    product_version: str,
) -> None:
    """Mutate `tree` in place to replace its RT_VERSION resource.

    Use this (rather than `inject_version_info`) when composing multiple
    resource patches into a single load_resource_tree/save_resource_tree
    round trip -- see build_cmd.py.
    """
    # The RT_VERSION leaf's (name, lang) keys in the resource directory are
    # plain (resource id, LANGID) -- unrelated to the VarFileInfo
    # Translation pair packed *inside* the resource's own bytes below.
    name_id, lang = 1, _DEFAULT_LANG
    existing = tree.get(RT_VERSION, {})
    if existing:
        name_id = next(iter(existing))
        lang = next(iter(existing[name_id]))

    version_data = build_version_info(fields, file_version, product_version, lang=lang)
    tree[RT_VERSION] = {name_id: {lang: (version_data, 0)}}


def inject_version_info(
    exe_bytes: bytes,
    fields: dict[str, str],
    file_version: str,
    product_version: str,
) -> bytes:
    """Return `exe_bytes` with its RT_VERSION resource replaced."""
    pe, tree, old_resource_section = pe_resources.load_resource_tree(exe_bytes)
    apply_version_info(tree, fields, file_version, product_version)
    return pe_resources.save_resource_tree(pe, exe_bytes, tree, old_resource_section)
