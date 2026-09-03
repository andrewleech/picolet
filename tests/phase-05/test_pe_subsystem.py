"""pe_subsystem unit tests.

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

from picolet import pe_subsystem  # noqa: E402

RUNTIME_PATH = (
    _REPO_ROOT / "packages" / "picolet-runtime" / "build" / "picolet-runtime-windows-x64-cli.exe"
)

pytestmark = pytest.mark.skipif(
    not RUNTIME_PATH.is_file(), reason=f"windows runtime binary not present: {RUNTIME_PATH}"
)


def test_flips_cui_to_gui_and_checksum_verifies():
    original = RUNTIME_PATH.read_bytes()
    pe_before = pefile.PE(data=original, fast_load=True)
    assert pe_before.OPTIONAL_HEADER.Subsystem == pe_subsystem.IMAGE_SUBSYSTEM_WINDOWS_CUI

    patched = pe_subsystem.set_subsystem_gui(original)

    pe_after = pefile.PE(data=patched, fast_load=True)
    assert pe_after.OPTIONAL_HEADER.Subsystem == pe_subsystem.IMAGE_SUBSYSTEM_WINDOWS_GUI
    assert pe_after.OPTIONAL_HEADER.CheckSum == pefile.PE(
        data=patched, fast_load=True
    ).generate_checksum()
    # Nothing else about the file should have changed size or shifted.
    assert len(patched) == len(original)


def test_rejects_already_gui_subsystem():
    original = RUNTIME_PATH.read_bytes()
    already_gui = pe_subsystem.set_subsystem_gui(original)
    with pytest.raises(ValueError):
        pe_subsystem.set_subsystem_gui(already_gui)
