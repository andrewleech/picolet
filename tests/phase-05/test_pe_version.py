"""pe_version unit tests.

Runs against the real windows-x64 cli runtime binary checked into the repo
(packages/picolet-runtime/build/) -- the only file this module is meant to
patch in practice. Skipped if that binary isn't present locally.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_PKG_PARENT = _REPO_ROOT / "packages" / "picolet"
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

pefile = pytest.importorskip("pefile")

from picolet import pe_resources, pe_version  # noqa: E402

RUNTIME_PATH = (
    _REPO_ROOT / "packages" / "picolet-runtime" / "build" / "picolet-runtime-windows-x64-cli.exe"
)

pytestmark = pytest.mark.skipif(
    not RUNTIME_PATH.is_file(), reason=f"windows runtime binary not present: {RUNTIME_PATH}"
)


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.2.3", (0x00010002, 0x00030000)),
        ("1.2.3.4", (0x00010002, 0x00030004)),
        ("1.2", (0x00010002, 0x00000000)),
        ("0.1.1.dev3+g1a2b3c4", (0x00000001, 0x00010003)),  # trailing "+..." dropped
    ],
)
def test_parse_file_version(version, expected):
    assert pe_version.parse_file_version(version) == expected


def test_build_version_info_round_trips_through_resource_tree():
    data = pe_version.build_version_info(
        {"CompanyName": "Acme", "FileDescription": "Widget", "ProductName": "Widget"},
        file_version="1.2.3",
        product_version="1.2.3",
    )
    # Must be 4-byte aligned throughout, and at minimum contain the
    # UTF-16LE bytes of every string we asked for.
    assert len(data) % 4 == 0
    assert "Acme".encode("utf-16-le") in data
    assert "Widget".encode("utf-16-le") in data
    assert "FileVersion".encode("utf-16-le") in data
    assert "1.2.3".encode("utf-16-le") in data


def test_empty_fields_omitted_from_string_table():
    data = pe_version.build_version_info(
        {"CompanyName": "", "FileDescription": "Widget", "ProductName": "Widget"},
        file_version="1.0.0",
        product_version="1.0.0",
    )
    assert "CompanyName".encode("utf-16-le") not in data


def test_inject_version_info_preserves_lang_and_other_resources():
    runtime_bytes = RUNTIME_PATH.read_bytes()
    pe_before = pefile.PE(data=runtime_bytes, fast_load=True)
    pe_before.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    before = pe_resources._extract_existing_resources(pe_before)
    version_type = next(t for t in before if t == pe_version.RT_VERSION)
    version_name = next(iter(before[version_type]))
    original_lang = next(iter(before[version_type][version_name]))

    patched = pe_version.inject_version_info(
        runtime_bytes,
        {"CompanyName": "Acme", "FileDescription": "Widget", "ProductName": "Widget"},
        file_version="1.2.3",
        product_version="1.2.3",
    )

    pe_after = pefile.PE(data=patched, fast_load=True)
    pe_after.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    assert not pe_after.get_warnings()
    after = pe_resources._extract_existing_resources(pe_after)

    assert list(after[pe_version.RT_VERSION][version_name].keys()) == [original_lang]
    new_data, _codepage = after[pe_version.RT_VERSION][version_name][original_lang]
    assert "Widget".encode("utf-16-le") in new_data
    assert "Acme".encode("utf-16-le") in new_data
    assert "Picolet Runtime".encode("utf-16-le") not in new_data

    assert pe_after.OPTIONAL_HEADER.CheckSum == pefile.PE(
        data=patched, fast_load=True
    ).generate_checksum()
