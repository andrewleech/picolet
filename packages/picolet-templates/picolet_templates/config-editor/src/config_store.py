"""config_store.py — load, validate, save TOML/YAML/JSON config files.

Supported formats (detected by file extension):
  .toml  — parsed via tomllib (vendored); serialised via _toml_dumps()
  .yaml, .yml — parsed via micro_yaml (vendored); serialised via _yaml_dumps()
  .json  — parsed via json (stdlib); serialised via json.dumps(indent=2)

Diff: generated Python-side via difflib.unified_diff() (vendored) before save.

Test isolation: PICOLET_CONFIG_DIR overrides the schemas base directory.
Default schemas dir: ~/.config/{{name}}/schemas/

Licence: MIT (Picolet project).
"""
from __future__ import annotations

import difflib
import json
import os
import sys
from pathlib import Path

import tomllib
import micro_yaml
import config_validator


# ---------------------------------------------------------------------------
# Schemas directory
# ---------------------------------------------------------------------------

def _schemas_dir() -> Path:
    """Return the schemas directory path, creating it if absent."""
    override = os.environ.get("PICOLET_CONFIG_DIR")
    if override:
        p = Path(override) / "schemas"
    elif sys.platform == "win32":
        base_env = os.environ.get("APPDATA")
        if not base_env:
            raise RuntimeError("APPDATA not set on Windows")
        p = Path(base_env) / "{{name}}" / "schemas"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        p = base / "{{name}}" / "schemas"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".toml":
        return "toml"
    if ext in (".yaml", ".yml"):
        return "yaml"
    if ext == ".json":
        return "json"
    raise ValueError(f"unsupported file extension: {ext!r}")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _parse(path: str, fmt: str):
    if fmt == "toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    if fmt == "yaml":
        with open(path, "r", encoding="utf-8") as f:
            result = micro_yaml.load(f.read())
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise ValueError(f"YAML root must be a mapping, got {type(result).__name__}")
        return result
    if fmt == "json":
        with open(path, "r", encoding="utf-8") as f:
            return json.loads(f.read())
    raise ValueError(f"unknown format: {fmt!r}")


# ---------------------------------------------------------------------------
# Serialise
# ---------------------------------------------------------------------------

def _serialise(doc, fmt: str) -> str:
    if fmt == "toml":
        return _toml_dumps(doc)
    if fmt == "yaml":
        return _yaml_dumps(doc)
    if fmt == "json":
        return json.dumps(doc, indent=2) + "\n"
    raise ValueError(f"unknown format: {fmt!r}")


def _toml_dumps(obj: dict, _section_prefix: str = "") -> str:
    """Minimal TOML serialiser.

    Handles: str, int, float, bool, list of scalars, dict (as [section]).
    Does not handle datetime values — raises TypeError with a clear message.

    Limitation (O4): datetime fields are read-only in this editor. If a
    loaded TOML file contains datetime values, save() will fail with:
        TypeError: _toml_dumps: unsupported type datetime.datetime —
        datetime fields are read-only in this editor
    """
    scalars = []
    tables = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            tables[k] = v
        else:
            scalars.append(f"{k} = {_toml_value(v)}")

    lines = scalars[:]
    for k, v in tables.items():
        section = f"{_section_prefix}{k}" if _section_prefix else k
        # Add a blank line separator before each table section, but not
        # at the very start of the file (when lines is still empty).
        if lines:
            lines.append(f"\n[{section}]")
        else:
            lines.append(f"[{section}]")
        for sk, sv in v.items():
            if isinstance(sv, dict):
                # Nested table — use dotted section name.
                nested_section = f"{section}.{sk}"
                lines.append(f"\n[{nested_section}]")
                for nk, nv in sv.items():
                    lines.append(f"{nk} = {_toml_value(nv)}")
            else:
                lines.append(f"{sk} = {_toml_value(sv)}")

    return "\n".join(lines) + "\n"


def _toml_value(v) -> str:
    """Serialise a TOML scalar value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:  # nan
            return "nan"
        if v == float('inf'):
            return "inf"
        if v == float('-inf'):
            return "-inf"
        return repr(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        items = ", ".join(_toml_value(i) for i in v)
        return f"[{items}]"
    # Guard for datetime or other unsupported types.
    type_name = type(v).__name__
    raise TypeError(
        f"_toml_dumps: unsupported type {type_name} — "
        f"{type_name} fields are read-only in this editor"
    )


def _yaml_dumps(obj, indent: int = 0) -> str:
    """Minimal YAML serialiser. Handles: str, int, float, bool, None, list, dict."""
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                sub = _yaml_dumps(v, indent + 1)
                lines.append(sub)
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return f"{pad}[]"
        lines = []
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(_yaml_dumps(item, indent + 1))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_yaml_scalar(obj)}"


def _yaml_scalar(v) -> str:
    """Serialise a YAML scalar value."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Quote strings that could be misread as scalars.
        needs_quote = any(c in v for c in (
            ':', '#', '[', ']', '{', '}', ',', '&', '*',
            '?', '|', '<', '>', '=', '!', '%', '@', '`', '"', "'",
        ))
        if needs_quote or v.lower() in ("true", "false", "null", "yes", "no", "on", "off") or not v:
            escaped = v.replace('"', '\\"')
            return f'"{escaped}"'
        return v
    raise TypeError(f"_yaml_scalar: unsupported type {type(v).__name__}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_dir(path: str) -> list:
    """List directory entries for autocomplete. Returns [{name, is_dir}]."""
    p = Path(path)
    if not p.is_dir():
        return []
    entries = []
    try:
        for name in sorted(os.listdir(str(p))):
            full = p / name
            try:
                entries.append({"name": name, "is_dir": full.is_dir()})
            except OSError:
                entries.append({"name": name, "is_dir": False})
    except PermissionError:
        pass
    return entries


def list_schemas() -> list:
    """Return schema names available in the schemas directory."""
    d = _schemas_dir()
    result = []
    try:
        for name in sorted(os.listdir(str(d))):
            if name.endswith(".json"):
                result.append(name[:-5])  # strip .json
    except OSError:
        pass
    return result


def load(path: str) -> dict:
    """Parse a config file. Returns {format, document, schema_hint}."""
    fmt = _detect_format(path)
    doc = _parse(path, fmt)
    # schema_hint: if a schema with the same stem exists, return its name.
    stem = Path(path).stem
    schema_hint = stem if (_schemas_dir() / f"{stem}.json").exists() else None
    return {"format": fmt, "document": doc, "schema_hint": schema_hint}


def validate(fmt: str, document: dict, schema_name: str) -> list:
    """Validate document against named schema. Returns list of error dicts."""
    schema_path = _schemas_dir() / f"{schema_name}.json"
    if not schema_path.exists():
        return [{"path": "", "message": f"schema not found: {schema_name!r}"}]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return config_validator.validate(document, schema)


def save(path: str, fmt: str, document: dict) -> dict:
    """Serialise document and write to path. Returns unified diff lines."""
    p = Path(path)
    original_text = p.read_text(encoding="utf-8") if p.exists() else ""
    new_text = _serialise(document, fmt)

    # R2: validate TOML round-trip to catch serialiser errors before writing.
    if fmt == "toml":
        try:
            tomllib.loads(new_text)
        except Exception as e:
            raise ValueError(f"TOML round-trip validation failed: {e}") from e

    original_lines = original_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        new_lines,
        fromfile="original",
        tofile="new",
        lineterm="",
    )
    p.write_text(new_text, encoding="utf-8")
    return {"diff": diff, "ok": True}
