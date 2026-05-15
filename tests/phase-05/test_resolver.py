"""
Unit tests for packages/picolet-cli/picolet/runtime_resolver.py — PH05.

Each test isolates the resolver from the real user cache and real network
by setting PICOLET_CACHE_DIR and PICOLET_RUNTIME_SOURCE to temporary directories.
"""

from __future__ import annotations

import hashlib
import io
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
    include_sha256: bool = True,
    include_cdx: bool = True,
) -> Path:
    """Populate a fake release directory and return the artifact path."""
    artifact = _artifact_name(target, variant)
    release_dir = base_dir / tag
    release_dir.mkdir(parents=True, exist_ok=True)

    bin_path = release_dir / artifact
    bin_path.write_bytes(content)

    if include_sha256:
        sha256_path = release_dir / f"{artifact}.sha256"
        sha256_path.write_text(hashlib.sha256(content).hexdigest() + "\n")

    if include_cdx:
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
        """First call with empty cache: artifact + .sha256 + .cdx.json all cached."""
        result = resolve_runtime("linux-x64", "cli")
        self.assertIsInstance(result, ResolvedRuntime)

        # Binary must be inside the cache dir.
        self.assertTrue(
            str(result.binary).startswith(str(self.cache_dir)),
            f"binary not in cache: {result.binary}",
        )
        self.assertTrue(result.binary.is_file())
        self.assertEqual(result.binary.read_bytes(), FAKE_BINARY)

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

        # Simulate network outage by removing the source binary.
        artifact = _artifact_name("linux-x64", "cli")
        (self.fake_release_dir / self.tag / artifact).unlink()

        # Should still succeed from cache.
        result = resolve_runtime("linux-x64", "cli")
        self.assertTrue(result.binary.is_file())
        self.assertEqual(result.binary.read_bytes(), FAKE_BINARY)

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
    # test_corrupt_download_sha256_raises
    # -------------------------------------------------------------------------
    def test_corrupt_download_sha256_raises(self) -> None:
        """Tampered .sha256 in the source (mismatches actual content) causes download failure."""
        # Create a release where the .sha256 sidecar is wrong.
        tampered_dir = self.tmp / "tampered-release"
        artifact = _artifact_name("linux-x64", "cli")
        release_dir = tampered_dir / self.tag
        release_dir.mkdir(parents=True)
        (release_dir / artifact).write_bytes(FAKE_BINARY)
        # Write a deliberately wrong sha256.
        (release_dir / f"{artifact}.sha256").write_text("0" * 64 + "\n")
        (release_dir / f"{artifact}.cdx.json").write_text("{}\n")

        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(tampered_dir)

        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_intree_fallback", return_value=None):
            with self.assertRaises(RuntimeNotFound) as ctx:
                resolve_runtime("linux-x64", "cli")

        # The error must propagate as a download failure, not a silent pass.
        msg = str(ctx.exception)
        self.assertIn("runtime artifact not available", msg)

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
        # Populate cache first.
        resolve_runtime("linux-x64", "cli")

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
    # test_no_cache_disables_cache_writes
    # NOTE: This test documents a known bug in the current implementation.
    # The spec (phase file Gate 7) requires the cache directory to remain
    # empty after a --no-cache build. The current _download() always writes
    # to cfg.cache_root regardless of no_cache. This test is marked expected
    # failure until the developer fixes _download() to accept a no_cache flag.
    # See [PH05] Caveat commit for details.
    # -------------------------------------------------------------------------
    @unittest.expectedFailure
    def test_no_cache_disables_cache_writes(self) -> None:
        """--no-cache (no_cache=True) must not populate the cache directory."""
        result = resolve_runtime("linux-x64", "cli", no_cache=True)
        self.assertTrue(result.binary.is_file())

        # Cache directory should be empty (or not exist) after --no-cache.
        cache_runtime_dir = self.cache_dir / "runtime"
        if cache_runtime_dir.exists():
            cached_files = list(cache_runtime_dir.rglob("*"))
            self.assertEqual(
                cached_files, [],
                f"--no-cache wrote {len(cached_files)} file(s) to cache: "
                + ", ".join(str(f) for f in cached_files),
            )

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
    # test_offline_no_cache_hard_errors_no_fallback
    # -------------------------------------------------------------------------
    def test_offline_no_cache_hard_errors_no_fallback(self) -> None:
        """--no-cache + network unavailable: hard error; no in-tree fallback attempted."""
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        from picolet import runtime_resolver as rr
        # Patch _intree_fallback to ensure it is NOT called with no_cache=True.
        with mock.patch.object(rr, "_intree_fallback") as mock_fallback:
            with self.assertRaises(RuntimeNotFound):
                resolve_runtime("linux-x64", "cli", no_cache=True)
            mock_fallback.assert_not_called()

    # -------------------------------------------------------------------------
    # test_intree_fallback
    # -------------------------------------------------------------------------
    def test_intree_fallback(self) -> None:
        """Empty cache + bad URL + existing in-tree binary → in-tree fallback used."""
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
            with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                result = resolve_runtime("linux-x64", "cli")
                stderr_out = mock_stderr.getvalue()

        self.assertTrue(result.binary.is_file())
        self.assertEqual(result.binary.read_bytes(), b"INTREE")
        self.assertIn("fallback", stderr_out.lower())

    # -------------------------------------------------------------------------
    # test_intree_fallback_warning_message
    # -------------------------------------------------------------------------
    def test_intree_fallback_warning_message(self) -> None:
        """In-tree fallback emits a warning to stderr."""
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        artifact = _artifact_name("linux-x64", "cli")
        intree_dir = self.tmp / "fake-repo" / "packages" / "picolet-runtime" / "build"
        intree_dir.mkdir(parents=True, exist_ok=True)
        (intree_dir / artifact).write_bytes(b"INTREE")

        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_find_repo_root", return_value=self.tmp / "fake-repo"):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                resolve_runtime("linux-x64", "cli")
                stderr_out = mock_stderr.getvalue()

        self.assertIn("warning", stderr_out.lower())

    # -------------------------------------------------------------------------
    # test_config_reads_runtime_tag_sidecar
    # -------------------------------------------------------------------------
    def test_config_reads_runtime_tag_sidecar(self) -> None:
        """_load_config() reads tag from RUNTIME_TAG sidecar when no env/toml override."""
        # Remove env override so sidecar is consulted.
        del os.environ["PICOLET_RUNTIME_TAG"]

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
    # test_picolet_runtime_source_env_override
    # -------------------------------------------------------------------------
    def test_picolet_runtime_source_env_override(self) -> None:
        """PICOLET_RUNTIME_SOURCE env var overrides the default base URL."""
        custom_url = "file:///tmp/custom-source"
        os.environ["PICOLET_RUNTIME_SOURCE"] = custom_url
        cfg = _load_config()
        # The resolver strips trailing slashes.
        self.assertEqual(cfg.base_url, custom_url.rstrip("/"))

    # -------------------------------------------------------------------------
    # test_runtime_table_source_in_config
    # -------------------------------------------------------------------------
    def test_runtime_table_source_in_config(self) -> None:
        """[runtime] source in picolet.toml config dict is read by _load_config()."""
        del os.environ["PICOLET_RUNTIME_SOURCE"]
        toml_data = {
            "runtime": {
                "source": "file:///tmp/toml-source",
                "tag": "runtime-v2.0.0",
            }
        }
        cfg = _load_config(config=toml_data)
        self.assertEqual(cfg.base_url, "file:///tmp/toml-source")

    # -------------------------------------------------------------------------
    # test_runtime_table_tag_in_config
    # -------------------------------------------------------------------------
    def test_runtime_table_tag_in_config(self) -> None:
        """[runtime] tag in picolet.toml config dict overrides sidecar."""
        del os.environ["PICOLET_RUNTIME_TAG"]
        toml_data = {
            "runtime": {
                "tag": "runtime-v2.0.0",
            }
        }
        cfg = _load_config(config=toml_data)
        self.assertEqual(cfg.tag, "runtime-v2.0.0")

    # -------------------------------------------------------------------------
    # test_env_var_overrides_toml_table
    # -------------------------------------------------------------------------
    def test_env_var_overrides_toml_table(self) -> None:
        """PICOLET_RUNTIME_TAG env var takes precedence over [runtime] tag in picolet.toml."""
        os.environ["PICOLET_RUNTIME_TAG"] = "runtime-v9.9.9"
        toml_data = {"runtime": {"tag": "runtime-v1.0.0"}}
        cfg = _load_config(config=toml_data)
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
        """XDG_CACHE_HOME sets cache root to XDG_CACHE_HOME/picolet."""
        del os.environ["PICOLET_CACHE_DIR"]
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "xdg")
        try:
            root = _cache_root()
            self.assertEqual(root, self.tmp / "xdg" / "picolet")
        finally:
            del os.environ["XDG_CACHE_HOME"]
            os.environ["PICOLET_CACHE_DIR"] = str(self.cache_dir)

    # -------------------------------------------------------------------------
    # test_cache_root_picolet_cache_dir_override
    # -------------------------------------------------------------------------
    def test_cache_root_picolet_cache_dir_override(self) -> None:
        """PICOLET_CACHE_DIR env var sets cache root and is used by the resolver."""
        custom_cache = self.tmp / "custom-cache"
        os.environ["PICOLET_CACHE_DIR"] = str(custom_cache)

        resolve_runtime("linux-x64", "cli")

        artifact = _artifact_name("linux-x64", "cli")
        expected = custom_cache / "runtime" / self.tag / artifact
        self.assertTrue(expected.is_file(), f"binary not in custom cache: {expected}")

    # -------------------------------------------------------------------------
    # test_artifact_name_linux
    # -------------------------------------------------------------------------
    def test_artifact_name_linux(self) -> None:
        """linux-x64 target produces artifact name without .exe."""
        name = _artifact_name("linux-x64", "cli")
        self.assertEqual(name, "picolet-runtime-linux-x64-cli")
        self.assertFalse(name.endswith(".exe"))

    # -------------------------------------------------------------------------
    # test_artifact_name_windows
    # -------------------------------------------------------------------------
    def test_artifact_name_windows(self) -> None:
        """windows-x64 target produces artifact name with .exe suffix."""
        name = _artifact_name("windows-x64", "cli")
        self.assertEqual(name, "picolet-runtime-windows-x64-cli.exe")

    # -------------------------------------------------------------------------
    # test_cross_target_windows_from_same_release
    # -------------------------------------------------------------------------
    def test_cross_target_windows_from_same_release(self) -> None:
        """Resolver selects the correct artifact for windows-x64 from the same release tree."""
        # Add windows artifact to existing fake release.
        win_artifact = _artifact_name("windows-x64", "cli")
        release_dir = self.fake_release_dir / self.tag
        win_content = b"FAKE_WIN_BINARY"
        (release_dir / win_artifact).write_bytes(win_content)
        (release_dir / f"{win_artifact}.sha256").write_text(
            hashlib.sha256(win_content).hexdigest() + "\n"
        )
        (release_dir / f"{win_artifact}.cdx.json").write_text("{}\n")

        result = resolve_runtime("windows-x64", "cli")
        self.assertTrue(result.binary.is_file())
        self.assertIn("windows-x64", result.binary.name)
        self.assertTrue(result.binary.name.endswith(".exe"))
        self.assertEqual(result.binary.read_bytes(), win_content)

    # -------------------------------------------------------------------------
    # test_partial_tmp_file_cleaned_on_download_failure
    # -------------------------------------------------------------------------
    def test_partial_tmp_file_cleaned_on_download_failure(self) -> None:
        """A .tmp file left by a failed download is removed; no partial artifact in cache."""
        # Point at nonexistent source to force download failure.
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.tmp / "nonexistent")

        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_intree_fallback", return_value=None):
            with self.assertRaises(RuntimeNotFound):
                resolve_runtime("linux-x64", "cli")

        # No .tmp files should remain in the cache.
        artifact = _artifact_name("linux-x64", "cli")
        tag_dir = self.cache_dir / "runtime" / self.tag
        if tag_dir.exists():
            tmp_files = list(tag_dir.glob(f".{artifact}*.tmp"))
            self.assertEqual(tmp_files, [], f"stale .tmp files found: {tmp_files}")

    # -------------------------------------------------------------------------
    # test_from_source_docker_absent_raises_clear_error
    # -------------------------------------------------------------------------
    def test_from_source_docker_absent_raises_clear_error(self) -> None:
        """from_source=True with Docker absent raises RuntimeNotFound with clear message."""
        from picolet import runtime_resolver as rr
        with mock.patch.object(rr, "_check_docker", return_value=False):
            with self.assertRaises(RuntimeNotFound) as ctx:
                resolve_runtime("linux-x64", "cli", from_source=True)
        msg = str(ctx.exception)
        self.assertIn("docker", msg.lower())
        self.assertIn("required", msg.lower())

    # -------------------------------------------------------------------------
    # test_from_source_invokes_build_script
    # -------------------------------------------------------------------------
    def test_from_source_invokes_build_script(self) -> None:
        """from_source=True with Docker available calls _build_from_source."""
        from picolet import runtime_resolver as rr

        fake_built = self.tmp / "fake-built-runtime"
        fake_built.write_bytes(b"BUILT")

        def fake_build(target, variant, verbose):
            return fake_built

        with mock.patch.object(rr, "_check_docker", return_value=True):
            with mock.patch.object(rr, "_build_from_source", side_effect=fake_build) as mock_build:
                result = resolve_runtime("linux-x64", "cli", from_source=True)

        mock_build.assert_called_once()
        call_args = mock_build.call_args
        self.assertEqual(call_args[0][0], "linux-x64")  # target
        self.assertEqual(call_args[0][1], "cli")        # variant
        self.assertEqual(result.binary, fake_built)

    # -------------------------------------------------------------------------
    # test_sbom_absent_from_release_does_not_fail
    # -------------------------------------------------------------------------
    def test_sbom_absent_from_release_does_not_fail(self) -> None:
        """Missing .cdx.json at the source is treated as best-effort; download succeeds."""
        no_sbom_dir = self.tmp / "no-sbom-release"
        _make_fake_release(no_sbom_dir, tag=self.tag, include_cdx=False)
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(no_sbom_dir)

        result = resolve_runtime("linux-x64", "cli")
        self.assertTrue(result.binary.is_file())
        self.assertIsNone(result.sbom)


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

    def test_runtime_section_absent_is_valid(self) -> None:
        """picolet.toml without [runtime] is valid (table is optional)."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(Path(d))
            errors = validate_toml(toml)
        self.assertEqual(errors, [])

    def test_runtime_section_valid(self) -> None:
        """[runtime] with source and tag passes validation."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                '\n[runtime]\nsource = "file:///tmp/releases"\ntag = "runtime-v0.1.0"\n',
            )
            errors = validate_toml(toml)
        self.assertEqual(errors, [])

    def test_runtime_section_source_only_valid(self) -> None:
        """[runtime] with only source (no tag) passes validation."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                '\n[runtime]\nsource = "https://example.com/releases"\n',
            )
            errors = validate_toml(toml)
        self.assertEqual(errors, [])

    def test_runtime_section_tag_only_valid(self) -> None:
        """[runtime] with only tag (no source) passes validation."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                '\n[runtime]\ntag = "runtime-v0.1.0"\n',
            )
            errors = validate_toml(toml)
        self.assertEqual(errors, [])

    def test_runtime_section_wrong_type(self) -> None:
        """[runtime] tag with wrong type (integer) yields a validation error."""
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

    def test_runtime_section_source_wrong_type(self) -> None:
        """[runtime] source with wrong type (integer) yields a validation error."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                "\n[runtime]\nsource = 99\n",
            )
            errors = validate_toml(toml)
        self.assertEqual(len(errors), 1)
        self.assertIn("source", str(errors[0]))

    def test_runtime_both_source_and_tag_wrong_type(self) -> None:
        """[runtime] with both source and tag as wrong types yields two errors."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(
                Path(d),
                "\n[runtime]\nsource = 1\ntag = 2\n",
            )
            errors = validate_toml(toml)
        self.assertEqual(len(errors), 2)
        keys = {e.key for e in errors}
        self.assertIn("source", keys)
        self.assertIn("tag", keys)

    def test_unknown_section_rejected(self) -> None:
        """Top-level section not in the allowed list is rejected."""
        import tempfile
        from picolet.validator import validate_toml

        with tempfile.TemporaryDirectory() as d:
            toml = self._make_toml(Path(d), "\n[notavalidsection]\nfoo = 1\n")
            errors = validate_toml(toml)
        self.assertTrue(any("notavalidsection" in str(e) for e in errors))


if __name__ == "__main__":
    unittest.main()
