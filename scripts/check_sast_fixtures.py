#!/usr/bin/env python3
"""Check that the selected analysis tool distinguishes its positive and negative fixtures."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


FIXTURES = Path(__file__).resolve().parents[1] / "tests/phase-31/fixtures/sast"
PICKLE_RULE = "python.lang.security.deserialization.pickle.avoid-pickle"


def _run(command: list[str], repo_root: Path) -> None:
    subprocess.run(command, cwd=repo_root, check=True)


def _sarif_findings(path: Path) -> list[tuple[str, str]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return [
        (item["ruleId"], item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"])
        for run in report["runs"]
        for item in run.get("results", [])
    ]


def _check_sast(scanner: str, repo_root: Path, temp: Path) -> None:
    report = temp / "sast.sarif"
    binary = "semgrep" if scanner == "semgrep-ce" else "opengrep"
    command = [binary, "scan", "--config", "p/security-audit"]
    if scanner == "semgrep-ce":
        command.extend(["--no-error", "--sarif-output", str(report)])
    else:
        command.extend(["--sarif-output", str(report)])
        if scanner == "opengrep-interfile-alpha":
            command.append("--taint-interfile")
    command.extend(
        str(FIXTURES / name)
        for name in ("deserialization_positive.py", "deserialization_negative.py")
    )
    _run(command, repo_root)
    findings = _sarif_findings(report)
    positive = "deserialization_positive.py"
    negative = "deserialization_negative.py"
    if not any(rule == PICKLE_RULE and positive in path for rule, path in findings):
        raise RuntimeError(f"{scanner} did not flag the pickle positive fixture")
    if any(rule == PICKLE_RULE and negative in path for rule, path in findings):
        raise RuntimeError(f"{scanner} flagged the JSON negative fixture")


def _check_ruff(repo_root: Path, temp: Path) -> None:
    report = temp / "ruff.json"
    _run(
        [
            "ruff", "check", "--isolated", "--select", "S", "--exit-zero", "--output-format", "json",
            "--output-file", str(report), str(FIXTURES / "shell_positive.py"),
            str(FIXTURES / "shell_negative.py"),
        ],
        repo_root,
    )
    findings = json.loads(report.read_text(encoding="utf-8"))
    positive = [item for item in findings if item["filename"].endswith("shell_positive.py")]
    negative = [item for item in findings if item["filename"].endswith("shell_negative.py")]
    if not any(item["code"] == "S602" for item in positive) or negative:
        raise RuntimeError("Ruff S fixture results did not match the expected shell=True finding")


def _check_pyrefly(repo_root: Path, temp: Path) -> None:
    report = temp / "pyrefly.sarif"
    pysa_report = temp / "pyrefly-pysa.json"
    config = temp / "pyrefly.toml"
    config.write_text(f'search-path = ["{FIXTURES.as_posix()}"]\n', encoding="utf-8")
    command = [
        "pyrefly", "check", "--config", str(config), "--search-path", str(FIXTURES),
        "--disable-search-path-heuristics", "true", "--report-pysa", str(pysa_report),
        "--report-pysa-format", "json", "--output", f"sarif:{report}",
        str(FIXTURES / "typing_positive.py"), str(FIXTURES / "typing_negative.py"),
    ]
    result = subprocess.run(command, cwd=repo_root, check=False)
    if result.returncode and not report.is_file():
        raise RuntimeError(f"Pyrefly failed without writing SARIF (exit {result.returncode})")
    findings = _sarif_findings(report)
    if not any(rule == "bad-assignment" and "typing_positive.py" in path for rule, path in findings):
        raise RuntimeError("Pyrefly did not report the deliberately incompatible assignment")
    if any("typing_negative.py" in path for _, path in findings):
        raise RuntimeError("Pyrefly reported a diagnostic on the valid typing fixture")


def _check_pysa(repo_root: Path, temp: Path) -> None:
    from run_sast import _run_type_tools

    scope: dict[str, Any] = {
        "python_files": [
            {
                "path": (FIXTURES / name).relative_to(repo_root).as_posix(),
                "target_path": name,
                "owner": "fixture",
            }
            for name in ("pysa_positive.py", "pysa_negative.py")
        ]
    }
    _run_type_tools("pysa", scope, repo_root, temp)
    report = temp / "pysa-results" / "taint-output.json"
    results = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines() if line]
    if not any("pysa_positive.py" in json.dumps(item) for item in results):
        raise RuntimeError("Pysa did not report input() flowing to eval()")
    if any("pysa_negative.py" in json.dumps(item) for item in results):
        raise RuntimeError("Pysa reported a finding for the constant eval() negative fixture")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scanner",
        choices=("opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce", "ruff-s", "pysa", "pyrefly"),
        required=True,
    )
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        with tempfile.TemporaryDirectory(prefix="picolet-sast-fixtures-") as temp_dir:
            temp = Path(temp_dir)
            if args.scanner in {"opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce"}:
                _check_sast(args.scanner, repo_root, temp)
            elif args.scanner == "ruff-s":
                _check_ruff(repo_root, temp)
            elif args.scanner == "pyrefly":
                _check_pyrefly(repo_root, temp)
            else:
                _check_pysa(repo_root, temp)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{args.scanner}: positive and negative fixtures passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
