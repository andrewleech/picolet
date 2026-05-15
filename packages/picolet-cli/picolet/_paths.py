"""
Shared path / project-resolution helpers for the picolet CLI.

Used by run_cmd, dev_cmd, and build_cmd. Pulled out of run_cmd in PH16 cleanup
so command modules can be symmetric leaves rather than importing from
siblings.
"""
from __future__ import annotations

import stat
import subprocess
import sys
import tomllib
from pathlib import Path

from picolet.validator import validate_toml

# Directory components that should never be watched or scanned for source
# freshness — build outputs, caches, compiled artifacts.
_IGNORE_DIRS = frozenset({"target", ".picolet-cache", "__pycache__"})
_IGNORE_SUFFIXES = frozenset({".pyc", ".mpy"})


def resolve_app(args) -> tuple[Path, dict, str, Path]:
    """Validate picolet.toml and resolve the binary path.

    Returns (toml_path, data, target, binary_path). Exits 1 on any
    validation error.
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


def should_ignore(path: Path) -> bool:
    """Return True if path is in a watch-ignored location."""
    for part in path.parts:
        if part in _IGNORE_DIRS or part.startswith("."):
            return True
    return path.suffix in _IGNORE_SUFFIXES


def collect_watch_paths(app_root: Path, data: dict) -> list[Path]:
    """Return the list of paths/dirs to watch (may not all exist)."""
    src_dir = app_root / Path(data["app"]["entry"]).parent
    paths = [app_root / "picolet.toml", src_dir]

    ui = data.get("ui", {})
    if ui:
        ui_root = ui.get("root")
        if ui_root:
            paths.append(app_root / ui_root)
        else:
            fallback = app_root / "ui"
            if fallback.is_dir():
                paths.append(fallback)

    return paths


def sources_newer_than(
    toml_path: Path, data: dict, binary: Path
) -> bool:
    """Return True if any watched source is newer than the binary."""
    binary_mtime = binary.stat().st_mtime
    watch_paths = collect_watch_paths(toml_path.parent, data)
    for _path, mtime, _size in iter_watched_files(watch_paths):
        if mtime > binary_mtime:
            return True
    return False


def iter_watched_files(watch_paths):
    """Yield (path, mtime, size) for every watched non-ignored regular file.

    Single stat per file via stat.S_ISREG — no separate is_file() pass.
    """
    for watch_path in watch_paths:
        if should_ignore(watch_path):
            continue
        try:
            st = watch_path.stat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            yield watch_path, st.st_mtime, st.st_size
            continue
        if not stat.S_ISDIR(st.st_mode):
            continue
        for f in watch_path.rglob("*"):
            if should_ignore(f):
                continue
            try:
                fst = f.stat()
            except OSError:
                continue
            if stat.S_ISREG(fst.st_mode):
                yield f, fst.st_mtime, fst.st_size


def invoke_build(
    target: str | None, verbose: bool, cwd: Path | None = None
) -> int:
    """Spawn `picolet build` with the given flags. Returns the rc."""
    cmd = [sys.executable, "-m", "picolet", "build"]
    if target:
        cmd += ["--target", target]
    if verbose:
        cmd += ["--verbose"]
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode
