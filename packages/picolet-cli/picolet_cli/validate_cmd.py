"""
picolet validate — validate a picolet.toml file against the schema.

Exit 0 if the file is valid. Exit 1 and print structured errors otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

from picolet_cli.validator import validate_toml


def add_parser(subparsers) -> None:
    """Register the validate subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "validate",
        help="validate a picolet.toml against the schema",
        description=(
            "Parse and validate a picolet.toml file. "
            "Exits 0 if valid, 1 if errors are found."
        ),
    )
    p.add_argument(
        "file",
        nargs="?",
        default="picolet.toml",
        help="path to the picolet.toml to validate (default: ./picolet.toml)",
    )
    p.set_defaults(func=run)


def run(args) -> None:
    """Entry point for `picolet validate`."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    results = validate_toml(path)
    hard_errors = [r for r in results if r.level != "warn"]
    warnings = [r for r in results if r.level == "warn"]

    for w in warnings:
        print(str(w), file=sys.stderr)
    if hard_errors:
        for e in hard_errors:
            print(str(e), file=sys.stderr)
        sys.exit(1)
    # No output on success — callers rely on exit 0.
