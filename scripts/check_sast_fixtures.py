#!/usr/bin/env python3
"""Check Picolet's scanner policy against the shared packaged fixtures.

Picolet selects the fixtures, expected rule ids and callables for each scanner in
scripts/sast/fixtures.json. Scanning, coverage accounting and expectation matching
are the shared mpy_analysis fixture checker's; policy is scripts/sast, the same policy
scripts/run_sast.py uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from static_analysis_scope import RUNTIME_TARGETS, runtime_port

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = Path(__file__).resolve().parent / "sast"
FIXTURE_ROOT = "fixtures"
FIXTURE_OWNER = "picolet-sast-fixtures"
GROUPS = ("runtime_frozen_python", "host_and_example_python", "frontend", "native")


def _shared_fixtures() -> ModuleType:
    try:
        from mpy_analysis import fixtures
    except ImportError as exc:
        raise RuntimeError(
            "shared mpy_analysis core is not installed in this interpreter "
            f"({exc}); install the revision pinned in {POLICY_DIR / 'shared-core.json'} with "
            "scripts/install_sast_core.py --source <MicroPython checkout containing it>"
        ) from exc
    return fixtures


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload



def _profile(policy_dir: Path, scanner: str) -> dict[str, Any]:
    config = _load_json(policy_dir / "fixtures.json")
    if config.get("schema_version") != 1:
        raise ValueError("fixtures.json requires schema_version 1")
    name = config["scanners"].get(scanner)
    if name is None:
        raise ValueError(f"fixtures.json selects no fixtures for scanner {scanner!r}")
    return config["profiles"][name]


def build_inputs(
    scanner: str, target: str, variant: str, fixture_directory: Path, policy_dir: Path, core: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the explicit shared scope and expectations for one scanner and selection."""
    profile = _profile(policy_dir, scanner)
    if target not in RUNTIME_TARGETS or variant not in RUNTIME_TARGETS[target]:
        raise ValueError(f"unsupported target/variant combination: {target}/{variant}")
    paths = list(dict.fromkeys(item["path"] for kind in ("positive", "negative") for item in profile[kind]))
    for path in paths:
        if not (fixture_directory / path).is_file():
            raise FileNotFoundError(f"selected shared fixture is not packaged: {fixture_directory / path}")
    scope = {
        "schema_version": 1,
        "roots": {FIXTURE_ROOT: str(fixture_directory)},
        "target": target,
        "variant": variant,
        "port": runtime_port(target),
        "groups": {
            **{group: [] for group in GROUPS},
            "runtime_frozen_python": [
                {
                    "root": FIXTURE_ROOT,
                    "path": path,
                    "target_path": path,
                    "owner": FIXTURE_OWNER,
                    "provenance": {"fixture_directory": "mpy_analysis.fixtures.fixture_directory", **core},
                }
                for path in paths
            ],
        },
    }
    expectations = {
        "schema_version": 1,
        **{
            kind: [{"root": FIXTURE_ROOT, **item} for item in profile[kind]]
            for kind in ("positive", "negative")
        },
    }
    return scope, expectations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scanner",
        choices=("opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce", "ruff-s", "pysa", "pyrefly"),
        required=True,
    )
    parser.add_argument("--port", choices=("unix", "windows"), help="MicroPython port; selects the default target (unix: linux-x64, windows: windows-x64)")
    parser.add_argument("--target", choices=sorted(RUNTIME_TARGETS), help="Runtime target; default derived from --port, else linux-x64")
    parser.add_argument("--variant", default="cli")
    parser.add_argument("--output-dir", type=Path, help="Default: sast-results/fixtures/<scanner>-<target>-<variant> under the repository")
    parser.add_argument("--executable", help="Selected analyser executable (Pysa: pyre)")
    parser.add_argument("--pyrefly-executable", help="Pysa's explicit Pyrefly prerequisite executable")
    args = parser.parse_args(argv)
    target = args.target or {"unix": "linux-x64", "windows": "windows-x64"}.get(args.port, "linux-x64")
    if args.port and runtime_port(target) != args.port:
        parser.error(f"--port {args.port} does not match target {target} ({runtime_port(target)})")
    args.target = target
    output_dir = (
        args.output_dir or REPO_ROOT / "sast-results" / "fixtures" / f"{args.scanner}-{args.target}-{args.variant}"
    ).resolve()
    try:
        shared = _shared_fixtures()
        core = {
            "requested_core_revision": _load_json(POLICY_DIR / "shared-core.json")["revision"],
            "installed_core_path": str(Path(shared.__file__).resolve().parent),
        }
        scope, expectations = build_inputs(
            args.scanner, args.target, args.variant, shared.fixture_directory(), POLICY_DIR, core
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        scope_path = output_dir / "fixture-scope.json"
        expectations_path = output_dir / "fixture-expectations.json"
        scope_path.write_text(json.dumps(scope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        expectations_path.write_text(json.dumps(expectations, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    status = shared.check(
        args.scanner,
        scope_path,
        expectations_path,
        POLICY_DIR,
        output_dir,
        args.executable,
        args.pyrefly_executable,
    )
    if status == 0:
        print(f"{args.scanner}: positive and negative fixtures passed")
    else:
        print(f"{args.scanner}: fixture check did not pass; see {output_dir / 'fixture-summary.json'}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
