"""
PH17 unit tests — picolet.testing.AppHarness.

Covers:
  - _autodetect_browser routing from binary name + platform.
  - AppHarness constructor: PICOLET_TEST_MODE=1 is always injected into env.
  - AppHarness constructor: _running_proc / _port shortcut sets _owns_proc=False.
  - _wait_for_port parses 'picolet:test-port=<N>' correctly from stderr.
  - _wait_for_port returns None when no port line appears (timeout path).
  - start() raises RuntimeError when the process exits without announcing a port.
  - stop() terminates the process and returns its exit code.
  - stop() is a no-op (returns 0) when called without a running process.
  - browser='lvgl' sets self.page to None after start() (no inspector attach).
  - tap() raises NotImplementedError for non-lvgl browsers.
  - key() raises NotImplementedError for non-lvgl browsers.
  - xvfb-run prepended in _spawn when DISPLAY unset and xvfb-run present.
  - _spawn raises RuntimeError when DISPLAY unset and xvfb-run absent.
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import importlib.util

_REPO_ROOT = Path(__file__).parent.parent.parent
_TESTING_ROOT = _REPO_ROOT / "packages" / "picolet-testing" / "picolet" / "testing"


def _load_module(name: str, path: Path):
    """Load a module directly from a file path without disturbing sys.modules['picolet']."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_harness_mod = _load_module("_harness", _TESTING_ROOT / "_harness.py")
AppHarness = _harness_mod.AppHarness
_autodetect_browser = _harness_mod._autodetect_browser


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _autodetect_browser
# ---------------------------------------------------------------------------

class TestAutodetectBrowser(unittest.TestCase):

    def test_lvgl_binary_returns_lvgl(self):
        self.assertEqual(
            _autodetect_browser("picolet-runtime-linux-x64-lvgl", "linux"),
            "lvgl",
        )

    def test_webview_on_linux_returns_webkit(self):
        self.assertEqual(
            _autodetect_browser("picolet-runtime-linux-x64-webview", "linux"),
            "webkit",
        )

    def test_webview_on_win32_returns_chromium(self):
        self.assertEqual(
            _autodetect_browser("picolet-runtime-windows-x64-webview.exe", "win32"),
            "chromium",
        )

    def test_cli_binary_on_linux_defaults_to_webkit(self):
        # CLI binary has neither 'lvgl' nor a platform hint; falls through to
        # the platform branch which picks webkit on linux.
        self.assertEqual(
            _autodetect_browser("picolet-runtime-linux-x64-cli", "linux"),
            "webkit",
        )

    def test_path_object_accepted(self):
        result = _autodetect_browser(
            Path("/build/picolet-runtime-linux-x64-lvgl"), "linux"
        )
        self.assertEqual(result, "lvgl")


# ---------------------------------------------------------------------------
# AppHarness constructor
# ---------------------------------------------------------------------------

