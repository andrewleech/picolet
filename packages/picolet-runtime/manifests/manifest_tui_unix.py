# Frozen-manifest baseline for the picolet `tui` variant on unix (Phase 2a).
#
# Phase plan: docs/tui/research/00-synthesis.md §2.
# Spec:      docs/tui/tui-v0.1-spec.md §3.1 (variant skeleton) +
#            §3.2 (Python package layout, frozen as .mpy).
#
# Pulls in:
#   - asyncio        (FR-TUI-53/54: the App joins the picolet asyncio pump
#                    and uses no worker threads)
#   - os-path        (used by user code)
#   - picolet        (the existing runtime-side IPC dispatcher — FR-TUI-56
#                    keeps @picolet.command + picolet.invoke working
#                    inside a TUI app)
#   - picolet_tui    (NFR-TUI-19 frozen ≤ 120 KiB romfs; Phase 4-5 fills
#                    the modules in)
#   - picolet_tui._shims (Phase 2b shim pack — NFR-TUI-19 ≤ 20 KiB; loaded
#                    via the picolet_tui package __init__ so downstream
#                    imports of dataclasses / typing / weakref resolve)
#
# Does NOT pull in picolet_ui — that package is the webview/lvgl renderer
# facade.  TUI is its own renderer; mixing the two would link surface area
# that the binary cannot afford under NFR-TUI-1's 2 MiB ceiling.
#
# asyncio lives in extmod/asyncio (not in micropython-lib) so it is
# included via include() rather than require().  Mirrors manifest_cli.py.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")
require("functools")

freeze("../python", "picolet")
freeze("../python", "picolet_tui")
