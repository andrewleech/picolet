"""
picolet dev — watch mode: rebuild + relaunch on source change.

Usage:
    picolet dev [--target TARGET] [--verbose]

Watch strategy: stdlib polling at 500 ms intervals (no external deps).
Debounce: changes detected within one poll window are batched into a
single rebuild.  A quiet period of 500 ms after the last change must
pass before the rebuild fires.

Process management: SIGTERM → 3 s grace → SIGKILL on rebuild.
CTRL-C (SIGINT) kills the child cleanly and exits.

Closes: FR-CLI-7.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from picolet.cli import build_cmd
from picolet.cli._paths import (
    collect_watch_paths,
    iter_watched_files,
    resolve_app,
)


_POLL_INTERVAL = 0.5
_DEBOUNCE_DELAY = 0.5
_SIGTERM_GRACE = 3.0


_DEV_EPILOG = """\
Examples:
  picolet dev
  picolet dev --verbose
  picolet dev --target linux-x64
"""


def add_parser(subparsers) -> None:
    """Register the dev subcommand with the given subparsers object."""
    import argparse as _argparse
    p = subparsers.add_parser(
        "dev",
        help="watch for changes and rebuild + relaunch the app",
        description=(
            "Watch src/, ui/, and picolet.toml for changes.  On change, "
            "rebuild the app and relaunch it.  Press CTRL-C to stop.\n\n"
            "Run from the app directory (the one containing picolet.toml). "
            "Uses polling at 500 ms intervals; no external dependencies required."
        ),
        epilog=_DEV_EPILOG,
        formatter_class=_argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--target",
        default=None,
        metavar="TARGET",
        help="build target (default: host; supported: linux-x64, windows-x64)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="print watch and build steps to stderr",
    )
    p.set_defaults(func=run)


def run(args) -> None:
    """Entry point for `picolet dev`."""
    toml_path, data, target, binary_path = resolve_app(args)
    app_root = toml_path.parent
    watch_dirs = collect_watch_paths(app_root, data)

    # Detect Vue (or other non-vanilla) frontend (FR-VUE-2, FR-VUE-5).
    frontend = data.get("ui", {}).get("frontend", {})
    framework = frontend.get("framework", "vanilla")
    dev_url: Optional[str] = None
    vite_proc: Optional[subprocess.Popen] = None

    if framework != "vanilla":
        dev_url = frontend.get("dev_url", "http://localhost:5173/")

    print(f"picolet dev: watching {app_root}", file=sys.stderr)
    if args.verbose:
        for p in watch_dirs:
            print(f"  watch: {p}", file=sys.stderr)
        print(f"  target: {target}", file=sys.stderr)
        print(f"  binary: {binary_path}", file=sys.stderr)
        if dev_url:
            print(f"  frontend: {framework}, dev_url={dev_url}", file=sys.stderr)

    watcher = _Watcher(watch_dirs, args.verbose)
    child: Optional[subprocess.Popen] = None
    build_args = build_cmd.build_args_namespace(args.target, args.verbose)

    def _kill_child() -> None:
        nonlocal child
        if child is not None and child.poll() is None:
            if args.verbose:
                print("dev: stopping running process …", file=sys.stderr)
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=_SIGTERM_GRACE)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child = None

    def _kill_vite() -> None:
        """Terminate the Vite process group (D3).

        POSIX: send SIGTERM to the process group (killpg) so ESBuild/rollup
        children also die.  On timeout escalate to SIGKILL.

        Windows: send CTRL_BREAK_EVENT to the process group (created with
        CREATE_NEW_PROCESS_GROUP) so Vite's worker children also receive the
        event.  On timeout fall back to TerminateProcess.
        """
        nonlocal vite_proc
        if vite_proc is None:
            return
        if vite_proc.poll() is not None:
            vite_proc = None
            return
        if args.verbose:
            print("dev: stopping Vite …", file=sys.stderr)
        try:
            if sys.platform == "win32":
                # Send Ctrl+Break to the process group created with
                # CREATE_NEW_PROCESS_GROUP so all Vite worker processes
                # (ESBuild, Rollup) receive the signal (D3, A7).
                try:
                    os.kill(vite_proc.pid, signal.CTRL_BREAK_EVENT)
                except (OSError, ProcessLookupError):
                    pass
            else:
                # POSIX: kill the entire process group so ESBuild/rollup
                # children also die (D3).
                try:
                    pgid = os.getpgid(vite_proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                vite_proc.wait(timeout=_SIGTERM_GRACE)
            except subprocess.TimeoutExpired:
                try:
                    if sys.platform != "win32":
                        pgid = os.getpgid(vite_proc.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    else:
                        vite_proc.kill()
                except ProcessLookupError:
                    pass
                vite_proc.wait()
        except Exception:
            pass
        vite_proc = None

    def _build_and_launch() -> Optional[subprocess.Popen]:
        print("dev: building …", file=sys.stderr)
        # In-process call to build_cmd.run — no subprocess overhead per
        # rebuild. build_cmd raises BuildFailed on failure; run() catches
        # and returns rc=1, so we get the same int-rc contract subprocess
        # would have provided.
        rc = build_cmd.run(build_args)
        if rc != 0:
            print("dev: build failed; waiting for next change …", file=sys.stderr)
            return None
        if not binary_path.exists():
            print(
                f"dev: binary not found after build: {binary_path}",
                file=sys.stderr,
            )
            return None
        print(f"dev: launching {binary_path.name}", file=sys.stderr)
        # For Vue apps, inject PICOLET_DEV_URL so the runtime loads from Vite
        # instead of from romfs (F5, D1).
        if dev_url is not None:
            child_env = {**os.environ, "PICOLET_DEV_URL": dev_url}
        else:
            child_env = None
        return subprocess.Popen(
            [str(binary_path)],
            cwd=str(app_root),
            env=child_env,
        )

    atexit.register(_kill_child)
    atexit.register(_kill_vite)

    # Spawn Vite before the first build so the dev server is ready when the
    # binary launches (FR-VUE-2).
    if dev_url is not None:
        vite_env = {**os.environ, "FORCE_COLOR": "1"}
        if sys.platform == "win32":
            # Windows: CREATE_NEW_PROCESS_GROUP gives Vite its own console
            # process group so CTRL_BREAK_EVENT propagates to all children
            # (ESBuild, Rollup).  start_new_session has no equivalent effect
            # on Windows (D3, A7).
            vite_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(app_root),
                env=vite_env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            # POSIX: own process group via start_new_session (D3).
            vite_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(app_root),
                env=vite_env,
                start_new_session=True,
            )
        print(
            f"dev: Vite dev server spawned (PID {vite_proc.pid}), "
            f"loading from {dev_url}",
            file=sys.stderr,
        )

    child = _build_and_launch()
    # Build outputs land inside the watched tree; snapshot after the
    # initial build so the very next tick doesn't re-trigger.
    watcher.snapshot()

    print("dev: watching for changes … (CTRL-C to stop)", file=sys.stderr)

    last_change_time: Optional[float] = None
    pending_rebuild = False

    try:
        while True:
            time.sleep(_POLL_INTERVAL)
            if child is not None:
                child.poll()
            if vite_proc is not None:
                vite_proc.poll()

            changed = watcher.changed()
            now = time.monotonic()

            if changed:
                if args.verbose and not pending_rebuild:
                    print("dev: change detected; debouncing …", file=sys.stderr)
                last_change_time = now
                pending_rebuild = True

            if pending_rebuild and last_change_time is not None:
                if now - last_change_time >= _DEBOUNCE_DELAY:
                    pending_rebuild = False
                    last_change_time = None
                    _kill_child()
                    child = _build_and_launch()
                    watcher.snapshot()

    except KeyboardInterrupt:
        print("\ndev: shutting down …", file=sys.stderr)
    finally:
        _kill_child()
        _kill_vite()

    sys.exit(0)


class _Watcher:
    """Poll-based file watcher using mtime + size fingerprints.

    Hot-path discipline: ``changed()`` does NOT allocate a fresh dict
    every tick. It streams the current file state and compares against
    the previous snapshot in place — on a no-change tick we touch
    nothing but a counter and the iterator's tuple yields. Snapshot
    materialisation is deferred to ``snapshot()``, called only after
    a real change has been processed.
    """

    def __init__(self, watch_paths: list[Path], verbose: bool = False) -> None:
        self._paths = watch_paths
        self._verbose = verbose
        self._state: dict[Path, tuple[float, int]] = {}
        self.snapshot()

    def snapshot(self) -> None:
        """Record the current mtime+size of all watched files."""
        self._state = {
            p: (mtime, size)
            for p, mtime, size in iter_watched_files(self._paths)
        }

    def changed(self) -> bool:
        """Return True if anything has changed; commit new snapshot on change.

        No-change ticks stream-compare against state and allocate nothing —
        avoiding the dict-per-tick churn that would fragment heap on
        MicroPython. Change ticks pay one extra scan to commit the new
        snapshot, which is the cost of having state to compare against
        on the next tick.
        """
        seen = 0
        for path, mtime, size in iter_watched_files(self._paths):
            old = self._state.get(path)
            if old is None or old != (mtime, size):
                if self._verbose:
                    label = "changed" if old is not None else "new"
                    print(f"  {label}: {path}", file=sys.stderr)
                self.snapshot()
                return True
            seen += 1
        if seen != len(self._state):
            if self._verbose:
                print("  files deleted from watched tree", file=sys.stderr)
            self.snapshot()
            return True
        return False
