#!/usr/bin/env python3
"""Select the Picolet sources for static analysis as a shared mpy_analysis scope.

The runtime target/variant matrix, the manifest per variant, the tracked
host/example/frontend sources and the ownership classes are Picolet policy and
live here. Manifest resolution, schema validation and compiler-database
collection are done by the installed mpy_analysis package.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RUNTIME_TARGETS: dict[str, set[str]] = {
    "linux-x64": {"cli", "webview", "lvgl", "mcp", "tui"},
    "windows-x64": {"cli", "webview", "lvgl", "tui"},
    "macos-x64": {"cli", "webview", "lvgl"},
    "macos-arm64": {"cli", "webview", "lvgl"},
}
PYTHON_SUFFIXES = {".py"}
FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".vue"}
PROJECT_PREFIXES = (
    "packages/picolet/picolet/",
    "packages/picolet-bridge-js/src/",
    "examples/",
)
EXCLUDED_PARTS = {"tests", "scripts", "screenshots", "node_modules", "dist", "public", "_vendor", "vendor", "third_party", "__pycache__"}
OTHER_EXCLUSIONS = [
    "runtime Python not selected by the resolved build manifest",
    "MicroPython mpy-cross host-tool sources",
    "generated frontend bundles and installed dependencies",
    "test, screenshot and build tooling",
    "type stubs (.pyi); stub dependencies are configured by the typing policy",
]
PROVENANCE_FORMAT = "picolet-normalised-compile-database"
INSTALL_HINT = (
    "mpy_analysis is not importable; install the pinned shared tooling into the interpreter "
    "running this script (see scripts/install_sast_core.py)"
)

# Explicit roots, relative to the repository, with the owner their files carry
# unless an input (a compiler record) names a more specific one. Nested roots
# take precedence over the repository root in the shared collector.
_ROOTS = {
    "repo": ("", "picolet"),
    "picolet-runtime-python": ("packages/picolet-runtime/python", "picolet-runtime"),
    "micropython": ("packages/picolet-runtime/micropython", "micropython-integration"),
    "micropython-lib": ("packages/picolet-runtime/lib/micropython-lib", "micropython-lib"),
    "lv-binding": ("packages/picolet-runtime/lib/lv_binding_micropython", "lv-binding"),
}
# Frozen Python must come from a nested root; the repository root has no frozen owner.
_FROZEN_UNOWNED_ROOT = "repo"


def shared_scope():
    """Return mpy_analysis.scope, failing with install guidance when it is absent."""
    try:
        from mpy_analysis import scope
    except ImportError as exc:
        raise RuntimeError(f"{INSTALL_HINT}: {exc}") from exc
    return scope


def runtime_port(target: str) -> str:
    return "windows" if target == "windows-x64" else "unix"


def _manifest_for(runtime: Path, target: str, variant: str) -> Path:
    port = runtime_port(target)
    if variant in {"cli", "mcp"}:
        manifest = f"manifest_{variant}.py"
    elif variant == "webview":
        manifest = "manifest_webview_windows.py" if port == "windows" else "manifest_webview_unix.py"
    elif variant == "lvgl":
        manifest = "manifest_lvgl_windows.py" if port == "windows" else "manifest_lvgl.py"
    elif variant == "tui":
        manifest = "manifest_tui_windows.py" if port == "windows" else "manifest_tui_unix.py"
    else:
        raise ValueError(f"unsupported runtime variant: {variant}")
    return (runtime / "manifests" / manifest).resolve()


def _check_target(target: str, variant: str) -> None:
    if target not in RUNTIME_TARGETS or variant not in RUNTIME_TARGETS[target]:
        raise ValueError(f"unsupported target/variant combination: {target}/{variant}")


def roots_and_owners(repo_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Explicit existing roots (absolute) and the owner each one carries."""
    roots, owners = {}, {}
    for name, (relative, owner) in _ROOTS.items():
        path = (repo_root / relative).resolve()
        if name == "repo" or path.is_dir():
            roots[name] = str(path)
            owners[name] = owner
    return roots, owners


def resolve_scope(repo_root: Path, target: str, variant: str) -> dict[str, Any]:
    """Frozen Python selected by the variant's manifest, as a shared schema-1 scope."""
    _check_target(target, variant)
    repo_root = repo_root.resolve()
    runtime = repo_root / "packages/picolet-runtime"
    mpy = runtime / "micropython"
    port = runtime_port(target)
    manifest = _manifest_for(runtime, target, variant)
    if not manifest.is_file():
        raise FileNotFoundError(f"runtime manifest not found: {manifest}")
    roots, owners = roots_and_owners(repo_root)
    scope = shared_scope().collect_manifest_scope(
        mpy,
        [manifest],
        roots=roots,
        path_vars={
            "PORT_DIR": str(mpy / "ports" / port),
            "MPY_LIB_DIR": str(runtime / "lib" / "micropython-lib"),
        },
        owners=owners,
        target=target,
        variant=variant,
        port=port,
    )
    for record in scope["groups"]["runtime_frozen_python"]:
        if record["root"] == _FROZEN_UNOWNED_ROOT:
            raise ValueError(f"manifest input has no ownership mapping: {record['path']}")
    scope["manifest"] = manifest.relative_to(repo_root).as_posix()
    return scope


