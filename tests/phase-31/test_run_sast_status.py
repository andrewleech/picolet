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


def test_invoke_accepts_findings_report_and_rejects_operational_error(tmp_path, capsys):
    tool = tmp_path / "scanner"
    _executable(tool, "import pathlib, sys\np=pathlib.Path(sys.argv[1]); p.write_text('fresh')\nprint('finding')\nsys.exit(1)\n")
    report = tmp_path / "report.sarif"
    assert RUNNER._invoke([str(tool), str(report)], tmp_path, report, allowed_exit_codes=(0, 1)) == 1
    assert report.read_text() == "fresh"
    assert "finding" in capsys.readouterr().out

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


