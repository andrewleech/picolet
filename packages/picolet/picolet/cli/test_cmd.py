"""
picolet test — launch a Picolet app in test mode and drive it via the debug port.

Usage:
    picolet test [--target TARGET]
               [--no-build]
               [--browser {webkit,chromium,auto}]
               [--screenshot PATH]
               [--run SCRIPT_PY]
               [--timeout SECONDS]
               [--verbose]
               [BINARY]
               [-- arg1 arg2 ...]

Modes:
    bare (no --screenshot / --run):
        Spawn the binary with PICOLET_TEST_MODE=1, wait for
        'picolet:test-port=<N>' on stderr (up to --timeout seconds), print
        connection info, then terminate the child.

    --screenshot PATH:
        Spawn, wait for the port line, attach via AppHarness, wait for
        window.picolet.__ready__, capture a PNG to PATH, terminate.

    --run SCRIPT_PY:
        Spawn, attach, execute SCRIPT_PY in a context where 'harness' is
        pre-bound to the AppHarness.  Exit code mirrors the script's exit.

Closes: FR-TEST-3, FR-TEST-4.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


_TEST_DESCRIPTION = """\
Spawn the app with PICOLET_TEST_MODE=1, wait for the inspector port announcement
on stderr, then drive it in one of three modes:

  bare (no flags):       connect, print connection info, then exit
  --screenshot PATH:     capture a PNG screenshot to PATH, then exit
  --run SCRIPT_PY:       execute SCRIPT_PY with `harness` bound to AppHarness

The BINARY argument is optional; if omitted, the binary is resolved from
picolet.toml the same way `picolet run` does.
"""

_TEST_EPILOG = """\
Examples:
  picolet test
  picolet test --screenshot home.png
  picolet test --screenshot home.png ./target/linux-x64/my-app
  picolet test --run tests/test_flow.py
  picolet test --run tests/test_flow.py -- --some-arg-for-the-binary
  picolet test --browser webkit --timeout 30 --verbose