def tracked_project_files(repo_root: Path) -> list[Path]:
    """Tracked host, example and frontend sources, excluding tooling and generated trees."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *PROJECT_PREFIXES],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    files = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        parts = Path(path).parts
        if EXCLUDED_PARTS.intersection(parts):
            continue
        suffix = Path(path).suffix
        if path.startswith("packages/picolet/picolet/") and suffix not in PYTHON_SUFFIXES:
            continue
        if path.startswith("packages/picolet-bridge-js/src/") and suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        if path.startswith("examples/"):
            is_app_source = "src" in parts or "ui" in parts and parts[parts.index("ui") + 1 : parts.index("ui") + 2] == ("src",)
            if not is_app_source or suffix not in PYTHON_SUFFIXES | FRONTEND_SUFFIXES:
                continue
        files.append(repo_root / path)
    return files


def _project_record(repo_root: Path, path: Path, owner: str) -> dict[str, Any]:
    relative = path.relative_to(repo_root).as_posix()
    return {
        "root": "repo",
        "path": relative,
        "owner": owner,
        "provenance": {"kind": "picolet-tracked-source", "selection": "git ls-files", "path": relative},
    }


def _picolet_file_mapping(repo_root: Path, index: int, entry: dict[str, Any]) -> dict[str, Any] | None:
    """Receipt for a normalised Picolet entry whose `file` is repo-relative, else None.

    Only entries carrying the normaliser's explicit provenance are reinterpreted, and only when the
    recorded original file and directory agree with the repository-relative value. Everything else
    keeps the shared meaning: an absolute file, or one relative to the entry's directory.
    """
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("format") != PROVENANCE_FORMAT:
        return None
    file = entry.get("file")
    original_file = provenance.get("original_file")
    if (
        provenance.get("file_base") != "repo-root"
        or not isinstance(file, str)
        or Path(file).is_absolute()
        or not isinstance(original_file, str)
        or provenance.get("original_directory") != entry.get("directory")
    ):
        raise ValueError(f"compiler database entry {index} has an inconsistent Picolet file mapping")
    if (repo_root / file).resolve() != Path(original_file):
        raise ValueError(
            f"compiler database entry {index} file {file} does not map to its recorded input {original_file}"
        )
    return {
        "format": PROVENANCE_FORMAT,
        "file_base": "repo-root",
        "database_file": file,
        "database_directory": entry["directory"],
        "resolved_file": original_file,
    }


def _native_records(repo_root: Path, compile_database: Path) -> list[dict[str, Any]]:
    """Collect compiler records through the shared collector, retaining the database entries as written."""
    database = compile_database.resolve(strict=True)
    entries = json.loads(database.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"compiler database must contain selected entries: {database}")
    roots, owners = roots_and_owners(repo_root)
    collect = shared_scope().collect_compdb_sources
    mappings: dict[int, dict[str, Any]] = {}
    resolved = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"compiler database entry {index} must be an object")
        mapping = _picolet_file_mapping(repo_root, index, entry)
        if mapping is not None:
            mappings[index] = mapping
            entry = {**entry, "file": mapping["resolved_file"]}
        resolved.append(entry)
    if not mappings:
        records = collect(database, roots=roots, owners=owners)
    else:
        with tempfile.TemporaryDirectory(prefix="picolet-compdb-") as temp_dir:
            mapped = Path(temp_dir) / database.name
            mapped.write_text(json.dumps(resolved), encoding="utf-8")
            records = collect(mapped, roots=roots, owners=owners)
        for index, record in enumerate(records):
            record["provenance"]["database"] = str(database)
            record["compilation"] = entries[index]
            if index in mappings:
                record["provenance"]["file_mapping"] = mappings[index]
    return records


def build_scope(
    repo_root: Path, target: str, variant: str, compile_database: Path | None = None
) -> dict[str, Any]:
    """Full analysis inventory: frozen runtime, host/example Python, frontend and native sources."""
    repo_root = repo_root.resolve()
    scope = resolve_scope(repo_root, target, variant)
    shared = shared_scope()
    frozen = scope["groups"]["runtime_frozen_python"]
    frozen_files = {shared.source_path(scope, record) for record in frozen}
    project_files = tracked_project_files(repo_root)
    host_python = [
        path for path in project_files
        if path.suffix in PYTHON_SUFFIXES and path.resolve() not in frozen_files
    ]
    frontend = [path for path in project_files if path.suffix in FRONTEND_SUFFIXES]
    in_examples = repo_root / "examples"
    scope["groups"].update({
        "host_and_example_python": [
            _project_record(repo_root, path, "example-application" if path.is_relative_to(in_examples) else "picolet-host")
            for path in host_python
        ],
        "frontend": [
            _project_record(repo_root, path, "example-frontend" if path.is_relative_to(in_examples) else "picolet-bridge")
            for path in frontend
        ],
        "native": _native_records(repo_root, compile_database) if compile_database is not None else [],
    })
    scope["native_selection"] = (
        {"status": "selected", "compile_database": str(compile_database.resolve())}
        if compile_database is not None
        else {"status": "omitted", "reason": "no compiler database supplied; native sources are not selected"}
    )
    scope["excluded_path_segments"] = sorted(EXCLUDED_PARTS)
    scope["other_exclusions"] = OTHER_EXCLUSIONS
    return shared.parse_scope(scope)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", required=True, choices=sorted(RUNTIME_TARGETS))
    parser.add_argument("--variant", required=True)
    parser.add_argument("--compile-database", type=Path, help="normalised compiler database to add as the native group")
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    compile_database = args.compile_database
    if compile_database is not None and not compile_database.is_absolute():
        compile_database = repo_root / compile_database

    try:
        data = build_scope(repo_root, args.target, args.variant, compile_database)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))

    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
