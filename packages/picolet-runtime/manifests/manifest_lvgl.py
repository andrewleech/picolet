# Frozen-manifest baseline for the picolet `lvgl` variant (PH11).
#
# Same shape as manifest_webview_unix.py.  The `lvgl` C module enters
# the runtime via USER_C_MODULES (set in the variant .mk), not via
# this manifest.
#
# Pulls in:
#   - asyncio      (FR-IPC-5 prerequisite, used by picolet._dispatcher + picolet_ui._loop._lvgl_pump)
#   - os-path      (used by user code)
#   - picolet        (PH06+ IPC dispatcher; PH11 adds InProcessTransport)
#   - picolet_ui     (PH07 webview + PH11 lvgl shared facade)

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")

freeze("../python", "picolet")
freeze("../python", "picolet_ui")
