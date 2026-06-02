"""Hatch build hook: materialize sibling-package resources into picolet/.

The picolet wheel ships three pieces of data that live in sibling workspace
packages outside packages/picolet/:

  packages/picolet-bridge-js/dist/picolet-bridge.js
  packages/picolet-runtime/sbom/runtime.toml
  packages/picolet-runtime/mbm.toml
  packages/picolet-runtime/RUNTIME_TAG

A ``[tool.hatch.build.targets.wheel.force-include]`` rule with ``../`` paths
*almost* works — it does when hatch is building directly from the source
workspace.  It fails when uv (or pip) builds the wheel from an extracted
sdist, because the extracted sdist root has no siblings.

This hook runs in ``initialize`` (before file enumeration) and copies the
sibling files into the picolet/ package tree at:

  picolet/_bridge/picolet-bridge.js
  picolet/_runtime_data/sbom/runtime.toml
  picolet/_runtime_data/mbm.toml
  picolet/_runtime_data/RUNTIME_TAG

Once present in the tree they get enumerated as ordinary package files for
both sdist and wheel.  The sdist therefore contains them, so a downstream
``pip wheel`` from the sdist works without needing the workspace.

The destination files are git-ignored in the repo so they don't drift
between dev builds and CI rebuilds.

When the hook can't find the source files (the workspace root has no
siblings — e.g. an extracted sdist), it skips silently; the assumption is
that the files are already in the package tree because the sdist build hook
already put them there.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


# (workspace-relative source, package-relative destination)
_FILE_MAP = (
    ("packages/picolet-bridge-js/dist/picolet-bridge.js",
     "picolet/_bridge/picolet-bridge.js"),
    ("packages/picolet-runtime/sbom/runtime.toml",
     "picolet/_runtime_data/sbom/runtime.toml"),
    ("packages/picolet-runtime/mbm.toml",
     "picolet/_runtime_data/mbm.toml"),
    ("packages/picolet-runtime/RUNTIME_TAG",
     "picolet/_runtime_data/RUNTIME_TAG"),
)


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "picolet-resources"

    def initialize(self, version, build_data):
        pkg_root = Path(self.root)              # packages/picolet/
        # workspace root: parent.parent from packages/picolet/ → repo root
        repo_root = pkg_root.parent.parent

        for src_rel, dst_rel in _FILE_MAP:
            src = repo_root / src_rel
            dst = pkg_root / dst_rel

            if not src.is_file():
                # Likely building from an extracted sdist where siblings
                # don't exist.  The sdist build hook should have already
                # placed the file at dst; if it's missing too the build
                # will fail later with a clearer error.
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
