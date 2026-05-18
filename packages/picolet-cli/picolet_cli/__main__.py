# /// script
# requires-python = ">=3.11"
# dependencies = ["mpremote"]
# ///
"""
picolet — the Picolet framework CLI.

Invocation paths:
  uv run packages/picolet-cli/picolet_cli/__main__.py <args>   # zero-install
  picolet <args>                                              # after pip install
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# When invoked via `uv run picolet_cli/__main__.py`, the parent of the picolet_cli
# package directory (packages/picolet-cli/) is not automatically on sys.path.
# Insert it so `from picolet_cli import ...` resolves correctly in both the
# uv-run and installed invocation paths.
_PKG_PARENT = Path(__file__).parent.parent  # packages/picolet-cli/
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    VERSION = _pkg_version("picolet-cli")
except Exception:
    VERSION = "0.2.0-dev"


_TOP_DESCRIPTION = """\
Picolet builds single-binary MicroPython apps with optional webview or LVGL GUI.
Each app is scaffolded from a template, compiled to .mpy bytecode, and packed
with a pre-built runtime into one self-contained executable.

Quick start:
  picolet init my-app --template hello-vue
  cd my-app
  picolet dev
"""

_TOP_EPILOG = """\
Run `picolet <command> --help` for command-specific options and examples.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="picolet",
        description=_TOP_DESCRIPTION,
        epilog=_TOP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"picolet {VERSION}",
    )

    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="subcommand",
        metavar="<command>",
    )

    # Register subcommands.
    from picolet_cli import build_cmd, dev_cmd, init_cmd, run_cmd, test_cmd, validate_cmd

    init_cmd.add_parser(subparsers)
    validate_cmd.add_parser(subparsers)
    build_cmd.add_parser(subparsers)
    run_cmd.add_parser(subparsers)
    dev_cmd.add_parser(subparsers)
    test_cmd.add_parser(subparsers)

    # 'picolet help <subcommand>' as an alias for 'picolet <subcommand> --help'.
    help_parser = subparsers.add_parser(
        "help",
        help="show help for a subcommand",
    )
    help_parser.add_argument(
        "topic",
        nargs="?",
        default=None,
        metavar="<command>",
        help="subcommand to show help for",
    )
    help_parser.set_defaults(func=_help_cmd)

    return parser


def _help_cmd(args) -> None:
    """Handle `picolet help [<subcommand>]`."""
    if args.topic:
        # Re-parse with --help appended so argparse prints the subcommand help.
        sys.argv = ["picolet", args.topic, "--help"]
        _build_parser().parse_args()
    else:
        _build_parser().print_help()
        sys.exit(0)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        sys.exit(0)

    try:
        rc = args.func(args)
    except NotImplementedError as exc:
        print(f"error: not implemented: {exc}", file=sys.stderr)
        sys.exit(1)
    if rc is not None:
        sys.exit(rc)


if __name__ == "__main__":
    main()
