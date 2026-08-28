"""
build_cmd integration tests — PH05.

NOTE: The full build pipeline requires mpremote (from the project .venv) to
be on sys.executable's path. When pytest runs under the system Python
(/usr/bin/python) rather than the project .venv, mpremote is unavailable
and the subprocess-based integration tests fail at the romfs build step.

The integration gate tests (cache hit, full build, --from-source, --runtime,
--no-cache) are therefore in tests/phase-05/run.sh which uses 'uv run' to
invoke picolet with the correct Python and dependencies.

This file contains only the tests that can run correctly under pytest without
the full build pipeline:
  - Argument parsing: --from-source, --no-cache, --runtime parsed correctly.
  - resolve_runtime integration: build_cmd passes the right args to the resolver.
"""

from __future__ import annotations

import hashlib
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_PKG_PARENT = _REPO_ROOT / "packages" / "picolet-cli"
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from picolet.cli import build_cmd
from picolet.cli.runtime_resolver import ResolvedRuntime

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hello-cli"
LINUX_RUNTIME = _REPO_ROOT / "packages" / "picolet-runtime" / "build" / "picolet-runtime-linux-x64-cli"

FAKE_BINARY = b"FAKE_RUNTIME_BINARY_CONTENT"


def _file_url(path: Path) -> str:
    return path.as_uri()


def _make_fake_release(base_dir: Path, tag: str, content: bytes = FAKE_BINARY) -> None:
    artifact = "picolet-runtime-linux-x64-cli"
    release_dir = base_dir / tag
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / artifact).write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()
    (release_dir / f"{artifact}.sha256").write_text(sha256 + "\n")
    (release_dir / f"{artifact}.cdx.json").write_text("{}\n")


class TestBuildCmdArgParsing(unittest.TestCase):
    """Verify the --from-source, --no-cache, and --runtime flags are wired correctly."""

    def _make_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        build_cmd.add_parser(subparsers)
        return parser

    def test_from_source_flag_parsed(self) -> None:
        """--from-source sets args.from_source = True."""
        parser = self._make_parser()
        args = parser.parse_args(["build", "--from-source"])
        self.assertTrue(args.from_source)

    def test_no_cache_flag_parsed(self) -> None:
        """--no-cache sets args.no_cache = True."""
        parser = self._make_parser()
        args = parser.parse_args(["build", "--no-cache"])
        self.assertTrue(args.no_cache)

    def test_runtime_flag_parsed(self) -> None:
        """--runtime /path sets args.runtime = '/path'."""
        parser = self._make_parser()
        args = parser.parse_args(["build", "--runtime", "/some/path"])
        self.assertEqual(args.runtime, "/some/path")

    def test_defaults_are_false(self) -> None:
        """Without flags, from_source and no_cache default to False."""
        parser = self._make_parser()
        args = parser.parse_args(["build"])
        self.assertFalse(args.from_source)
        self.assertFalse(args.no_cache)
        self.assertIsNone(args.runtime)


