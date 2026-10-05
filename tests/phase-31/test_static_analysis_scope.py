import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCOPE = _load_module("static_analysis_scope", REPO_ROOT / "scripts/static_analysis_scope.py")
RECORDER = _load_module("record_compile_command", REPO_ROOT / "scripts/record_compile_command.py")
NORMALISER = _load_module("normalise_compile_database", REPO_ROOT / "scripts/normalise_compile_database.py")
RUNNER = _load_module("run_sast", REPO_ROOT / "scripts/run_sast.py")
def test_project_source_scope_includes_apps_and_excludes_tooling():
    paths = {path.relative_to(REPO_ROOT).as_posix() for path in RUNNER._tracked_project_files(REPO_ROOT)}
    assert "packages/picolet/picolet/cli/build_cmd.py" in paths
    assert "packages/picolet-bridge-js/src/index.ts" in paths
    assert "examples/notes/ui/src/App.vue" in paths
    assert "examples/dashboard/scripts/generate_screenshots.py" not in paths
    assert "packages/picolet/picolet/_vendor/manifestfile.py" not in paths
RUNTIME_TARGETS = SCOPE.RUNTIME_TARGETS
resolve_scope = SCOPE.resolve_scope

def test_compiler_sources_keep_separate_ownership_classes(tmp_path):
    runtime = tmp_path / "packages/picolet-runtime"

    assert RECORDER._owner(runtime / "variants/cli/unix/handler.c", tmp_path) == "picolet-runtime"
    assert RECORDER._owner(runtime / "micropython/ports/unix/main.c", tmp_path) == "micropython-integration"
    assert RECORDER._owner(runtime / "lib/lv_binding_micropython/lv_mpy.c", tmp_path) == "generated-lv-binding"
    assert RECORDER._owner(runtime / "micropython/ports/unix/build/frozen_content.c", tmp_path) == "generated-frozen-python"


def test_compiler_sources_without_owner_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="no ownership mapping"):
        RECORDER._owner(tmp_path / "unclassified.c", tmp_path)


def test_compile_database_normaliser_preserves_command_and_owner(tmp_path):
    source = tmp_path / "packages/picolet-runtime/variants/cli/unix/handler.c"
    record_path = tmp_path / "commands.jsonl"
    record_path.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "build"),
                "file": str(source),
                "arguments": ["gcc", "-I", "include", "-c", str(source)],
                "owner": "picolet-runtime",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [entry] = NORMALISER.normalise(tmp_path, record_path)

    assert entry["file"] == "packages/picolet-runtime/variants/cli/unix/handler.c"
    assert entry["arguments"] == ["gcc", "-I", "include", "-c", str(source)]
    assert entry["owner"] == "picolet-runtime"


def test_compile_database_rejects_empty_or_external_inputs(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="capture is empty"):
        NORMALISER.normalise(tmp_path, empty)

    outside = tmp_path.parent / "outside.c"
    record = tmp_path / "outside.jsonl"
    record.write_text(
        json.dumps(
            {
                "directory": str(tmp_path),
                "file": str(outside),
                "arguments": ["cc", "-c", str(outside)],
                "owner": "external",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="outside the repository"):
        NORMALISER.normalise(tmp_path, record)


@pytest.mark.parametrize(
    ("target", "variant"),
    [
        (target, variant)
        for target, variants in RUNTIME_TARGETS.items()
        for variant in sorted(variants)
    ],
)
def test_supported_runtime_scope_resolves_existing_owned_python_files(target, variant):
    scope = resolve_scope(REPO_ROOT, target, variant)

    assert scope["target"] == target
    assert scope["variant"] == variant
    assert scope["python_files"]
    assert all((REPO_ROOT / source["path"]).is_file() for source in scope["python_files"])
    assert all(source["owner"] in {"picolet-runtime", "micropython-integration", "micropython-lib"} for source in scope["python_files"])


def test_linux_cli_scope_includes_manifest_dependencies_and_runtime_sources():
    scope = resolve_scope(REPO_ROOT, "linux-x64", "cli")
    files = {source["path"]: source for source in scope["python_files"]}

    assert "packages/picolet-runtime/micropython/extmod/asyncio/core.py" in files
    assert "packages/picolet-runtime/lib/micropython-lib/python-stdlib/os-path/os/path.py" in files
    assert "packages/picolet-runtime/python/picolet/_dispatcher.py" in files
    assert "packages/picolet-runtime/python/picolet_ui/_app.py" not in files
    assert files["packages/picolet-runtime/python/picolet/_dispatcher.py"]["owner"] == "picolet-runtime"


def test_unsupported_runtime_combination_is_rejected():
    with pytest.raises(ValueError, match="unsupported target/variant combination"):
        resolve_scope(REPO_ROOT, "windows-x64", "mcp")
