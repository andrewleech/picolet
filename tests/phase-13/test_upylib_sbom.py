"""
PH13 — micropython-lib manifest auto-discovery for SBOM (A8 fix).

Covers:
  - parse_upylib_manifest: extracts version and description from manifest.py.
  - find_upylib_manifest: locates manifest.py for named modules.
  - upylib_components: emits correctly structured CycloneDX components.
  - emit_app_sbom: micropython-lib list in [dependencies] produces components
    with MIT licence and correct versions sourced from vendored manifests.

Fixture app uses micropython-lib = ["fnmatch", "gzip"] — both modules
are present in the vendored python-stdlib tree with known manifest versions.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys
import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-cli"))

from picolet.cli.sbom_gen import (
    emit_app_sbom,
    find_upylib_manifest,
    parse_upylib_manifest,
    upylib_components,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


# ---------------------------------------------------------------------------
# parse_upylib_manifest
# ---------------------------------------------------------------------------

class TestParseUpylibManifest:
    """parse_upylib_manifest extracts metadata from manifest.py files."""

    def test_fnmatch_version(self):
        mpath = find_upylib_manifest("fnmatch", _REPO_ROOT)
        assert mpath is not None, "fnmatch manifest not found in vendored micropython-lib"
        meta = parse_upylib_manifest(mpath)
        assert meta["version"], "fnmatch manifest must have a non-empty version"
        # version should be a semver-like string
        parts = meta["version"].split(".")
        assert len(parts) >= 2, f"expected semver, got {meta['version']!r}"

    def test_gzip_version(self):
        mpath = find_upylib_manifest("gzip", _REPO_ROOT)
        assert mpath is not None, "gzip manifest not found in vendored micropython-lib"
        meta = parse_upylib_manifest(mpath)
        assert meta["version"], "gzip manifest must have a non-empty version"

    def test_abc_version_present(self):
        mpath = find_upylib_manifest("abc", _REPO_ROOT)
        assert mpath is not None, "abc manifest not found"
        meta = parse_upylib_manifest(mpath)
        assert meta["version"] != ""

    def test_returns_dict_with_required_keys(self):
        mpath = find_upylib_manifest("fnmatch", _REPO_ROOT)
        assert mpath is not None
        meta = parse_upylib_manifest(mpath)
        assert "version" in meta
        assert "description" in meta

    def test_synthetic_manifest_no_description(self, tmp_path):
        mf = tmp_path / "manifest.py"
        mf.write_text('metadata(version="1.2.3")\n', encoding="utf-8")
        meta = parse_upylib_manifest(mf)
        assert meta["version"] == "1.2.3"
        assert meta["description"] == ""

    def test_synthetic_manifest_with_description(self, tmp_path):
        mf = tmp_path / "manifest.py"
        mf.write_text(
            'metadata(version="0.5.0", description="A test module.")\n',
            encoding="utf-8",
        )
        meta = parse_upylib_manifest(mf)
        assert meta["version"] == "0.5.0"
        assert meta["description"] == "A test module."

    def test_synthetic_manifest_missing_metadata_call(self, tmp_path):
        mf = tmp_path / "manifest.py"
        mf.write_text('module("foo.py")\n', encoding="utf-8")
        meta = parse_upylib_manifest(mf)
        assert meta["version"] == ""
        assert meta["description"] == ""

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ValueError, match="cannot read"):
            parse_upylib_manifest(tmp_path / "nonexistent.py")


# ---------------------------------------------------------------------------
# find_upylib_manifest
# ---------------------------------------------------------------------------

class TestFindUpylibManifest:
    """find_upylib_manifest locates manifests in the vendored tree."""

    def test_fnmatch_found(self):
        path = find_upylib_manifest("fnmatch", _REPO_ROOT)
        assert path is not None
        assert path.is_file()

    def test_gzip_found(self):
        path = find_upylib_manifest("gzip", _REPO_ROOT)
        assert path is not None
        assert path.is_file()

    def test_nonexistent_returns_none(self):
        path = find_upylib_manifest("__no_such_module_xyz__", _REPO_ROOT)
        assert path is None

    def test_manifest_path_ends_with_manifest_py(self):
        path = find_upylib_manifest("fnmatch", _REPO_ROOT)
        assert path is not None
        assert path.name == "manifest.py"


# ---------------------------------------------------------------------------
# upylib_components
# ---------------------------------------------------------------------------

class TestUpylibComponents:
    """upylib_components emits CycloneDX components with correct structure."""

    def test_produces_one_component_per_module(self):
        comps = upylib_components(["fnmatch", "gzip"], _REPO_ROOT, offset=0)
        assert len(comps) == 2

    def test_component_names_match_input(self):
        comps = upylib_components(["fnmatch", "gzip"], _REPO_ROOT, offset=0)
        names = [c["name"] for c in comps]
        assert names == ["fnmatch", "gzip"]

    def test_component_type_is_library(self):
        comps = upylib_components(["fnmatch"], _REPO_ROOT, offset=0)
        assert comps[0]["type"] == "library"

    def test_component_licence_is_mit(self):
        comps = upylib_components(["fnmatch", "gzip"], _REPO_ROOT, offset=0)
        for comp in comps:
            lic = comp["licenses"][0]["license"]
            assert lic.get("id") == "MIT", f"expected MIT, got {lic}"

    def test_component_version_is_nonempty(self):
        comps = upylib_components(["fnmatch", "gzip"], _REPO_ROOT, offset=0)
        for comp in comps:
            assert comp["version"], f"version empty for {comp['name']}"

    def test_bom_refs_are_unique(self):
        comps = upylib_components(["fnmatch", "gzip", "abc"], _REPO_ROOT, offset=0)
        refs = [c["bom-ref"] for c in comps]
        assert len(refs) == len(set(refs)), f"duplicate bom-refs: {refs}"

    def test_offset_shifts_bom_refs(self):
        comps_0 = upylib_components(["fnmatch"], _REPO_ROOT, offset=0)
        comps_5 = upylib_components(["fnmatch"], _REPO_ROOT, offset=5)
        assert comps_0[0]["bom-ref"] != comps_5[0]["bom-ref"]

    def test_external_reference_points_to_upylib(self):
        comps = upylib_components(["fnmatch"], _REPO_ROOT, offset=0)
        refs = comps[0].get("externalReferences", [])
        assert any("micropython-lib" in r.get("url", "") for r in refs)

    def test_properties_include_source_micropython_lib(self):
        comps = upylib_components(["fnmatch"], _REPO_ROOT, offset=0)
        props = {p["name"]: p["value"] for p in comps[0].get("properties", [])}
        assert props.get("picolet:source") == "micropython-lib"

    def test_properties_link_type_is_static(self):
        comps = upylib_components(["fnmatch"], _REPO_ROOT, offset=0)
        props = {p["name"]: p["value"] for p in comps[0].get("properties", [])}
        assert props.get("picolet:link_type") == "static"

    def test_unknown_module_produces_unknown_version(self):
        comps = upylib_components(["__no_such_xyz__"], _REPO_ROOT, offset=0)
        assert len(comps) == 1
        assert comps[0]["version"] == "unknown"

    def test_unknown_module_licence_is_licenseref_unknown(self):
        comps = upylib_components(["__no_such_xyz__"], _REPO_ROOT, offset=0)
        lic = comps[0]["licenses"][0]["license"]
        assert lic.get("name", "").startswith("LicenseRef-")


# ---------------------------------------------------------------------------
# emit_app_sbom integration: micropython-lib list in [dependencies]
# ---------------------------------------------------------------------------

class TestEmitAppSbomUpylib:
    """emit_app_sbom with micropython-lib = [...] produces valid SBOM."""

    @pytest.fixture()
    def fixture_app_data(self):
        return {
            "app": {"name": "my-upylib-app", "version": "1.0.0", "entry": "src/main.py"},
            "dependencies": {
                "micropython-lib": ["fnmatch", "gzip"],
            },
        }

    def test_sbom_contains_fnmatch_and_gzip(self, fixture_app_data):
        out = _tmp() / "app.cdx.json"
        emit_app_sbom(
            output_path=out,
            runtime_sbom_path=None,
            app_data=fixture_app_data,
            target="linux-x64",
            variant="cli",
            repo_root=_REPO_ROOT,
        )
        doc = json.loads(out.read_text(encoding="utf-8"))
        names = [c["name"] for c in doc["components"]]
        assert "fnmatch" in names, f"fnmatch not in SBOM components: {names}"
        assert "gzip" in names, f"gzip not in SBOM components: {names}"

    def test_sbom_fnmatch_has_mit_licence(self, fixture_app_data):
        out = _tmp() / "app.cdx.json"
        emit_app_sbom(
            output_path=out,
            runtime_sbom_path=None,
            app_data=fixture_app_data,
            target="linux-x64",
            variant="cli",
            repo_root=_REPO_ROOT,
        )
        doc = json.loads(out.read_text(encoding="utf-8"))
        fnmatch_comp = next(c for c in doc["components"] if c["name"] == "fnmatch")
        lic = fnmatch_comp["licenses"][0]["license"]
        assert lic.get("id") == "MIT"

    def test_sbom_policy_clean_for_upylib(self, fixture_app_data):
        out = _tmp() / "app.cdx.json"
        violations = emit_app_sbom(
            output_path=out,
            runtime_sbom_path=None,
            app_data=fixture_app_data,
            target="linux-x64",
            variant="cli",
            repo_root=_REPO_ROOT,
        )
        fail_violations = [v for v in violations if v.severity == "fail"]
        assert not fail_violations, f"unexpected policy failures: {fail_violations}"

    def test_sbom_component_version_from_manifest(self, fixture_app_data):
        out = _tmp() / "app.cdx.json"
        emit_app_sbom(
            output_path=out,
            runtime_sbom_path=None,
            app_data=fixture_app_data,
            target="linux-x64",
            variant="cli",
            repo_root=_REPO_ROOT,
        )
        doc = json.loads(out.read_text(encoding="utf-8"))
        fnmatch_comp = next(c for c in doc["components"] if c["name"] == "fnmatch")
        assert fnmatch_comp["version"] not in ("", "unknown"), (
            f"expected real version from manifest, got {fnmatch_comp['version']!r}"
        )

    def test_sbom_upylib_property_source(self, fixture_app_data):
        out = _tmp() / "app.cdx.json"
        emit_app_sbom(
            output_path=out,
            runtime_sbom_path=None,
            app_data=fixture_app_data,
            target="linux-x64",
            variant="cli",
            repo_root=_REPO_ROOT,
        )
        doc = json.loads(out.read_text(encoding="utf-8"))
        fnmatch_comp = next(c for c in doc["components"] if c["name"] == "fnmatch")
        props = {p["name"]: p["value"] for p in fnmatch_comp.get("properties", [])}
        assert props.get("picolet:source") == "micropython-lib"


# ---------------------------------------------------------------------------
# A8 follow-up — warning path and repo_root=None guard
# ---------------------------------------------------------------------------

class TestUpylibWarningAndGuard:
    """A8 follow-up: unknown module warns; repo_root=None with micropython-lib raises."""

    def test_unknown_module_warns_to_stderr(self, capsys):
        """upylib_components emits a warning to stderr for a module with no manifest."""
        comps = upylib_components(["__no_such_module_xyzzy__"], _REPO_ROOT, offset=0)
        captured = capsys.readouterr()
        assert "warning:" in captured.err, (
            "expected a 'warning:' line on stderr for an unresolvable module"
        )
        assert "__no_such_module_xyzzy__" in captured.err
        assert "LicenseRef-Unknown" in captured.err
        # Component is still emitted (downstream policy enforcement handles it).
        assert len(comps) == 1
        assert comps[0]["version"] == "unknown"

    def test_repo_root_none_with_upylib_raises(self):
        """_app_dep_components raises ValueError when repo_root is None and micropython-lib is declared."""
        from picolet.cli.sbom_gen import _app_dep_components
        app_data = {
            "app": {"name": "test", "version": "0.1.0", "entry": "src/main.py"},
            "dependencies": {"micropython-lib": ["fnmatch"]},
        }
        with pytest.raises(ValueError, match="requires repo_root"):
            _app_dep_components(app_data, offset=0, repo_root=None)
