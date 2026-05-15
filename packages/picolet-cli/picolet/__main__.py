# /// script
# requires-python = ">=3.11"
# dependencies = ["mpremote"]
# ///
"""
picolet — the Picolet framework CLI.

Invocation paths:
  uv run packages/picolet-cli/picolet/__main__.py <args>   # zero-install
  picolet <args>                                          # after pip install
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# When invoked via `uv run picolet/__main__.py`, the parent of the picolet package
# directory (packages/picolet-cli/) is not automatically on sys.path.  Insert it
# so `from picolet import ...` resolves correctly in both the uv-run and installed
# invocation paths.
_PKG_PARENT = Path(__file__).parent.parent  # packages/picolet-cli/
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    VERSION = _pkg_version("picolet-cli")
except Exception:
    VERSION = "0.2.0-dev"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="picolet",
        description="The Picolet framework CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"picolet {VERSION}",
    )

    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="subcommand",
        metavar="<command>",
    )

    # Register subcommands.
    from picolet import build_cmd, init_cmd, validate_cmd

    init_cmd.add_parser(subparsers)
    validate_cmd.add_parser(subparsers)
    build_cmd.add_parser(subparsers)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
