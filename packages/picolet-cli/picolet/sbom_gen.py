"""
picolet SBOM generator — CycloneDX 1.5 JSON emitter.

Public API
----------
emit_runtime_sbom(output_path, target, variant, repo_root) -> None
    Assemble and write a CycloneDX 1.5 SBOM for a runtime artifact.

emit_app_sbom(output_path, runtime_sbom_path, app_data, target, variant,
              repo_root) -> list[SbomViolation]
    Merge runtime SBOM + app [dependencies], enforce policy, write output.
    Returns a (possibly empty) list of SbomViolation records.

SbomViolation
    Dataclass: component (str), reason (str), severity ("warn"|"fail").

CLI shim (FR-SBOM-1, build-runtime.sh post-build)
----------
    python -m picolet.sbom_gen emit-runtime \\
        --output <path> --target <t> --variant <v> --repo-root <r>

Design notes
------------
AD1: runtime.toml is the authoritative source for native components.
AD2: This file lives in picolet-cli so the whole pipeline shares one package.
AD3: No external CycloneDX library — the format is well-bounded for this
     use-case; a dependency on cyclonedx-python-lib adds a transitive dep
     chain for marginal benefit at this scale.
AD4: Enforcement defaults match docs/sbom.md allowlist table.
AD5: build-runtime.sh calls the CLI shim on the host shell after the
     Docker container exits (Risk 1 mitigation).

[PH13] Caveat: micropython-lib manifest parsing for frozen-module
auto-discovery is deferred.  Users declare micropython-lib modules in
[dependencies] / [dependency_meta] instead.  The SBOM is still fully
valid — the caveat only means the generator does not auto-discover
frozen modules from a manifest file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

CDX_SPEC_VERSION = "1.5"
CDX_BOM_FORMAT = "CycloneDX"

try:
    from importlib.metadata import version as _v
    _PICOLET_CLI_VERSION = _v("picolet-cli")
except Exception:
    _PICOLET_CLI_VERSION = "0.0.0-dev"

# ---------------------------------------------------------------------------
# Default allowlists (FR-SBOM-3, docs/sbom.md)
# ---------------------------------------------------------------------------

_DEFAULT_ALLOW_LICENCES: list[str] = [
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "Zlib",
    "0BSD",
    "ISC",
    "Python-2.0",
]
_DEFAULT_ALLOW_DYNAMIC: list[str] = [
    "LGPL-2.1-or-later",
    "LicenseRef-MS-WebView2-Fixed",
]


# ---------------------------------------------------------------------------
# SbomViolation dataclass
# ---------------------------------------------------------------------------

@dataclass
class SbomViolation:
    """A single policy violation detected during SBOM emission.

    severity is "warn" when warn_unknown=true and fail_unknown=false;
    "fail" when fail_unknown=true (fail always implies warn too).
    """
    component: str
    reason: str
    severity: str   # "warn" | "fail"


# ---------------------------------------------------------------------------
# runtime.toml loader
# ---------------------------------------------------------------------------

def _runtime_toml_path(repo_root: Path) -> Path:
    return repo_root / "packages" / "picolet-runtime" / "sbom" / "runtime.toml"


def load_runtime_toml(repo_root: Path) -> list[dict]:
    """Parse runtime.toml; return the raw list of component dicts."""
    path = _runtime_toml_path(repo_root)
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return data.get("component", [])


def filter_components(
    components: list[dict],
    target: str,
    variant: str,
) -> list[dict]:
    """Apply variants/targets inclusion filters to the component list."""
    result = []
    for c in components:
        variants_filter = c.get("variants")
        if variants_filter is not None and variant not in variants_filter:
            continue
        targets_filter = c.get("targets")
        if targets_filter is not None and target not in targets_filter:
            continue
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# mbm.toml PR list loader
# ---------------------------------------------------------------------------

def _mbm_toml_path(repo_root: Path) -> Path:
    return repo_root / "packages" / "picolet-runtime" / "mbm.toml"


def load_mbm_prs(repo_root: Path) -> list[str]:
    """Read mbm.toml and return a list of PR title strings.

    These are used to populate the MicroPython component's notes field
    in the runtime SBOM.  Returns an empty list if mbm.toml is absent.
    """
    path = _mbm_toml_path(repo_root)
    if not path.is_file():
        return []
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    titles = []
    for sub in data.get("submodules", []):
        for branch in sub.get("branches", []):
            name = branch.get("name", "")
            title = branch.get("title", "")
            if name or title:
                titles.append(f"{name}: {title}" if name and title else name or title)
    return titles


# ---------------------------------------------------------------------------
# CycloneDX component converter
# ---------------------------------------------------------------------------

def _to_cdx_component(entry: dict, bom_ref: str) -> dict:
    """Convert a runtime.toml entry dict to a CycloneDX 1.5 component object.

    component type:
      - MicroPython → "framework" (it is an interpreter/runtime framework)
      - all others  → "library"
    """
    name = entry["name"]
    comp_type = "framework" if name == "MicroPython" else "library"

    cdx: dict[str, Any] = {
        "type": comp_type,
        "bom-ref": bom_ref,
        "name": name,
        "version": entry["version"],
    }

    # Licence
    licence_id = entry["licence"]
    if licence_id.startswith("LicenseRef-"):
        cdx["licenses"] = [{"license": {"name": licence_id}}]
    else:
        cdx["licenses"] = [{"license": {"id": licence_id}}]

    # External reference
    source_url = entry.get("source_url", "")
    if source_url:
        cdx["externalReferences"] = [{"type": "website", "url": source_url}]

    # purl
    purl = entry.get("purl")
    if purl:
        cdx["purl"] = purl

    # Properties: picolet-specific metadata
    properties = []
    link_type = entry.get("link_type", "")
    if link_type:
        properties.append({"name": "picolet:link_type", "value": link_type})

    variants = entry.get("variants")
    if variants:
        properties.append({"name": "picolet:variants", "value": ",".join(variants)})

    targets = entry.get("targets")
    if targets:
        properties.append({"name": "picolet:targets", "value": ",".join(targets)})

    if properties:
        cdx["properties"] = properties

    # Notes as description
    notes = entry.get("notes", "").strip()
    if notes:
        cdx["description"] = notes

    return cdx


def _inject_mbm_prs(cdx_components: list[dict], pr_titles: list[str]) -> None:
    """Append PR title list to the MicroPython component's description.

    Operates in-place on the list of CycloneDX component dicts.
    """
    if not pr_titles:
        return
    for comp in cdx_components:
        if comp.get("name") == "MicroPython":
            existing = comp.get("description", "")
            pr_block = "\n".join(pr_titles)
            if existing:
                comp["description"] = existing + "\n" + pr_block
            else:
                comp["description"] = pr_block
            break


# ---------------------------------------------------------------------------
# Document assembly helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    """Return the current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _make_document(
    artifact_name: str,
    artifact_version: str,
    artifact_type: str,   # "application" | "library"
    cdx_components: list[dict],
) -> dict:
    """Assemble a minimal valid CycloneDX 1.5 JSON document."""
    return {
        "bomFormat": CDX_BOM_FORMAT,
        "specVersion": CDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": _now_utc(),
            "tools": [
                {
                    "vendor": "picolet",
                    "name": "picolet-sbom-gen",
                    "version": _PICOLET_CLI_VERSION,
                }
            ],
            "component": {
                "type": artifact_type,
                "name": artifact_name,
                "version": artifact_version,
            },
        },
        "components": cdx_components,
    }


