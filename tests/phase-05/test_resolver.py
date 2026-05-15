"""
Unit tests for packages/picolet-cli/picolet/runtime_resolver.py — PH05.

Each test isolates the resolver from the real user cache and real network
by setting PICOLET_CACHE_DIR and PICOLET_RUNTIME_SOURCE to temporary directories.
"""

from __future__ import annotations

import hashlib
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

# Ensure picolet package is importable without installation.
_REPO_ROOT = Path(__file__).parent.parent.parent
_PKG_PARENT = _REPO_ROOT / "packages" / "picolet-cli"
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from picolet.runtime_resolver import (
    ResolvedRuntime,
    RuntimeIntegrityError,
    RuntimeNotFound,
    _artifact_name,
    _cache_root,
    _load_config,
    resolve_runtime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_BINARY = b"FAKE_RUNTIME_BINARY_CONTENT"
FAKE_BINARY_SHA256 = hashlib.sha256(FAKE_BINARY).hexdigest()


def _make_fake_release(
    base_dir: Path,
    tag: str = "runtime-v0.1.0-test",
    target: str = "linux-x64",
    variant: str = "cli",
    content: bytes = FAKE_BINARY,
) -> Path:
    """Populate a fake release directory and return the artifact path."""
    artifact = _artifact_name(target, variant)
    release_dir = base_dir / tag
    release_dir.mkdir(parents=True, exist_ok=True)

    bin_path = release_dir / artifact
    bin_path.write_bytes(content)

    sha256_path = release_dir / f"{artifact}.sha256"
    sha256_path.write_text(hashlib.sha256(content).hexdigest() + "\n")

    sbom_path = release_dir / f"{artifact}.cdx.json"
    sbom_path.write_text("{}\n")

    return bin_path


def _file_url(path: Path) -> str:
    """Return a file:// URL for a directory path."""
    return path.as_uri()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestResolverUnit(unittest.TestCase):

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

        self.fake_release_dir = self.tmp / "fake-release"
        self.cache_dir = self.tmp / "cache"
        self.tag = "runtime-v0.1.0-test"

        _make_fake_release(
            self.fake_release_dir,
            tag=self.tag,
            target="linux-x64",
            variant="cli",
        )

        os.environ["PICOLET_RUNTIME_TAG"] = self.tag
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.fake_release_dir)
        os.environ["PICOLET_CACHE_DIR"] = str(self.cache_dir)

    def tearDown(self) -> None:
        for key in ("PICOLET_RUNTIME_TAG", "PICOLET_RUNTIME_SOURCE", "PICOLET_CACHE_DIR"):
            os.environ.pop(key, None)
        self._tmpdir.cleanup()

    # -------------------------------------------------------------------------
    # test_download_and_cache_populate
    # -------------------------------------------------------------------------
    def test_download_and_cache_populate(self) -> None:
        """First call with empty cache: artifact is downloaded and cached."""
        result = resolve_runtime("linux-x64", "cli")
        self.assertIsInstance(result, ResolvedRuntime)

        # Binary must be inside the cache dir.
        self.assertTrue(
            str(result.binary).startswith(str(self.cache_dir)),
            f"binary not in cache: {result.binary}",
        )
        self.assertTrue(result.binary.is_file())

        # Sibling files must also be cached.
        sha256_path = result.binary.parent / f"{result.binary.name}.sha256"
        self.assertTrue(sha256_path.is_file(), ".sha256 not cached")

        sbom_path = result.binary.parent / f"{result.binary.name}.cdx.json"
        self.assertTrue(sbom_path.is_file(), ".cdx.json not cached")
        self.assertIsNotNone(result.sbom)

    # -------------------------------------------------------------------------
    # test_cache_hit_no_redownload
    # -------------------------------------------------------------------------
    def test_cache_hit_no_redownload(self) -> None:
        """Second call returns cached artifact without hitting the network."""
        # Populate cache.
        resolve_runtime("linux-x64", "cli")

        # Simulate network outage by pointing source at a non-existent dir.
        artifact = _artifact_name("linux-x64", "cli")
        (self.fake_release_dir / self.tag / artifact).unlink()

        # Should still succeed from cache.
        result = resolve_runtime("linux-x64", "cli")
        self.assertTrue(result.binary.is_file())

    # -------------------------------------------------------------------------
    # test_sha256_mismatch_triggers_redownload
    # -------------------------------------------------------------------------
    def test_sha256_mismatch_triggers_redownload(self) -> None:
        """Corrupted cache artifact triggers re-download and repair."""
        # Populate cache first.
        resolve_runtime("linux-x64", "cli")

        # Corrupt the cached binary.
        artifact = _artifact_name("linux-x64", "cli")
        cached_bin = self.cache_dir / "runtime" / self.tag / artifact
        cached_bin.write_bytes(b"CORRUPTED")

        # Resolver should re-download and succeed.
        result = resolve_runtime("linux-x64", "cli")
        self.assertTrue(result.binary.is_file())
        # Content must now be the original fake binary.
        self.assertEqual(result.binary.read_bytes(), FAKE_BINARY)

    # -------------------------------------------------------------------------
    # test_tampered_cache_no_network_raises
    # -------------------------------------------------------------------------
    def test_tampered_cache_no_network_raises(self) -> None:
        """Corrupted cache + no network raises RuntimeNotFound."""
        resolve_runtime("linux-x64", "cli")

        artifact = _artifact_name("linux-x64", "cli")
        cached_bin = self.cache_dir / "runtime" / self.tag / artifact
        cached_bin.write_bytes(b"CORRUPTED")

        # Point source at a non-existent directory to simulate network absence.
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        # Patch _intree_fallback to return None so in-tree binary doesn't save us.
        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_intree_fallback", return_value=None):
            with self.assertRaises(RuntimeNotFound):
                resolve_runtime("linux-x64", "cli")

    # -------------------------------------------------------------------------
    # test_explicit_runtime_path
    # -------------------------------------------------------------------------
    def test_explicit_runtime_path(self) -> None:
        """explicit_path bypasses all resolution; returns that path directly."""
        explicit = self.tmp / "my-runtime"
        explicit.write_bytes(b"EXPLICIT")

        result = resolve_runtime("linux-x64", "cli", explicit_path=explicit)
        self.assertEqual(result.binary.resolve(), explicit.resolve())
        self.assertIsNone(result.sbom)

    # -------------------------------------------------------------------------
    # test_explicit_runtime_path_missing
    # -------------------------------------------------------------------------
    def test_explicit_runtime_path_missing(self) -> None:
        """explicit_path pointing to non-existent file raises RuntimeNotFound."""
        explicit = self.tmp / "does-not-exist"
        with self.assertRaises(RuntimeNotFound) as ctx:
            resolve_runtime("linux-x64", "cli", explicit_path=explicit)
        self.assertIn("not found", str(ctx.exception).lower())

    # -------------------------------------------------------------------------
    # test_no_cache_flag_downloads_fresh
    # -------------------------------------------------------------------------
    def test_no_cache_flag_downloads_fresh(self) -> None:
        """no_cache=True downloads even when cache is populated."""
        import unittest.mock as mock

        # Populate cache.
        resolve_runtime("linux-x64", "cli")

        # Verify cache is populated.
        artifact = _artifact_name("linux-x64", "cli")
        cached_bin = self.cache_dir / "runtime" / self.tag / artifact
        self.assertTrue(cached_bin.is_file())

        # Count urlopen calls with no_cache=True.
        import urllib.request as _urq
        call_count = 0
        original_urlopen = _urq.urlopen

        def counting_urlopen(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            return original_urlopen(url, *a, **kw)

        with mock.patch("urllib.request.urlopen", side_effect=counting_urlopen):
            result = resolve_runtime("linux-x64", "cli", no_cache=True)

        self.assertGreater(call_count, 0, "urlopen was not called with no_cache=True")
        self.assertTrue(result.binary.is_file())

    # -------------------------------------------------------------------------
    # test_offline_with_empty_cache_raises
    # -------------------------------------------------------------------------
    def test_offline_with_empty_cache_raises(self) -> None:
        """Empty cache + bad URL + no in-tree fallback → structured RuntimeNotFound."""
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        # Patch _intree_fallback to return None so in-tree binary doesn't save us.
        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_intree_fallback", return_value=None):
            with self.assertRaises(RuntimeNotFound) as ctx:
                resolve_runtime("linux-x64", "cli")

        msg = str(ctx.exception)
        self.assertIn("Tried:", msg)
        self.assertIn("cache:", msg)
        self.assertIn("download:", msg)
        self.assertIn("fallback:", msg)
        self.assertIn("1. Connect", msg)
        self.assertIn("2. Run `picolet build --from-source`", msg)
        self.assertIn("3. Run `picolet build --runtime", msg)

    # -------------------------------------------------------------------------
    # test_intree_fallback
    # -------------------------------------------------------------------------
    def test_intree_fallback(self) -> None:
        """Empty cache + bad URL + existing in-tree binary → in-tree fallback used."""
        import unittest.mock as mock

        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        # Create a fake in-tree artifact.
        artifact = _artifact_name("linux-x64", "cli")
        intree_dir = self.tmp / "fake-repo" / "packages" / "picolet-runtime" / "build"
        intree_dir.mkdir(parents=True, exist_ok=True)
        intree_bin = intree_dir / artifact
        intree_bin.write_bytes(b"INTREE")

        # Patch _find_repo_root to return our fake repo root.
        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_find_repo_root", return_value=self.tmp / "fake-repo"):
            import io
            with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                result = resolve_runtime("linux-x64", "cli")
                stderr_out = mock_stderr.getvalue()

        self.assertTrue(result.binary.is_file())
        self.assertEqual(result.binary.read_bytes(), b"INTREE")
        self.assertIn("fallback", stderr_out.lower())

    # -------------------------------------------------------------------------
    # test_config_reads_runtime_tag_sidecar
    # -------------------------------------------------------------------------
    def test_config_reads_runtime_tag_sidecar(self) -> None:
        """_load_config() reads tag from RUNTIME_TAG sidecar when no env/toml override."""
        # Remove env override so sidecar is consulted.
        del os.environ["PICOLET_RUNTIME_TAG"]

        import unittest.mock as mock
        from picolet import runtime_resolver as rr

        with mock.patch.object(rr, "_read_runtime_tag_sidecar", return_value="runtime-v0.0.1"):
            cfg = _load_config()

        self.assertEqual(cfg.tag, "runtime-v0.0.1")

    # -------------------------------------------------------------------------
    # test_env_var_overrides_sidecar
    # -------------------------------------------------------------------------
    def test_env_var_overrides_sidecar(self) -> None:
        """PICOLET_RUNTIME_TAG env var overrides the sidecar file."""
        os.environ["PICOLET_RUNTIME_TAG"] = "runtime-v9.9.9"
        cfg = _load_config()
        self.assertEqual(cfg.tag, "runtime-v9.9.9")

    # -------------------------------------------------------------------------
    # test_cache_root_linux
    # -------------------------------------------------------------------------
    def test_cache_root_linux(self) -> None:
        """On Linux with no overrides, cache root is ~/.cache/picolet."""
        if sys.platform != "linux":
            self.skipTest("Linux-only test")

        del os.environ["PICOLET_CACHE_DIR"]
        env_backup = os.environ.pop("XDG_CACHE_HOME", None)
        try:
            root = _cache_root()
            self.assertEqual(root, Path.home() / ".cache" / "picolet")
        finally:
            if env_backup is not None:
                os.environ["XDG_CACHE_HOME"] = env_backup
            os.environ["PICOLET_CACHE_DIR"] = str(self.cache_dir)

    # -------------------------------------------------------------------------
    # test_cache_root_xdg
    # -------------------------------------------------------------------------
    def test_cache_root_xdg(self) -> None:
        """XDG_CACHE_HOME sets cache root."""
        del os.environ["PICOLET_CACHE_DIR"]
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "xdg")
        try:
            root = _cache_root()
            self.assertEqual(root, self.tmp / "xdg" / "picolet")
        finally:
            del os.environ["XDG_CACHE_HOME"]
            os.environ["PICOLET_CACHE_DIR"] = str(self.cache_dir)


class TestValidatorRuntimeSection(unittest.TestCase):
    """Test the [runtime] table in picolet.toml validator."""

    def _make_toml(self, tmpdir: Path, extra: str = "") -> Path:
        toml = tmpdir / "picolet.toml"
        toml.write_text(
            "[app]\n"
            'name = "test"\n'
            'version = "0.1.0"\n'
            'entry = "src/main.py"\n'
            + extra
        )
        return toml

    def test_runtime_section_valid(self) -> None:
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                '\n[runtime]\nsource = "file:///tmp/releases"\ntag = "runtime-v0.1.0"\n',
            )
            errors = validate_toml(toml)
        self.assertEqual(errors, [])

    def test_runtime_section_wrong_type(self) -> None:
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                "\n[runtime]\ntag = 123\n",
            )
            errors = validate_toml(toml)
        self.assertEqual(len(errors), 1)
        self.assertIn("tag", str(errors[0]))

    def test_unknown_section_rejected(self) -> None:
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(Path(d), "\n[notavalidsection]\nfoo = 1\n")
            errors = validate_toml(toml)
        self.assertTrue(any("notavalidsection" in str(e) for e in errors))


if __name__ == "__main__":
    unittest.main()
