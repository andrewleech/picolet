# Frozen-manifest baseline for the picolet `lvgl` variant (windows-x64, PH12).
#
# Same Python surface as manifest_lvgl.py (asyncio, os-path, picolet,
# picolet_ui) plus the LVGL C module, registered via c_module() rather than
# the legacy USER_C_MODULES make-arg.
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

# Register the LVGL binding as a C module through the manifest (relative to
# manifests/, like the freeze() calls).  See manifest_lvgl.py.
c_module("../lib/lv_binding_micropython")
