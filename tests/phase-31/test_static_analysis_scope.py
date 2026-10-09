import importlib.util
import json
import sys
from pathlib import Path

import pytest
from mpy_analysis import scope as shared_scope


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCOPE = _load_module("static_analysis_scope", REPO_ROOT / "scripts/static_analysis_scope.py")
RECORDER = _load_module("record_compile_command", REPO_ROOT / "scripts/record_compile_command.py")
NORMALISER = _load_module("normalise_compile_database", REPO_ROOT / "scripts/normalise_compile_database.py")

RUNTIME_TARGETS = SCOPE.RUNTIME_TARGETS
resolve_scope = SCOPE.resolve_scope
FROZEN = "runtime_frozen_python"
RUNTIME_OWNERS = {"picolet-runtime", "micropython-integration", "micropython-lib", "lv-binding"}


def _physical(scope, record):
    return shared_scope.source_path(scope, record).relative_to(REPO_ROOT).as_posix()


def test_project_source_scope_includes_apps_and_excludes_tooling():
    paths = {path.relative_to(REPO_ROOT).as_posix() for path in SCOPE.tracked_project_files(REPO_ROOT)}
    assert "packages/picolet/picolet/cli/build_cmd.py" in paths
    assert "packages/picolet-bridge-js/src/index.ts" in paths
    assert "examples/notes/ui/src/App.vue" in paths
    assert "examples/dashboard/scripts/generate_screenshots.py" not in paths
    assert "packages/picolet/picolet/_vendor/manifestfile.py" not in paths


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
    assert scope["port"] == ("windows" if target == "windows-x64" else "unix")
    assert scope["manifest"].startswith("packages/picolet-runtime/manifests/manifest_")
    records = scope["groups"][FROZEN]
    assert records
    assert all(shared_scope.source_path(scope, record).is_file() for record in records)
    assert all(record["owner"] in RUNTIME_OWNERS for record in records)
    assert all(record["root"] != "repo" for record in records)
    assert all(record["provenance"]["kind"] == "micropython-manifest" for record in records)
    assert shared_scope.parse_scope(scope) == scope


def test_linux_cli_scope_includes_manifest_dependencies_and_runtime_sources():
    scope = resolve_scope(REPO_ROOT, "linux-x64", "cli")
    files = {_physical(scope, record): record for record in scope["groups"][FROZEN]}

    assert "packages/picolet-runtime/micropython/extmod/asyncio/core.py" in files
    assert "packages/picolet-runtime/lib/micropython-lib/python-stdlib/os-path/os/path.py" in files
    assert "packages/picolet-runtime/python/picolet/_dispatcher.py" in files
    assert "packages/picolet-runtime/python/picolet_ui/_app.py" not in files
    dispatcher = files["packages/picolet-runtime/python/picolet/_dispatcher.py"]
    assert dispatcher["owner"] == "picolet-runtime"
    assert dispatcher["root"] == "picolet-runtime-python"
    assert dispatcher["path"] == "picolet/_dispatcher.py"
    assert dispatcher["target_path"] == "picolet/_dispatcher.py"
    assert files["packages/picolet-runtime/micropython/extmod/asyncio/core.py"]["owner"] == "micropython-integration"


def test_frozen_report_location_is_the_physical_file_not_the_import_identity():
    scope = resolve_scope(REPO_ROOT, "linux-x64", "cli")
    record = next(
        record for record in scope["groups"][FROZEN]
        if record["root"] == "micropython-lib" and record["path"].endswith("os/path.py")
    )

    assert record["target_path"] == "os/path.py"
    assert shared_scope.report_path(scope, record).endswith("python-stdlib/os-path/os/path.py")
    assert record["target_path"] != record["path"]


def test_unsupported_runtime_combination_is_rejected():
    with pytest.raises(ValueError, match="unsupported target/variant combination"):
        resolve_scope(REPO_ROOT, "windows-x64", "mcp")


def test_build_scope_adds_project_groups_without_selecting_frozen_files_twice():
    scope = SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli")
    frozen = {_physical(scope, record) for record in scope["groups"][FROZEN]}
    host = scope["groups"]["host_and_example_python"]
    frontend = scope["groups"]["frontend"]

    assert scope["groups"]["native"] == []
    assert scope["native_selection"]["status"] == "omitted"
    assert host and frontend
    assert not frozen & {_physical(scope, record) for record in host}
    assert {record["owner"] for record in host} <= {"picolet-host", "example-application"}
    assert {record["owner"] for record in frontend} <= {"picolet-bridge", "example-frontend"}
    assert all(record["root"] == "repo" and "target_path" not in record for record in host + frontend)
    assert all(record["provenance"]["kind"] == "picolet-tracked-source" for record in host + frontend)
    assert "tests" in scope["excluded_path_segments"]
    assert scope["other_exclusions"]


