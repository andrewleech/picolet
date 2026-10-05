#!/usr/bin/env python3
"""Export the frozen Python files selected by a Picolet runtime manifest."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


RUNTIME_TARGETS: dict[str, set[str]] = {
    "linux-x64": {"cli", "webview", "lvgl", "mcp", "tui"},
    "windows-x64": {"cli", "webview", "lvgl", "tui"},
    "macos-x64": {"cli", "webview", "lvgl"},
    "macos-arm64": {"cli", "webview", "lvgl"},
}


def _load_manifestfile(tools_dir: Path) -> ModuleType:
    module_path = tools_dir / "manifestfile.py"
    spec = importlib.util.spec_from_file_location("picolet_manifestfile", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load MicroPython manifest resolver at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest_for(runtime: Path, target: str, variant: str) -> tuple[str, str]:
    port = "windows" if target == "windows-x64" else "unix"
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
    return port, str((runtime / "manifests" / manifest).resolve())


def _owner(path: Path, runtime: Path) -> str:
    try:
        path.relative_to(runtime / "python")
        return "picolet-runtime"
    except ValueError:
        pass
    try:
        path.relative_to(runtime / "micropython")
        return "micropython-integration"
    except ValueError:
        pass
    try:
        path.relative_to(runtime / "lib" / "micropython-lib")
        return "micropython-lib"
    except ValueError:
        pass
    try:
        path.relative_to(runtime / "lib" / "lv_binding_micropython")
        return "lv-binding"
    except ValueError:
        pass
    raise ValueError(f"manifest input has no ownership mapping: {path}")

def resolve_scope(repo_root: Path, target: str, variant: str) -> dict[str, Any]:
    if target not in RUNTIME_TARGETS or variant not in RUNTIME_TARGETS[target]:
        raise ValueError(f"unsupported target/variant combination: {target}/{variant}")

    runtime = (repo_root / "packages/picolet-runtime").resolve()
    mpy = runtime / "micropython"
    port, manifest_path = _manifest_for(runtime, target, variant)
    manifest = Path(manifest_path)
    if not manifest.is_file():
        raise FileNotFoundError(f"runtime manifest not found: {manifest}")

    module = _load_manifestfile(mpy / "tools")
    resolver = module.ManifestFile(
        module.MODE_FREEZE,
        {
            "MPY_DIR": str(mpy),
            "PORT_DIR": str(mpy / "ports" / port),
            "MPY_LIB_DIR": str(runtime / "lib" / "micropython-lib"),
        },
    )
    resolver.execute(str(manifest))

    files: dict[tuple[str, str], dict[str, str]] = {}
    for result in resolver.files():
        if result.file_type != module.FILE_TYPE_LOCAL:
            raise ValueError(f"non-local manifest input is not supported: {result}")
        source = Path(result.full_path).resolve()
        if source.suffix != ".py":
            continue
        if not source.is_file():
            raise FileNotFoundError(f"manifest input does not exist: {source}")
        relpath = source.relative_to(repo_root.resolve()).as_posix()
        files[(relpath, result.target_path)] = {
            "path": relpath,
            "target_path": result.target_path,
            "owner": _owner(source, runtime),
        }

    if not files:
        raise ValueError(f"manifest resolved no Python files: {manifest}")
    return {
        "schema_version": 1,
        "target": target,
        "variant": variant,
        "port": port,
        "manifest": manifest.relative_to(repo_root.resolve()).as_posix(),
        "python_files": [files[key] for key in sorted(files)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", required=True, choices=sorted(RUNTIME_TARGETS))
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()

    try:
        data = resolve_scope(args.repo_root.resolve(), args.target, args.variant)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
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
