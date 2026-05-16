"""
picolet.testing._harness — AppHarness: spawn → wait-ready → drive → terminate.

FR-TEST-5 / FR-TEST-6.  Wraps the full test workflow for both webview and
LVGL binary variants.  For webview binaries, attaches via CDP (Chromium) or
WebKit Inspector Protocol (WebKit).  For LVGL binaries, drives via stdio
commands to the in-process picolet._test module.

Design decision D2: AppHarness lives in picolet-cli / picolet-testing (host-side
CPython), not in the runtime.  Playwright and websockets are host-only deps.

Decision D3: webkit path returns a WebKitPage duck; chromium path returns the
literal Playwright Page.

The 'binary=' argument is accepted for compatibility with test_cmd usage but
when _running_proc/_port are supplied (test_cmd pre-spawned the process and
found the port), we reuse those directly.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


_PORT_RE = re.compile(r"^picolet:test-port=(\d+)$")

# Timeout for window.picolet.__ready__ === true poll (FR-TEST-3 R3).
_READY_POLL_TIMEOUT = 10.0
_READY_POLL_INTERVAL = 0.25


def _autodetect_browser(binary: str | Path, platform: str = sys.platform) -> str:
    """Choose the driver from binary name and host platform."""
    name = Path(binary).name
    if "lvgl" in name:
        return "lvgl"
    if platform == "win32":
        return "chromium"
    return "webkit"


class AppHarness:
    """Spawn a Picolet binary in test mode, attach a debug driver, drive, terminate.

    Usage (async context manager):

        async with AppHarness("./picolet-runtime-linux-x64-webview") as h:
            title = await h.page.evaluate("document.title")
            await h.screenshot("/tmp/shot.png")

    Usage (manual):

        h = AppHarness("./picolet-runtime-linux-x64-webview")
        await h.start()
        ...
        await h.stop()

    Attributes:
        page: Playwright Page (chromium) or WebKitPage duck (webkit) after
              start() completes.  None before start() or after stop().
    """

    def __init__(
        self,
        binary: str | Path,
        browser: str = "auto",
        env: dict | None = None,
        args: tuple = (),
        timeout: float = 10.0,
        # Internal: test_cmd may pass a pre-spawned process + known port.
        _running_proc: subprocess.Popen | None = None,
        _port: int | None = None,
    ):
        self._binary = Path(binary)
        self._browser = (
            _autodetect_browser(binary) if browser == "auto" else browser
        )
        self._env = dict(os.environ)
        if env:
            self._env.update(env)
        self._env["PICOLET_TEST_MODE"] = "1"
        self._args = list(args)
        self._timeout = timeout

        self._proc: subprocess.Popen | None = _running_proc
        self._port: int | None = _port
        self.page: Any = None
        self._owns_proc = _running_proc is None

    # -------------------------------------------------------------------------
    # Spawn + attach
    # -------------------------------------------------------------------------

    async def start(self) -> "AppHarness":
        """Spawn the child (unless pre-spawned), wait for the port, attach.

        Returns self.
        """
        if self._proc is None:
            self._proc = self._spawn()

        # BUG-D fix: LVGL path uses stdio transport, not an inspector port.
        # Skip port waiting entirely for LVGL — the transport readiness is
        # signalled by a successful ping reply from the dispatcher, not by a
        # 'picolet:test-port=<N>' stderr line.
        if self._browser != "lvgl" and self._port is None:
            self._port = await self._wait_for_port()
            if self._port is None:
                self._proc.terminate()
                raise RuntimeError(
                    "AppHarness: timed out waiting for 'picolet:test-port=<N>' "
                    "({}s)".format(self._timeout)
                )

        if self._browser == "chromium":
            from picolet.testing._chromium import attach_chromium
            self.page = await attach_chromium(self._port, timeout=self._timeout)
        elif self._browser == "webkit":
            from picolet.testing._webkit import attach_webkit
            self.page = await attach_webkit(self._port, timeout=self._timeout)
        elif self._browser == "lvgl":
            # LVGL path: no inspector port — page is None; use tap/press/screenshot
            # directly on self.  The stdio transport (stdin/stdout pipes) must
            # already be open (enforced by _spawn() for lvgl or by test_cmd which
            # opens them before passing _running_proc).
            self.page = None
            # Wait for the LVGL dispatcher to be ready via a ping handshake.
            await self._lvgl_wait_ready()
        else:
            raise ValueError("AppHarness: unknown browser: {}".format(self._browser))

        # Wait for window.picolet.__ready__ === true (R3) for webview variants.
        if self._browser in ("chromium", "webkit") and self.page is not None:
            await self._wait_for_ready()

        return self

    def _spawn(self) -> subprocess.Popen:
        """Spawn the binary with PICOLET_TEST_MODE=1."""
        import shutil
        cmd = [str(self._binary)] + self._args

        # xvfb wrapping (D7 / Chunk 6) — same logic as test_cmd.
        if sys.platform == "linux" and not os.environ.get("DISPLAY"):
            if shutil.which("xvfb-run"):
                cmd = [
                    "xvfb-run", "-a", "-s", "-screen 0 1280x800x24",
                    "-e", "/dev/stderr",
                ] + cmd
            else:
                raise RuntimeError(
                    "AppHarness: $DISPLAY is unset and xvfb-run is not installed. "
                    "Install xvfb: apt install xvfb"
                )

        # For the LVGL path the transport is stdin/stdout JSON-lines (BUG-C fix).
        # For webview paths stdin/stdout are inherited (not piped) so the app's
        # own output reaches the terminal; only stderr is captured for the port
        # announcement.
        if self._browser == "lvgl":
            return subprocess.Popen(
                cmd,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        return subprocess.Popen(
            cmd,
            env=self._env,
            stderr=subprocess.PIPE,
        )

    async def _wait_for_port(self) -> int | None:
        """Read stderr lines until 'picolet:test-port=<N>' appears or timeout."""
        port_found: list[int] = []
        done = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _reader():
            try:
                for raw in self._proc.stderr:
                    line = raw.rstrip(b"\n\r").decode("utf-8", "replace")
                    m = _PORT_RE.match(line)
                    if m:
                        port_found.append(int(m.group(1)))
                        loop.call_soon_threadsafe(done.set)
                    # Drain remaining stderr to prevent pipe blocking.
                    if done.is_set():
                        for _ in self._proc.stderr:
                            pass
                        break
            except Exception:
                pass
            finally:
                try:
                    loop.call_soon_threadsafe(done.set)
                except RuntimeError:
                    # Loop closed (timeout path) — done.wait() already returned.
                    pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        try:
            await asyncio.wait_for(done.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            pass

        return port_found[0] if port_found else None

    async def _wait_for_ready(self) -> None:
        """Poll until window.picolet.__ready__ === true (FR-TEST-3/R3).

        PH17 adds `(window as any).picolet.__ready__ = true` to bridge-js
        after __picolet_recv is assigned.  We poll via page.evaluate.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _READY_POLL_TIMEOUT
        while loop.time() < deadline:
            try:
                ready = await self.page.evaluate("window.picolet && window.picolet.__ready__ === true")
                if ready:
                    return
            except Exception:
                pass
            await asyncio.sleep(_READY_POLL_INTERVAL)
        # Not a hard error — some test contexts don't need the ready signal
        # (e.g. bare content-only pages).  Log and continue.
        sys.stderr.write(
            "AppHarness: window.picolet.__ready__ did not become true within {}s "
            "(non-fatal for non-picolet pages)\n".format(_READY_POLL_TIMEOUT)
        )

    async def _lvgl_wait_ready(self) -> None:
        """Send a __test__.ping to the LVGL dispatcher and wait for 'pong'.

        The LVGL binary imports picolet._test (which registers the @picolet.command
        handlers) and then runs the dispatcher event loop.  The first valid ping
        reply signals that the runtime is ready to receive drive commands.

        Retries for up to self._timeout seconds with exponential back-off.
        """
        import json
        deadline = asyncio.get_running_loop().time() + self._timeout
        delay = 0.05  # start at 50 ms
        req_id = 0
        while asyncio.get_running_loop().time() < deadline:
            req_id += 1
            req = '{{"id":{},"cmd":"__test__.ping","args":{{}}}}\n'.format(req_id)
            try:
                self._proc.stdin.write(req.encode())
                self._proc.stdin.flush()
            except (OSError, BrokenPipeError):
                break

            # Read reply with a short timeout via a background thread.
            reply_box: list = []
            done_evt = threading.Event()

            def _read_reply():
                try:
                    line = self._proc.stdout.readline()
                    if line:
                        reply_box.append(line.decode("utf-8", "replace").strip())
                except Exception:
                    pass
                finally:
                    done_evt.set()

            t = threading.Thread(target=_read_reply, daemon=True)
            t.start()

            # Yield to the event loop while waiting.
            wait_start = asyncio.get_running_loop().time()
            while not done_evt.is_set() and (asyncio.get_running_loop().time() - wait_start) < delay * 2:
                await asyncio.sleep(0.02)

            if reply_box:
                try:
                    reply = json.loads(reply_box[0])
                    if reply.get("ok") and reply.get("result") == "pong":
                        return
                except Exception:
                    pass

            delay = min(delay * 2, 0.5)
            await asyncio.sleep(delay)

        sys.stderr.write(
            "AppHarness: LVGL dispatcher did not respond to ping within {}s "
            "(non-fatal — proceeding)\n".format(self._timeout)
        )

    # -------------------------------------------------------------------------
    # Drive API
    # -------------------------------------------------------------------------

    async def screenshot(self, path: str | Path) -> None:
        """Capture a screenshot to path (PNG).

        Delegates to page.screenshot() for webview variants.
        For LVGL variants, calls into picolet._test.snapshot() via stdio.
        """
        if self._browser in ("chromium", "webkit") and self.page is not None:
            await self.page.screenshot(path=str(path))
        elif self._browser == "lvgl":
            await self._lvgl_screenshot(path)

    async def _lvgl_screenshot(self, path: str | Path) -> None:
        """Capture an LVGL snapshot via stdio invoke to picolet._test.snapshot()."""
        # Invoke __test__.snapshot via the existing picolet IPC dispatcher
        # (StdioTransport path). The LVGL runtime's picolet._test module
        # registers command handlers on import.
        # Implementation: send a JSON request over stdin, read reply on stdout.
        req = '{"id":1,"cmd":"__test__.snapshot","args":{}}\n'
        self._proc.stdin.write(req.encode())
        self._proc.stdin.flush()

        # Read one line from stdout (JSON reply).
        line = self._proc.stdout.readline().decode("utf-8", "replace").strip()
        try:
            import json
            reply = json.loads(line)
        except Exception:
            raise RuntimeError("AppHarness LVGL screenshot: bad reply: {}".format(line))
        if not reply.get("ok"):
            raise RuntimeError(
                "AppHarness LVGL screenshot: remote error: {}".format(reply.get("error"))
            )
        # result is base64-encoded PNG bytes.
        import base64
        png_bytes = base64.b64decode(reply["result"])
        with open(path, "wb") as fh:
            fh.write(png_bytes)

    async def tap(self, x: int, y: int) -> None:
        """Synthesise a pointer tap (LVGL variants only)."""
        if self._browser != "lvgl":
            raise NotImplementedError("tap() is only available for LVGL variants")
        req = '{{"id":2,"cmd":"__test__.tap","args":{{"x":{},"y":{}}}}}\n'.format(x, y)
        self._proc.stdin.write(req.encode())
        self._proc.stdin.flush()

    async def key(self, code: int) -> None:
        """Synthesise a key press (LVGL variants only)."""
        if self._browser != "lvgl":
            raise NotImplementedError("key() is only available for LVGL variants")
        req = '{{"id":3,"cmd":"__test__.press","args":{{"key":{}}}}}\n'.format(code)
        self._proc.stdin.write(req.encode())
        self._proc.stdin.flush()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def stop(self) -> int:
        """Terminate the child process and return its exit code."""
        if self.page is not None:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None

        if self._proc is None:
            return 0

        if self._owns_proc:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()

        rc = self._proc.returncode if self._proc.returncode is not None else 0
        self._proc = None
        return rc

    # -------------------------------------------------------------------------
    # Async context manager
    # -------------------------------------------------------------------------

    async def __aenter__(self) -> "AppHarness":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()