@pytest.mark.parametrize("content", ["[]", "[{\"directory\": \"/tmp\"}]"])
def test_empty_or_malformed_compile_database_is_a_selection_failure(tmp_path, content):
    database = tmp_path / "compile_commands.json"
    database.write_text(content)

    with pytest.raises(ValueError):
        SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)


def test_compile_database_source_outside_explicit_roots_is_rejected(tmp_path):
    outside = tmp_path / "outside.c"
    outside.write_text("int x;\n")
    database = tmp_path / "compile_commands.json"
    database.write_text(json.dumps([{"directory": str(tmp_path), "file": str(outside), "arguments": ["cc", "-c", str(outside)]}]))

    with pytest.raises(ValueError, match="no explicit root"):
        SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)


def test_missing_shared_tooling_reports_install_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "mpy_analysis", None)

    with pytest.raises(RuntimeError, match="mpy_analysis is not importable"):
        SCOPE.shared_scope()


def _normalised_database(tmp_path, source, **overrides):
    """A real normaliser output for one compile of `source`, recorded in a separate build directory."""
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    record = tmp_path / "commands.jsonl"
    absolute = REPO_ROOT / source
    record.write_text(json.dumps({
        "directory": str(build), "file": str(absolute), "arguments": ["gcc", "-c", str(absolute)],
        "owner": "picolet-runtime",
    }) + "\n")
    entries = NORMALISER.normalise(REPO_ROOT, record)
    entries[0].update(overrides)
    database = tmp_path / "compile_commands.json"
    database.write_text(json.dumps(entries))
    return database, entries


def test_provenance_format_matches_the_normaliser():
    assert SCOPE.PROVENANCE_FORMAT == NORMALISER.PROVENANCE_FORMAT


def test_normalised_entries_record_the_original_file_and_directory(tmp_path):
    source = "packages/picolet-runtime/variants/common/romfs_trailer.c"

    _, [entry] = _normalised_database(tmp_path, source)

    assert entry["file"] == source
    assert entry["provenance"] == {
        "format": NORMALISER.PROVENANCE_FORMAT,
        "file_base": "repo-root",
        "original_file": str(REPO_ROOT / source),
        "original_directory": str(tmp_path / "build"),
    }


def test_normalised_database_maps_to_the_physical_file_with_a_receipt(tmp_path):
    source = "packages/picolet-runtime/variants/common/romfs_trailer.c"
    database, [entry] = _normalised_database(tmp_path, source)

    [record] = SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)["groups"]["native"]

    assert (record["root"], record["path"], record["owner"]) == ("repo", source, "picolet-runtime")
    assert record["compilation"] == entry
    assert record["compilation"]["directory"] == str(tmp_path / "build")
    assert record["provenance"]["database"] == str(database)
    assert record["provenance"]["entry_index"] == 0
    assert record["provenance"]["file_mapping"] == {
        "format": NORMALISER.PROVENANCE_FORMAT,
        "file_base": "repo-root",
        "database_file": source,
        "database_directory": str(tmp_path / "build"),
        "resolved_file": str(REPO_ROOT / source),
    }


def test_repeated_compiles_of_one_source_remain_distinct_native_records(tmp_path):
    source = "packages/picolet-runtime/variants/common/romfs_trailer.c"
    database, [entry] = _normalised_database(tmp_path, source)
    second = {**entry, "arguments": ["gcc", "-DALT", "-c", str(REPO_ROOT / source)]}
    database.write_text(json.dumps([entry, second]))

    native = SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)["groups"]["native"]

    assert [record["compilation"]["arguments"][1] for record in native] == ["-c", "-DALT"]
    assert [record["provenance"]["entry_index"] for record in native] == [0, 1]


@pytest.mark.parametrize(
    "override",
    [
        {"file": "packages/picolet-runtime/variants/common/other.c"},
        {"directory": "/somewhere/else"},
        {"provenance": {"format": "picolet-normalised-compile-database", "file_base": "compiler-directory",
                        "original_file": "x", "original_directory": "y"}},
    ],
)
def test_inconsistent_picolet_mapping_is_rejected(tmp_path, override):
    database, _ = _normalised_database(tmp_path, "packages/picolet-runtime/variants/common/romfs_trailer.c", **override)

    with pytest.raises(ValueError, match="Picolet file mapping|does not map"):
        SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)


def test_repo_relative_file_without_picolet_provenance_is_not_reinterpreted(tmp_path):
    source = "packages/picolet-runtime/variants/common/romfs_trailer.c"
    database, [entry] = _normalised_database(tmp_path, source)
    del entry["provenance"]
    database.write_text(json.dumps([entry]))

    with pytest.raises((ValueError, OSError)):
        SCOPE.build_scope(REPO_ROOT, "linux-x64", "cli", database)
