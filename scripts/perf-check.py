#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
perf-check.py — measure NFR-EX-2 and NFR-TEST-1 startup latency for Picolet apps.

NFR-EX-2  : spawn → window visible + first interactive frame  ≤ 1500 ms (median)
NFR-TEST-1 : picolet test --screenshot spawn → port announcement ≤ 3000 ms (median)

Both gates use 5 timed runs; the median is compared against the bound.
If the median exceeds the bound the exit code is non-zero.
If the median is within the bound but any individual run exceeds 2× the bound,
a soft warning is emitted (the gate still passes on that single run).

AppHarness records spawn_ms immediately after Popen(). Linux uses ready_ms
when the port appears; macOS stamps completion of AppHarness's port wait.
The macOS window gate completes after System Events reports a visible native
window and WKRemoteInspector returns a PNG frame.

Usage:
    uv run --project packages/picolet python scripts/perf-check.py --help
    uv run --project packages/picolet python scripts/perf-check.py \\
        --binary packages/picolet-runtime/build/picolet-runtime-linux-x64-webview \\
        --example examples/notes \\
        --example examples/pydfu \\
        --runs 5 \\
        --output perf-results.json

Linux uses Xvfb + xdotool. macOS uses AppHarness for port timing, checks
native window visibility with System Events, then captures a WKWebView
screenshot to confirm the first frame was painted.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from statistics import median
from typing import Any

# ---------------------------------------------------------------------------
# Resolve AppHarness from the monorepo tree without requiring an install.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_TESTING_PKG = _REPO_ROOT / "packages" / "picolet"
if str(_TESTING_PKG) not in sys.path:
    sys.path.insert(0, str(_TESTING_PKG))

# AppHarness is imported lazily so --help does not require the package import.

# ---------------------------------------------------------------------------
# NFR bounds
# ---------------------------------------------------------------------------

NFR_EX2_MEDIAN_MS = 1500       # median spawn→window-visible cap
NFR_TEST1_MEDIAN_MS = 3000     # median spawn→port-announcement cap
SOFT_WARN_MULTIPLIER = 2.0     # single-run soft-warn threshold (2× bound)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_xvfb() -> str | None:
    """Return path to Xvfb binary, or None if unavailable."""
    return shutil.which("Xvfb")


def _find_free_display() -> int:
    for n in range(99, 200):
        if not os.path.exists(f"/tmp/.X{n}-lock"):
            return n
    return 99


