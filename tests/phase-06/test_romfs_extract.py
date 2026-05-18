"""
PH06 — tests for picolet.romfs_extract.

Uses real tmp files and tmp_path so no romfs is required on the host.
sys.platform and os.getenv are monkeypatched where needed to exercise
the Windows code path from a Linux host.
"""
from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path is extended by tests/phase-06/conftest.py so `picolet` resolves
# from packages/picolet-runtime/python without a host install.
# ---------------------------------------------------------------------------
from picolet.romfs_extract import extract_dir, extract_to_temp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path: Path, content: bytes = b"hello") -> Path:
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# extract_to_temp — non-Windows
# ---------------------------------------------------------------------------

class TestExtractToTempNonWindows:

    def test_returns_input_unchanged(self, monkeypatch, tmp_path):
        """On non-Windows, extract_to_temp must return the input path unchanged."""
        monkeypatch.setattr(sys, "platform", "linux")
        result = extract_to_temp("/rom/foo/bar.so")
        assert result == "/rom/foo/bar.so"

    def test_returns_input_unchanged_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        result = extract_to_temp("/rom/foo/libfoo.dylib")
        assert result == "/rom/foo/libfoo.dylib"


# ---------------------------------------------------------------------------
# extract_to_temp — Windows path (using real tmp files)
# ---------------------------------------------------------------------------

class TestExtractToTempWindows:
    """Windows code path tests.

    The module builds paths with Windows-style backslash separators when
    sys.platform == "win32".  On a Linux host the resulting path strings
    contain literal backslashes (e.g. "/tmp/foo\\picolet_test\\libfoo.dll"),
    which Python's open() accepts as a regular filename component on Linux.
    Tests therefore use str-based path construction rather than pathlib.Path
    to avoid cross-platform separator confusion.
    """

    def test_copies_file_and_returns_dest(self, monkeypatch, tmp_path):
        """On Windows, extract_to_temp copies the source to <TEMP>\\<subdir>\\<name>."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.delenv("TMP", raising=False)

        # Pre-create the subdir the module will try to os.mkdir() — on Linux
        # the "Windows" path with embedded backslash is a single flat dirname.
        subdir_name = "picolet_test"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        src = _make_file(tmp_path / "libfoo.dll", b"dlldata")
        result = extract_to_temp(str(src), subdir=subdir_name)

        assert os.path.isfile(result)
        with open(result, "rb") as f:
            assert f.read() == b"dlldata"
        assert result.endswith("libfoo.dll")
        assert subdir_name in result

    def test_raises_oserror_when_source_missing(self, monkeypatch, tmp_path):
        """extract_to_temp raises OSError when the romfs file does not exist."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        with pytest.raises(OSError, match="file not found in romfs"):
            extract_to_temp(str(tmp_path / "nonexistent.dll"), subdir="picolet_test")

    def test_idempotent_skips_copy_when_size_matches(self, monkeypatch, tmp_path):
        """Second call with same-size dest skips the copy (mtime unchanged)."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        subdir_name = "picolet_idem"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        src = _make_file(tmp_path / "lib.dll", b"PAYLOAD")

        result1 = extract_to_temp(str(src), subdir=subdir_name)
        mtime_after_first = os.stat(result1).st_mtime

        result2 = extract_to_temp(str(src), subdir=subdir_name)
        mtime_after_second = os.stat(result2).st_mtime

        assert result1 == result2
        with open(result1, "rb") as f:
            assert f.read() == b"PAYLOAD"
        assert mtime_after_first == mtime_after_second

    def test_overwrites_when_size_differs(self, monkeypatch, tmp_path):
        """If dest exists with a different size, the copy runs again."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        subdir_name = "picolet_overwrite"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        src = _make_file(tmp_path / "lib2.dll", b"NEW_CONTENT")

        # Pre-place a stale dest with different size.
        stale = expected_subdir + "\\lib2.dll"
        with open(stale, "wb") as f:
            f.write(b"OLD")

        result = extract_to_temp(str(src), subdir=subdir_name)
        with open(result, "rb") as f:
            assert f.read() == b"NEW_CONTENT"


# ---------------------------------------------------------------------------
# extract_dir
# ---------------------------------------------------------------------------

class TestExtractDir:
    """Tests for extract_dir.

    When monkeypatching sys.platform = "win32" on a Linux host, path
    strings returned contain Windows-style backslash separators.  Tests
    use str operations and os.path rather than pathlib.Path to avoid the
    separator mismatch.
    """

    def test_returns_romfs_dir_unchanged_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        result = extract_dir("/rom/src/_usb")
        assert result == "/rom/src/_usb"

    def test_returns_dest_dir_on_windows(self, monkeypatch, tmp_path):
        """extract_dir returns the real dest dir on Windows and extracts files."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        src_dir = tmp_path / "romfs_dir"
        src_dir.mkdir()
        _make_file(src_dir / "libfoo.dll", b"dllcontent")

        subdir_name = "picolet_dirtest"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        result = extract_dir(str(src_dir), subdir=subdir_name)

        assert os.path.isdir(result)
        dest_file = result + "\\libfoo.dll"
        assert os.path.isfile(dest_file)
        with open(dest_file, "rb") as f:
            assert f.read() == b"dllcontent"

    def test_skips_nested_directories(self, monkeypatch, tmp_path):
        """extract_dir must not recurse into subdirectories."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        src_dir = tmp_path / "romfs_nested"
        src_dir.mkdir()
        _make_file(src_dir / "top.dll", b"toplevel")
        nested = src_dir / "subdir"
        nested.mkdir()
        _make_file(nested / "nested.dll", b"nested")

        subdir_name = "picolet_nested"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        result = extract_dir(str(src_dir), subdir=subdir_name)

        # Only the top-level file must appear.
        assert os.path.isfile(result + "\\top.dll")
        assert not os.path.isfile(result + "\\nested.dll")
        assert not os.path.isdir(result + "\\subdir")

    def test_dest_dir_created_when_absent(self, monkeypatch, tmp_path):
        """extract_dir creates the destination subdir if it does not exist."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        src_dir = tmp_path / "romfs_new"
        src_dir.mkdir()
        _make_file(src_dir / "a.dll", b"A")

        subdir_name = "picolet_brand_new"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        result = extract_dir(str(src_dir), subdir=subdir_name)
        assert os.path.isdir(result)

    def test_idempotent_on_repeated_call(self, monkeypatch, tmp_path):
        """Calling extract_dir twice does not raise and content is identical."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TEMP", str(tmp_path))

        src_dir = tmp_path / "romfs_idem"
        src_dir.mkdir()
        _make_file(src_dir / "b.dll", b"BBBBB")

        subdir_name = "picolet_idem2"
        expected_subdir = str(tmp_path) + "\\" + subdir_name
        os.makedirs(expected_subdir, exist_ok=True)

        r1 = extract_dir(str(src_dir), subdir=subdir_name)
        r2 = extract_dir(str(src_dir), subdir=subdir_name)
        assert r1 == r2
        with open(r1 + "\\b.dll", "rb") as f:
            assert f.read() == b"BBBBB"
