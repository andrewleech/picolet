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
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from picolet._paths import (
    collect_watch_paths,
    invoke_build,
    iter_watched_files,
    resolve_app,
)


_POLL_INTERVAL = 0.5
_DEBOUNCE_DELAY = 0.5
_SIGTERM_GRACE = 3.0


def add_parser(subparsers) -> None:
    """Register the dev subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "dev",
        help="watch for changes and rebuild + relaunch the app",
        description=(
            "Watch src/, ui/, and picolet.toml for changes.  On change, "
            "rebuild the app and relaunch it.  Press CTRL-C to stop."
        ),
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

    print(f"picolet dev: watching {app_root}", file=sys.stderr)
    if args.verbose:
        for p in watch_dirs:
            print(f"  watch: {p}", file=sys.stderr)
        print(f"  target: {target}", file=sys.stderr)
        print(f"  binary: {binary_path}", file=sys.stderr)

    watcher = _Watcher(watch_dirs, args.verbose)
    child: Optional[subprocess.Popen] = None

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

    def _build_and_launch() -> Optional[subprocess.Popen]:
        print("dev: building …", file=sys.stderr)
        rc = invoke_build(target, args.verbose, cwd=app_root)
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
        return subprocess.Popen([str(binary_path)], cwd=str(app_root))

    atexit.register(_kill_child)

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

    sys.exit(0)


class _Watcher:
    """Poll-based file watcher using mtime + size fingerprints."""

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
        """Return True if any file has changed; update snapshot on change."""
        current: dict[Path, tuple[float, int]] = {}
        diff = False
        for path, mtime, size in iter_watched_files(self._paths):
            current[path] = (mtime, size)
            if not diff and self._state.get(path) != (mtime, size):
                if self._verbose:
                    print(f"  changed: {path}", file=sys.stderr)
                diff = True

        if not diff:
            for path in self._state:
                if path not in current:
                    if self._verbose:
                        print(f"  deleted: {path}", file=sys.stderr)
                    diff = True
                    break

        if diff:
            self._state = current
        return diff
