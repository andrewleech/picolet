#!/usr/bin/env bash
# scripts/mirror-examples-to-templates.sh
#
# Mirror the four worked-example apps into picolet_templates/ with per-field
# {{name}} substitution. Examples are authoritative; templates are derived.
#
# Usage:
#   bash scripts/mirror-examples-to-templates.sh           # write + report
#   bash scripts/mirror-examples-to-templates.sh --check   # dry-run, exit 1 on drift
#
# Exit codes:
#   0  — templates match (no drift)
#   1  — drift detected (unified diff printed; files not written in --check mode)
#
# Substitution rules (per-field, not naive text replace):
#
#   picolet.toml  [app] name field  → "{{name}}"  (all four)
#   picolet.toml  [window] title    → "{{name}}"  (pydfu, notes, config-editor)
#   picolet.toml  [window] title    unchanged      (dashboard: preserves "System Dashboard")
#   package.json "name" field     → "{{name}}"  (all four)
#   All other text files          → verbatim copy (no substitution)
#   Binary files (woff2, png …)   → byte-for-byte copy
#
# Excluded from mirror:
#   node_modules/, dist/, target/, screenshots/, scripts/, tests/,
#   package-lock.json, .pytest_cache/, __pycache__/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLES_DIR="$REPO_ROOT/examples"
TEMPLATES_DIR="$REPO_ROOT/packages/picolet-templates/picolet_templates"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=1
fi

python3 - "$EXAMPLES_DIR" "$TEMPLATES_DIR" "$CHECK_ONLY" "$REPO_ROOT" <<'PYEOF'
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Mirror examples → templates with per-field {{name}} substitution."""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EXAMPLES_DIR = Path(sys.argv[1])
TEMPLATES_DIR = Path(sys.argv[2])
CHECK_ONLY = sys.argv[3] == "1"
REPO_ROOT = Path(sys.argv[4])

# Text extensions — everything else is binary-copied verbatim.
TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".toml", ".html", ".css", ".js", ".ts", ".vue",
    ".md", ".txt", ".json", ".yaml", ".yml",
})

# Directories to skip when walking example source.
EXCLUDE_DIRS: frozenset[str] = frozenset({
    "node_modules", "dist", "target", "screenshots",
    "scripts", "tests", ".pytest_cache", "__pycache__",
})

# Examples to mirror: name → whether [window] title gets {{name}} substitution.
EXAMPLES: dict[str, bool] = {
    "pydfu":         True,
    "notes":         True,
    "config-editor": True,
    "dashboard":     False,   # preserves "System Dashboard"
}


def transform_picolet_toml(content: str, title_token: bool) -> str:
    """Replace [app] name and (optionally) [window] title with {{name}}."""
    # [app] name = "..."  — first occurrence only.
    content = re.sub(
        r'(?m)^(name\s*=\s*)"[^"]*"',
        r'\1"{{name}}"',
        content,
        count=1,
    )
    if title_token:
        # [window] title = "..."  — first occurrence only.
        content = re.sub(
            r'(?m)^(title\s*=\s*)"[^"]*"',
            r'\1"{{name}}"',
            content,
            count=1,
        )
    return content


def transform_package_json(content: str) -> str:
    """Replace top-level "name" field value with "{{name}}"."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content
    if "name" in data:
        data["name"] = "{{name}}"
    return json.dumps(data, indent=2) + "\n"


def collect_src_files(src_root: Path) -> list[Path]:
    """Return all files under src_root, excluding ignored dirs/files."""
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fname in sorted(filenames):
            if fname == "package-lock.json":
                continue
            result.append(Path(dirpath) / fname)
    return result


def compute_new_bytes(src_path: Path, example_name: str, title_token: bool) -> bytes:
    """Compute the desired template content for a given source file."""
    if src_path.suffix.lower() not in TEXT_EXTENSIONS:
        return src_path.read_bytes()

    content = src_path.read_text(encoding="utf-8")

    if src_path.name == "picolet.toml":
        content = transform_picolet_toml(content, title_token)
    elif src_path.name == "package.json":
        content = transform_package_json(content)
    # All other text files: verbatim.

    return content.encode("utf-8")


def unified_diff_str(path: Path, old_bytes: bytes | None, new_bytes: bytes | None) -> str:
    """Return unified diff string; empty string if files are identical."""
    def to_lines(b: bytes | None, label: str) -> tuple[list[str], str]:
        if b is None:
            return [], "/dev/null"
        try:
            return b.decode("utf-8").splitlines(keepends=True), label
        except UnicodeDecodeError:
            return None, label  # type: ignore

    old_lines, old_label = to_lines(old_bytes, f"a/{path}")
    new_lines, new_label = to_lines(new_bytes, f"b/{path}")

    if old_lines is None or new_lines is None:
        if old_bytes != new_bytes:
            return f"Binary files {old_label} and {new_label} differ\n"
        return ""

    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=str(old_label), tofile=str(new_label),
    ))


def main() -> int:
    all_diffs: list[tuple[Path, bytes | None, bytes]] = []

    for example_name, title_token in EXAMPLES.items():
        src_root = EXAMPLES_DIR / example_name
        dst_root = TEMPLATES_DIR / example_name

        if not src_root.is_dir():
            print(f"ERROR: example directory not found: {src_root}", file=sys.stderr)
            return 1

        src_files = collect_src_files(src_root)

        # Files that should exist in template after mirroring.
        expected_rels: set[Path] = set()
        for src_path in src_files:
            rel = src_path.relative_to(src_root)
            expected_rels.add(rel)
            dst_path = dst_root / rel

            new_bytes = compute_new_bytes(src_path, example_name, title_token)
            old_bytes = dst_path.read_bytes() if dst_path.exists() else None

            if old_bytes != new_bytes:
                all_diffs.append((dst_path, old_bytes, new_bytes))

        # Detect orphan files in template that no longer exist in example.
        if dst_root.is_dir():
            for dirpath, dirnames, filenames in os.walk(dst_root):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
                for fname in filenames:
                    dst_path = Path(dirpath) / fname
                    rel = dst_path.relative_to(dst_root)
                    if rel not in expected_rels:
                        all_diffs.append((dst_path, dst_path.read_bytes(), None))

    if not all_diffs:
        print("mirror: no drift — templates match examples")
        return 0

    if CHECK_ONLY:
        print(f"mirror: drift detected in {len(all_diffs)} file(s):")
        for dst_path, old_bytes, new_bytes in all_diffs:
            diff = unified_diff_str(dst_path, old_bytes, new_bytes)
            if diff:
                print(diff, end="")
            else:
                action = "deleted" if new_bytes is None else ("added" if old_bytes is None else "changed")
                print(f"  {action}: {dst_path}")
        return 1

    # Write mode: apply all changes.
    for dst_path, old_bytes, new_bytes in all_diffs:
        if new_bytes is None:
            dst_path.unlink(missing_ok=True)
            print(f"  deleted: {dst_path.relative_to(REPO_ROOT)}")
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_bytes(new_bytes)
            action = "added" if old_bytes is None else "updated"
            print(f"  {action}: {dst_path.relative_to(REPO_ROOT)}")

    print(f"mirror: wrote {len(all_diffs)} file(s)")
    return 0


sys.exit(main())
PYEOF