class TestBuildCmdResolverIntegration(unittest.TestCase):
    """Verify build_cmd.run() passes the correct arguments to resolve_runtime.

    These tests patch resolve_runtime to raise immediately after capturing args,
    avoiding the need to mock the entire downstream build pipeline.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not FIXTURE_DIR.is_dir():
            raise unittest.SkipTest(f"fixture not found: {FIXTURE_DIR}")

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_args(self, **kwargs):
        """Build a minimal args namespace for build_cmd.run()."""
        class Args:
            target = "linux-x64"
            verbose = False
            keep_staging = False
            runtime = None
            from_source = False
            no_cache = False
            allow_unverified_runtime = False
            no_sbom = True
        for k, v in kwargs.items():
            setattr(Args, k, v)
        return Args()

    def _capture_resolve_args(self, args):
        """Run build_cmd.run() with a resolve_runtime that captures its call args.

        resolve_runtime raises RuntimeNotFound after capturing, which causes
        build_cmd.run() to call sys.exit(1). We catch SystemExit.
        Returns the captured keyword arguments dict.
        """
        from picolet.cli import runtime_resolver as rr

        captured = {}

        class _Captured(Exception):
            pass

        def fake_resolve(target, variant, **kwargs):
            captured.update({
                "target": target,
                "variant": variant,
                **kwargs,
            })
            raise rr.RuntimeNotFound("captured")

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(FIXTURE_DIR))
            # build_cmd imports resolve_runtime directly; patch it in build_cmd's namespace.
            with mock.patch.object(build_cmd, "resolve_runtime", side_effect=fake_resolve):
                try:
                    build_cmd.run(args)
                except SystemExit:
                    pass  # expected: resolve_runtime raised RuntimeNotFound → sys.exit(1)
        finally:
            os.chdir(orig_cwd)

        return captured

    def test_resolve_runtime_called_with_explicit_path(self) -> None:
        """--runtime arg is forwarded to resolve_runtime as explicit_path."""
        fake_runtime = self.tmp / "my-runtime"
        fake_runtime.write_bytes(b"FAKE")

        args = self._make_args(runtime=str(fake_runtime))
        captured = self._capture_resolve_args(args)

        self.assertIn("explicit_path", captured)
        self.assertEqual(captured["explicit_path"], fake_runtime)

    def test_resolve_runtime_called_with_from_source(self) -> None:
        """--from-source arg is forwarded to resolve_runtime as from_source=True."""
        args = self._make_args(from_source=True)
        captured = self._capture_resolve_args(args)

        self.assertTrue(captured.get("from_source"), "from_source not forwarded to resolve_runtime")

    def test_resolve_runtime_called_with_no_cache(self) -> None:
        """--no-cache arg is forwarded to resolve_runtime as no_cache=True."""
        args = self._make_args(no_cache=True)
        captured = self._capture_resolve_args(args)

        self.assertTrue(captured.get("no_cache"), "no_cache not forwarded to resolve_runtime")

    def test_resolve_runtime_no_flags_default_args(self) -> None:
        """Without flags, resolve_runtime is called with from_source=False, no_cache=False, explicit_path=None."""
        args = self._make_args()
        captured = self._capture_resolve_args(args)

        self.assertFalse(captured.get("from_source"))
        self.assertFalse(captured.get("no_cache"))
        self.assertIsNone(captured.get("explicit_path"))


class TestBuildCmdVariantOverride(unittest.TestCase):
    """[build].variant explicit override wins over [ui].renderer-derived variant."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.app_root = Path(self._tmpdir.name)
        (self.app_root / "src").mkdir()
        (self.app_root / "src" / "main.py").write_text("print('hi')\n")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_toml(self, extra: str) -> None:
        (self.app_root / "picolet.toml").write_text(
            "[app]\n"
            'name = "test"\n'
            'version = "0.1.0"\n'
            'entry = "src/main.py"\n'
            + extra
        )

    def _make_args(self):
        class Args:
            target = "linux-x64"
            verbose = False
            keep_staging = False
            runtime = None
            from_source = False
            no_cache = False
            allow_unverified_runtime = False
            no_sbom = True
        return Args()

    def _capture_variant(self, args) -> str | None:
        """Run build_cmd.run() with resolve_runtime patched to capture `variant`.

        Same technique as TestBuildCmdResolverIntegration._capture_resolve_args,
        against a per-test app_root rather than the shared hello-cli fixture.
        """
        from picolet.cli import runtime_resolver as rr

        captured: dict = {}

        def fake_resolve(target, variant, **kwargs):
            captured["variant"] = variant
            raise rr.RuntimeNotFound("captured")

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(self.app_root))
            with mock.patch.object(build_cmd, "resolve_runtime", side_effect=fake_resolve):
                try:
                    build_cmd.run(args)
                except SystemExit:
                    pass
        finally:
            os.chdir(orig_cwd)
        return captured.get("variant")

    def test_explicit_variant_wins(self) -> None:
        """[build].variant = "mcp" is used when [ui] is absent."""
        self._write_toml('\n[build]\nvariant = "mcp"\n')
        self.assertEqual(self._capture_variant(self._make_args()), "mcp")

    def test_no_override_falls_back_to_renderer(self) -> None:
        """Without [build].variant, absent [ui] still resolves to "cli"."""
        self._write_toml("")
        self.assertEqual(self._capture_variant(self._make_args()), "cli")

    def test_explicit_variant_wins_over_explicit_renderer(self) -> None:
        """[build].variant wins even when [ui].renderer is also set."""
        self._write_toml('\n[ui]\nrenderer = "tui"\n\n[build]\nvariant = "mcp"\n')
        self.assertEqual(self._capture_variant(self._make_args()), "mcp")


