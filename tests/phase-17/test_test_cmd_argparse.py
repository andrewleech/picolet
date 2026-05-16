"""
PH17 unit tests — picolet_cli.test_cmd: argument parsing and CLI wiring.

Covers:
  - The 'test' subcommand is registered in the top-level parser.
  - --screenshot, --run, --browser, --no-build, --timeout, --verbose flags.
  - _resolve_browser selects the correct driver from binary name + platform.
  - _build_child_cmd strips the leading '--' separator from forwarded args.
  - _build_child_cmd prepends xvfb-run when DISPLAY is unset (mocked).
  - _wait_for_port returns the parsed port for a well-formed line.
  - _wait_for_port returns None when the line is absent (timeout 0).
  - _wait_for_port handles malformed port lines gracefully.
  - run() returns 1 when the binary path does not exist.
  - run() prints a clear error and returns 2 for --browser chromium on Linux
    webview binaries.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure picolet-cli is importable from the workspace root.
_REPO_ROOT = Path(__file__).parent.parent.parent
_CLI_PKG = _REPO_ROOT / "packages" / "picolet-cli"
if str(_CLI_PKG) not in sys.path:
    sys.path.insert(0, str(_CLI_PKG))

from picolet_cli import test_cmd
from picolet_cli.__main__ import _build_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    """Return a Namespace with test_cmd defaults, overridden by kwargs."""
    defaults = dict(
        binary=None,
        args=[],
        browser="auto",
        screenshot=None,
        run_script=None,
        timeout=10.0,
        no_build=True,
        verbose=False,
        target=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# CLI wiring — test subcommand present in top-level parser
# ---------------------------------------------------------------------------

class TestCliWiring(unittest.TestCase):

    def test_test_subcommand_registered(self):
        parser = _build_parser()
        help_text = parser.format_help()
        self.assertIn("test", help_text)

    def test_test_help_shows_screenshot(self):
        parser = _build_parser()
        # Manually invoke parse_args on 'test --help' and capture output.
        with self.assertRaises(SystemExit):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                parser.parse_args(["test", "--help"])
        # The --help is printed to stdout; confirm flag is mentioned.
        # argparse prints to sys.stdout before raising SystemExit.
        # We need a different approach: format subparser help directly.
        subparsers_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        test_parser = subparsers_action.choices["test"]
        test_help = test_parser.format_help()
        self.assertIn("--screenshot", test_help)

    def test_test_help_shows_browser(self):
        parser = _build_parser()
        subparsers_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        test_parser = subparsers_action.choices["test"]
        test_help = test_parser.format_help()
        self.assertIn("--browser", test_help)

    def test_test_help_shows_run(self):
        parser = _build_parser()
        subparsers_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        test_parser = subparsers_action.choices["test"]
        test_help = test_parser.format_help()
        self.assertIn("--run", test_help)

    def test_test_help_shows_timeout(self):
        parser = _build_parser()
        subparsers_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        test_parser = subparsers_action.choices["test"]
        test_help = test_parser.format_help()
        self.assertIn("--timeout", test_help)


# ---------------------------------------------------------------------------
# Argument parsing — parse_args round-trips
# ---------------------------------------------------------------------------

class TestArgParsing(unittest.TestCase):

    def _parse(self, *argv):
        parser = _build_parser()
        return parser.parse_args(["test"] + list(argv))

    def test_no_build_flag(self):
        args = self._parse("--no-build", "/some/binary")
        self.assertTrue(args.no_build)

    def test_screenshot_flag(self):
        args = self._parse("--screenshot", "/tmp/out.png", "/some/binary")
        self.assertEqual(args.screenshot, "/tmp/out.png")

    def test_browser_webkit(self):
        args = self._parse("--browser", "webkit", "/some/binary")
        self.assertEqual(args.browser, "webkit")

    def test_browser_chromium(self):
        args = self._parse("--browser", "chromium", "/some/binary")
        self.assertEqual(args.browser, "chromium")

    def test_browser_auto_default(self):
        args = self._parse("/some/binary")
        self.assertEqual(args.browser, "auto")

    def test_run_flag(self):
        args = self._parse("--run", "/some/script.py", "/some/binary")
        self.assertEqual(args.run_script, "/some/script.py")

    def test_timeout_default(self):
        args = self._parse("/some/binary")
        self.assertAlmostEqual(args.timeout, 10.0)

    def test_timeout_custom(self):
        args = self._parse("--timeout", "5.5", "/some/binary")
        self.assertAlmostEqual(args.timeout, 5.5)

    def test_verbose_default_false(self):
        args = self._parse("/some/binary")
        self.assertFalse(args.verbose)

    def test_verbose_flag(self):
        args = self._parse("--verbose", "/some/binary")
        self.assertTrue(args.verbose)

    def test_browser_invalid_choice_raises(self):
        with self.assertRaises(SystemExit):
            self._parse("--browser", "firefox", "/some/binary")

    def test_extra_args_after_double_dash(self):
        args = self._parse("/bin/foo", "--", "file:///tmp/index.html")
        # argparse.REMAINDER collects the -- and the following tokens.
        self.assertIn("file:///tmp/index.html", args.args)


# ---------------------------------------------------------------------------
# _resolve_browser — routing logic from binary name + platform
# ---------------------------------------------------------------------------

class TestResolveBrowser(unittest.TestCase):

    def _resolve(self, browser_arg, binary_name, platform="linux"):
        args = _make_args(browser=browser_arg)
        binary = Path("/fake/build") / binary_name
        with patch.object(sys, "platform", platform):
            return test_cmd._resolve_browser(args, binary)

    def test_auto_webview_linux_returns_webkit(self):
        result = self._resolve("auto", "picolet-runtime-linux-x64-webview", "linux")
        self.assertEqual(result, "webkit")

    def test_auto_webview_win32_returns_chromium(self):
        result = self._resolve("auto", "picolet-runtime-windows-x64-webview.exe", "win32")
        self.assertEqual(result, "chromium")

    def test_auto_lvgl_returns_lvgl(self):
        result = self._resolve("auto", "picolet-runtime-linux-x64-lvgl", "linux")
        self.assertEqual(result, "lvgl")

    def test_explicit_webkit_overrides(self):
        result = self._resolve("webkit", "picolet-runtime-linux-x64-webview", "linux")
        self.assertEqual(result, "webkit")

    def test_explicit_chromium_overrides(self):
        result = self._resolve("chromium", "picolet-runtime-linux-x64-webview", "win32")
        self.assertEqual(result, "chromium")


# ---------------------------------------------------------------------------
# _build_child_cmd — xvfb autodetect + arg forwarding
# ---------------------------------------------------------------------------

class TestBuildChildCmd(unittest.TestCase):

    def test_no_xvfb_when_display_set(self):
        """When $DISPLAY is set, no xvfb-run prefix."""
        args = _make_args(args=[], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        env_with_display = {"DISPLAY": ":0"}
        with patch.dict(os.environ, env_with_display, clear=False):
            cmd = test_cmd._build_child_cmd(args, binary)
        self.assertNotIn("xvfb-run", cmd)
        self.assertEqual(cmd[0], str(binary))

    def test_xvfb_prepended_when_display_unset_and_xvfb_present(self):
        """When $DISPLAY is unset and xvfb-run is available, it is prepended."""
        args = _make_args(args=[], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        env_without_display = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with patch.dict(os.environ, env_without_display, clear=True):
            with patch("shutil.which", return_value="/usr/bin/xvfb-run"):
                with patch.object(sys, "platform", "linux"):
                    cmd = test_cmd._build_child_cmd(args, binary)
        self.assertEqual(cmd[0], "xvfb-run")
        self.assertIn(str(binary), cmd)

    def test_xvfb_screen_size_in_args(self):
        """xvfb-run includes -screen 0 1280x800x24."""
        args = _make_args(args=[], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        env_without_display = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with patch.dict(os.environ, env_without_display, clear=True):
            with patch("shutil.which", return_value="/usr/bin/xvfb-run"):
                with patch.object(sys, "platform", "linux"):
                    cmd = test_cmd._build_child_cmd(args, binary)
        cmd_str = " ".join(cmd)
        self.assertIn("1280x800x24", cmd_str)

    def test_exit_when_display_unset_and_xvfb_missing(self):
        """Exits with code 1 if $DISPLAY is unset and xvfb-run is missing."""
        args = _make_args(args=[], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        env_without_display = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with patch.dict(os.environ, env_without_display, clear=True):
            with patch("shutil.which", return_value=None):
                with patch.object(sys, "platform", "linux"):
                    with self.assertRaises(SystemExit) as cm:
                        test_cmd._build_child_cmd(args, binary)
        self.assertEqual(cm.exception.code, 1)

    def test_double_dash_separator_stripped_from_forwarded_args(self):
        """Leading '--' is removed from forwarded args."""
        args = _make_args(args=["--", "file:///tmp/index.html"], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            cmd = test_cmd._build_child_cmd(args, binary)
        self.assertIn("file:///tmp/index.html", cmd)
        self.assertNotIn("--", cmd)

    def test_forwarded_args_without_separator(self):
        """Args without '--' separator are still forwarded."""
        args = _make_args(args=["http://example.com"], verbose=False)
        binary = Path("/fake/picolet-runtime-linux-x64-webview")
        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            cmd = test_cmd._build_child_cmd(args, binary)
        self.assertIn("http://example.com", cmd)


# ---------------------------------------------------------------------------
# _wait_for_port — port announcement parsing
# ---------------------------------------------------------------------------

class TestWaitForPort(unittest.TestCase):
    """Feed deterministic stderr bytes through a fake Popen and assert parsing."""

    def _make_proc(self, lines: list[str]) -> MagicMock:
        """Return a mock Popen whose stderr yields the given byte lines."""
        proc = MagicMock(spec=subprocess.Popen)
        byte_lines = [l.encode() + b"\n" for l in lines]
        proc.stderr = iter(byte_lines)
        return proc

    def test_parses_well_formed_port_line(self):
        proc = self._make_proc(["startup message", "picolet:test-port=12345"])
        port = test_cmd._wait_for_port(proc, timeout=2.0, verbose=False)
        self.assertEqual(port, 12345)

    def test_returns_none_on_empty_stderr(self):
        proc = self._make_proc([])
        # With an empty iterator done is set immediately.
        port = test_cmd._wait_for_port(proc, timeout=0.1, verbose=False)
        self.assertIsNone(port)

    def test_returns_none_when_no_port_line(self):
        proc = self._make_proc([
            "some startup text",
            "another line",
            "no port announcement here",
        ])
        port = test_cmd._wait_for_port(proc, timeout=0.5, verbose=False)
        self.assertIsNone(port)

    def test_malformed_port_line_not_matched(self):
        """picolet:test-port=abc should not parse (letters not digits)."""
        proc = self._make_proc(["picolet:test-port=abc"])
        port = test_cmd._wait_for_port(proc, timeout=0.5, verbose=False)
        self.assertIsNone(port)

    def test_partial_match_not_accepted(self):
        """A prefix-match like 'xpicolet:test-port=9000' must not match."""
        proc = self._make_proc(["xpicolet:test-port=9000"])
        port = test_cmd._wait_for_port(proc, timeout=0.5, verbose=False)
        self.assertIsNone(port)

    def test_port_line_with_trailing_content_not_matched(self):
        """'picolet:test-port=9000 extra' must not match the regex."""
        proc = self._make_proc(["picolet:test-port=9000 extra"])
        port = test_cmd._wait_for_port(proc, timeout=0.5, verbose=False)
        self.assertIsNone(port)

    def test_first_port_returned_when_multiple_lines_present(self):
        proc = self._make_proc([
            "noise",
            "picolet:test-port=1111",
            "picolet:test-port=2222",
        ])
        port = test_cmd._wait_for_port(proc, timeout=2.0, verbose=False)
        self.assertEqual(port, 1111)

    def test_port_line_before_noise(self):
        proc = self._make_proc([
            "picolet:test-port=9999",
            "trailing noise",
        ])
        port = test_cmd._wait_for_port(proc, timeout=2.0, verbose=False)
        self.assertEqual(port, 9999)


# ---------------------------------------------------------------------------
# run() — error-path behaviour without spawning a real binary
# ---------------------------------------------------------------------------

class TestRunErrorPaths(unittest.TestCase):

    def test_run_returns_1_when_binary_not_found(self):
        args = _make_args(binary="/no/such/binary/picolet-runtime-linux-x64-webview")
        rc = test_cmd.run(args)
        self.assertEqual(rc, 1)

    def test_run_returns_2_for_chromium_on_linux_webview(self):
        """--browser chromium against a webview binary on Linux must exit 2."""
        with tempfile.NamedTemporaryFile(
            prefix="picolet-runtime-linux-x64-webview", delete=False
        ) as f:
            binary_path = f.name

        try:
            os.chmod(binary_path, 0o755)
            args = _make_args(
                binary=binary_path,
                browser="chromium",
            )
            with patch.object(sys, "platform", "linux"):
                rc = test_cmd.run(args)
            self.assertEqual(rc, 2)
        finally:
            os.unlink(binary_path)

    def test_run_chromium_error_message_is_informative(self):
        """The chromium/Linux error message mentions 'not supported'."""
        with tempfile.NamedTemporaryFile(
            prefix="picolet-runtime-linux-x64-webview", delete=False
        ) as f:
            binary_path = f.name

        try:
            os.chmod(binary_path, 0o755)
            args = _make_args(binary=binary_path, browser="chromium")
            stderr_capture = io.StringIO()
            with patch.object(sys, "platform", "linux"):
                with patch("sys.stderr", stderr_capture):
                    test_cmd.run(args)
            output = stderr_capture.getvalue()
            self.assertIn("not supported", output.lower())
        finally:
            os.unlink(binary_path)

    def test_run_returns_1_on_port_timeout(self):
        """When the binary exits before announcing a port, run() returns 1."""
        # A real subprocess that exits immediately without printing the port.
        with tempfile.NamedTemporaryFile(
            prefix="picolet-runtime-linux-x64-webview",
            suffix="", delete=False
        ) as f:
            binary_path = f.name

        # Write a script that exits immediately.
        script = "#!/bin/sh\nexit 0\n"
        with open(binary_path, "w") as fh:
            fh.write(script)
        os.chmod(binary_path, 0o755)

        try:
            args = _make_args(
                binary=binary_path,
                timeout=0.5,   # short timeout so the test is fast
            )
            with patch.dict(os.environ, {"DISPLAY": ":0"}):
                rc = test_cmd.run(args)
            self.assertEqual(rc, 1)
        finally:
            os.unlink(binary_path)

    def test_run_bare_mode_prints_port_info(self):
        """Bare mode: run() prints 'connected browser=... port=...' to stdout."""
        # Write a script that prints the port line and then sleeps briefly.
        with tempfile.NamedTemporaryFile(
            prefix="picolet-runtime-linux-x64-webview",
            suffix="", delete=False
        ) as f:
            binary_path = f.name

        script = "#!/bin/sh\necho 'picolet:test-port=54321' >&2\nsleep 10\n"
        with open(binary_path, "w") as fh:
            fh.write(script)
        os.chmod(binary_path, 0o755)

        try:
            args = _make_args(
                binary=binary_path,
                timeout=3.0,
                screenshot=None,
                run_script=None,
            )
            stdout_capture = io.StringIO()
            with patch.dict(os.environ, {"DISPLAY": ":0"}):
                with patch("sys.stdout", stdout_capture):
                    rc = test_cmd.run(args)
            self.assertEqual(rc, 0)
            output = stdout_capture.getvalue()
            self.assertIn("54321", output)
            self.assertIn("connected", output)
        finally:
            os.unlink(binary_path)


if __name__ == "__main__":
    unittest.main()
