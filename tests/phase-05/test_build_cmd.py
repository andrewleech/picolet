"""
Integration tests for build_cmd.py — PH05.

These tests invoke build_cmd.run() (or subprocess picolet build) against
the hello-cli fixture in tests/phase-05/fixtures/hello-cli/.

All tests isolate from the real user cache and real network by setting
PICOLET_CACHE_DIR and PICOLET_RUNTIME_SOURCE in the test environment.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_PKG_PARENT = _REPO_ROOT / "packages" / "picolet-cli"
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

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


class TestBuildCmdIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        """Check that the real runtime exists; skip integration tests if absent."""
        if not LINUX_RUNTIME.is_file():
            raise unittest.SkipTest(
                f"linux runtime not present: {LINUX_RUNTIME}; "
                "run build-runtime.sh --target linux-x64 --variant cli first"
            )
        if not FIXTURE_DIR.is_dir():
            raise unittest.SkipTest(f"fixture not found: {FIXTURE_DIR}")

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.fake_release_dir = self.tmp / "fake-release"
        self.cache_dir = self.tmp / "cache"
        self.tag = "runtime-v0.1.0-test"

        # Copy the real runtime into the fake release so the built app actually runs.
        content = LINUX_RUNTIME.read_bytes()
        _make_fake_release(self.fake_release_dir, self.tag, content=content)
        self._release_content = content

        # App working directory: copy fixture to a temp dir to avoid polluting source.
        self.app_dir = self.tmp / "hello-cli"
        shutil.copytree(FIXTURE_DIR, self.app_dir)

        os.environ["PICOLET_RUNTIME_TAG"] = self.tag
        os.environ["PICOLET_RUNTIME_SOURCE"] = _file_url(self.fake_release_dir)
        os.environ["PICOLET_CACHE_DIR"] = str(self.cache_dir)

    def tearDown(self) -> None:
        for key in ("PICOLET_RUNTIME_TAG", "PICOLET_RUNTIME_SOURCE", "PICOLET_CACHE_DIR"):
            os.environ.pop(key, None)
        self._tmpdir.cleanup()

    def _run_build(self, extra_args: list[str] | None = None, cwd: Path | None = None) -> int:
        """Run picolet build via subprocess; return exit code."""
        import subprocess
        cmd = [
            sys.executable,
            str(_REPO_ROOT / "packages" / "picolet-cli" / "picolet" / "__main__.py"),
            "build",
            "--target", "linux-x64",
        ] + (extra_args or [])
        env = {**os.environ}
        result = subprocess.run(
            cmd,
            cwd=str(cwd or self.app_dir),
            env=env,
            capture_output=True,
        )
        return result.returncode

    # -------------------------------------------------------------------------
    # test_build_with_file_url_source
    # -------------------------------------------------------------------------
    def test_build_with_file_url_source(self) -> None:
        """picolet build with a file:// PICOLET_RUNTIME_SOURCE downloads and builds."""
        rc = self._run_build()
        self.assertEqual(rc, 0, "picolet build failed")
        binary = self.app_dir / "target" / "linux-x64" / "hello-cli"
        self.assertTrue(binary.is_file(), f"output binary not found: {binary}")
        binary.chmod(0o755)

        import subprocess
        out = subprocess.check_output([str(binary)], text=True).strip()
        self.assertEqual(out, "Hello from hello-cli")

    # -------------------------------------------------------------------------
    # test_build_cache_hit
    # -------------------------------------------------------------------------
    def test_build_cache_hit(self) -> None:
        """Second build uses cache; urlopen is not called."""
        import subprocess
        from picolet import runtime_resolver as rr

        # First build to populate cache.
        rc = self._run_build()
        self.assertEqual(rc, 0)

        # Remove source to simulate network absence.
        artifact = "picolet-runtime-linux-x64-cli"
        (self.fake_release_dir / self.tag / artifact).unlink()

        # Second build must succeed from cache.
        rc2 = self._run_build()
        self.assertEqual(rc2, 0, "second build (cache hit) failed")

    # -------------------------------------------------------------------------
    # test_build_from_source_invokes_script
    # -------------------------------------------------------------------------
    def test_build_from_source_invokes_script(self) -> None:
        """--from-source calls _build_from_source which checks Docker + invokes script."""
        from picolet import runtime_resolver as rr
        from picolet.runtime_resolver import ResolvedRuntime

        # Monkeypatch _check_docker to return True and _build_from_source
        # to capture the call without actually running Docker.
        calls: list[tuple] = []

        def fake_build_from_source(target, variant, verbose):
            calls.append((target, variant))
            # Return the real runtime so the rest of build_cmd succeeds.
            return LINUX_RUNTIME.resolve()

        with mock.patch.object(rr, "_build_from_source", side_effect=fake_build_from_source):
            from picolet import build_cmd

            # Build a minimal args namespace.
            class Args:
                target = "linux-x64"
                verbose = False
                keep_staging = False
                runtime = None
                from_source = True
                no_cache = False

            orig_cwd = os.getcwd()
            try:
                os.chdir(str(self.app_dir))
                build_cmd.run(Args())
            except SystemExit as e:
                if e.code != 0:
                    self.fail(f"build_cmd.run exited with code {e.code}")
            finally:
                os.chdir(orig_cwd)

        self.assertEqual(len(calls), 1, "build_from_source not called")
        target, variant = calls[0]
        self.assertEqual(target, "linux-x64")
        self.assertEqual(variant, "cli")

    # -------------------------------------------------------------------------
    # test_build_explicit_runtime
    # -------------------------------------------------------------------------
    def test_build_explicit_runtime(self) -> None:
        """--runtime /path/to/binary uses that binary directly."""
        rc = self._run_build(extra_args=["--runtime", str(LINUX_RUNTIME)])
        self.assertEqual(rc, 0, "build with --runtime failed")

        binary = self.app_dir / "target" / "linux-x64" / "hello-cli"
        self.assertTrue(binary.is_file())


if __name__ == "__main__":
    unittest.main()
