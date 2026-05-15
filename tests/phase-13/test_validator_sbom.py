"""
PH13 gate-8: validator.py [sbom] schema type-checking.

Tests that _SBOM_SCHEMA is populated with the four typed keys and that
validate_toml() rejects invalid types for each key.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "picolet-cli"))

from picolet.validator import validate_toml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(content: str) -> Path:
    """Write content to a temp file and return its Path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".toml", delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(content)
    tmp.flush()
    return Path(tmp.name)


_BASE = """\
[app]
name = "test-app"
version = "0.1.0"
entry = "src/main.py"
"""


# ---------------------------------------------------------------------------
# Tests — valid [sbom] tables (should produce no errors)
# ---------------------------------------------------------------------------

class TestSbomSchemaValid:
    def test_sbom_absent(self):
        p = _write_toml(_BASE)
        errors = validate_toml(p)
        assert errors == []

    def test_sbom_all_valid_keys(self):
        p = _write_toml(
            _BASE
            + '[sbom]\n'
            + 'allow_licences = ["MIT", "BSD-3-Clause"]\n'
            + 'allow_dynamic  = ["LGPL-2.1-or-later"]\n'
            + 'warn_unknown   = true\n'
            + 'fail_unknown   = false\n'
        )
        errors = validate_toml(p)
        assert errors == [], errors

    def test_sbom_partial_keys(self):
        """Only fail_unknown present — the others remain at defaults."""
        p = _write_toml(_BASE + '[sbom]\nfail_unknown = true\n')
        errors = validate_toml(p)
        assert errors == [], errors

    def test_sbom_empty_table(self):
        p = _write_toml(_BASE + '[sbom]\n')
        errors = validate_toml(p)
        assert errors == [], errors


# ---------------------------------------------------------------------------
# Tests — invalid [sbom] key types (must produce errors)
# ---------------------------------------------------------------------------

class TestSbomSchemaInvalid:
    def test_allow_licences_not_list(self):
        p = _write_toml(_BASE + '[sbom]\nallow_licences = "MIT"\n')
        errors = validate_toml(p)
        assert any(e.section == "sbom" and e.key == "allow_licences" for e in errors), errors

    def test_allow_dynamic_not_list(self):
        p = _write_toml(_BASE + '[sbom]\nallow_dynamic = 42\n')
        errors = validate_toml(p)
        assert any(e.section == "sbom" and e.key == "allow_dynamic" for e in errors), errors

    def test_warn_unknown_not_bool(self):
        p = _write_toml(_BASE + '[sbom]\nwarn_unknown = "yes"\n')
        errors = validate_toml(p)
        assert any(e.section == "sbom" and e.key == "warn_unknown" for e in errors), errors

    def test_fail_unknown_not_bool(self):
        p = _write_toml(_BASE + '[sbom]\nfail_unknown = 1\n')
        errors = validate_toml(p)
        assert any(e.section == "sbom" and e.key == "fail_unknown" for e in errors), errors

    def test_sbom_not_table(self):
        # sbom must be at the top level (before [app]) to be a non-table value.
        # After [app], 'sbom = "string"' would become app.sbom, not a top-level key.
        p = _write_toml('sbom = "string"\n\n' + _BASE)
        errors = validate_toml(p)
        assert any(e.section == "sbom" for e in errors), errors


# ---------------------------------------------------------------------------
# Test — [dependencies] and [dependency_meta] are allowed
# ---------------------------------------------------------------------------

class TestDependenciesAllowed:
    def test_dependencies_section_accepted(self):
        p = _write_toml(
            _BASE
            + '[dependencies]\n'
            + 'requests = "2.31.0"\n'
        )
        errors = validate_toml(p)
        assert errors == [], errors

    def test_dependency_meta_section_accepted(self):
        p = _write_toml(
            _BASE
            + '[dependencies]\n'
            + 'requests = "2.31.0"\n'
            + '[dependency_meta.requests]\n'
            + 'licence = "Apache-2.0"\n'
        )
        errors = validate_toml(p)
        assert errors == [], errors