def _write_document(doc: dict, output_path: Path) -> None:
    """Write a CycloneDX document as pretty-printed JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Runtime tag helper
# ---------------------------------------------------------------------------

def _runtime_tag(repo_root: Path) -> str:
    """Read RUNTIME_TAG sidecar or return a fallback."""
    tag_file = repo_root / "packages" / "picolet-runtime" / "RUNTIME_TAG"
    if tag_file.is_file():
        return tag_file.read_text().strip()
    return "runtime-v0.1.0"


# ---------------------------------------------------------------------------
# Public: emit_runtime_sbom
# ---------------------------------------------------------------------------

def emit_runtime_sbom(
    output_path: Path,
    target: str,
    variant: str,
    repo_root: Path,
) -> None:
    """Assemble and write a CycloneDX 1.5 SBOM for a runtime artifact.

    Reads runtime.toml, filters by (target, variant), injects mbm.toml PR
    list into the MicroPython component notes, and writes the document.
    """
    all_components = load_runtime_toml(repo_root)
    filtered = filter_components(all_components, target, variant)

    pr_titles = load_mbm_prs(repo_root)

    cdx_components = []
    for i, entry in enumerate(filtered):
        bom_ref = f"runtime-{entry['name'].lower().replace(' ', '-').replace('.','-')}-{i}"
        cdx_components.append(_to_cdx_component(entry, bom_ref))

    _inject_mbm_prs(cdx_components, pr_titles)

    artifact_name = f"picolet-runtime-{target}-{variant}"
    tag = _runtime_tag(repo_root)

    doc = _make_document(
        artifact_name=artifact_name,
        artifact_version=tag,
        artifact_type="library",
        cdx_components=cdx_components,
    )
    _write_document(doc, output_path)


# ---------------------------------------------------------------------------
# Public: emit_app_sbom
# ---------------------------------------------------------------------------

def emit_app_sbom(
    output_path: Path,
    runtime_sbom_path: "Path | None",
    app_data: dict,
    target: str,
    variant: str,
    repo_root: Path,
) -> list[SbomViolation]:
    """Merge runtime + app deps; enforce policy; write CycloneDX 1.5 SBOM.

    Parameters
    ----------
    output_path:
        Destination path for the .cdx.json file (always written, even on
        policy violations, so downstream tooling can inspect the SBOM).
    runtime_sbom_path:
        Path to the runtime's pre-built .cdx.json sidecar, or None when
        building from source (the resolver could not provide one).  When
        None, falls back to reading runtime.toml + mbm.toml directly.
    app_data:
        Parsed picolet.toml dict.
    target, variant:
        Build target and runtime variant strings.
    repo_root:
        Absolute path to the repository root.

    Returns
    -------
    list[SbomViolation]
        Empty when the build is policy-clean.  Caller must print warnings
        for severity="warn" and exit 1 for any severity="fail" entry.
    """
    # Step 1 — Build the runtime component list.
    if runtime_sbom_path is not None and runtime_sbom_path.is_file():
        # Merge from pre-built runtime SBOM.
        with open(runtime_sbom_path, encoding="utf-8") as fh:
            runtime_doc = json.load(fh)
        runtime_cdx_components = list(runtime_doc.get("components", []))
    else:
        # Fall back to reading runtime.toml directly.
        all_rt = load_runtime_toml(repo_root)
        filtered_rt = filter_components(all_rt, target, variant)
        pr_titles = load_mbm_prs(repo_root)
        runtime_cdx_components = []
        for i, entry in enumerate(filtered_rt):
            bom_ref = f"runtime-{entry['name'].lower().replace(' ', '-').replace('.','-')}-{i}"
            runtime_cdx_components.append(_to_cdx_component(entry, bom_ref))
        _inject_mbm_prs(runtime_cdx_components, pr_titles)

    # Step 2 — Build the app dependency component list.
    app_cdx_components = _app_dep_components(app_data, offset=len(runtime_cdx_components))

    # Step 3 — Merge; detect name collisions and keep both with warning.
    runtime_by_name: dict[str, dict] = {c["name"]: c for c in runtime_cdx_components}
    collision_violations: list[SbomViolation] = []
    merged = list(runtime_cdx_components)
    seen_names: set[str] = set(runtime_by_name.keys())
    for comp in app_cdx_components:
        name = comp["name"]
        if name in runtime_by_name:
            # Collision: preserve both entries.  Rewrite bom-ref to avoid
            # duplicates — app entry gets "app-<name>-..." prefix.
            rt_comp = runtime_by_name[name]
            rt_version = rt_comp.get("version", "?")
            rt_licence = _get_component_licence(rt_comp)
            app_version = comp.get("version", "?")
            app_licence = _get_component_licence(comp)
            # Ensure the app component's bom-ref is distinguishable from the
            # runtime one by prepending "app-" if not already present.
            old_ref = comp.get("bom-ref", "")
            if not old_ref.startswith("app-"):
                comp = dict(comp)
                comp["bom-ref"] = "app-" + old_ref
            collision_violations.append(SbomViolation(
                severity="warn",
                component=name,
                reason=(
                    f"app [dependencies] declaration collides with runtime component "
                    f"(runtime: {rt_version}/{rt_licence}; app: {app_version}/{app_licence})"
                ),
            ))
            merged.append(comp)
        else:
            merged.append(comp)
            seen_names.add(name)

    # Step 4 — Policy enforcement (FR-SBOM-3).
    sbom_config = app_data.get("sbom") or {}
    violations = collision_violations + _enforce_policy(merged, sbom_config)

    # Step 5 — Write SBOM (always, even on violations).
    app_name = app_data.get("app", {}).get("name", "unknown-app")
    app_version = app_data.get("app", {}).get("version", "0.0.0")
    doc = _make_document(
        artifact_name=app_name,
        artifact_version=app_version,
        artifact_type="application",
        cdx_components=merged,
    )
    _write_document(doc, output_path)

    return violations


# ---------------------------------------------------------------------------
# App dependency components
# ---------------------------------------------------------------------------

def _app_dep_components(app_data: dict, offset: int) -> list[dict]:
    """Convert app [dependencies] + [dependency_meta] to CDX components.

    [dependencies] is a flat table: name = "version".
    [dependency_meta.<name>] may carry a 'licence' key.  Without it the
    generator emits LicenseRef-Unknown and the policy enforcement handles it.
    """
    deps: dict = app_data.get("dependencies") or {}
    meta_root: dict = app_data.get("dependency_meta") or {}

    if not deps or not isinstance(deps, dict):
        return []

    components = []
    for i, (name, version) in enumerate(deps.items()):
        meta = meta_root.get(name) or {}
        licence = meta.get("licence") or meta.get("license") or "LicenseRef-Unknown"
        source_url = meta.get("source_url") or meta.get("url") or ""
        link_type = meta.get("link_type") or "dynamic"
        purl = meta.get("purl") or ""

        bom_ref = f"app-dep-{name.lower().replace(' ', '-')}-{offset + i}"

        cdx: dict[str, Any] = {
            "type": "library",
            "bom-ref": bom_ref,
            "name": name,
            "version": str(version),
        }
        if licence.startswith("LicenseRef-"):
            cdx["licenses"] = [{"license": {"name": licence}}]
        else:
            cdx["licenses"] = [{"license": {"id": licence}}]
        if source_url:
            cdx["externalReferences"] = [{"type": "website", "url": source_url}]
        if purl:
            cdx["purl"] = purl
        cdx["properties"] = [{"name": "picolet:link_type", "value": link_type}]

        components.append(cdx)

    return components


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------

def _get_component_licence(comp: dict) -> str:
    """Extract the SPDX id or LicenseRef-* name from a CDX component."""
    for lic_entry in comp.get("licenses") or []:
        lic = lic_entry.get("license") or {}
        return lic.get("id") or lic.get("name") or "LicenseRef-Unknown"
    return "LicenseRef-Unknown"


def _get_component_link_type(comp: dict) -> str:
    """Extract picolet:link_type from a CDX component's properties."""
    for prop in comp.get("properties") or []:
        if prop.get("name") == "picolet:link_type":
            return prop.get("value", "static")
    return "static"


