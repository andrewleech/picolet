import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "scripts/sast"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# check_sast_fixtures imports the scope module by name.
_load_module("static_analysis_scope", REPO_ROOT / "scripts/static_analysis_scope.py")
FIXTURES = _load_module("check_sast_fixtures", REPO_ROOT / "scripts/check_sast_fixtures.py")
INSTALLER = _load_module("install_sast_core", REPO_ROOT / "scripts/install_sast_core.py")


def _distribution(site, name, version):
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


def test_missing_typeshed_fails_closed_with_the_missing_file(tmp_path):
    from mpy_analysis.typing_environment import load_typing_environment

    stubs = tmp_path / "policy/stubs"
    stubs.mkdir(parents=True)
    (stubs / "micropython.pyi").write_text("")
    _distribution(stubs, "micropython-unix-stubs", "1.29.0.post1")
    _distribution(stubs, "micropython-stdlib-stubs", "1.29.0.post2")
    (tmp_path / "policy/typing.json").write_text(json.dumps({"environments": [{
        "port": "unix", "target": "linux-x64", "variant": "cli", "directory": "stubs",
        "typeshed": "missing-typeshed", "python_version": "3.9", "python_platform": "linux",
        "pyrefly_version": "1.3.2", "target_package": "micropython-unix-stubs",
        "packages": {"micropython-unix-stubs": "1.29.0.post1", "micropython-stdlib-stubs": "1.29.0.post2"},
        "provenance": {"firmware_version": "1.29.0", "source_revision": "unrecorded"},
    }]}))
    with pytest.raises(FileNotFoundError, match=r"stdlib/builtins\.pyi"):
        load_typing_environment(tmp_path / "policy", "unix", target="linux-x64", variant="cli")


def test_unsupported_selection_has_no_typing_environment():
    from mpy_analysis.typing_environment import load_typing_environment

    with pytest.raises(ValueError, match="environment"):
        load_typing_environment(POLICY, "windows", target="windows-x64", variant="mcp")


def test_unsupported_fixture_selection_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsupported target/variant"):
        FIXTURES.build_inputs("ruff-s", "windows-x64", "mcp", tmp_path, POLICY, {})


def test_unselected_scanner_profile_is_rejected(tmp_path):
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "fixtures.json").write_text('{"schema_version": 1, "scanners": {}, "profiles": {}}')
    with pytest.raises(ValueError, match="selects no fixtures"):
        FIXTURES.build_inputs("ruff-s", "linux-x64", "cli", tmp_path, policy, {})


def test_missing_shared_core_is_an_error_not_a_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mpy_analysis", None)
    monkeypatch.delitem(sys.modules, "mpy_analysis.fixtures", raising=False)
    assert FIXTURES.main(["--scanner", "ruff-s", "--output-dir", str(tmp_path / "out")]) == 1
    assert "install_sast_core.py" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


PYPROJECT = """\
[project]
name = "mpy-analysis"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["dep==1.0"]

[project.scripts]
sast-example = "mpy_analysis:main"
"""


def _core_repository(tmp_path):
    repo = tmp_path / "checkout"
    project = repo / "tools/mpy_analysis"
    package = project / "mpy_analysis"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text(PYPROJECT)
    (package / "__init__.py").write_text("VALUE = 1\n")
    (package / "data").mkdir()
    (package / "data/table.txt").write_text("data\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "core"]):
        subprocess.run(["git", "-C", str(repo), *command], check=True, env=env)
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    pin = {"distribution": "mpy-analysis", "package": "mpy_analysis",
           "subdirectory": "tools/mpy_analysis", "revision": revision}
    return repo, pin


def _installed_site(tmp_path, project, requires_python=">=3.10"):
    site = tmp_path / "site"
    shutil.copytree(project / "mpy_analysis", site / "mpy_analysis")
    info = site / "mpy_analysis-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: mpy-analysis\nVersion: 0.1.0\n"
        f"Requires-Python: {requires_python}\nRequires-Dist: dep==1.0\n"
    )
    (info / "entry_points.txt").write_text("[console_scripts]\nsast-example = mpy_analysis:main\n")
    return site


def test_installed_core_must_match_the_pinned_tree(tmp_path):
    repo, pin = _core_repository(tmp_path)
    # A later edit in the worktree must not change what the pin exports.
    (repo / "tools/mpy_analysis/mpy_analysis/__init__.py").write_text("VALUE = 2\n")
    project = INSTALLER.export_revision(repo, pin, tmp_path / "export")
    site = _installed_site(tmp_path, project)

    receipt = INSTALLER.verify(project, pin, sys.executable, site)
    assert receipt["revision"] == pin["revision"]
    assert "requires_python" in receipt["metadata_verified"]

    (site / "mpy_analysis/__init__.py").write_text("VALUE = 2\n")
    with pytest.raises(INSTALLER.CoreInstallError, match="not revision"):
        INSTALLER.verify(project, pin, sys.executable, site)


def test_installed_metadata_must_match_the_pinned_project(tmp_path):
    repo, pin = _core_repository(tmp_path)
    project = INSTALLER.export_revision(repo, pin, tmp_path / "export")
    site = _installed_site(tmp_path, project, requires_python=">=3.9")
    with pytest.raises(INSTALLER.CoreInstallError, match="requires_python"):
        INSTALLER.verify(project, pin, sys.executable, site)


def test_missing_pinned_revision_fails_without_fetching(tmp_path):
    repo, pin = _core_repository(tmp_path)
    pin = {**pin, "revision": "0" * 40}
    with pytest.raises(INSTALLER.CoreInstallError, match="not present"):
        INSTALLER.export_revision(repo, pin, tmp_path / "export")
