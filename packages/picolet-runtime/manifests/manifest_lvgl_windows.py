# Frozen-manifest baseline for the picolet `lvgl` variant (windows-x64, PH12).
#
# Identical content to manifest_lvgl.py — the same Python surface (asyncio,
# os-path, picolet, picolet_ui) is frozen regardless of platform.  The `lv` C
# module enters the runtime via USER_C_MODULES (set in mpconfigvariant.mk),
# not via this manifest.
#
# picolet_ui (not picolet_ui_win) is frozen here: _lvgl.py is platform-agnostic
# and calls only lv.init(), lv.sdl_window_create(), and asyncio primitives
# — none of which are Linux-specific (AD3).

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")

freeze("../python", "picolet")
freeze("../python", "picolet_ui")