"""


def add_parser(subparsers) -> None:
    """Register the test subcommand."""
    p = subparsers.add_parser(
        "test",
        help="launch app in test mode and drive it via the debug port",
        description=_TEST_DESCRIPTION,
        epilog=_TEST_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--target",
        default=None,
        metavar="TARGET",
        help="build target (default: host; supported: linux-x64, windows-x64)",
    )
    p.add_argument(
        "--no-build",
        action="store_true",
        default=False,
        dest="no_build",
        help="skip build freshness check; use existing binary",
    )
    p.add_argument(
        "--browser",
        choices=["webkit", "chromium", "auto"],
        default="auto",
        metavar="BROWSER",
        help="debug driver: webkit (Linux) / chromium (Windows) / auto (default)",
    )
    p.add_argument(
        "--screenshot",
        default=None,
        metavar="PATH",
        help="capture a screenshot to PATH (PNG) then exit",
    )
    p.add_argument(
        "--run",
        default=None,
        metavar="SCRIPT_PY",
        dest="run_script",
        help="execute SCRIPT_PY with 'harness' pre-bound in globals",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="seconds to wait for port announcement (default: 10)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="print extra diagnostic information",
    )
    p.add_argument(
        "binary",
        nargs="?",
        default=None,
        metavar="BINARY",
        help="path to the runtime binary (resolved from picolet.toml if omitted)",
    )
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="extra arguments forwarded to the binary (after --)",
    )
    p.set_defaults(func=run)


# ---------------------------------------------------------------------------
# Port-waiting helpers
# ---------------------------------------------------------------------------

_PORT_RE = re.compile(r"^picolet:test-port=(\d+)$")


def _wait_for_port(
    proc: subprocess.Popen,
    timeout: float,
    verbose: bool,
    scan_stdout: bool = False,
) -> int | None:
    """Read from proc's stderr (and optionally stdout) until a port announcement is seen.

    Returns the port number, or None on timeout.  Continues draining the pipe(s)
    in daemon threads after the port is found so the child doesn't block on a
    full pipe.

    scan_stdout: set True when xvfb-run is used.  xvfb-run redirects the child's
    stderr to its own stdout (``"$@" 2>&1`` in /usr/bin/xvfb-run line 184), so
    the port announcement arrives on proc.stdout, not proc.stderr.
    """
    port_found: list[int] = []
    done = threading.Event()

    def _make_reader(pipe, is_stderr: bool):
        def _reader():
            try:
                for raw in pipe:
                    line = raw.rstrip(b"\n\r").decode("utf-8", "replace")
                    m = _PORT_RE.match(line)
                    if m:
                        port_found.append(int(m.group(1)))
                        done.set()
                    if is_stderr:
                        # Forward stderr to ours so the caller sees it.
                        sys.stderr.write(line + "\n")
                        sys.stderr.flush()
                    if done.is_set():
                        for _ in pipe:
                            pass
                        break
            except Exception:
                pass
            finally:
                done.set()
        return _reader

    threads = []
    if proc.stderr is not None:
        t = threading.Thread(target=_make_reader(proc.stderr, is_stderr=True), daemon=True)
        t.start()
        threads.append(t)
    if scan_stdout and proc.stdout is not None:
        t = threading.Thread(target=_make_reader(proc.stdout, is_stderr=False), daemon=True)
        t.start()
        threads.append(t)

    done.wait(timeout=timeout)

    if port_found:
        return port_found[0]
    return None


def _resolve_binary(args) -> Path | None:
    """Find the binary: explicit arg, or inferred from picolet.toml."""
    if args.binary:
        p = Path(args.binary)
        if p.exists():
            return p
        print(f"error: binary not found: {p}", file=sys.stderr)
        return None

    # Fall back to picolet.toml resolution (same as run_cmd).
    try:
        from picolet.cli._paths import resolve_app
        _, _, _, binary_path = resolve_app(args)
        if binary_path.exists():
            return binary_path
        print(
            f"error: binary not found: {binary_path}\n"
            "Run `picolet build` to produce it, or pass the binary path directly.",
            file=sys.stderr,
        )
        return None
    except SystemExit:
        return None


def _resolve_browser(args, binary: Path) -> str:
    """Determine the effective browser driver from args + binary name + platform."""
    if args.browser != "auto":
        return args.browser
    name = binary.name
    if "lvgl" in name:
        return "lvgl"
    # webview binary: platform decides the engine.
    if sys.platform == "win32":
        return "chromium"
    return "webkit"


def _find_free_display() -> int:
    """Find a free X display number by checking the lock files in /tmp."""
    for n in range(99, 200):
        if not os.path.exists("/tmp/.X{}-lock".format(n)):
            return n
    return 99  # fallback


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """Send SIGTERM to the entire process group of *proc*, then wait.

    WebKitGTK spawns WebKitWebProcess and WebKitNetworkProcess as children of
    the runtime binary.  When the runtime exits (due to proc.terminate()), its
    children are re-parented to init and continue running — consuming CPU and
    X display resources.  Killing the process group ensures all descendants
    receive SIGTERM at once.

    Only signals the process group when proc is the group leader (pgid ==
    proc.pid), i.e. when proc was started with start_new_session=True.
    Otherwise falls back to proc.terminate() to avoid killing unrelated
    processes in the caller's own process group.
    """
    if proc.poll() is not None:
        return  # already exited

    try:
        pgid = os.getpgid(proc.pid)
        if pgid == proc.pid:
            # proc leads its own group — safe to kill the whole group.
            os.killpg(pgid, signal.SIGTERM)
        else:
            # proc shares its parent's process group — kill only proc.
            proc.terminate()
    except (OSError, ProcessLookupError):
        if proc.poll() is None:
            proc.terminate()

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
            if pgid == proc.pid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            proc.kill()
        proc.wait()


def _start_xvfb(display: int, verbose: bool = False) -> subprocess.Popen | None:
    """Start an Xvfb server on the given display number.

    Returns the Xvfb process if started, or None if Xvfb is not available.
    The caller is responsible for terminating the process.
    """
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        return None
    cmd = [xvfb, ":{}".format(display), "-screen", "0", "1280x800x24", "-nolisten", "tcp"]
    if verbose:
        sys.stderr.write("picolet test: starting Xvfb on display :{}\n".format(display))
    return subprocess.Popen(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


def _default_child_args(browser: str) -> list[str]:
    """Return a default '-c <code>' argument list for binaries with no romfs.

    When no romfs is present, the binary exits immediately without any Python
    code to run.  For test mode (--screenshot / --run) to work, the binary
    needs a '-c <code>' argument that starts the appropriate event loop.

    For webview binaries: opens a window, loads a minimal page, and pumps
    the GTK event loop until killed.
    For LVGL binaries: loads picolet._test command handlers and starts the
    stdio dispatcher loop.
    """
    if browser == "lvgl":
        code = (
            "import picolet._test; "
            "from picolet_ui._lvgl import LvglDisplay; "
            "import picolet._dispatcher as d; "
            "LvglDisplay(); "
            "d.run()"
        )
    else:
        # Webview path: open a window and pump the GTK event loop.
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


def _build_child_cmd(
    args, binary: Path, xvfb_display: int | None = None, browser: str = "webkit"
) -> tuple[list[str], bool]:
    """Build the subprocess argv list.  Start Xvfb manually if no DISPLAY is set.

    Returns (cmd, uses_xvfb) where uses_xvfb is True when an Xvfb display is
    being used.  Unlike xvfb-run, this approach starts Xvfb as a separate
    process (tracked by the caller), keeping the child's stdout/stderr pipes
    intact and accessible via proc.stdout/proc.stderr.

    When no extra args are passed and the binary has no romfs (determined by
    the absence of user-provided args), injects a default '-c <code>' argument
    that starts the appropriate event loop so test mode works out of the box.
    """
    forward = list(args.args or [])
    if forward and forward[0] == "--":
        forward = forward[1:]

    # Auto-inject a default '-c <startup>' when the caller passes no args.
    # Without this, binaries without a romfs exit immediately with no output.
    if not forward:
        forward = _default_child_args(browser)

    cmd = [str(binary)] + forward
    uses_xvfb = False

    # Chunk 6 — xvfb autodetect (FR-TEST-4, D7).
    # When no DISPLAY is set, start Xvfb separately (tracked by the caller)
    # instead of wrapping in xvfb-run.  xvfb-run redirects the child's stderr
    # to its own stdout (``"$@" 2>&1``), which breaks the port-announcement
    # pipe.  A manual Xvfb start lets us keep stdout/stderr separate.
    if sys.platform == "linux" and not os.environ.get("DISPLAY") and browser != "lvgl":
        if shutil.which("Xvfb"):
            uses_xvfb = True
            if args.verbose:
                sys.stderr.write(
                    "picolet test: no $DISPLAY, starting Xvfb on display :{}\n".format(
                        xvfb_display
                    )
                )
        elif shutil.which("xvfb-run"):
            # Fall back to xvfb-run if Xvfb is not directly available.
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24"] + cmd
            uses_xvfb = True
            if args.verbose:
                sys.stderr.write("picolet test: no $DISPLAY, wrapping in xvfb-run\n")
        else:
            print(
                "error: $DISPLAY is not set and Xvfb is not installed.\n"
                "Install xvfb with: apt install xvfb",
                file=sys.stderr,
            )
            sys.exit(1)

    return cmd, uses_xvfb


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(args) -> int:
    """Entry point for `picolet test`."""

    binary = _resolve_binary(args)
    if binary is None:
        return 1

    if not args.no_build:
        # Rebuild if needed (same freshness logic as run_cmd).
        try:
            from picolet.cli._paths import resolve_app, sources_newer_than
            from picolet.cli import build_cmd
            _, data, _, binary_path = resolve_app(args)
            if not binary_path.exists() or sources_newer_than(
                binary_path.parent.parent.parent / "picolet.toml", data, binary_path
            ):
                if args.verbose:
                    sys.stderr.write("binary out of date; running build first\n")
                rc = build_cmd.run(
                    build_cmd.build_args_namespace(args.target, args.verbose)
                )
                if rc != 0:
                    return rc
        except (SystemExit, Exception):
            pass  # no picolet.toml — user passed explicit binary

    browser = _resolve_browser(args, binary)

    # Validate: chromium driver requires a WebView2/Chromium binary.
    if browser == "chromium" and sys.platform == "linux" and "webview" in binary.name:
        print(
            "error: --browser chromium is not supported against a WebKitGTK binary "
            "on Linux.  WebKitGTK uses the WebKit Inspector Protocol, not CDP.\n"
            "Use --browser webkit (default on Linux) or rebuild for Windows.",
            file=sys.stderr,
        )
        return 2

    # Spawn the child with PICOLET_TEST_MODE=1.
    child_env = dict(os.environ)
    child_env["PICOLET_TEST_MODE"] = "1"

    # LVGL binaries use SDL2.  On WSL2 with a Wayland compositor (WSLg),
    # WAYLAND_DISPLAY is set and SDL2 connects to it.  If the compositor is
    # busy rendering WebKitGTK output from a prior test gate, SDL2 can block
    # indefinitely in wl_display_connect / poll(), preventing the stdio
    # dispatcher from responding to pings.  Force SDL2 offscreen rendering to
    # avoid any display server dependency for LVGL tests.
    if browser == "lvgl" and sys.platform == "linux":
        child_env["SDL_VIDEODRIVER"] = "offscreen"
        child_env.pop("WAYLAND_DISPLAY", None)
        child_env.pop("DISPLAY", None)

    # Start Xvfb manually if no display is available (Linux headless).
    # Unlike xvfb-run, starting Xvfb as a separate process keeps the child's
    # stdout/stderr pipes intact — xvfb-run's ``"$@" 2>&1`` redirect breaks them.
    xvfb_proc: subprocess.Popen | None = None
    xvfb_display: int | None = None
    if sys.platform == "linux" and not os.environ.get("DISPLAY") and browser != "lvgl":
        if shutil.which("Xvfb"):
            xvfb_display = _find_free_display()
            xvfb_proc = _start_xvfb(xvfb_display, verbose=args.verbose)
            if xvfb_proc is not None:
                # Give Xvfb a moment to start and create the socket.
                # 0.1 s is sufficient; Xvfb binds its socket within a few ms.
                import time
                time.sleep(0.1)
                child_env["DISPLAY"] = ":{}".format(xvfb_display)
                # Force GDK to use the X11 backend.  On systems where
                # WAYLAND_DISPLAY is set (e.g. WSL2 with a Wayland compositor),
                # GTK4 prefers Wayland even when DISPLAY is set.  Unsetting
                # WAYLAND_DISPLAY and setting GDK_BACKEND=x11 ensures GTK
                # connects to the Xvfb display instead.
                child_env["GDK_BACKEND"] = "x11"
                child_env.pop("WAYLAND_DISPLAY", None)
                if args.verbose:
                    sys.stderr.write(
                        "picolet test: Xvfb started on DISPLAY=:{} (pid={})\n".format(
                            xvfb_display, xvfb_proc.pid
                        )
                    )
        elif not shutil.which("xvfb-run"):
            print(
                "error: $DISPLAY is not set and Xvfb is not installed.\n"
                "Install xvfb with: apt install xvfb",
                file=sys.stderr,
            )
            return 1

    cmd, uses_xvfb = _build_child_cmd(args, binary, xvfb_display=xvfb_display, browser=browser)
    if args.verbose:
        sys.stderr.write("picolet test: spawn: {}\n".format(" ".join(cmd)))

    # BUG-D fix: LVGL binaries use stdio as the transport, not an inspector port.
    # Open stdin+stdout pipes for the LVGL path so the AppHarness can write JSON
    # commands and read JSON replies.
    #
    # For webview paths: pipe stderr always.  When using manual Xvfb (xvfb_proc),
    # the child's stderr and stdout are separate — no redirect needed.
    # When falling back to xvfb-run (uses_xvfb but no xvfb_proc), xvfb-run
    # redirects the child's stderr to stdout, so we also pipe stdout.
    if browser == "lvgl":
        proc = subprocess.Popen(
            cmd,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # LVGL transport readiness: ping the dispatcher and wait for 'pong'.
        # The runtime needs a moment to start the event loop and import picolet._test.
        port = None  # LVGL has no inspector port — sentinel None is fine.
        # AppHarness will handle the LVGL-specific initialization.
    else:
        # When using xvfb-run (fallback), pipe stdout too so we can scan it for
        # the port announcement (xvfb-run does ``"$@" 2>&1``).
        # When using manual Xvfb (xvfb_proc), stdout is NOT redirected.
        xvfbrun_fallback = uses_xvfb and xvfb_proc is None
        popen_kwargs: dict = {"env": child_env, "stderr": subprocess.PIPE}
        if xvfbrun_fallback:
            popen_kwargs["stdout"] = subprocess.PIPE
        # On Linux, start the runtime in a new session so it leads its own
        # process group.  WebKitGTK spawns WebKitWebProcess / WebKitNetworkProcess
        # as children; without a separate group those children are re-parented to
        # init when the runtime exits and keep running, exhausting CPU and X
        # display resources across successive test gates.  _kill_proc_group() then
        # sends SIGTERM to the entire group, not just the runtime PID.
        if sys.platform == "linux":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
        port = _wait_for_port(
            proc,
            timeout=args.timeout,
            verbose=args.verbose,
            scan_stdout=xvfbrun_fallback,
        )

        if port is None:
            print(
                "error: timed out waiting for 'picolet:test-port=<N>' on stderr "
                "({}s).  Is PICOLET_TEST_MODE=1 handled by this binary?".format(args.timeout),
                file=sys.stderr,
            )
            _kill_proc_group(proc)
            if xvfb_proc is not None:
                xvfb_proc.terminate()
            return 1

    if args.verbose:
        sys.stderr.write(
            "picolet test: connected browser={} port={} binary={}\n".format(
                browser, port, binary
            )
        )

    # ---- bare mode (no screenshot / no run) --------------------------------
    if args.screenshot is None and args.run_script is None:
        print("connected browser={} port={} binary={}".format(browser, port, binary))
        _kill_proc_group(proc)
        if xvfb_proc is not None:
            xvfb_proc.terminate()
        return 0

    # ---- screenshot / run modes — use AppHarness --------------------------
    try:
        from picolet.testing import AppHarness
    except ImportError:
        print(
            "error: picolet.testing not installed.\n"
            "Install picolet-testing: pip install picolet-testing (or use `uv`)",
            file=sys.stderr,
        )
        _kill_proc_group(proc)
        if xvfb_proc is not None:
            xvfb_proc.terminate()
        return 1

    async def _async_main():
        harness = AppHarness(
            binary=str(binary),
            browser=browser,
            timeout=args.timeout,
            _running_proc=proc,     # pass the already-running process
            _port=port,             # pass the already-known port
            _xvfb_display=xvfb_display,  # pass display for xwd screenshot
        )
        try:
            await harness.start()
        except Exception as exc:
            print("error: AppHarness.start() failed: {}".format(exc), file=sys.stderr)
            return 1

        try:
            if args.screenshot:
                await harness.screenshot(args.screenshot)
                if args.verbose:
                    sys.stderr.write("picolet test: screenshot saved to {}\n".format(
                        args.screenshot
                    ))
                return 0

            if args.run_script:
                script_path = Path(args.run_script)
                try:
                    script_src = script_path.read_text()
                except OSError as e:
                    print("error: cannot read script {}: {}".format(script_path, e),
                          file=sys.stderr)
                    return 1
                g = {"harness": harness, "__file__": str(script_path)}
                try:
                    exec(compile(script_src, str(script_path), "exec"), g)  # noqa: S102
                    return 0
                except SystemExit as e:
                    return int(e.code) if e.code is not None else 0
                except Exception as e:
                    print("error: script raised: {}".format(e), file=sys.stderr)
                    return 1
        finally:
            await harness.stop()

    rc = asyncio.run(_async_main())
    # AppHarness.stop() does not terminate the proc when _running_proc was
    # pre-supplied (_owns_proc=False).  Kill the entire process group so
    # WebKitWebProcess / WebKitNetworkProcess children are also reaped.
    _kill_proc_group(proc)
    if xvfb_proc is not None:
        xvfb_proc.terminate()
        try:
            xvfb_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            xvfb_proc.kill()
    return rc if rc is not None else 0
