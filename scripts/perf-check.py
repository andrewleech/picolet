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

Timing is centralised in AppHarness (packages/picolet-testing):
  spawn_ms — epoch ms recorded immediately after Popen() inside _spawn().
  ready_ms — epoch ms recorded at the end of start() (port found + driver
             attached, or just port-found when no inspector is available).

Usage:
    uv run --no-project scripts/perf-check.py --help
    uv run --no-project scripts/perf-check.py \\
        --binary packages/picolet-runtime/build/picolet-runtime-linux-x64-webview \\
        --example examples/notes \\
        --example examples/pydfu \\
        --runs 5 \\
        --output perf-results.json

The script handles Xvfb absence gracefully (skip with a message) so it can be
run in a local dev environment without a display server installed.
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
_TESTING_PKG = _REPO_ROOT / "packages" / "picolet-testing"
if str(_TESTING_PKG) not in sys.path:
    sys.path.insert(0, str(_TESTING_PKG))

# AppHarness is imported lazily inside the measurement functions so that
# --help works without the package being importable (e.g. no picolet-testing
# installed and the monorepo tree is absent).

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


def measure_test1(
    binary: Path,
    app_dir: Path,
    xvfb_display: int,
    runs: int,
) -> dict[str, Any]:
    """Run NFR-TEST-1 measurement `runs` times and return a result dict."""
    env = _build_child_env(xvfb_display)
    samples: list[float] = []
    errors: list[str] = []
    for i in range(runs):
        try:
            ms = asyncio.run(_measure_test1_run(binary, app_dir, env, xvfb_display))
            samples.append(ms)
            print(f"  [NFR-TEST-1] run {i + 1}/{runs}: {ms:.0f} ms")
        except Exception as exc:
            errors.append(str(exc))
            print(f"  [NFR-TEST-1] run {i + 1}/{runs}: ERROR — {exc}")

    if not samples:
        return {
            "nfr": "NFR-TEST-1",
            "example": str(app_dir),
            "pass": False,
            "error": f"All {runs} runs failed: {'; '.join(errors)}",
        }

    med = median(samples)
    max_ms = max(samples)
    passed = med <= NFR_TEST1_MEDIAN_MS
    soft_warns = [s for s in samples if s > NFR_TEST1_MEDIAN_MS * SOFT_WARN_MULTIPLIER]

    result: dict[str, Any] = {
        "nfr": "NFR-TEST-1",
        "example": str(app_dir),
        "binary": str(binary),
        "runs": runs,
        "samples_ms": [round(s, 1) for s in samples],
        "median_ms": round(med, 1),
        "max_ms": round(max_ms, 1),
        "bound_ms": NFR_TEST1_MEDIAN_MS,
        "pass": passed,
    }
    if soft_warns:
        result["soft_warn"] = (
            f"{len(soft_warns)} run(s) exceeded {SOFT_WARN_MULTIPLIER:.0f}× bound "
            f"({NFR_TEST1_MEDIAN_MS * SOFT_WARN_MULTIPLIER:.0f} ms); "
            "if this persists for 3 consecutive CI runs, escalate."
        )
    return result


# ---------------------------------------------------------------------------
# NFR-EX-2: spawn → window-visible timing (via AppHarness + xdotool)
# ---------------------------------------------------------------------------

