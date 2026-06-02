"""
PH13 gate-5: App SBOM is a superset of the runtime SBOM.

Usage (standalone):
    python3 tests/phase-13/test_sbom_union.py \\
        <runtime-sbom.cdx.json> <app-sbom.cdx.json>

Usage (pytest):
    pytest tests/phase-13/test_sbom_union.py -q

When run under pytest, it generates fresh SBOMs using emit_runtime_sbom /
emit_app_sbom to ensure the superset property holds programmatically.
When run standalone, it verifies two pre-existing .cdx.json files.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-cli"))

from picolet.cli.sbom_gen import emit_app_sbom, emit_runtime_sbom


def check_superset(runtime_sbom: Path, app_sbom: Path) -> bool:
    """Return True iff every component name in runtime_sbom appears in app_sbom."""
    rt_doc = json.loads(runtime_sbom.read_text())
    app_doc = json.loads(app_sbom.read_text())

    rt_names = {c["name"] for c in rt_doc.get("components", [])}
    app_names = {c["name"] for c in app_doc.get("components", [])}

    missing = rt_names - app_names
    if missing:
        print(f"ERROR: App SBOM is missing runtime components: {missing}", file=sys.stderr)
        return False

    print(f"OK: App SBOM ({len(app_names)} components) is a superset of "
          f"runtime SBOM ({len(rt_names)} components)")
    return True


def test_app_sbom_superset_of_runtime() -> None:
    """Pytest-callable: generate fresh SBOMs and verify superset property."""
    rt_out = Path(tempfile.mktemp(suffix="-runtime.cdx.json"))
    app_out = Path(tempfile.mktemp(suffix="-app.cdx.json"))

    emit_runtime_sbom(rt_out, "linux-x64", "cli", _REPO_ROOT)
    emit_app_sbom(
        output_path=app_out,
        runtime_sbom_path=rt_out,
        app_data={"app": {"name": "test-union", "version": "0.1.0"}},
        target="linux-x64",
        variant="cli",
        repo_root=_REPO_ROOT,
    )

    assert check_superset(rt_out, app_out), \
        "App SBOM must be a superset of the runtime SBOM"


if __name__ == "__main__":
    if len(sys.argv) == 3:
        rt_path = Path(sys.argv[1])
        app_path = Path(sys.argv[2])
        ok = check_superset(rt_path, app_path)
        sys.exit(0 if ok else 1)
    else:
        # Run the programmatic check.
        test_app_sbom_superset_of_runtime()
        print("Superset check passed.")