def _enforce_policy(
    components: list[dict],
    sbom_config: dict,
) -> list[SbomViolation]:
    """Check each component against the [sbom] allow-lists.

    FR-SBOM-3 enforcement logic:
      - static link: licence must be in allow_licences.
      - dynamic link: licence must be in allow_licences OR allow_dynamic.
      - build-time-only: skipped (not shipped in artifact).
      - LicenseRef-Unknown: apply warn_unknown / fail_unknown policy.
    """
    allow_licences = sbom_config.get("allow_licences") or _DEFAULT_ALLOW_LICENCES
    allow_dynamic  = sbom_config.get("allow_dynamic")  or _DEFAULT_ALLOW_DYNAMIC
    warn_unknown   = sbom_config.get("warn_unknown", True)
    fail_unknown   = sbom_config.get("fail_unknown", False)

    # fail_unknown implies warn_unknown.
    if fail_unknown:
        warn_unknown = True

    violations: list[SbomViolation] = []

    for comp in components:
        name = comp.get("name", "<unknown>")
        licence = _get_component_licence(comp)
        link_type = _get_component_link_type(comp)

        # build-time-only components are not in the shipped artifact.
        if link_type == "build-time-only":
            continue

        if licence == "LicenseRef-Unknown":
            severity = "fail" if fail_unknown else ("warn" if warn_unknown else None)
            if severity:
                violations.append(SbomViolation(
                    component=name,
                    reason=f"licence is LicenseRef-Unknown (link_type={link_type})",
                    severity=severity,
                ))
            continue

        if link_type == "static":
            allowed = set(allow_licences)
        else:
            # dynamic
            allowed = set(allow_licences) | set(allow_dynamic)

        if licence not in allowed:
            violations.append(SbomViolation(
                component=name,
                reason=(
                    f"licence {licence!r} not in allow_licences"
                    + (" or allow_dynamic" if link_type == "dynamic" else "")
                    + f" (link_type={link_type})"
                ),
                severity="fail",
            ))

    return violations


# ---------------------------------------------------------------------------
# CLI shim
# ---------------------------------------------------------------------------

def _cli_emit_runtime(args: argparse.Namespace) -> None:
    """Implement 'emit-runtime' subcommand."""
    output = Path(args.output)
    repo_root = Path(args.repo_root)
    emit_runtime_sbom(
        output_path=output,
        target=args.target,
        variant=args.variant,
        repo_root=repo_root,
    )
    print(f"SBOM written: {output}", file=sys.stderr)


def main() -> None:
    """Entry point for 'python -m picolet.sbom_gen'."""
    parser = argparse.ArgumentParser(
        prog="python -m picolet.sbom_gen",
        description="Picolet CycloneDX 1.5 SBOM generator",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_rt = sub.add_parser("emit-runtime", help="emit a runtime artifact SBOM")
    p_rt.add_argument("--output", required=True, help="output .cdx.json path")
    p_rt.add_argument("--target", required=True, help="e.g. linux-x64")
    p_rt.add_argument("--variant", required=True, help="e.g. cli")
    p_rt.add_argument("--repo-root", required=True, dest="repo_root",
                      help="absolute path to repo root")
    p_rt.set_defaults(func=_cli_emit_runtime)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