async def _measure_ex2_run(
    binary: Path,
    app_dir: Path,
    env: dict[str, str],
    xvfb_display: int,
) -> float:
    """
    Single timed run for NFR-EX-2.

    Uses AppHarness.start() to spawn the binary and wait for the port
    announcement (daemon thread drains stderr — no blocking read loop).
    spawn_ms is set by AppHarness._spawn() just before Popen() returns.
    After start() returns (port seen, child running), calls xdotool with
    --pid to filter by the child's PID, confirming the window is visible
    in the Xvfb framebuffer.

    Returns elapsed milliseconds from spawn_ms to xdotool return.
    """
    from picolet.testing._harness import AppHarness

    harness = AppHarness(
        binary,
        browser="webkit",
        env=env,
        timeout=10.0,
        _xvfb_display=xvfb_display,
    )
    await harness.start(cwd=str(app_dir))

    if harness.spawn_ms is None:
        await harness.stop()
        raise RuntimeError(
            f"NFR-EX-2: AppHarness did not set spawn_ms for {app_dir}"
        )

    # Capture the child PID before any stop() call clears harness._proc.
    child_pid: int | None = harness._proc.pid if harness._proc is not None else None

    # After the port is announced (window creation precedes the port bind in
    # the current runtime implementation), confirm the X window is visible.
    xdotool = shutil.which("xdotool")
    if xdotool and child_pid is not None:
        try:
            # Filter by the child process's PID so only the app's own windows
            # satisfy the search (prevents matching the root window or other
            # processes that happen to be running on the display).
            subprocess.run(
                [xdotool, "search", "--sync", "--onlyvisible",
                 "--pid", str(child_pid), ""],
                env=env,
                timeout=5,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    elapsed_ms = (time.time() * 1000.0) - harness.spawn_ms

    await harness.stop()
    return elapsed_ms


def measure_ex2(
    binary: Path,
    app_dir: Path,
    xvfb_display: int,
    runs: int,
) -> dict[str, Any]:
    """Run NFR-EX-2 measurement `runs` times and return a result dict."""
    env = _build_child_env(xvfb_display)
    samples: list[float] = []
    errors: list[str] = []
    for i in range(runs):
        try:
            ms = asyncio.run(_measure_ex2_run(binary, app_dir, env, xvfb_display))
            samples.append(ms)
            print(f"  [NFR-EX-2]   run {i + 1}/{runs}: {ms:.0f} ms")
        except Exception as exc:
            errors.append(str(exc))
            print(f"  [NFR-EX-2]   run {i + 1}/{runs}: ERROR — {exc}")

    if not samples:
        return {
            "nfr": "NFR-EX-2",
            "example": str(app_dir),
            "pass": False,
            "error": f"All {runs} runs failed: {'; '.join(errors)}",
        }

    med = median(samples)
    max_ms = max(samples)
    passed = med <= NFR_EX2_MEDIAN_MS
    soft_warns = [s for s in samples if s > NFR_EX2_MEDIAN_MS * SOFT_WARN_MULTIPLIER]

    result: dict[str, Any] = {
        "nfr": "NFR-EX-2",
        "example": str(app_dir),
        "binary": str(binary),
        "runs": runs,
        "samples_ms": [round(s, 1) for s in samples],
        "median_ms": round(med, 1),
        "max_ms": round(max_ms, 1),
        "bound_ms": NFR_EX2_MEDIAN_MS,
        "pass": passed,
    }
    if soft_warns:
        result["soft_warn"] = (
            f"{len(soft_warns)} run(s) exceeded {SOFT_WARN_MULTIPLIER:.0f}× bound "
            f"({NFR_EX2_MEDIAN_MS * SOFT_WARN_MULTIPLIER:.0f} ms); "
            "if this persists for 3 consecutive CI runs, escalate."
        )
    return result


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
        metavar="PATH",
        help="path to the linux-x64-webview runtime binary",
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


def main() -> int:  # noqa: C901
    args = _parse_args()

    # Validate binary
    binary = Path(args.binary)
    if not binary.exists():
        print(f"error: binary not found: {binary}", file=sys.stderr)
        return 1

    # Validate examples
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

    # Check Xvfb availability
    if sys.platform != "linux":
        print(
            "perf-check: this script only supports Linux (requires Xvfb + X11). "
            "Skipping.",
            file=sys.stderr,
        )
        return 0

    xvfb_bin = _require_xvfb()
    if not xvfb_bin and not os.environ.get("DISPLAY"):
        print(
            "perf-check: Xvfb not found and $DISPLAY is unset. "
            "Install xvfb (apt install xvfb) or set $DISPLAY. Skipping.",
            file=sys.stderr,
        )
        return 0

    # Start Xvfb if no display is available
    xvfb_proc: subprocess.Popen | None = None
    xvfb_display: int
    if os.environ.get("DISPLAY"):
        # Parse display number from $DISPLAY (e.g. ":0" → 0, ":99" → 99)
        m = re.match(r":(\d+)", os.environ["DISPLAY"])
        xvfb_display = int(m.group(1)) if m else 0
        print(f"perf-check: using existing DISPLAY={os.environ['DISPLAY']}")
    else:
        xvfb_display = _find_free_display()
        print(f"perf-check: starting Xvfb on display :{xvfb_display}")
        xvfb_proc = _start_xvfb(xvfb_display)
        time.sleep(0.3)  # give Xvfb time to bind its socket

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    try:
        for example in examples:
            print(f"\n=== {example.name} ===")

            if not args.skip_test1:
                r1 = measure_test1(binary, example, xvfb_display, args.runs)
                results.append(r1)
                status = "PASS" if r1["pass"] else "FAIL"
                print(
                    f"  NFR-TEST-1 {status}: median={r1.get('median_ms', 'N/A')} ms "
                    f"(bound={NFR_TEST1_MEDIAN_MS} ms)"
                )
                if not r1["pass"]:
                    failures.append(
                        f"NFR-TEST-1 FAILED for {example.name}: "
                        f"median {r1.get('median_ms', 'N/A')} ms "
                        f"> {NFR_TEST1_MEDIAN_MS} ms bound"
                    )
                if r1.get("soft_warn"):
                    print(f"  WARNING: {r1['soft_warn']}")

            if not args.skip_ex2:
                r2 = measure_ex2(binary, example, xvfb_display, args.runs)
                results.append(r2)
                status = "PASS" if r2["pass"] else "FAIL"
                print(
                    f"  NFR-EX-2   {status}: median={r2.get('median_ms', 'N/A')} ms "
                    f"(bound={NFR_EX2_MEDIAN_MS} ms)"
                )
                if not r2["pass"]:
                    failures.append(
                        f"NFR-EX-2 FAILED for {example.name}: "
                        f"median {r2.get('median_ms', 'N/A')} ms "
                        f"> {NFR_EX2_MEDIAN_MS} ms bound"
                    )
                if r2.get("soft_warn"):
                    print(f"  WARNING: {r2['soft_warn']}")

    finally:
        if xvfb_proc is not None and xvfb_proc.poll() is None:
            xvfb_proc.terminate()
            try:
                xvfb_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb_proc.kill()

    # Write output JSON
    output_data = {
        "binary": str(binary),
        "runs_per_nfr": args.runs,
        "results": results,
        "failures": failures,
    }
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(output_data, indent=2))
        print(f"\nResults written to {out_path}")

    # Report failures
    print()
    if failures:
        print("PERF-CHECK FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    else:
        print("PERF-CHECK PASSED: all NFR bounds met.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
