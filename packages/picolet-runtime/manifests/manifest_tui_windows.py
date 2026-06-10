# Frozen-manifest baseline for the picolet `tui` variant on windows-x64 (Phase 2a).
#
# Identical content to manifest_tui_unix.py — the same Python surface
# (asyncio, os-path, picolet, picolet_tui) is frozen regardless of platform.
# The `tuiterm` C module enters the runtime via the per-variant
# mpconfigvariant.mk (SRC_USERMOD_C += variants/tui/windows/tuiterm.c per
# spec §3.1), not via this manifest.
#
# The driver Python layer (picolet_tui/driver/{unix,windows}.py per spec
# §3.2) selects the right tuiterm wiring at import time via sys.platform;
# both branches ship in the same frozen package on every variant build.
#
# Pulls in:
#   - asyncio        (FR-TUI-53/54)
#   - os-path        (used by user code)
#   - picolet        (FR-TUI-56: IPC dispatcher co-resident with the TUI)
#   - picolet_tui    (NFR-TUI-19 frozen footprint budget gated in CI)
#   - picolet_tui._shims (Phase 2b shim pack, loaded via picolet_tui.__init__)

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")
require("functools")
require("itertools")

freeze("../python", "picolet")
freeze("../python", "picolet_tui")
