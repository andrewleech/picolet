"""
picolet run — build (if needed) and execute the produced binary.

Usage:
    picolet run [--target TARGET] [--verbose] [--no-build] [-- arg1 arg2 ...]

The binary is rebuilt if it does not exist or if any source file
(src/, ui/, picolet.toml) is newer than the binary.  Pass ``--no-build``
to skip the freshness check and run whatever binary is already present.

Any arguments after ``--`` are forwarded verbatim to the child binary.

Closes: FR-CLI-6.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from picolet_cli import build_cmd
from picolet_cli._paths import resolve_app, sources_newer_than


def add_parser(subparsers) -> None:
    """Register the run subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "run",
        help="build (if needed) and execute the app binary",
        description=(
            "Check whether the binary is up-to-date, rebuild if not, "
            "then execute it.  Arguments after -- are forwarded to the binary."
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
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="arguments forwarded to the binary (place after --)",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    """Entry point for `picolet run`. Returns the exit code."""
    toml_path, data, _target, binary_path = resolve_app(args)

    if not args.no_build:
        if not binary_path.exists() or sources_newer_than(toml_path, data, binary_path):
            if args.verbose:
                print("binary out of date; running build first", file=sys.stderr)
            rc = build_cmd.run(build_cmd.build_args_namespace(args.target, args.verbose))
            if rc != 0:
                return rc

    if not binary_path.exists():
        print(
            f"error: binary not found: {binary_path}\n"
            "Run `picolet build` to produce it.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"exec: {binary_path}", file=sys.stderr)

    # Strip a leading '--' separator if present (argparse.REMAINDER may
    # include it when the user writes `picolet run -- arg1 arg2`).
    forward = list(getattr(args, "args", None) or [])
    if forward and forward[0] == "--":
        forward = forward[1:]

    return subprocess.run([str(binary_path)] + forward).returncode
