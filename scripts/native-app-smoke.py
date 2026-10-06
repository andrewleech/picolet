#!/usr/bin/env python3
"""Build an observed copy of an example and run its real packaged entry point."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=("pydfu", "notes", "config-editor", "dashboard"))
    parser.add_argument("--target", required=True, choices=("macos-x64", "macos-arm64"))
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    app_root = repo / "examples" / args.app
    runtime = args.runtime.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    # The observer is test-only. Production binaries and application sources stay unchanged.
    with tempfile.TemporaryDirectory(prefix=f".picolet-native-{args.app}-", dir=app_root.parent) as temporary:
        fixture = Path(temporary)
        shutil.copytree(
            app_root, fixture,
            ignore=shutil.ignore_patterns("node_modules", "target", "__pycache__"),
            dirs_exist_ok=True,
        )
        node_modules = app_root / "node_modules"
        if node_modules.is_dir():
            (fixture / "node_modules").symlink_to(node_modules, target_is_directory=True)
        with (fixture / "picolet.toml").open("rb") as stream:
            config = tomllib.load(stream)
        entry = fixture / config["app"]["entry"]
        observer = entry.parent / "_picolet_native_app_smoke.py"
        shutil.copyfile(repo / "scripts" / "mac-app-smoke.py", observer)
        source = entry.read_text(encoding="utf-8")
        statements = ast.parse(source).body
        insertion = 0
        for statement in statements:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str) and insertion == 0:
                insertion = statement.end_lineno
            elif isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
                insertion = statement.end_lineno
            else:
                break
        lines = source.splitlines(keepends=True)
        lines.insert(insertion, (
            "import _picolet_native_app_smoke\n"
            f"_picolet_native_app_smoke.install({args.app!r}, {str(output)!r})\n"
        ))
        entry.write_text("".join(lines), encoding="utf-8")
        subprocess.run(
            [sys.executable, "-m", "picolet", "build", "--target", args.target,
             "--runtime", str(runtime)],
            cwd=fixture, check=True,
        )
        binary = fixture / "target" / args.target / config["app"]["name"]
        # No interpreter/script arguments: the runtime starts the original application entry.
        subprocess.run([str(binary)], cwd=app_root, check=True, timeout=45)


if __name__ == "__main__":
    main()
