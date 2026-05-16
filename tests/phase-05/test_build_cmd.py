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

from picolet import build_cmd
from picolet.runtime_resolver import ResolvedRuntime

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
        from picolet import runtime_resolver as rr

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


if __name__ == "__main__":
    unittest.main()