class TestCopyIncludesExcludeAndCompile(unittest.TestCase):
    """[romfs].exclude pruning, .py -> .mpy compilation, and the collision guard."""

    @classmethod
    def setUpClass(cls) -> None:
        from picolet.cli.runtime_resolver import locate_mpy_cross, RuntimeNotFound
        try:
            cls.mpy_cross = locate_mpy_cross()
        except RuntimeNotFound:
            raise unittest.SkipTest("mpy-cross not available on PATH or in-tree")

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self.app_root = tmp / "app"
        self.app_root.mkdir()
        self.romfs_root = tmp / "romfs"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_py_file_compiled_to_mpy(self) -> None:
        """A .py file under an include dir is compiled to .mpy, not copied verbatim."""
        lib = self.app_root / "lib"
        lib.mkdir()
        (lib / "mod.py").write_text("x = 1\n")

        build_cmd._copy_includes(
            self.app_root, ["lib"], [], self.romfs_root, self.mpy_cross, False
        )

        self.assertFalse((self.romfs_root / "lib" / "mod.py").exists())
        self.assertTrue((self.romfs_root / "lib" / "mod.mpy").exists())

    def test_non_py_file_copied_verbatim(self) -> None:
        """A non-.py asset file is copied unchanged, byte for byte."""
        assets = self.app_root / "assets"
        assets.mkdir()
        (assets / "cert.der").write_bytes(b"\x01\x02\x03")

        build_cmd._copy_includes(
            self.app_root, ["assets"], [], self.romfs_root, self.mpy_cross, False
        )

        copied = self.romfs_root / "assets" / "cert.der"
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), b"\x01\x02\x03")

    def test_exclude_prunes_matching_directory(self) -> None:
        """A directory name matching an exclude pattern is pruned, with its contents."""
        lib = self.app_root / "lib"
        (lib / "tests").mkdir(parents=True)
        (lib / "tests" / "test_mod.py").write_text("assert True\n")
        (lib / "mod.py").write_text("x = 1\n")

        build_cmd._copy_includes(
            self.app_root, ["lib"], ["tests"], self.romfs_root, self.mpy_cross, False
        )

        self.assertFalse((self.romfs_root / "lib" / "tests").exists())
        self.assertTrue((self.romfs_root / "lib" / "mod.mpy").exists())

    def test_exclude_prunes_matching_basename(self) -> None:
        """A file basename matching an exclude pattern is skipped."""
        lib = self.app_root / "lib"
        lib.mkdir()
        (lib / "README.md").write_text("docs\n")
        (lib / "mod.py").write_text("x = 1\n")

        build_cmd._copy_includes(
            self.app_root, ["lib"], ["README.md"], self.romfs_root, self.mpy_cross, False
        )

        self.assertFalse((self.romfs_root / "lib" / "README.md").exists())
        self.assertTrue((self.romfs_root / "lib" / "mod.mpy").exists())

    def test_py_and_mpy_collision_raises(self) -> None:
        """A .py and a pre-existing .mpy resolving to the same romfs path fails loudly."""
        lib = self.app_root / "lib"
        lib.mkdir()
        (lib / "mod.py").write_text("x = 1\n")
        (lib / "mod.mpy").write_bytes(b"STALE_PREBUILT_MPY")

        with self.assertRaises(build_cmd.BuildFailed):
            build_cmd._copy_includes(
                self.app_root, ["lib"], [], self.romfs_root, self.mpy_cross, False
            )

    def test_own_staging_output_never_included(self) -> None:
        """include=["."] doesn't walk back into app_root/target (this
        build's own staging/output tree), which would otherwise be live on
        disk by the time _copy_includes runs after _compile_mpy."""
        (self.app_root / "asset.der").write_bytes(b"\x01")
        stale_staging = self.app_root / "target" / "linux-x64" / ".picolet-build" / "romfs"
        stale_staging.mkdir(parents=True)
        (stale_staging / "main.mpy").write_bytes(b"SHOULD_NOT_BE_RECOPIED")

        build_cmd._copy_includes(
            self.app_root, ["."], [], self.romfs_root, self.mpy_cross, False
        )

        self.assertTrue((self.romfs_root / "asset.der").exists())
        self.assertFalse((self.romfs_root / "target").exists())

    def test_pycache_and_pyc_still_skipped(self) -> None:
        """Pre-existing __pycache__/.pyc skip behaviour is unchanged."""
        lib = self.app_root / "lib"
        cache = lib / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
        (lib / "mod.py").write_text("x = 1\n")

        build_cmd._copy_includes(
            self.app_root, ["lib"], [], self.romfs_root, self.mpy_cross, False
        )

        self.assertFalse((self.romfs_root / "lib" / "__pycache__").exists())
        self.assertTrue((self.romfs_root / "lib" / "mod.mpy").exists())