def _start_xvfb(display: int) -> subprocess.Popen:
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        raise RuntimeError("Xvfb not found in PATH")
    cmd = [xvfb, f":{display}", "-screen", "0", "1280x800x24", "-nolisten", "tcp"]
    return subprocess.Popen(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


def _build_child_env(display: int) -> dict[str, str]:
    env = dict(os.environ)
    env["PICOLET_TEST_MODE"] = "1"
    env["DISPLAY"] = f":{display}"
    env["GDK_BACKEND"] = "x11"
    env.pop("WAYLAND_DISPLAY", None)
    return env


def _build_macos_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PICOLET_TEST_MODE"] = "1"
    return env


# ---------------------------------------------------------------------------
# NFR-TEST-1: spawn → port-announcement timing (via AppHarness)
# ---------------------------------------------------------------------------

async def _measure_test1_run(
    binary: Path,
    app_dir: Path,
    env: dict[str, str],
    xvfb_display: int,
) -> float:
    """
    Single timed run for NFR-TEST-1.

    Uses AppHarness.start() which spawns the binary, drains stderr via a
    daemon thread (no blocking read loop), and records spawn_ms / ready_ms.
    On the Linux/webkit path with an explicit _xvfb_display, AppHarness sets
    page=None and returns as soon as the port line is seen — so
    (ready_ms - spawn_ms) captures spawn → port-announcement elapsed time.

    Returns elapsed milliseconds.
    """
    from picolet.testing._harness import AppHarness

    harness = AppHarness(
        binary,
        args=("-c", "import main"),
        browser="webkit",
        env=env,
        timeout=10.0,
        _xvfb_display=xvfb_display,
    )
    try:
        await harness.start(cwd=str(app_dir))
    finally:
        await harness.stop()

    if harness.spawn_ms is None or harness.ready_ms is None:
        raise RuntimeError(
            f"NFR-TEST-1: AppHarness did not set timing attributes for {app_dir}"
        )
    return harness.ready_ms - harness.spawn_ms


async def _measure_macos_test1_run(
    binary: Path,
    app_dir: Path,
    env: dict[str, str],
) -> float:
    """Measure native macOS spawn → the port line observed by AppHarness."""
    from picolet.testing._harness import AppHarness

    harness = AppHarness(
        binary, args=("-c", "import main"), browser="webkit",
        env=env, timeout=10.0, _cwd=app_dir,
    )
    try:
        harness._proc = harness._spawn()
        port = await harness._wait_for_port()
        port_seen_ms = time.time() * 1000.0
        if port is None:
            raise RuntimeError(
                f"NFR-TEST-1: AppHarness timed out waiting for the inspector port for {app_dir}"
            )
    finally:
        await harness.stop()
    if harness.spawn_ms is None:
        raise RuntimeError(
            f"NFR-TEST-1: AppHarness did not record spawn time for {app_dir}"
        )
    return port_seen_ms - harness.spawn_ms

def measure_test1(
    binary: Path,
    app_dir: Path,
    xvfb_display: int,
    runs: int,
) -> dict[str, Any]:
    """Run NFR-TEST-1 measurement `runs` times and return a result dict."""
    env = _build_child_env(xvfb_display)
    return _measure_test1_runs(binary, app_dir, runs, lambda: asyncio.run(
        _measure_test1_run(binary, app_dir, env, xvfb_display)
    ))


def measure_macos_test1(
    binary: Path,
    app_dir: Path,
    runs: int,
) -> dict[str, Any]:
    """Run native macOS NFR-TEST-1 measurements."""
    env = _build_macos_env()
    return _measure_test1_runs(binary, app_dir, runs, lambda: asyncio.run(
        _measure_macos_test1_run(binary, app_dir, env)
    ))


def _measure_test1_runs(
    binary: Path,
    app_dir: Path,
    runs: int,
    run_once,
) -> dict[str, Any]:
    samples: list[float] = []
    errors: list[str] = []
    for i in range(runs):
        try:
            ms = run_once()
            samples.append(ms)
            print(f"  [NFR-TEST-1] run {i + 1}/{runs}: {ms:.0f} ms")
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            print(f"  [NFR-TEST-1] run {i + 1}/{runs}: ERROR: {exc}")
    return _measurement_result(
        "NFR-TEST-1", binary, app_dir, runs, samples, errors, NFR_TEST1_MEDIAN_MS
    )


def _measurement_result(
    nfr: str,
    binary: Path,
    app_dir: Path,
    runs: int,
    samples: list[float],
    errors: list[str],
    bound_ms: int,
) -> dict[str, Any]:
    if not samples:
        return {
            "nfr": nfr,
            "example": str(app_dir),
            "pass": False,
            "error": f"All {runs} runs failed: {'; '.join(errors)}",
        }
    med = median(samples)
    soft_warns = [s for s in samples if s > bound_ms * SOFT_WARN_MULTIPLIER]
    result: dict[str, Any] = {
        "nfr": nfr,
        "example": str(app_dir),
        "binary": str(binary),
        "runs": runs,
        "samples_ms": [round(s, 1) for s in samples],
        "median_ms": round(med, 1),
        "max_ms": round(max(samples), 1),
        "bound_ms": bound_ms,
        "pass": med <= bound_ms and not errors,
    }
    if errors:
        result["errors"] = errors
    if soft_warns:
        result["soft_warn"] = (
            f"{len(soft_warns)} run(s) exceeded {SOFT_WARN_MULTIPLIER:.0f}× bound "
            f"({bound_ms * SOFT_WARN_MULTIPLIER:.0f} ms); "
            "if this persists for 3 consecutive CI runs, escalate."
        )
    return result


# ---------------------------------------------------------------------------
# NFR-EX-2: spawn → window-visible + first frame
# ---------------------------------------------------------------------------

async def _measure_macos_ex2_run(
    binary: Path,
    app_dir: Path,
    env: dict[str, str],
) -> float:
    """Measure through native window visibility and a captured WKWebView frame."""
    from picolet.testing._harness import AppHarness

    harness = AppHarness(
        binary, args=("-c", "import main"), browser="webkit", env=env, timeout=10.0,
    )
    try:
        await harness.start(cwd=str(app_dir))
        if harness.spawn_ms is None or harness._proc is None:
            raise RuntimeError(f"NFR-EX-2: AppHarness did not spawn {app_dir}")
        if harness.page is None:
            raise RuntimeError(
                f"NFR-EX-2: AppHarness did not attach the macOS WebKit inspector for {app_dir}"
            )

        child_pid = harness._proc.pid
        applescript = (
            'on run argv\n'
            '  tell application "System Events"\n'
            '    try\n'
            '      set appProc to first process whose unix id is (item 1 of argv as integer)\n'
            '    on error\n'
            '      return "hidden"\n'
            '    end try\n'
            '    tell appProc\n'
            '      if visible and (count of windows) > 0 and visible of window 1 then return "visible"\n'
            '    end tell\n'
            '  end tell\n'
            '  return "hidden"\n'
            'end run'
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if harness._proc.poll() is not None:
                raise RuntimeError(
                    f"NFR-EX-2: app exited before its native window appeared ({app_dir})"
                )
            check = await asyncio.to_thread(
                subprocess.run,
                ["osascript", "-e", applescript, str(child_pid)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if check.returncode == 0 and check.stdout.strip() == "visible":
                break
            if check.returncode != 0:
                raise RuntimeError(
                    "NFR-EX-2: System Events could not inspect the native window: "
                    + check.stderr.strip()
                )
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError(
                f"NFR-EX-2: native window not visible within 10 s for {app_dir}"
            )

        # WKRemoteInspector captures the rendered WebView pixels. This only
        # succeeds once the first frame exists, rather than treating a live
        # process or a port announcement as paint readiness.
        png = await harness.page.screenshot()
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"NFR-EX-2: WKWebView returned no PNG frame for {app_dir}")
        return time.time() * 1000.0 - harness.spawn_ms
    finally:
        await harness.stop()


def measure_macos_ex2(
    binary: Path,
    app_dir: Path,
    runs: int,
) -> dict[str, Any]:
    """Run native macOS NFR-EX-2 measurements."""
    env = _build_macos_env()
    return _measure_ex2_runs(
        binary,
        app_dir,
        runs,
        lambda: asyncio.run(_measure_macos_ex2_run(binary, app_dir, env)),
    )


# ---------------------------------------------------------------------------
# Linux NFR-EX-2 implementation remains Xvfb + xdotool.
# ---------------------------------------------------------------------------

async def _measure_ex2_run(
    binary: Path,
    app_dir: Path,
    env: dict[str, str],
    xvfb_display: int,
) -> float:
    """
    Single timed run for NFR-EX-2.

    Spawn the embedded app and use xdotool's PID-filtered visible-window
    search on the assigned Xvfb display. Inspector readiness is measured
    independently by NFR-TEST-1, it is not a prerequisite for visibility.

    Returns elapsed milliseconds from spawn_ms to xdotool return.
    """
    from picolet.testing._harness import AppHarness

    harness = AppHarness(
        binary,
        args=("-c", "import main"),
        browser="webkit",
        env=env,
        timeout=10.0,
        _xvfb_display=xvfb_display,
        _cwd=app_dir,
    )

    try:
        harness._proc = harness._spawn()
        if harness.spawn_ms is None:
            raise RuntimeError(f"NFR-EX-2: no spawn timestamp for {app_dir}")
        child_pid = harness._proc.pid if harness._proc is not None else None
        xdotool = shutil.which("xdotool")
        if not xdotool or child_pid is None:
            raise RuntimeError("NFR-EX-2: xdotool and a running child are required")
        await asyncio.to_thread(
            subprocess.run,
            [xdotool, "search", "--sync", "--onlyvisible", "--all",
             "--pid", str(child_pid), ""],
            env=env,
            timeout=5,
            capture_output=True,
            check=True,
        )
        return (time.time() * 1000.0) - harness.spawn_ms
    finally:
        await harness.stop()


def _measure_ex2_runs(
    binary: Path,
    app_dir: Path,
    runs: int,
    run_once,
) -> dict[str, Any]:
    samples: list[float] = []
    errors: list[str] = []
    for i in range(runs):
        try:
            ms = run_once()
            samples.append(ms)
            print(f"  [NFR-EX-2]   run {i + 1}/{runs}: {ms:.0f} ms")
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            print(f"  [NFR-EX-2]   run {i + 1}/{runs}: ERROR: {exc}")
    return _measurement_result(
        "NFR-EX-2", binary, app_dir, runs, samples, errors, NFR_EX2_MEDIAN_MS
    )


def measure_ex2(
    binary: Path,
    app_dir: Path,
    xvfb_display: int,
    runs: int,
) -> dict[str, Any]:
    """Run Linux NFR-EX-2 measurement `runs` times and return a result dict."""
    env = _build_child_env(xvfb_display)
    return _measure_ex2_runs(
        binary,
        app_dir,
        runs,
        lambda: asyncio.run(_measure_ex2_run(binary, app_dir, env, xvfb_display)),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Measure NFR-EX-2 (startup ≤1500 ms) and NFR-TEST-1 "
            "(test-port ≤3000 ms) for Picolet example apps."
        )
    )
    p.add_argument(
        "--binary",
        required=True,
        action="append",
        metavar="PATH",
        help=(
            "path to the platform-specific webview runtime binary; repeat once "
            "per example to measure distinct built apps"
        ),
    )
    p.add_argument(
        "--example",
        action="append",
        metavar="DIR",
        dest="examples",
        default=[],
        help="path to an example app directory (repeat for multiple apps)",
    )
    p.add_argument(
        "--runs",
        type=int,
        default=5,
        metavar="N",
        help="number of timed runs per example per NFR (default: 5)",
    )
    p.add_argument(
        "--output",
        metavar="JSON",
        default=None,
        help="write full result JSON to this file",
    )
    p.add_argument(
        "--skip-ex2",
        action="store_true",
        default=False,
        help="skip NFR-EX-2 measurements (window-visible timing)",
    )
    p.add_argument(
        "--skip-test1",
        action="store_true",
        default=False,
        help="skip NFR-TEST-1 measurements (port-announcement timing)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    binaries = [Path(path).resolve() for path in args.binary]
    for binary in binaries:
        if not binary.exists():
            print(f"error: binary not found: {binary}", file=sys.stderr)
            return 1

    examples: list[Path] = []
    for ex in args.examples:
        p = Path(ex)
        if not p.is_dir():
            print(f"error: example directory not found: {p}", file=sys.stderr)
            return 1
        examples.append(p)
    if not examples:
        print("error: specify at least one --example directory", file=sys.stderr)
        return 1
    if len(binaries) not in (1, len(examples)):
        print(
            "error: specify one --binary for all examples or one per --example",
            file=sys.stderr,
        )
        return 1

    if args.runs < 1:
        print("error: --runs must be at least 1", file=sys.stderr)
        return 1

    if sys.platform == "linux":
        xvfb_bin = _require_xvfb()
        if not xvfb_bin and not os.environ.get("DISPLAY"):
            print(
                "error: Xvfb not found and $DISPLAY is unset. "
                "Install xvfb (apt install xvfb) or set $DISPLAY.",
                file=sys.stderr,
            )
            return 1
        xvfb_proc: subprocess.Popen | None = None
        if os.environ.get("DISPLAY"):
            m = re.match(r":(\d+)", os.environ["DISPLAY"])
            xvfb_display = int(m.group(1)) if m else 0
            print(f"perf-check: using existing DISPLAY={os.environ['DISPLAY']}")
        else:
            xvfb_display = _find_free_display()
            print(f"perf-check: starting Xvfb on display :{xvfb_display}")
            xvfb_proc = _start_xvfb(xvfb_display)
            time.sleep(0.3)
    elif sys.platform == "darwin":
        xvfb_display = None
        xvfb_proc = None
        for command in ("osascript",):
            if not shutil.which(command):
                print(f"error: required macOS command not found: {command}", file=sys.stderr)
                return 1
    else:
        print(f"error: unsupported platform for perf-check: {sys.platform}", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        for index, example in enumerate(examples):
            binary = binaries[index] if len(binaries) > 1 else binaries[0]
            print(f"\n=== {example.name} ===")
            if not args.skip_test1:
                r1 = (
                    measure_macos_test1(binary, example, args.runs)
                    if sys.platform == "darwin"
                    else measure_test1(binary, example, xvfb_display, args.runs)
                )
                results.append(r1)
                status = "PASS" if r1["pass"] else "FAIL"
                print(
                    f"  NFR-TEST-1 {status}: median={r1.get('median_ms', 'N/A')} ms "
                    f"(bound={NFR_TEST1_MEDIAN_MS} ms)"
                )
                if not r1["pass"]:
                    detail = r1.get("error") or (
                        f"median {r1.get('median_ms', 'N/A')} ms exceeds bound"
                    )
                    failures.append(f"NFR-TEST-1 FAILED for {example.name}: {detail}")
                if r1.get("soft_warn"):
                    print(f"  WARNING: {r1['soft_warn']}")
            if not args.skip_ex2:
                r2 = (
                    measure_macos_ex2(binary, example, args.runs)
                    if sys.platform == "darwin"
                    else measure_ex2(binary, example, xvfb_display, args.runs)
                )
                results.append(r2)
                status = "PASS" if r2["pass"] else "FAIL"
                print(
                    f"  NFR-EX-2   {status}: median={r2.get('median_ms', 'N/A')} ms "
                    f"(bound={NFR_EX2_MEDIAN_MS} ms)"
                )
                if not r2["pass"]:
                    detail = r2.get("error") or (
                        f"median {r2.get('median_ms', 'N/A')} ms exceeds bound"
                    )
                    failures.append(f"NFR-EX-2 FAILED for {example.name}: {detail}")
                if r2.get("soft_warn"):
                    print(f"  WARNING: {r2['soft_warn']}")
    finally:
        if xvfb_proc is not None and xvfb_proc.poll() is None:
            xvfb_proc.terminate()
            try:
                xvfb_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb_proc.kill()

    output_data = {
        "runs_per_nfr": args.runs,
        "results": results,
        "failures": failures,
    }
    if len(binaries) == 1:
        output_data["binary"] = str(binaries[0])
    else:
        output_data["binaries"] = [str(path) for path in binaries]
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(output_data, indent=2))
        print(f"\nResults written to {out_path}")
    print()
    if failures:
        print("PERF-CHECK FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("PERF-CHECK PASSED: all NFR bounds met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
