"""
Centralised target / variant / renderer constants.

These strings appear in:
  - ``picolet.toml`` ``[ui] renderer`` values
  - ``picolet build / dev / run --target`` flag
  - file paths: ``picolet-runtime-{target}-{variant}[.exe]``
  - subprocess args, error messages

Picolet is intended to be self-hostable on MicroPython, which does not
ship ``enum``. Plain ``str`` constants only.
"""
from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Build targets (host platform × architecture).
# ---------------------------------------------------------------------------

TARGET_LINUX_X64 = "linux-x64"
TARGET_WINDOWS_X64 = "windows-x64"
TARGET_MACOS_X64 = "macos-x64"
TARGET_MACOS_ARM64 = "macos-arm64"
SUPPORTED_TARGETS: frozenset[str] = frozenset({
    TARGET_LINUX_X64,
    TARGET_WINDOWS_X64,
    TARGET_MACOS_X64,
    TARGET_MACOS_ARM64,
})


# ---------------------------------------------------------------------------
# Runtime variants. Appear in artifact filenames + mpconfigvariant dirs.
# ---------------------------------------------------------------------------

VARIANT_CLI = "cli"
VARIANT_WEBVIEW = "webview"
VARIANT_LVGL = "lvgl"
SUPPORTED_VARIANTS: frozenset[str] = frozenset({
    VARIANT_CLI,
    VARIANT_WEBVIEW,
    VARIANT_LVGL,
})

# Renderers permitted in picolet.toml [ui] renderer. The cli variant has
# no [ui] section; non-cli variants map 1:1 to renderer names.
RENDERER_WEBVIEW = VARIANT_WEBVIEW
RENDERER_LVGL = VARIANT_LVGL
SUPPORTED_RENDERERS: frozenset[str] = frozenset({
    RENDERER_WEBVIEW,
    RENDERER_LVGL,
})


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def target_exe_suffix(target: str) -> str:
    """Return the executable file suffix for the named target."""
    return ".exe" if target == TARGET_WINDOWS_X64 else ""


def host_target() -> str:
    """Return the target string matching the current host."""
    import platform
    if sys.platform.startswith("linux"):
        return TARGET_LINUX_X64
    if sys.platform.startswith("win"):
        return TARGET_WINDOWS_X64
    if sys.platform == "darwin":
        machine = platform.machine()
        if machine == "arm64":
            return TARGET_MACOS_ARM64
        return TARGET_MACOS_X64
    raise RuntimeError("unsupported host platform: " + sys.platform)


def variant_for_renderer(renderer: str | None) -> str:
    """Map a picolet.toml ``[ui] renderer`` value to a runtime variant.

    ``renderer=None`` (no ``[ui]`` section) → ``cli``.
    """
    if renderer is None:
        return VARIANT_CLI
    if renderer == RENDERER_WEBVIEW:
        return VARIANT_WEBVIEW
    if renderer == RENDERER_LVGL:
        return VARIANT_LVGL
    raise ValueError("unknown renderer: " + repr(renderer))
