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

import subprocess
import sys

from picolet._paths import invoke_build, resolve_app, sources_newer_than


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
    toml_path, data, _target, binary_path = resolve_app(args)

    if not args.no_build:
        if not binary_path.exists() or sources_newer_than(toml_path, data, binary_path):
            if args.verbose:
                print("binary out of date; running build first", file=sys.stderr)
            rc = invoke_build(args.target, args.verbose)
            if rc != 0:
                sys.exit(rc)

    if not binary_path.exists():
        print(
            f"error: binary not found: {binary_path}\n"
            "Run `picolet build` to produce it.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.verbose:
        print(f"exec: {binary_path}", file=sys.stderr)

    sys.exit(subprocess.run([str(binary_path)]).returncode)