class TestAppHarnessConstructor(unittest.TestCase):

    def test_picolet_test_mode_injected(self):
        h = AppHarness("/fake/picolet-runtime-linux-x64-webview")
        self.assertEqual(h._env.get("PICOLET_TEST_MODE"), "1")

    def test_custom_env_merged(self):
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            env={"MY_VAR": "hello"},
        )
        self.assertEqual(h._env.get("MY_VAR"), "hello")
        self.assertEqual(h._env.get("PICOLET_TEST_MODE"), "1")

    def test_browser_auto_resolves_to_webkit_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            h = AppHarness("/fake/picolet-runtime-linux-x64-webview", browser="auto")
        self.assertEqual(h._browser, "webkit")

    def test_browser_explicit_overrides_auto(self):
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            browser="chromium",
        )
        self.assertEqual(h._browser, "chromium")

    def test_owns_proc_false_when_proc_supplied(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        h = AppHarness(
            "/fake/binary",
            _running_proc=mock_proc,
            _port=8080,
        )
        self.assertFalse(h._owns_proc)

    def test_owns_proc_true_when_no_proc_supplied(self):
        h = AppHarness("/fake/binary")
        self.assertTrue(h._owns_proc)

    def test_port_stored_from_kwarg(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        h = AppHarness("/fake/binary", _running_proc=mock_proc, _port=7777)
        self.assertEqual(h._port, 7777)

    def test_page_is_none_before_start(self):
        h = AppHarness("/fake/picolet-runtime-linux-x64-webview")
        self.assertIsNone(h.page)


# ---------------------------------------------------------------------------
# _wait_for_port — async port parsing
# ---------------------------------------------------------------------------

class TestWaitForPort(unittest.TestCase):
    """Drive the async _wait_for_port method with deterministic stderr content."""

    def _make_harness_with_stderr(self, lines: list[str]) -> AppHarness:
        mock_proc = MagicMock(spec=subprocess.Popen)
        byte_lines = [l.encode() + b"\n" for l in lines]
        mock_proc.stderr = iter(byte_lines)
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            _running_proc=mock_proc,
            _port=None,  # port not yet known
        )
        # Clear _port so _wait_for_port actually runs.
        h._port = None
        return h

    def test_parses_valid_port_line(self):
        h = self._make_harness_with_stderr(["picolet:test-port=9876"])
        port = _run(h._wait_for_port())
        self.assertEqual(port, 9876)

    def test_parses_port_after_noise(self):
        h = self._make_harness_with_stderr([
            "Initialising...",
            "Loading UI...",
            "picolet:test-port=4567",
        ])
        port = _run(h._wait_for_port())
        self.assertEqual(port, 4567)

    def test_returns_none_when_no_port_line(self):
        h = self._make_harness_with_stderr(["no port here"])
        # Use a short timeout so the test completes quickly.
        h._timeout = 0.3
        port = _run(h._wait_for_port())
        self.assertIsNone(port)

    def test_malformed_port_line_returns_none(self):
        h = self._make_harness_with_stderr(["picolet:test-port=notanumber"])
        h._timeout = 0.3
        port = _run(h._wait_for_port())
        self.assertIsNone(port)

    def test_port_line_with_trailing_text_not_matched(self):
        h = self._make_harness_with_stderr(["picolet:test-port=8000 extra"])
        h._timeout = 0.3
        port = _run(h._wait_for_port())
        self.assertIsNone(port)


# ---------------------------------------------------------------------------
# start() — raises RuntimeError on timeout (no port announced)
# ---------------------------------------------------------------------------

class TestHarnessStart(unittest.TestCase):

    def test_start_raises_runtime_error_on_timeout(self):
        """If the process never announces a port, start() raises RuntimeError."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        # Stderr yields nothing before being exhausted.
        mock_proc.stderr = iter([])
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            _running_proc=mock_proc,
            _port=None,
        )
        h._timeout = 0.2  # keep the test fast
        with self.assertRaises(RuntimeError) as ctx:
            _run(h.start())
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_start_with_pre_known_port_and_lvgl_sets_page_none(self):
        """With browser=lvgl and a pre-known port, start() sets page=None."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-lvgl",
            _running_proc=mock_proc,
            _port=12345,
            browser="lvgl",
        )
        _run(h.start())
        self.assertIsNone(h.page)


# ---------------------------------------------------------------------------
# stop() — lifecycle
# ---------------------------------------------------------------------------

