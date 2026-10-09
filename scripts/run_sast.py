#!/usr/bin/env python3
"""Run one report-only SAST or type-analysis tool against a selected Picolet runtime scope.

This selects the Picolet sources for the target/variant (static_analysis_scope.py)
and hands the resulting inventory to the shared mpy_analysis runner, which owns
scanner execution, report rebasing and the summary/receipt contract. Picolet's
scanner policy is read from scripts/sast.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCANNERS = (
    "opengrep-stable",
    "opengrep-interfile-alpha",
    "semgrep-ce",
    "ruff-s",
    "pysa",
    "pyrefly",
)
POLICY_DIR = Path(__file__).resolve().parent / "sast"


def _load_scope_module():
    path = Path(__file__).resolve().with_name("static_analysis_scope.py")
    spec = importlib.util.spec_from_file_location("picolet_static_analysis_scope", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source scope adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _remove_owned_reports(output_dir: Path, owned: set[str]) -> None:
    for name in owned:
        path = output_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _overlaps_owned(path: Path, output_dir: Path, owned: set[str]) -> bool:
    """Whether path is, or lies inside, an artifact the runner would remove from output_dir."""
    candidates = {Path(os.path.abspath(path)), path.resolve()}
    return any(
        candidate == output_dir / name or output_dir / name in candidate.parents
        for candidate in candidates
        for name in owned
    )



def _record_selection_failure(output_dir: Path, scanner: str, target: str, variant: str, error: str) -> None:
    """Replace stale reports with an operation_failed summary when no inventory could be built."""
    from mpy_analysis.runner import OWNED_ARTIFACTS

    _remove_owned_reports(output_dir, set(OWNED_ARTIFACTS))
    summary = {
        "schema_version": 1,
        "scanner": scanner,
        "report_only": True,
        "target": target,
        "variant": variant,
        "port": None,
        "status": "operation_failed",
        "scanner_exit_code": None,
        "invocations": [],
        "findings_count": 0,
        "source_counts": {},
        "source_owners": [],
        "groups": {},
        "coverage_status": "not_assessed",
        "operation_error": error,
    }
    (output_dir / "analysis-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run(
    scanner: str,
    repo_root: Path,
    target: str,
    variant: str,
    compile_database: Path | None,
    output_dir: Path,
    executable: str | None = None,
) -> int:
    """Select the scope, then run the shared scanner runner. Returns the shared exit code."""
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    try:
        import mpy_analysis.runner as shared_runner
    except ImportError as exc:
        print(f"mpy_analysis is not importable; install the pinned shared tooling: {exc}", file=sys.stderr)
        return 1
    if scanner not in shared_runner.SCANNERS:
        print(f"unknown scanner: {scanner}", file=sys.stderr)
        return 1
    if compile_database is not None and _overlaps_owned(compile_database, output_dir, shared_runner.OWNED_ARTIFACTS):
        print(f"compiler database conflicts with an owned output artifact in {output_dir}: {compile_database}", file=sys.stderr)
        return 1
    if compile_database is None and scanner in shared_runner.PATTERN_SCANNERS:
        error = (
            f"{scanner} requires --compile-database: without compiler inputs the native group "
            "would be empty and native sources silently unassessed"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        _record_selection_failure(output_dir, scanner, target, variant, error)
        print(error, file=sys.stderr)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        scope = _load_scope_module().build_scope(repo_root, target, variant, compile_database)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        _record_selection_failure(output_dir, scanner, target, variant, str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="picolet-sast-scope-") as temp_dir:
        scope_path = Path(temp_dir) / "scope.json"
        scope_path.write_text(json.dumps(scope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return shared_runner.run(scanner, scope_path, POLICY_DIR, output_dir, executable)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", choices=SCANNERS, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--compile-database", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("sast-results"))
    parser.add_argument("--executable", help="Override the selected analyser executable (Pysa: pyre; prerequisite: pyrefly on PATH)")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    compile_database = args.compile_database
    if compile_database is not None and not compile_database.is_absolute():
        compile_database = repo_root / compile_database
    return run(args.scanner, repo_root, args.target, args.variant, compile_database, output_dir, args.executable)


if __name__ == "__main__":
    raise SystemExit(main())
