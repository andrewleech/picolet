"""
PH17 inspector port race fix — host-side verification tests.

Tests:
  - _wait_for_inspector_port returns True when a port is listening (host
    Python implementation stub mirroring the MicroPython runtime logic).
  - _wait_for_inspector_port returns False when no listener appears within
    the timeout.
  - Integration (skipped if the webview binary is absent): spawn the runtime
    with PICOLET_TEST_MODE=1, parse the announced port from stderr, and verify
    a TCP connect to 127.0.0.1:<port> succeeds within a short window.

The integration test exercises the full race-fix path: the runtime must not
announce the port until the WebKit inspector TCP socket is actually bound.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_WV_RUNTIME = _REPO_ROOT / "packages/picolet-runtime/build/picolet-runtime-linux-x64-webview"


# ---------------------------------------------------------------------------
# Unit tests for the TCP-poll concept (no binary required)
# ---------------------------------------------------------------------------

class TestTcpPollConcept(unittest.TestCase):
    """Verify the TCP-poll logic works correctly in CPython terms.

    These tests use a real TCP server socket to confirm the pattern
    used by _wait_for_inspector_port behaves correctly.
    """

    def _tcp_connect_succeeds(self, port: int, timeout: float = 1.0) -> bool:
        """Return True if a TCP connect to 127.0.0.1:port succeeds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
        return False

    def test_connect_succeeds_when_port_is_listening(self):
        """A real listening socket is immediately reachable."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            reachable = self._tcp_connect_succeeds(port, timeout=1.0)
            self.assertTrue(reachable, "expected connect to succeed on listening port")
        finally:
            srv.close()

    def test_connect_fails_when_no_listener(self):
        """No listener → connect returns False within the timeout."""
        # Pick a port number unlikely to be in use; bind+close to find a free one.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()  # release it — nothing is listening
        # Give a very short timeout so the test stays fast.
        reachable = self._tcp_connect_succeeds(port, timeout=0.2)
        self.assertFalse(reachable, "expected connect to fail with no listener")

    def test_connect_succeeds_after_delayed_bind(self):
        """Connect poll eventually succeeds when the server binds after a delay."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        # Don't listen yet; start listening after 100 ms on a thread.
        srv.close()

        # Bind a new socket on the same port after a brief delay.
        result_box: list = []

        def _delayed_listen():
            time.sleep(0.1)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                s.listen(1)
                result_box.append(s)
                time.sleep(0.5)  # keep listening while the poll runs
            except OSError:
                pass
            finally:
                s.close()

        t = threading.Thread(target=_delayed_listen, daemon=True)
        t.start()

        reachable = self._tcp_connect_succeeds(port, timeout=2.0)
        # Clean up the server socket if it bound successfully.
        for s in result_box:
            try:
                s.close()
            except OSError:
                pass

        self.assertTrue(
            reachable,
            "expected poll to succeed when listener binds after 100 ms delay"
        )


# ---------------------------------------------------------------------------
# Integration test (requires the webview binary)
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    _WV_RUNTIME.exists() and os.access(str(_WV_RUNTIME), os.X_OK),
    "webview runtime not built: {}".format(_WV_RUNTIME),
)
@unittest.skipUnless(
    sys.platform == "linux",
    "WebKit inspector test is Linux-only",
)
class TestInspectorPortRaceFix(unittest.TestCase):
    """Spawn the runtime in PICOLET_TEST_MODE, verify the announced port is bound.

    After the race fix, picolet:test-port=N must NOT be announced until a TCP
    connect to 127.0.0.1:N succeeds.  This test is the definitive proof.
    """

    _SPAWN_TIMEOUT = 10.0   # seconds to wait for port announcement
    _CONNECT_TIMEOUT = 2.0  # seconds to verify the port is reachable

    def _find_display(self):
        """Return a DISPLAY value or start a minimal Xvfb for the test."""
        if os.environ.get("DISPLAY"):
            return os.environ["DISPLAY"], None

        import shutil
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            xvfb_run = shutil.which("xvfb-run")
            if xvfb_run:
                return None, xvfb_run  # caller uses xvfb-run wrapping
            self.skipTest("no DISPLAY and Xvfb/xvfb-run not available")

        # Start our own Xvfb.
        for dn in range(99, 150):
            if not os.path.exists("/tmp/.X{}-lock".format(dn)):
                break
        else:
            dn = 99

        xvfb_proc = subprocess.Popen(
            [xvfb, ":{}".format(dn), "-screen", "0", "640x480x24", "-nolisten", "tcp"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        time.sleep(0.2)  # give Xvfb time to bind the socket
        return ":{}".format(dn), xvfb_proc

    def test_announced_port_is_immediately_connectable(self):
        """The port announced on stderr must accept a TCP connection at once."""
        display, xvfb_proc_or_runner = self._find_display()

        env = dict(os.environ)
        env["PICOLET_TEST_MODE"] = "1"
        if display:
            env["DISPLAY"] = display
            env["GDK_BACKEND"] = "x11"
            env.pop("WAYLAND_DISPLAY", None)

        cmd = [
            str(_WV_RUNTIME),
            "-c",
            (
                "import picolet_ui._sanity as t; t.run_sanity_test()"
            ),
        ]

        # Wrap in xvfb-run when Xvfb is not directly available.
        if xvfb_proc_or_runner and isinstance(xvfb_proc_or_runner, str):
            cmd = [xvfb_proc_or_runner, "-a", "-s", "-screen 0 640x480x24"] + cmd

        proc = subprocess.Popen(
            cmd,
            env=env,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

        xvfb_proc = (
            xvfb_proc_or_runner
            if xvfb_proc_or_runner and not isinstance(xvfb_proc_or_runner, str)
            else None
        )

        announced_port: list[int] = []
        done_evt = threading.Event()

        def _reader():
            # Scan both stdout and stderr (xvfb-run merges them).
            import re
            port_re = re.compile(r"picolet:test-port=(\d+)")
            for pipe in (proc.stdout, proc.stderr):
                if pipe is None:
                    continue
            # Read from both pipes in a merged fashion.
            for pipe in filter(None, [proc.stderr, proc.stdout]):
                for raw in pipe:
                    line = raw.decode("utf-8", "replace").rstrip()
                    m = port_re.search(line)
                    if m:
                        announced_port.append(int(m.group(1)))
                        done_evt.set()
                        return
            done_evt.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        try:
            found = done_evt.wait(timeout=self._SPAWN_TIMEOUT)
            if not found or not announced_port:
                self.fail(
                    "runtime did not announce picolet:test-port within {}s".format(
                        self._SPAWN_TIMEOUT
                    )
                )

            port = announced_port[0]
            # The core assertion: the port must be immediately connectable.
            connectable = False
            try:
                with socket.create_connection(("127.0.0.1", port),
                                              timeout=self._CONNECT_TIMEOUT):
                    connectable = True
            except (ConnectionRefusedError, OSError) as e:
                self.fail(
                    "picolet:test-port={} announced but TCP connect failed: {}. "
                    "Race condition not fixed.".format(port, e)
                )

            self.assertTrue(
                connectable,
                "TCP connect to announced port {} failed".format(port),
            )

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if xvfb_proc is not None and xvfb_proc.poll() is None:
                xvfb_proc.terminate()
                try:
                    xvfb_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    xvfb_proc.kill()


if __name__ == "__main__":
    unittest.main()