class TestHarnessStop(unittest.TestCase):

    def test_stop_returns_0_when_no_proc(self):
        h = AppHarness("/fake/binary")
        h._proc = None
        rc = _run(h.stop())
        self.assertEqual(rc, 0)

    def test_stop_terminates_owned_running_proc(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None   # still running
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0

        h = AppHarness("/fake/binary")
        h._proc = mock_proc
        h._owns_proc = True
        h.page = None

        _run(h.stop())
        mock_proc.terminate.assert_called_once()

    def test_stop_does_not_terminate_borrowed_proc(self):
        """When _owns_proc is False, stop() must not terminate the process."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        h = AppHarness("/fake/binary")
        h._proc = mock_proc
        h._owns_proc = False
        h.page = None

        _run(h.stop())
        mock_proc.terminate.assert_not_called()

    def test_stop_clears_proc_reference(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0

        h = AppHarness("/fake/binary")
        h._proc = mock_proc
        h._owns_proc = True
        h.page = None

        _run(h.stop())
        self.assertIsNone(h._proc)

    def test_stop_closes_page(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0

        mock_page = AsyncMock()
        mock_page.close = AsyncMock()

        h = AppHarness("/fake/binary")
        h._proc = mock_proc
        h._owns_proc = True
        h.page = mock_page

        _run(h.stop())
        mock_page.close.assert_called_once()
        self.assertIsNone(h.page)


# ---------------------------------------------------------------------------
# Non-lvgl tap/key raise NotImplementedError
# ---------------------------------------------------------------------------

class TestNonLvglRaisesForLvglApi(unittest.TestCase):

    def test_tap_raises_for_webkit(self):
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            browser="webkit",
        )
        with self.assertRaises(NotImplementedError):
            _run(h.tap(100, 200))

    def test_key_raises_for_webkit(self):
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            browser="webkit",
        )
        with self.assertRaises(NotImplementedError):
            _run(h.key(13))

    def test_tap_raises_for_chromium(self):
        h = AppHarness(
            "/fake/picolet-runtime-linux-x64-webview",
            browser="chromium",
        )
        with self.assertRaises(NotImplementedError):
            _run(h.tap(0, 0))


# ---------------------------------------------------------------------------
# _spawn xvfb logic
# ---------------------------------------------------------------------------

class TestSpawnXvfb(unittest.TestCase):
    """Test _spawn's xvfb-run autodetection.

    Because _harness.py is loaded via importlib (to avoid namespace package
    conflicts), we patch subprocess.Popen on the module object directly.
    """

    def _make_fake_popen(self, captured_cmd: list):
        """Return a fake Popen callable that records argv and returns a mock."""
        def fake_popen(cmd, **kw):
            captured_cmd.extend(cmd)
            mock = MagicMock()
            mock.stderr = iter([])
            return mock
        return fake_popen

    def test_spawn_xvfb_prepended_when_display_unset(self):
        """_spawn wraps command in xvfb-run when DISPLAY is absent."""
        h = AppHarness("/fake/picolet-runtime-linux-x64-webview", browser="webkit")
        captured_cmd = []
        env_without_display = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        # Patch subprocess.Popen on the loaded module object directly.
        with patch.dict(os.environ, env_without_display, clear=True):
            with patch("shutil.which", return_value="/usr/bin/xvfb-run"):
                with patch.object(sys, "platform", "linux"):
                    with patch.object(_harness_mod.subprocess, "Popen",
                                      side_effect=self._make_fake_popen(captured_cmd)):
                        h._spawn()

        self.assertEqual(captured_cmd[0], "xvfb-run")

    def test_spawn_raises_when_display_unset_and_no_xvfb(self):
        """_spawn raises RuntimeError when DISPLAY unset and xvfb-run missing."""
        h = AppHarness("/fake/picolet-runtime-linux-x64-webview", browser="webkit")
        env_without_display = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with patch.dict(os.environ, env_without_display, clear=True):
            with patch("shutil.which", return_value=None):
                with patch.object(sys, "platform", "linux"):
                    with self.assertRaises(RuntimeError) as ctx:
                        h._spawn()
        self.assertIn("xvfb-run", str(ctx.exception))

    def test_spawn_no_xvfb_when_display_set(self):
        """_spawn does not prepend xvfb-run when DISPLAY is set."""
        h = AppHarness("/fake/picolet-runtime-linux-x64-webview", browser="webkit")
        captured_cmd = []
        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            with patch.object(sys, "platform", "linux"):
                with patch.object(_harness_mod.subprocess, "Popen",
                                  side_effect=self._make_fake_popen(captured_cmd)):
                    h._spawn()

        self.assertNotIn("xvfb-run", captured_cmd)


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------

class TestAsyncContextManager(unittest.TestCase):

    def test_aenter_returns_self(self):
        """__aenter__ calls start() and returns the AppHarness."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0

        async def runner():
            h = AppHarness(
                "/fake/picolet-runtime-linux-x64-lvgl",
                _running_proc=mock_proc,
                _port=1234,
                browser="lvgl",
            )
            result = await h.__aenter__()
            await h.__aexit__(None, None, None)
            return result is h

        self.assertTrue(_run(runner()))


if __name__ == "__main__":
    unittest.main()
