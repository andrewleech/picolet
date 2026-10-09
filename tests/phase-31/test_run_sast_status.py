import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_sast_status", REPO_ROOT / "scripts/run_sast.py")
RUNNER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RUNNER)


def test_scope_failure_clears_owned_reports_and_preserves_other_outputs(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    (output / "opengrep-stable-project.sarif").write_text("stale")
    (output / "analysis-scope.json").write_text("stale")
    (output / "pysa-results").mkdir()
    (output / "pysa-results/errors.json").write_text("stale")
    (output / "compile_commands.json").write_text("compiler inputs")
    (output / "user-notes.txt").write_text("keep")

    class FailingScope:
        @staticmethod
        def build_scope(*_):
            raise ValueError("manifest unavailable")

    monkeypatch.setattr(RUNNER, "_load_scope_module", lambda: FailingScope)

    assert RUNNER.run("ruff-s", tmp_path, "linux-x64", "cli", None, output) == 1

    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["status"] == "operation_failed"
    assert summary["operation_error"] == "manifest unavailable"
    assert summary["scanner_exit_code"] is None
    assert summary["source_counts"] == {}
    assert not (output / "opengrep-stable-project.sarif").exists()
    assert not (output / "analysis-scope.json").exists()
    assert not (output / "pysa-results").exists()
    assert (output / "compile_commands.json").read_text() == "compiler inputs"
    assert (output / "user-notes.txt").read_text() == "keep"


def test_unsupported_target_variant_records_selection_failure(tmp_path):
    output = tmp_path / "out"

    assert RUNNER.run("pyrefly", REPO_ROOT, "windows-x64", "mcp", None, output) == 1

    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["status"] == "operation_failed"
    assert "unsupported target/variant combination" in summary["operation_error"]


def test_missing_shared_tooling_fails_without_running_a_scanner(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mpy_analysis.runner", None)

    assert RUNNER.run("ruff-s", REPO_ROOT, "linux-x64", "cli", None, tmp_path / "out") == 1

    assert "mpy_analysis is not importable" in capsys.readouterr().err


def test_compile_database_colliding_with_owned_output_is_not_removed(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    database = output / "analysis-scope.json"
    database.write_text("compiler inputs")
    (output / "opengrep-stable-project.sarif").write_text("stale")
    monkeypatch.setattr(RUNNER, "_load_scope_module", lambda: pytest.fail("selection ran with a colliding input"))

    assert RUNNER.run("opengrep-stable", REPO_ROOT, "linux-x64", "cli", database, output) == 1

    assert database.read_text() == "compiler inputs"
    assert (output / "opengrep-stable-project.sarif").read_text() == "stale"
    assert not (output / "analysis-summary.json").exists()


def test_compile_database_beside_owned_outputs_survives_selection_failure(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    database = output / "compile_commands.json"
    database.write_text("[]")

    assert RUNNER.run("ruff-s", REPO_ROOT, "linux-x64", "cli", database, output) == 1

    assert database.read_text() == "[]"
    assert json.loads((output / "analysis-summary.json").read_text())["status"] == "operation_failed"


def _recorded_database(tmp_path):
    """Normaliser output for one real compile, as the build capture would produce it."""
    normaliser = importlib.util.spec_from_file_location(
        "normalise_compile_database", REPO_ROOT / "scripts/normalise_compile_database.py"
    )
    module = importlib.util.module_from_spec(normaliser)
    normaliser.loader.exec_module(module)
    source = REPO_ROOT / "packages/picolet-runtime/variants/common/romfs_trailer.c"
    record = tmp_path / "commands.jsonl"
    record.write_text(json.dumps({
        "directory": str(tmp_path), "file": str(source), "arguments": ["gcc", "-c", str(source)],
        "owner": "picolet-runtime",
    }) + "\n")
    database = tmp_path / "compile_commands.json"
    database.write_text(json.dumps(module.normalise(REPO_ROOT, record)))
    return database


@pytest.mark.parametrize("scanner", ["opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce"])
def test_pattern_scanners_require_a_compile_database(tmp_path, scanner, capsys):
    output = tmp_path / "out"
    (output).mkdir()
    (output / "analysis-scope.json").write_text("stale")

    assert RUNNER.run(scanner, REPO_ROOT, "linux-x64", "cli", None, output, str(tmp_path / "absent-tool")) == 1

    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["status"] == "operation_failed"
    assert summary["coverage_status"] == "not_assessed"
    assert "--compile-database" in summary["operation_error"]
    assert not (output / "analysis-scope.json").exists()
    assert "--compile-database" in capsys.readouterr().err


@pytest.mark.parametrize("scanner", RUNNER.SCANNERS)
def test_missing_executable_records_failure_with_the_selected_sources(tmp_path, scanner):
    output = tmp_path / "out"
    database = _recorded_database(tmp_path)

    result = RUNNER.run(scanner, REPO_ROOT, "linux-x64", "cli", database, output, str(tmp_path / "absent-tool"))

    assert result == 1
    summary = json.loads((output / "analysis-summary.json").read_text())
    assert summary["status"] == "operation_failed"
    assert summary["scanner_exit_code"] is None
    assert summary["port"] == "unix"
    assert summary["source_counts"]["runtime_frozen_python"] > 0
    assert summary["source_counts"]["native"] == 1
    assert "picolet-runtime" in summary["source_owners"]
    assert (output / "analysis-scope.json").is_file()
