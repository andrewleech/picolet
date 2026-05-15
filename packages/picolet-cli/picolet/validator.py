"""
Validator for app-level picolet.toml files.

Schema source: docs/architecture.md §App-level picolet.toml schema.

Usage:
    from picolet.validator import validate_toml, PicoletTomlError
    errors = validate_toml(Path("picolet.toml"))
    if errors:
        for e in errors:
            print(e)
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Top-level sections the schema allows.
_ALLOWED_SECTIONS: frozenset[str] = frozenset(
    {"app", "ui", "window", "build", "romfs", "sbom"}
)

# Keys allowed in each section, with their expected Python types.
# None means "any scalar" (used for keys we don't type-check beyond being present).
_APP_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "name": str,
    "version": str,
    "entry": str,
}
_APP_REQUIRED: frozenset[str] = frozenset({"name", "version", "entry"})

_UI_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "renderer": str,
    "root": str,
}
_UI_RENDERER_VALUES: frozenset[str] = frozenset({"webview", "lvgl"})

_WINDOW_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "title": str,
    "size": list,
    "resizable": bool,
}

_BUILD_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "targets": list,
}

_ROMFS_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "include": list,
}

_SBOM_SCHEMA: dict[str, type | tuple[type, ...]] = {}

_SECTION_SCHEMAS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "app": _APP_SCHEMA,
    "ui": _UI_SCHEMA,
    "window": _WINDOW_SCHEMA,
    "build": _BUILD_SCHEMA,
    "romfs": _ROMFS_SCHEMA,
    "sbom": _SBOM_SCHEMA,
}


@dataclass
class PicoletTomlError:
    """A structured validation error from a picolet.toml file."""

    file: str
    section: str
    key: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file}: [{self.section}] {self.key}: {self.reason}"


def validate_toml(path: Path) -> list[PicoletTomlError]:
    """Parse and validate a picolet.toml file.

    Returns a list of PicoletTomlError instances. An empty list means valid.
    Raises SystemExit on TOML syntax errors (unrecoverable).
    """
    file_str = str(path)
    errors: list[PicoletTomlError] = []

    # Pass 1: parse.
    try:
        with open(path, "rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        # Syntax errors are unrecoverable; report and return immediately.
        errors.append(
            PicoletTomlError(
                file=file_str,
                section="(syntax)",
                key="(parse)",
                reason=str(exc),
            )
        )
        return errors
    except OSError as exc:
        errors.append(
            PicoletTomlError(
                file=file_str,
                section="(io)",
                key="(open)",
                reason=str(exc),
            )
        )
        return errors

    # Pass 2: schema check.

    # Unknown top-level sections.
    for section in data:
        if section not in _ALLOWED_SECTIONS:
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section=section,
                    key="(section)",
                    reason=f'unknown section "{section}"; allowed sections are: '
                    + ", ".join(sorted(_ALLOWED_SECTIONS)),
                )
            )

    # [app] — required.
    if "app" not in data:
        errors.append(
            PicoletTomlError(
                file=file_str,
                section="app",
                key="(section)",
                reason='required section "[app]" is missing',
            )
        )
    else:
        app = data["app"]
        if not isinstance(app, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="app",
                    key="(section)",
                    reason='"[app]" must be a table',
                )
            )
        else:
            errors.extend(_check_section(file_str, "app", app, _APP_SCHEMA))
            for key in _APP_REQUIRED:
                if key not in app:
                    errors.append(
                        PicoletTomlError(
                            file=file_str,
                            section="app",
                            key=key,
                            reason=f'required key "{key}" is missing',
                        )
                    )

    # [ui] — optional; if present, validate.
    if "ui" in data:
        ui = data["ui"]
        if not isinstance(ui, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="ui",
                    key="(section)",
                    reason='"[ui]" must be a table',
                )
            )
        else:
            errors.extend(_check_section(file_str, "ui", ui, _UI_SCHEMA))
            renderer = ui.get("renderer")
            if renderer is not None and isinstance(renderer, str):
                if renderer not in _UI_RENDERER_VALUES:
                    errors.append(
                        PicoletTomlError(
                            file=file_str,
                            section="ui",
                            key="renderer",
                            reason=(
                                f'unknown value "{renderer}"; '
                                f'expected "webview" or "lvgl"'
                            ),
                        )
                    )

    # [window] — optional; if present, validate.
    if "window" in data:
        window = data["window"]
        if not isinstance(window, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="window",
                    key="(section)",
                    reason='"[window]" must be a table',
                )
            )
        else:
            errors.extend(_check_section(file_str, "window", window, _WINDOW_SCHEMA))

    # [build] — optional.
    if "build" in data:
        build = data["build"]
        if not isinstance(build, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="build",
                    key="(section)",
                    reason='"[build]" must be a table',
                )
            )
        else:
            errors.extend(_check_section(file_str, "build", build, _BUILD_SCHEMA))

    # [romfs] — optional.
    if "romfs" in data:
        romfs = data["romfs"]
        if not isinstance(romfs, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="romfs",
                    key="(section)",
                    reason='"[romfs]" must be a table',
                )
            )
        else:
            errors.extend(_check_section(file_str, "romfs", romfs, _ROMFS_SCHEMA))

    # [sbom] — optional, no typed keys defined yet.
    if "sbom" in data:
        sbom = data["sbom"]
        if not isinstance(sbom, dict):
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section="sbom",
                    key="(section)",
                    reason='"[sbom]" must be a table',
                )
            )

    return errors


def _check_section(
    file_str: str,
    section: str,
    table: dict[str, Any],
    schema: dict[str, type | tuple[type, ...]],
) -> list[PicoletTomlError]:
    """Type-check known keys in a section table.

    Unknown keys within a section are not currently rejected (they may be
    added by future schema versions). Only type mismatches are reported.
    """
    errors: list[PicoletTomlError] = []
    for key, expected_type in schema.items():
        if key not in table:
            continue
        value = table[key]
        if not isinstance(value, expected_type):
            type_name = (
                expected_type.__name__
                if isinstance(expected_type, type)
                else " or ".join(t.__name__ for t in expected_type)
            )
            errors.append(
                PicoletTomlError(
                    file=file_str,
                    section=section,
                    key=key,
                    reason=(
                        f"expected {type_name}, "
                        f"got {type(value).__name__} ({value!r})"
                    ),
                )
            )
    return errors
