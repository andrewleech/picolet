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

import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Optional

from picolet.validator import validate_toml


# How often the watcher polls the filesystem (seconds).
_POLL_INTERVAL = 0.5

# Quiet period after the last detected change before triggering a rebuild.
# Must be >= _POLL_INTERVAL to guarantee the flurry-of-edits debounce.
_DEBOUNCE_DELAY = 0.5

# How long to wait for the child to exit after SIGTERM before sending SIGKILL.
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
    from picolet.build_cmd import _find_picolet_toml, _host_target

    toml_path = _find_picolet_toml(Path.cwd())
    if toml_path is None:
        print(
            "error: picolet.toml not found in current directory or any ancestor",
            file=sys.stderr,
        )
        sys.exit(1)

    errors = validate_toml(toml_path)
    if errors:
        for e in errors:
            print(str(e), file=sys.stderr)
        sys.exit(1)

    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    target = args.target if args.target else _host_target()
    app_name: str = data["app"]["name"]
    app_root: Path = toml_path.parent

    binary_path = app_root / "target" / target / app_name
    if target == "windows-x64":
        binary_path = binary_path.with_suffix(".exe")

    from picolet.run_cmd import _collect_watch_paths, _should_ignore
    watch_dirs = _collect_watch_paths(app_root, data)

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
        """Run picolet build, then launch the binary.  Returns Popen or None."""
        cmd = [sys.executable, "-m", "picolet", "build", "--target", target]
        if args.verbose:
            cmd.append("--verbose")
        print(f"dev: building …", file=sys.stderr)
        result = subprocess.run(cmd, cwd=str(app_root))
        if result.returncode != 0:
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

    # Register atexit so the child is cleaned up on unexpected exit.
    import atexit
    atexit.register(_kill_child)

    # Initial build + launch.
    child = _build_and_launch()

    # Take a snapshot after the initial build so we don't immediately
    # retrigger on the build outputs being written.
    watcher.snapshot()

    print("dev: watching for changes … (CTRL-C to stop)", file=sys.stderr)

    last_change_time: Optional[float] = None
    pending_rebuild = False

    try:
        while True:
            time.sleep(_POLL_INTERVAL)

            # Reap dead child so poll() reflects true state.
            if child is not None:
                child.poll()

            changed = watcher.changed()
            now = time.monotonic()

            if changed:
                if args.verbose:
                    print(f"dev: change detected; debouncing …", file=sys.stderr)
                last_change_time = now
                pending_rebuild = True

            if pending_rebuild and last_change_time is not None:
                if now - last_change_time >= _DEBOUNCE_DELAY:
                    pending_rebuild = False
                    last_change_time = None
                    _kill_child()
                    child = _build_and_launch()
                    # Re-snapshot to avoid re-triggering on build outputs.
                    watcher.snapshot()

    except KeyboardInterrupt:
        print("\ndev: shutting down …", file=sys.stderr)
    finally:
        _kill_child()

    sys.exit(0)


# ---------------------------------------------------------------------------
# Filesystem snapshot-based change detector
# ---------------------------------------------------------------------------

class _Watcher:
    """Poll-based file watcher using mtime + size fingerprints.

    snapshot() records the current state of all watched files.
    changed() compares the current state against the last snapshot,
    updates the snapshot on return, and returns True if anything differs.
    """

    def __init__(self, watch_paths: list[Path], verbose: bool = False) -> None:
        self._paths = watch_paths
        self._verbose = verbose
        self._state: dict[Path, tuple[float, int]] = {}
        self.snapshot()

    def snapshot(self) -> None:
        """Record the current mtime+size of all watched files."""
        self._state = self._scan()

    def changed(self) -> bool:
        """Return True if any file has changed since last snapshot, and update snapshot."""
        current = self._scan()
        changed = False

        # Detect modified or new files.
        for path, (mtime, size) in current.items():
            old = self._state.get(path)
            if old is None or old != (mtime, size):
                if self._verbose:
                    print(f"  changed: {path}", file=sys.stderr)
                changed = True
                break

        # Detect deleted files.
        if not changed:
            for path in self._state:
                if path not in current:
                    if self._verbose:
                        print(f"  deleted: {path}", file=sys.stderr)
                    changed = True
                    break

        if changed:
            self._state = current
        return changed

    def _scan(self) -> dict[Path, tuple[float, int]]:
        """Walk watched paths and return {path: (mtime, size)} for each file."""
        result: dict[Path, tuple[float, int]] = {}
        for watch_path in self._paths:
            if watch_path.is_file():
                if not _should_watch(watch_path):
                    continue
                try:
                    st = watch_path.stat()
                    result[watch_path] = (st.st_mtime, st.st_size)
                except OSError:
                    pass
            elif watch_path.is_dir():
                for f in watch_path.rglob("*"):
                    if not f.is_file():
                        continue
                    if not _should_watch(f):
                        continue
                    try:
                        st = f.stat()
                        result[f] = (st.st_mtime, st.st_size)
                    except OSError:
                        pass
        return result


def _should_watch(path: Path) -> bool:
    """Return True if path should be included in watch (inverse of ignore)."""
    from picolet.run_cmd import _should_ignore
    return not _should_ignore(path)