class TestCompileMpyExclude(unittest.TestCase):
    """_compile_mpy's [romfs].exclude support (entry tree, not just includes)."""

    @classmethod
    def setUpClass(cls) -> None:
        from picolet.cli.runtime_resolver import locate_mpy_cross, RuntimeNotFound
        try:
            cls.mpy_cross = locate_mpy_cross()
        except RuntimeNotFound:
            raise unittest.SkipTest("mpy-cross not available on PATH or in-tree")

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self.app_root = tmp / "app"
        self.app_root.mkdir()
        self.romfs_root = tmp / "romfs"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_excluded_sibling_not_compiled(self) -> None:
        """A test file alongside the entry, matching an exclude, is skipped."""
        (self.app_root / "plugin.py").write_text("print('hi')\n")
        tests_dir = self.app_root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_plugin.py").write_text("assert True\n")

        build_cmd._compile_mpy(
            self.app_root, "plugin.py", self.romfs_root, self.mpy_cross,
            ["tests"], False,
        )

        self.assertFalse((self.romfs_root / "tests").exists())
        self.assertTrue((self.romfs_root / "main.mpy").exists())

    def test_entry_itself_never_excluded(self) -> None:
        """The entry point still compiles to main.mpy even if it would
        otherwise match an (overly broad) exclude pattern."""
        (self.app_root / "plugin.py").write_text("print('hi')\n")

        build_cmd._compile_mpy(
            self.app_root, "plugin.py", self.romfs_root, self.mpy_cross,
            ["*.py"], False,
        )

        self.assertTrue((self.romfs_root / "main.mpy").exists())


class TestRunVersionChecks(unittest.TestCase):
    """[[version_check]] enforcement."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.app_root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_no_entries_is_noop(self) -> None:
        """Absent [[version_check]] does nothing."""
        build_cmd._run_version_checks({}, self.app_root)

    def test_matching_sources_pass(self) -> None:
        """All sources extracting the same string passes silently."""
        (self.app_root / "a.py").write_text('VERSION = "1.2.3"\n')
        (self.app_root / "b.json").write_text('{"version": "1.2.3"}\n')
        data = {
            "version_check": [
                {"path": "a.py", "pattern": r'VERSION = "([^"]+)"'},
                {"path": "b.json", "pattern": r'"version": "([^"]+)"'},
            ]
        }
        build_cmd._run_version_checks(data, self.app_root)

    def test_mismatched_sources_raise(self) -> None:
        """Sources extracting different strings raises BuildFailed."""
        (self.app_root / "a.py").write_text('VERSION = "1.2.3"\n')
        (self.app_root / "b.json").write_text('{"version": "9.9.9"}\n')
        data = {
            "version_check": [
                {"path": "a.py", "pattern": r'VERSION = "([^"]+)"'},
                {"path": "b.json", "pattern": r'"version": "([^"]+)"'},
            ]
        }
        with self.assertRaises(build_cmd.BuildFailed):
            build_cmd._run_version_checks(data, self.app_root)

    def test_pattern_no_match_raises(self) -> None:
        """A pattern that doesn't match its file raises BuildFailed."""
        (self.app_root / "a.py").write_text("no version here\n")
        data = {"version_check": [{"path": "a.py", "pattern": r'VERSION = "([^"]+)"'}]}
        with self.assertRaises(build_cmd.BuildFailed):
            build_cmd._run_version_checks(data, self.app_root)

    def test_pattern_wrong_group_count_raises(self) -> None:
        """A pattern with zero capture groups raises BuildFailed."""
        (self.app_root / "a.py").write_text('VERSION = "1.2.3"\n')
        data = {"version_check": [{"path": "a.py", "pattern": r'VERSION = ".+"'}]}
        with self.assertRaises(build_cmd.BuildFailed):
            build_cmd._run_version_checks(data, self.app_root)

    def test_missing_file_raises(self) -> None:
        """A path that doesn't exist raises BuildFailed."""
        data = {"version_check": [{"path": "does-not-exist.py", "pattern": r"(.+)"}]}
        with self.assertRaises(build_cmd.BuildFailed):
            build_cmd._run_version_checks(data, self.app_root)


if __name__ == "__main__":
    unittest.main()
