import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_sast_status", REPO_ROOT / "scripts/run_sast.py")
RUNNER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RUNNER)


def _executable(path: Path, text: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_invoke_accepts_findings_report_and_rejects_operational_error(tmp_path):
    tool = tmp_path / "scanner"
    _executable(tool, "import pathlib, sys\np=pathlib.Path(sys.argv[1]); p.write_text('fresh')\nprint('finding')\nsys.exit(1)\n")
    report = tmp_path / "report.sarif"
    assert RUNNER._invoke([str(tool), str(report)], tmp_path, report, allowed_exit_codes=(0, 1)) == 1

    _executable(tool, "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('partial')\nsys.exit(2)\n")
    with pytest.raises(RuntimeError, match="exit 2"):
        RUNNER._invoke([str(tool), str(report)], tmp_path, report, allowed_exit_codes=(0, 1))


def test_invoke_removes_stale_report_before_tool_runs(tmp_path):
    tool = tmp_path / "scanner"
    report = tmp_path / "report.sarif"
    report.write_text("stale")
    _executable(tool, "import sys\nsys.exit(2)\n")
    with pytest.raises(RuntimeError, match="exit 2"):
        RUNNER._invoke([str(tool), str(report)], tmp_path, report, allowed_exit_codes=(0, 1))
    assert not report.exists()


def test_pysa_surfaces_pyrefly_prerequisite_status(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / "pyrefly",
        "import pathlib, sys\na=sys.argv\npathlib.Path(a[a.index('--report-pysa')+1]).write_text('{}')\nout=a[a.index('--output')+1].split(':',1)[1]\npathlib.Path(out).write_text('{}')\nsys.exit(1)\n",
    )
    _executable(
        bin_dir / "pyre",
        "import pathlib, sys\na=sys.argv\nout=pathlib.Path(a[a.index('--save-results-to')+1]); out.mkdir(parents=True); (out/'taint-output.json').write_text('')\nsys.exit(0)\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    scope = {"python_files": []}
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = RUNNER._run_type_tools("pysa", scope, tmp_path, output_dir)
    assert result == 0
    prerequisites = json.loads((output_dir / "analysis-prerequisites.json").read_text())
    assert prerequisites == {"pyrefly_exit_code": 1, "type_diagnostics_present": True}


@pytest.mark.parametrize("scanner", ["opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce"])
def test_scan_operation_failure_records_actual_exit_status(tmp_path, monkeypatch, scanner):
    repo_root = tmp_path / "repo"
    runtime = repo_root / "runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("value = 1\n", encoding="utf-8")
    scope = {
        "python_files": [
            {"path": "runtime.py", "target_path": "runtime.py", "owner": "runtime"}
        ]
    }
    monkeypatch.setattr(RUNNER, "_load_scope_resolver", lambda _: lambda *_args: scope)
    monkeypatch.setattr(RUNNER, "_tracked_project_files", lambda _: [runtime])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / ("semgrep" if scanner == "semgrep-ce" else "opengrep"),
        "import pathlib, sys\nargs=sys.argv\nreport=pathlib.Path(args[args.index('--sarif-output')+1])\nreport.write_text('partial report')\nprint('scanner terminated')\nsys.exit(143)\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    output_dir = tmp_path / "sast-results"

    result = RUNNER.run(scanner, repo_root, "linux-x64", "webview", None, output_dir)

    assert result == 1
    summary = json.loads((output_dir / "analysis-summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "operation_failed"
    assert summary["scanner_exit_code"] == 143
    assert "exit 143" in summary["operation_error"]


@pytest.mark.parametrize("scanner", RUNNER.SCANNERS)
def test_missing_executable_records_failure_for_every_scanner(tmp_path, monkeypatch, scanner):
    runtime = tmp_path / "runtime.py"
    runtime.write_text("value = 1\n")
    scope = {"python_files": [{"path": "runtime.py", "target_path": "runtime.py", "owner": "runtime"}]}
    monkeypatch.setattr(RUNNER, "_load_scope_resolver", lambda _: lambda *_: scope)
    monkeypatch.setattr(RUNNER, "_tracked_project_files", lambda _: [])
    monkeypatch.setenv("PATH", "")
    output = tmp_path / "out"
    assert RUNNER.run(scanner, tmp_path, "linux-x64", "cli", None, output) == 1
    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["status"] == "operation_failed"
    assert summary["scanner_exit_code"] is None
    assert summary["source_counts"]["runtime_python"] == 1


def test_scope_failure_clears_owned_reports_and_preserves_other_outputs(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    (output / "opengrep-stable-project.sarif").write_text("stale")
    (output / "analysis-scope.json").write_text("stale")
    (output / "compile_commands.json").write_text("compiler inputs")
    (output / "user-notes.txt").write_text("keep")

    def fail_scope(*_):
        raise ValueError("manifest unavailable")

    monkeypatch.setattr(RUNNER, "_load_scope_resolver", lambda _: fail_scope)
    assert RUNNER.run("ruff-s", tmp_path, "linux-x64", "cli", None, output) == 1
    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["operation_error"] == "manifest unavailable"
    assert summary["source_counts"] == {}
    assert not (output / "opengrep-stable-project.sarif").exists()
    assert not (output / "analysis-scope.json").exists()
    assert (output / "compile_commands.json").read_text() == "compiler inputs"
    assert (output / "user-notes.txt").read_text() == "keep"
