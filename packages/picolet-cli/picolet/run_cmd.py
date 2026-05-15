"""
picolet run — build (if needed) and execute the produced binary.

Usage:
    picolet run [--target TARGET] [--verbose] [--no-build]

The binary is rebuilt if it does not exist or if any source file
(src/, ui/, picolet.toml) is newer than the binary.  Pass --no-build
to skip the freshness check and run whatever binary is already present.

Closes: FR-CLI-6.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from picolet.validator import validate_toml


def add_parser(subparsers) -> None:
    """Register the run subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "run",
        help="build (if needed) and execute the app binary",
        description=(
            "Check whether the binary is up-to-date, rebuild if not, "
            "then execute it."
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
        help="print build steps to stderr",
    )
    p.add_argument(
        "--no-build",
        action="store_true",
        default=False,
        dest="no_build",
        help="skip build freshness check; run existing binary directly",
    )
    p.set_defaults(func=run)


def run(args) -> None:
    """Entry point for `picolet run`."""
    toml_path, data, target, binary_path = _resolve_app(args)

    if not args.no_build:
        if not binary_path.exists() or _sources_newer_than(toml_path, data, binary_path):
            if args.verbose:
                print(f"binary out of date; running build first", file=sys.stderr)
            _invoke_build(args)

    if not binary_path.exists():
        print(
            f"error: binary not found: {binary_path}\n"
            "Run `picolet build` to produce it.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.verbose:
        print(f"exec: {binary_path}", file=sys.stderr)

    result = subprocess.run([str(binary_path)])
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Shared helpers used by dev_cmd.py as well
# ---------------------------------------------------------------------------

def _resolve_app(args):
    """Validate picolet.toml and resolve the binary path.

    Returns (toml_path, data, target, binary_path).
    Exits 1 on any validation error.
    """
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

    return toml_path, data, target, binary_path


def _sources_newer_than(toml_path: Path, data: dict, binary: Path) -> bool:
    """Return True if any watched source is newer than the binary."""
    binary_mtime = binary.stat().st_mtime
    app_root = toml_path.parent

    watch_paths = _collect_watch_paths(app_root, data)
    for path in watch_paths:
        if path.is_file():
            if path.stat().st_mtime > binary_mtime:
                return True
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and not _should_ignore(f):
                    if f.stat().st_mtime > binary_mtime:
                        return True
    return False


def _collect_watch_paths(app_root: Path, data: dict) -> list[Path]:
    """Return the list of paths/dirs to watch (may not all exist)."""
    entry = data["app"]["entry"]          # e.g. "src/main.py"
    src_dir = app_root / Path(entry).parent  # e.g. app_root/"src"

    paths = [app_root / "picolet.toml", src_dir]

    ui = data.get("ui", {})
    if ui:
        ui_root = ui.get("root")
        if ui_root:
            paths.append(app_root / ui_root)
        else:
            # Fallback: watch ui/ if it exists
            fallback = app_root / "ui"
            if fallback.is_dir():
                paths.append(fallback)

    return paths


def _should_ignore(path: Path) -> bool:
    """Return True if path should be excluded from watching."""
    ignore_dirs = {"target", ".picolet-cache", "__pycache__"}
    ignore_suffixes = {".pyc", ".mpy"}

    for part in path.parts:
        if part in ignore_dirs or part.startswith("."):
            return True
    if path.suffix in ignore_suffixes:
        return True
    return False


def _invoke_build(args) -> None:
    """Run `picolet build` with the same target/verbose flags."""
    cmd = [sys.executable, "-m", "picolet", "build"]
    if args.target:
        cmd += ["--target", args.target]
    if args.verbose:
        cmd += ["--verbose"]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)
