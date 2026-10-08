#!/usr/bin/env python3
"""Run one report-only SAST or type-analysis tool against a selected runtime scope."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

SCANNERS = (
    "opengrep-stable",
    "opengrep-interfile-alpha",
    "semgrep-ce",
    "ruff-s",
    "pysa",
    "pyrefly",
)
POLICY_DIR = Path(__file__).resolve().parent / "sast"
PYTHON_SUFFIXES = {".py", ".pyi"}
NATIVE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".S", ".s"}
PROJECT_PREFIXES = (
    "packages/picolet/picolet/",
    "packages/picolet-bridge-js/src/",
    "examples/",
)
EXCLUDED_PARTS = {"tests", "scripts", "screenshots", "node_modules", "dist", "public", "_vendor", "vendor", "third_party", "__pycache__"}


class _AnalysisToolError(RuntimeError):
    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


def _load_scope_resolver(repo_root: Path):
    path = repo_root / "scripts/static_analysis_scope.py"
    spec = importlib.util.spec_from_file_location("picolet_static_analysis_scope", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source scope resolver: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.resolve_scope


def _tracked_project_files(repo_root: Path) -> list[Path]:
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
            if not is_app_source or suffix not in PYTHON_SUFFIXES | {".ts", ".tsx", ".js", ".jsx", ".vue"}:
                continue
        files.append(repo_root / path)
    return files


def _native_entries(repo_root: Path, compile_database: Path | None) -> list[dict[str, str]]:
    if compile_database is None:
        return []
    data = json.loads(compile_database.read_text(encoding="utf-8"))
    entries = {}
    for entry in data:
        path = (repo_root / entry["file"]).resolve()
        if path.suffix in NATIVE_SUFFIXES and path.is_file():
            relative = path.relative_to(repo_root).as_posix()
            entries[(relative, entry.get("owner", "unclassified"))] = {
                "path": relative,
                "owner": entry.get("owner", "unclassified"),
            }
    return [entries[key] for key in sorted(entries)]


def _runtime_files(scope: dict[str, Any], repo_root: Path) -> list[Path]:
    return [repo_root / item["path"] for item in scope["python_files"]]


def _write_summary(
    path: Path,
    scanner: str,
    target: str,
    variant: str,
    sources: dict[str, list[Path]],
    rc: int | None,
    operation_error: str | None = None,
) -> None:
    scope_path = path.parent / "analysis-scope.json"
    scope_report = json.loads(scope_path.read_text(encoding="utf-8")) if scope_path.exists() else {"groups": {}}
    owners = {
        entry["owner"]
        for group in scope_report["groups"].values()
        for entry in group
        if "owner" in entry
    }
    payload = {
        "scanner": scanner,
        "target": target,
        "variant": variant,
        "report_only": True,
        "scanner_exit_code": rc,
        "source_counts": {name: len(files) for name, files in sources.items()},
        "source_owners": sorted(owners),
    }
    if operation_error is not None:
        payload["status"] = "operation_failed"
        payload["operation_error"] = operation_error
    prerequisites_path = path.parent / "analysis-prerequisites.json"
    if scanner in {"pyrefly", "pysa"} and prerequisites_path.exists():
        prerequisites = json.loads(prerequisites_path.read_text(encoding="utf-8"))
        payload["prerequisites"] = prerequisites
        payload["coverage_status"] = "incomplete" if prerequisites["type_diagnostics_present"] else "not_assessed"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _invoke(
    command: list[str],
    cwd: Path,
    output: Path | None = None,
    log_path: Path | None = None,
    allowed_exit_codes: tuple[int, ...] = (0,),
) -> int:
    if output is not None:
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink(missing_ok=True)
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    log = result.stdout + result.stderr
    if log_path is not None:
        log_path.write_text(log, encoding="utf-8")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode not in allowed_exit_codes:
        raise _AnalysisToolError(f"analysis tool failed (exit {result.returncode}): {command[0]}", result.returncode)
    if output is not None and not output.exists():
        raise _AnalysisToolError(f"analysis tool did not produce its report: {command[0]}", result.returncode)
    return result.returncode


def _tool_status(scanner: str, rc: int) -> str:
    if scanner in {"semgrep-ce", "opengrep-stable", "opengrep-interfile-alpha"}:
        return "findings" if rc == 1 else "clean"
    if scanner == "pyrefly":
        return "diagnostics" if rc == 1 else "clean"
    if scanner == "pysa":
        return "diagnostics" if rc == 1 else "clean"
    return "clean"


def _stage_sources(paths: dict[str, str], repo_root: Path, destination: Path) -> dict[str, str]:
    staged = {}
    for source_path, target_path in paths.items():
        target = destination / target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / source_path, target)
        staged[str(target)] = source_path
    return staged


def _rebase_report_paths(path: Path, replacements: dict[str, str], work: Path, repo_root: Path) -> None:
    if path.is_dir():
        for report_path in path.rglob("*.json"):
            _rebase_report_paths(report_path, replacements, work, repo_root)
        return
    if not path.is_file():
        return
    report_text = path.read_text(encoding="utf-8")
    json_lines = False
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError:
        try:
            report = [json.loads(line) for line in report_text.splitlines() if line]
        except json.JSONDecodeError:
            return
        json_lines = True
    relative_replacements = {
        Path(staged).relative_to(work).as_posix(): source
        for staged, source in replacements.items()
    }
    work_prefix = str(work)

    def rebase(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, str):
            for staged, source in replacements.items():
                if value == staged or value.endswith(staged):
                    return source
            for staged, source in relative_replacements.items():
                if value == staged or value.endswith(f"/{staged}"):
                    return source
            if value == work_prefix:
                return str(repo_root)
            return value.replace(work_prefix, "<analysis-staging>")
        return value

    if json_lines:
        output = "\n".join(json.dumps(rebase(item), separators=(",", ":")) for item in report)
    else:
        output = json.dumps(rebase(report), indent=2)
    path.write_text(output + "\n", encoding="utf-8")


def _run_type_tools(scanner: str, scope: dict[str, Any], repo_root: Path, output_dir: Path, executable: str | None = None) -> int:
    pyrefly_report = output_dir / "pyrefly.sarif"
    with tempfile.TemporaryDirectory(prefix="picolet-sast-") as temp_dir:
        work = Path(temp_dir)
        staged = _stage_sources(
            {item["path"]: item["target_path"] for item in scope["python_files"]},
            repo_root, work / "src",
        )
        pyrefly_config = work / "pyrefly.toml"
        pyrefly_config.write_text(
            'project-includes = ["src/**/*.py"]\nsearch-path = ["src"]\n',
            encoding="utf-8",
        )
        pyrefly_pysa_report = work / "pyrefly-pysa.json"
        pyrefly_command = [
            executable if executable and scanner == "pyrefly" else "pyrefly",
            "check",
            "--config",
            str(pyrefly_config),
            "--search-path",
            str(work / "src"),
            "--disable-search-path-heuristics",
            "true",
            "--report-pysa",
            str(pyrefly_pysa_report),
            "--report-pysa-format",
            "json",
            "--output",
            f"sarif:{pyrefly_report}",
            *map(str, staged),
        ]
        try:
            # Exit 1 denotes type diagnostics, not a failed invocation.
            pyrefly_rc = _invoke(
                pyrefly_command, repo_root, pyrefly_report, output_dir / "pyrefly.log",
                allowed_exit_codes=(0, 1),
            )
        finally:
            _rebase_report_paths(pyrefly_report, staged, work, repo_root)
        (output_dir / "analysis-prerequisites.json").write_text(
            json.dumps({"pyrefly_exit_code": pyrefly_rc, "type_diagnostics_present": pyrefly_rc == 1}, indent=2) + "\n",
            encoding="utf-8",
        )
        if pyrefly_rc:
            print(f"Pyrefly reported diagnostics; downstream Pysa analysis may be incomplete (exit {pyrefly_rc}).", file=sys.stderr)
        if scanner == "pyrefly":
            return pyrefly_rc

        pyre_config = {
            "source_directories": [str(work / "src")],
            "search_path": [str(work / "src")],
            "workers": 1,
        }
        (work / ".pyre_configuration").write_text(json.dumps(pyre_config), encoding="utf-8")
        pysa_dir = output_dir / "pysa-results"
        pysa_command = [
            executable or "pyre",
            "--noninteractive",
            "--dot-pyre-directory",
            str(work / ".pyre"),
            "analyze",
            "--taint-models-path",
            str(POLICY_DIR / "pysa"),
            "--save-results-to",
            str(pysa_dir),
            "--output-format",
            "json",
            "--pyrefly-results",
            str(pyrefly_pysa_report),
        ]
        try:
            pysa_rc = _invoke(
                pysa_command, work, pysa_dir, output_dir / "pysa.log",
                allowed_exit_codes=(0, 1),
            )
        finally:
            _rebase_report_paths(pysa_dir, staged, work, repo_root)
        if pyrefly_rc:
            print(f"Pysa result is incomplete because Pyrefly exited {pyrefly_rc}.", file=sys.stderr)
        return pysa_rc



def _resolve_sources(repo_root: Path, target: str, variant: str, compile_database: Path | None, output_dir: Path):
    scope = _load_scope_resolver(repo_root)(repo_root, target, variant)
    (output_dir / "runtime-scope.json").write_text(
        json.dumps(scope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    runtime_files = _runtime_files(scope, repo_root)
    project_files = _tracked_project_files(repo_root)
    project_python = [p for p in project_files if p.suffix in PYTHON_SUFFIXES]
    host_python = [p for p in project_python if p not in runtime_files]
    frontend_files = [p for p in project_files if p.suffix in {".ts", ".tsx", ".js", ".jsx", ".vue"}]
    native_scope = _native_entries(repo_root, compile_database)
    native_files = [repo_root / item["path"] for item in native_scope]
    groups = {
        "runtime_frozen_python": [
            {key: item[key] for key in ("path", "target_path", "owner")}
            for item in scope["python_files"]
        ],
        "host_and_example_python": [
            {
                "path": path.relative_to(repo_root).as_posix(),
                "owner": "example-application" if path.is_relative_to(repo_root / "examples") else "picolet-host",
            }
            for path in host_python
        ],
        "frontend": [
            {
                "path": path.relative_to(repo_root).as_posix(),
                "owner": "example-frontend" if path.is_relative_to(repo_root / "examples") else "picolet-bridge",
            }
            for path in frontend_files
        ],
        "native": native_scope,
    }
    (output_dir / "analysis-scope.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": target,
                "variant": variant,
                "groups": groups,
                "excluded_path_segments": sorted(EXCLUDED_PARTS),
                "other_exclusions": [
                    "runtime Python not selected by the resolved build manifest",
                    "MicroPython mpy-cross host-tool sources",
                    "generated frontend bundles and installed dependencies",
                    "test, screenshot and build tooling",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    sources = {
        "runtime_python": runtime_files,
        "host_and_example_python": host_python,
        "frontend": frontend_files,
        "native": native_files,
    }
    return scope, sources


def _run_ruff(sources: dict[str, list[Path]], repo_root: Path, output_dir: Path, executable: str | None) -> int:
    scan_files = sources["runtime_python"] + sources["host_and_example_python"]
    if not scan_files:
        raise ValueError("source selection is empty")
    report = output_dir / "ruff-s.sarif"
    return _invoke(
        [executable or "ruff", "check", "--config", str(POLICY_DIR / "ruff.toml"), "--exit-zero",
         "--output-format", "sarif", "--output-file", str(report), *map(str, scan_files)],
        repo_root, report, output_dir / "ruff-s.log",
    )


def _run_pattern_scanner(scanner: str, sources: dict[str, list[Path]], repo_root: Path, output_dir: Path, executable: str | None) -> int:
    policy = json.loads((POLICY_DIR / "scanners.json").read_text(encoding="utf-8"))
    scans = {
        "project": sorted(set(sources["runtime_python"] + sources["host_and_example_python"] + sources["frontend"])),
        "native-c": [path for path in sources["native"] if path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}],
    }
    if not any(scans.values()):
        raise ValueError("source selection is empty")
    binary = executable or ("semgrep" if scanner == "semgrep-ce" else "opengrep")
    interfile = scanner == "opengrep-interfile-alpha"
    context = tempfile.TemporaryDirectory(prefix="picolet-sast-") if interfile else nullcontext(None)
    rc = 0
    with context as temp_dir:
        work = Path(temp_dir) if temp_dir is not None else None
        scan_root = repo_root
        staged = {}
        if work is not None:
            # Restrict companion discovery to the complete selected scope.
            scan_root = work / "src"
            paths = {path.relative_to(repo_root).as_posix(): path.relative_to(repo_root).as_posix()
                     for files in sources.values() for path in files}
            staged = _stage_sources(paths, repo_root, scan_root)
        for name, files in scans.items():
            if not files:
                continue
            report = output_dir / f"{scanner}-{name}.sarif"
            command = [binary, "scan"]
            for config in policy[name]:
                config_path = POLICY_DIR / config
                command.extend(["--config", str(config_path) if config_path.exists() else config])
            command.extend(["--no-git-ignore", "--x-ignore-semgrepignore-files", "--sarif-output", str(report)])
            if scanner == "semgrep-ce":
                command.append("--no-error")
            else:
                command.append("--jobs=4")
            if interfile:
                command.append("--taint-interfile")
            command.extend(str(scan_root / path.relative_to(repo_root)) for path in files)
            try:
                rc = max(rc, _invoke(command, scan_root, report, output_dir / f"{scanner}-{name}.log", allowed_exit_codes=(0, 1)))
            finally:
                if work is not None:
                    _rebase_report_paths(report, staged, work, repo_root)
    return rc


def _clear_reports(output_dir: Path) -> None:
    names = {
        "analysis-summary.json", "analysis-prerequisites.json", "runtime-scope.json", "analysis-scope.json",
        "pyrefly.sarif", "pyrefly.log", "pysa.log", "pysa-results", "ruff-s.sarif", "ruff-s.log",
    }
    for scanner in ("opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce"):
        for scope in ("project", "native-c"):
            names.update({f"{scanner}-{scope}.sarif", f"{scanner}-{scope}.log"})
    for name in names:
        path = output_dir / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def run(scanner: str, repo_root: Path, target: str, variant: str, compile_database: Path | None, output_dir: Path, executable: str | None = None) -> int:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    try:
        _clear_reports(output_dir)
        if scanner not in SCANNERS:
            raise ValueError(f"unknown scanner: {scanner}")
        if executable and ("/" in executable or "\\" in executable):
            executable = str(Path(executable).expanduser().resolve())
        scope, sources = _resolve_sources(repo_root, target, variant, compile_database, output_dir)
        if scanner in {"pyrefly", "pysa"}:
            rc = _run_type_tools(scanner, scope, repo_root, output_dir, executable)
        elif scanner == "ruff-s":
            rc = _run_ruff(sources, repo_root, output_dir, executable)
        else:
            rc = _run_pattern_scanner(scanner, sources, repo_root, output_dir, executable)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        _write_summary(output_dir / "analysis-summary.json", scanner, target, variant, sources,
                       getattr(exc, "returncode", None), str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    _write_summary(output_dir / "analysis-summary.json", scanner, target, variant, sources, rc)
    status = _tool_status(scanner, rc)
    if status != "clean":
        print(f"{scanner} completed with {status} (tool exit {rc}); findings and diagnostics are report-only.", file=sys.stderr)
    return 0


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
    try:
        return run(args.scanner, repo_root, args.target, args.variant, compile_database, output_dir, args.executable)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
