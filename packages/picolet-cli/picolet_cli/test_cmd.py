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
import subprocess
import sys
import threading
from pathlib import Path


def add_parser(subparsers) -> None:
    """Register the test subcommand."""
    p = subparsers.add_parser(
        "test",
        help="launch app in test mode and drive it via the debug port",
        description=(
            "Spawn the app with PICOLET_TEST_MODE=1, wait for the inspector "
            "port announcement on stderr, then optionally screenshot or run "
            "a test script.  See picolet test --help for full usage."
        ),
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


def _wait_for_port(proc: subprocess.Popen, timeout: float, verbose: bool) -> int | None:
    """Read stderr lines from proc until a port announcement is seen.

    Returns the port number, or None on timeout.  Continues draining stderr
    in a daemon thread after the port is found so the child doesn't block on
    a full pipe.
    """
    port_found: list[int] = []
    done = threading.Event()

    def _reader():
        try:
            for raw in proc.stderr:
                line = raw.rstrip(b"\n\r").decode("utf-8", "replace")
                m = _PORT_RE.match(line)
                if m:
                    port_found.append(int(m.group(1)))
                    done.set()
                # Always forward stderr to ours.
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
                if done.is_set():
                    # Keep draining (don't block the child's pipe) but
                    # stop forwarding to avoid spam in test output.
                    for leftover in proc.stderr:
                        pass
                    break
        except Exception:
            pass
        finally:
            done.set()  # unblock even on error

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
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
        from picolet_cli._paths import resolve_app
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


def _build_child_cmd(args, binary: Path) -> list[str]:
    """Build the subprocess argv list, prepending xvfb-run when needed."""
    forward = list(args.args or [])
    if forward and forward[0] == "--":
        forward = forward[1:]

    cmd = [str(binary)] + forward

    # Chunk 6 — xvfb autodetect (FR-TEST-4, D7).
    if sys.platform == "linux" and not os.environ.get("DISPLAY"):
        if shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "-e", "/dev/stderr"] + cmd
            if args.verbose:
                sys.stderr.write("picolet test: no $DISPLAY, wrapping in xvfb-run\n")
        else:
            print(
                "error: $DISPLAY is not set and xvfb-run is not installed.\n"
                "Install xvfb with: apt install xvfb",
                file=sys.stderr,
            )
            sys.exit(1)

    return cmd


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
            from picolet_cli._paths import resolve_app, sources_newer_than
            from picolet_cli import build_cmd
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

    cmd = _build_child_cmd(args, binary)
    if args.verbose:
        sys.stderr.write("picolet test: spawn: {}\n".format(" ".join(cmd)))

    # BUG-D fix: LVGL binaries use stdio as the transport, not an inspector port.
    # Open stdin+stdout pipes for the LVGL path so the AppHarness can write JSON
    # commands and read JSON replies.  For webview paths, stdout is inherited and
    # only stderr is piped (for the port announcement).
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
        proc = subprocess.Popen(
            cmd,
            env=child_env,
            stderr=subprocess.PIPE,
            # stdout inherited — let the app's own stdout reach the terminal.
        )
        port = _wait_for_port(proc, timeout=args.timeout, verbose=args.verbose)

        if port is None:
            print(
                "error: timed out waiting for 'picolet:test-port=<N>' on stderr "
                "({}s).  Is PICOLET_TEST_MODE=1 handled by this binary?".format(args.timeout),
                file=sys.stderr,
            )
            proc.terminate()
            proc.wait()
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
        proc.terminate()
        proc.wait()
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
        proc.terminate()
        proc.wait()
        return 1

    async def _async_main():
        harness = AppHarness(
            binary=str(binary),
            browser=browser,
            timeout=args.timeout,
            _running_proc=proc,   # pass the already-running process
            _port=port,           # pass the already-known port
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
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return rc if rc is not None else 0
