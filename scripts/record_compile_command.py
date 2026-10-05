#!/usr/bin/env python3
"""Record a compiler invocation, then execute it unchanged."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".s", ".S"}


def _source_argument(args: list[str], kind: str) -> Path | None:
    if kind == "cc" and "-c" not in args:
        return None
    for arg in args:
        if not arg.startswith("-") and Path(arg).suffix in SOURCE_SUFFIXES:
            return Path(arg)
    return None


def _owner(source: Path, repo_root: Path) -> str:
    runtime = repo_root / "packages/picolet-runtime"
    if source.name == "frozen_content.c":
        return "generated-frozen-python"
    if source.name == "lv_mpy.c":
        return "generated-lv-binding"
    if source.is_relative_to(runtime / "variants") or source.is_relative_to(runtime / "user_c_modules"):
        return "picolet-runtime"
    if source.is_relative_to(runtime / "lib" / "lv_binding_micropython"):
        return "lv-binding"
    if source.is_relative_to(runtime / "micropython"):
        return "micropython-integration"
    raise ValueError(f"compiler input has no ownership mapping: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("cc", "as"), default="cc")
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()

    compiler_args = options.args[1:] if options.args[:1] == ["--"] else options.args
    command = [options.compiler, *compiler_args]
    source_arg = _source_argument(compiler_args, options.kind)
    if source_arg is not None:
        source = source_arg.resolve()
        repo_root = options.repo_root.resolve()
        entry = {
            "directory": os.getcwd(),
            "file": str(source),
            "arguments": command,
            "owner": _owner(source, repo_root),
        }
        options.log.parent.mkdir(parents=True, exist_ok=True)
        with options.log.open("a", encoding="utf-8") as log:
            fcntl.flock(log, fcntl.LOCK_EX)
            log.write(json.dumps(entry, separators=(",", ":")) + "\n")
            log.flush()
            fcntl.flock(log, fcntl.LOCK_UN)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
