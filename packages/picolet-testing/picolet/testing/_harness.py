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
        # Internal: X display number used by Xvfb (for xwd-based screenshot).
        _xvfb_display: int | None = None,
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
        self._xvfb_display: int | None = _xvfb_display
        self.page: Any = None
        self._owns_proc = _running_proc is None
        # Set True by _spawn() when xvfb-run wraps the child; used by
        # _wait_for_port() to scan stdout instead of stderr for the port line.
        self._uses_xvfb = False

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
            if self._xvfb_display is not None:
                # On Linux with a manual Xvfb display, the WebKit remote inspector
                # uses a proprietary protocol that is not accessible via standard
                # WebSocket.  Skip the inspector attachment; screenshot() will use
                # xwd to capture the Xvfb framebuffer instead.
                self.page = None
            else:
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
        # Only applicable when a page/inspector connection is established.
        if self._browser in ("chromium", "webkit") and self.page is not None:
            await self._wait_for_ready()

        return self

    def _default_args(self) -> list[str]:
        """Return a default '-c <code>' arg list when the binary has no romfs.

        Without a romfs the binary exits immediately with no output.  Inject
        a minimal startup script that opens the appropriate event loop so test
        mode (port announcement, screenshot, etc.) works out of the box.
        """
        if self._browser == "lvgl":
            code = (
                "import picolet._test; "
                "from picolet_ui._lvgl import LvglDisplay; "
                "import picolet._dispatcher as d; "
                "LvglDisplay(); "
                "d.run()"
            )
        else:
            code = (
                "from picolet_ui._window import Window; "
                "from picolet_ui._webview import Webview, WebviewTransport; "
                "from picolet_ui import _loop; "
                "import asyncio; "
                "w = Window(title='Test', size=[640, 480], resizable=False); "
                "t = WebviewTransport(); "
                "v = Webview(w, root_uri='data:text/html,<html><body>ok</body></html>', transport=t); "
                "w.show(); "
                "asyncio.run(_loop._gtk_pump())"
            )
        return ["-c", code]

    def _spawn(self) -> subprocess.Popen:
        """Spawn the binary with PICOLET_TEST_MODE=1.

        On Linux without a DISPLAY, starts Xvfb on a dedicated display number
        and sets DISPLAY in the child's environment.  The Xvfb process is tracked
        so it can be terminated in stop().
        """
        import shutil
        import time
        args = list(self._args)
        # Auto-inject a default -c startup script when no args are provided.
        # Without this, binaries without a romfs exit immediately.
        if not args:
            args = self._default_args()
        cmd = [str(self._binary)] + args
        self._xvfb_proc: subprocess.Popen | None = None

        # Start Xvfb manually if no display is available (Linux headless).
        if sys.platform == "linux" and not os.environ.get("DISPLAY") and self._browser != "lvgl":
            xvfb_bin = shutil.which("Xvfb")
            if xvfb_bin:
                # Find a free display number.
                for dn in range(99, 200):
                    if not os.path.exists("/tmp/.X{}-lock".format(dn)):
                        xvfb_display = dn
                        break
                else:
                    xvfb_display = 99
                xvfb_cmd = [
                    xvfb_bin, ":{}".format(xvfb_display),
                    "-screen", "0", "1280x800x24", "-nolisten", "tcp",
                ]
                self._xvfb_proc = subprocess.Popen(
                    xvfb_cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
                )
                time.sleep(0.1)  # Give Xvfb a moment to start; 100 ms is sufficient.
                self._env["DISPLAY"] = ":{}".format(xvfb_display)
                # Force GDK to use the X11 backend.  On systems where
                # WAYLAND_DISPLAY is set (e.g. WSL2), GTK4 prefers Wayland
                # over X11.  Unsetting WAYLAND_DISPLAY and setting
                # GDK_BACKEND=x11 ensures GTK connects to our Xvfb display.
                self._env["GDK_BACKEND"] = "x11"
                self._env.pop("WAYLAND_DISPLAY", None)
                self._xvfb_display = xvfb_display
            elif shutil.which("xvfb-run"):
                # Fall back to xvfb-run; note this merges the child's stderr→stdout.
                cmd = [
                    "xvfb-run", "-a", "-s", "-screen 0 1280x800x24",
                ] + cmd
                self._uses_xvfb = True
            else:
                raise RuntimeError(
                    "AppHarness: $DISPLAY is unset and Xvfb is not installed. "
                    "Install xvfb: apt install xvfb"
                )

        # For the LVGL path the transport is stdin/stdout JSON-lines (BUG-C fix).
        # For webview paths: pipe stderr. Also pipe stdout when xvfb-run fallback
        # is used (xvfb-run routes the child's stderr to its stdout).
        if self._browser == "lvgl":
            return subprocess.Popen(
                cmd,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        popen_kwargs: dict = {"env": self._env, "stderr": subprocess.PIPE}
        if self._uses_xvfb:
            popen_kwargs["stdout"] = subprocess.PIPE
        return subprocess.Popen(cmd, **popen_kwargs)

    async def _wait_for_port(self) -> int | None:
        """Read pipe(s) until 'picolet:test-port=<N>' appears or timeout.

        When xvfb-run is wrapping the child, the port announcement arrives on
        proc.stdout (xvfb-run does ``"$@" 2>&1``), so we scan stdout in that
        case.  We always drain proc.stderr (xvfb's own messages) to prevent
        pipe blocking.
        """
        port_found: list[int] = []
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        uses_xvfb = getattr(self, "_uses_xvfb", False)

        def _make_pipe_reader(pipe, is_port_source: bool):
            def _reader():
                try:
                    for raw in pipe:
                        line = raw.rstrip(b"\n\r").decode("utf-8", "replace")
                        if is_port_source:
                            m = _PORT_RE.match(line)
                            if m:
                                port_found.append(int(m.group(1)))
                                try:
                                    loop.call_soon_threadsafe(done.set)
                                except RuntimeError:
                                    pass
                        if done.is_set():
                            for _ in pipe:
                                pass
                            break
                except Exception:
                    pass
                finally:
                    try:
                        loop.call_soon_threadsafe(done.set)
                    except RuntimeError:
                        pass
            return _reader

        threads = []
        if uses_xvfb and self._proc.stdout is not None:
            # Port arrives on stdout when xvfb-run is used.
            t = threading.Thread(
                target=_make_pipe_reader(self._proc.stdout, is_port_source=True),
                daemon=True,
            )
            t.start()
            threads.append(t)
            # Drain stderr (xvfb's own messages) without scanning for port.
            if self._proc.stderr is not None:
                t = threading.Thread(
                    target=_make_pipe_reader(self._proc.stderr, is_port_source=False),
                    daemon=True,
                )
                t.start()
                threads.append(t)
        else:
            # Normal path: port is on stderr.
            if self._proc.stderr is not None:
                t = threading.Thread(
                    target=_make_pipe_reader(self._proc.stderr, is_port_source=True),
                    daemon=True,
                )
                t.start()
                threads.append(t)

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

        For webview variants with an inspector connection: delegates to page.screenshot().
        For webview variants on Linux with a manual Xvfb display: uses xwd to capture
        the framebuffer directly (the WebKit Remote Inspector protocol is not accessible
        via standard HTTP/WebSocket).
        For LVGL variants: calls into picolet._test.snapshot() via stdio.
        """
        if self._browser in ("chromium", "webkit") and self.page is not None:
            await self.page.screenshot(path=str(path))
        elif self._browser == "webkit" and self._xvfb_display is not None:
            await self._xwd_screenshot(path)
        elif self._browser == "lvgl":
            await self._lvgl_screenshot(path)

    async def _xwd_screenshot(self, path: str | Path) -> None:
        """Capture the Xvfb display using xwininfo + xwd + ImageMagick convert.

        Used for the webkit path on Linux when the WebKit Remote Inspector
        protocol (WEBKIT_INSPECTOR_SERVER) is not accessible via standard
        HTTP/WebSocket.  xwininfo finds the application window ID by name,
        then xwd captures that window's pixels, and convert transforms to PNG.

        Falls back to root-window capture if window ID lookup fails.

        Requires: xwininfo, xwd (x11-apps) and convert (imagemagick) in PATH.
        """
        import shutil as _shutil

        display = ":{}".format(self._xvfb_display)

        # Give the webview a moment to render the first frame.
        # 0.5 s is sufficient for WebKitGTK to paint the initial page on Xvfb.
        await asyncio.sleep(0.5)

        xwininfo_bin = _shutil.which("xwininfo")
        xwd_bin = _shutil.which("xwd")
        convert_bin = _shutil.which("convert")
        if not xwd_bin or not convert_bin:
            raise RuntimeError(
                "AppHarness xwd screenshot: xwd or convert not found; "
                "install x11-apps and imagemagick"
            )

        # Try to find the first child window of the root that isn't tiny
        # (the GTK window is usually the first ≥100x100 child).
        # xwininfo -root -tree lists: 0xNN "title": ("class" ...) WxH+X+Y
        win_id = None
        if xwininfo_bin:
            try:
                r_info = subprocess.run(
                    [xwininfo_bin, "-display", display, "-root", "-tree"],
                    timeout=5,
                    capture_output=True,
                )
                import re as _re
                # Find windows with WxH where W,H >= 100.
                for line in r_info.stdout.decode("utf-8", "replace").splitlines():
                    m = _re.search(r"(0x[0-9a-f]+).*?(\d+)x(\d+)\+", line)
                    if m and int(m.group(2)) >= 100 and int(m.group(3)) >= 100:
                        win_id = m.group(1)
                        break
            except Exception:
                pass

        xwd_tmp = str(path) + ".xwd"
        try:
            if win_id:
                # Capture the application window by its X window ID.
                xwd_cmd = [xwd_bin, "-display", display, "-id", win_id, "-silent", "-out", xwd_tmp]
            else:
                # Fall back to root-window capture.
                xwd_cmd = [xwd_bin, "-display", display, "-root", "-screen", "-silent", "-out", xwd_tmp]

            r = subprocess.run(xwd_cmd, timeout=10, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(
                    "AppHarness xwd screenshot: xwd failed (rc={}): {}".format(
                        r.returncode, r.stderr.decode("utf-8", "replace")[:200]
                    )
                )
            # Convert xwd → PNG using ImageMagick.
            r2 = subprocess.run(
                [convert_bin, "xwd:{}".format(xwd_tmp), str(path)],
                timeout=10,
                capture_output=True,
            )
            if r2.returncode != 0:
                raise RuntimeError(
                    "AppHarness xwd screenshot: convert failed (rc={}): {}".format(
                        r2.returncode, r2.stderr.decode("utf-8", "replace")[:200]
                    )
                )
        finally:
            try:
                import os as _os
                _os.unlink(xwd_tmp)
            except OSError:
                pass

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

        # Terminate the Xvfb process if we started it in _spawn().
        xvfb_proc = getattr(self, "_xvfb_proc", None)
        if xvfb_proc is not None and xvfb_proc.poll() is None:
            try:
                xvfb_proc.terminate()
                xvfb_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb_proc.kill()
            self._xvfb_proc = None

        return rc

    # -------------------------------------------------------------------------
    # Async context manager
    # -------------------------------------------------------------------------

    async def __aenter__(self) -> "AppHarness":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()
