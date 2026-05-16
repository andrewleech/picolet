"""
picolet init — scaffold a new app from a template.

Usage:
    picolet init <name> [--template TEMPLATE] [--output-dir DIR]

Template resolution order:
  1. importlib.resources.files("picolet_templates") — works when picolet-templates
     is installed (uv pip install -e or wheel install).
  2. __file__-relative fallback — traverses up from this file to find
     packages/picolet-templates/picolet_templates/ in the source tree.
     Covers the `uv run packages/picolet-cli/picolet_cli/__main__.py` path where
     picolet-templates is not installed into the script's isolated environment.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from picolet_cli.validator import validate_toml

# Templates known to exist in this phase. `picolet init --template <name>` will
# be rejected with a clear error for any name not in this set.
_KNOWN_TEMPLATES: frozenset[str] = frozenset({"hello-cli", "hello-webview", "hello-lvgl", "hello-vue"})

# Name validation: alphanumerics, hyphens, underscores; no leading digit.
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


def add_parser(subparsers) -> None:
    """Register the init subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "init",
        help="scaffold a new app from a template",
        description="Create a new picolet app directory from a starter template.",
    )
    p.add_argument("name", help="app name (used as the project directory name)")
    p.add_argument(
        "--template",
        default="hello-cli",
        help='template to use (default: hello-cli; available: "hello-cli", "hello-webview", "hello-lvgl", "hello-vue")',
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="directory to create (default: ./<name>)",
    )
    p.set_defaults(func=run)


def run(args) -> None:
    """Entry point for `picolet init`."""
    name: str = args.name
    template: str = args.template
    output_dir = Path(args.output_dir) if args.output_dir else Path(name)

    # 1. Validate name.
    if not _NAME_RE.match(name):
        print(
            f"error: invalid app name {name!r}; "
            "must match [a-zA-Z_][a-zA-Z0-9_-]*",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Validate template name.
    if template not in _KNOWN_TEMPLATES:
        known = ", ".join(f'"{t}"' for t in sorted(_KNOWN_TEMPLATES))
        print(
            f"error: unknown template {template!r}; known templates: {known}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 3. Resolve template directory.
    template_dir = _resolve_template(template)
    if template_dir is None:
        print(
            f"error: template {template!r} not found in installed package or source tree",
            file=sys.stderr,
        )
        sys.exit(1)

    # 4. Check output directory.
    if output_dir.exists():
        contents = list(output_dir.iterdir())
        if contents:
            print(
                f"error: directory {output_dir} already exists and is non-empty; "
                "remove it or choose a different name",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        output_dir.mkdir(parents=True)

    # 5. Copy and substitute template files.
    try:
        _copy_template(template_dir, output_dir, name)
    except Exception as exc:
        # Roll back: remove the partially-created directory.
        shutil.rmtree(output_dir, ignore_errors=True)
        print(f"error: failed to scaffold template: {exc}", file=sys.stderr)
        sys.exit(1)

    # 6. Self-check: validate the produced picolet.toml.
    produced_toml = output_dir / "picolet.toml"
    all_results = validate_toml(produced_toml)
    hard_errors = [e for e in all_results if e.level != "warn"]
    if hard_errors:
        shutil.rmtree(output_dir, ignore_errors=True)
        print("error: scaffolded picolet.toml failed validation:", file=sys.stderr)
        for error in hard_errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print(f"Created {output_dir} from template {template!r}")


def _resolve_template(template_name: str) -> Path | None:
    """Resolve the template directory, trying importlib.resources then __file__."""

    # Attempt 1: importlib.resources — works when picolet-templates is installed.
    try:
        from importlib.resources import files

        pkg = files("picolet_templates")
        candidate = Path(str(pkg.joinpath(template_name)))
        if candidate.is_dir():
            return candidate
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        pass

    # Attempt 2: __file__-relative path — works in the source tree via uv run.
    # packages/picolet-cli/picolet_cli/init_cmd.py -> packages/picolet-templates/picolet_templates/
    here = Path(__file__).parent  # packages/picolet-cli/picolet_cli/
    candidate = (
        here.parent.parent.parent  # repo root
        / "packages"
        / "picolet-templates"
        / "picolet_templates"
        / template_name
    )
    if candidate.is_dir():
        return candidate

    return None


# Extensions treated as UTF-8 text with {{name}} substitution.
# Everything else is byte-copied verbatim (images, fonts, compiled assets).
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".toml", ".html", ".css", ".js", ".ts", ".vue", ".md", ".txt", ".json", ".yaml", ".yml"}
)


def _copy_template(template_dir: Path, output_dir: Path, name: str) -> None:
    """Recursively copy template_dir into output_dir, substituting {{name}}.

    Files whose suffix is in _TEXT_EXTENSIONS are read as UTF-8 text and have
    ``{{name}}`` replaced with *name*.  All other files (images, fonts, binary
    assets) are copied byte-for-byte via shutil.copy2 so they arrive intact.
    """
    for src_path in template_dir.rglob("*"):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(template_dir)
        dst_path = output_dir / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.suffix.lower() in _TEXT_EXTENSIONS:
            content = src_path.read_text(encoding="utf-8")
            if "{{name}}" in content:
                content = content.replace("{{name}}", name)
            dst_path.write_text(content, encoding="utf-8")
        else:
            shutil.copy2(src_path, dst_path)
