# Frozen-manifest baseline for the picolet `lvgl` variant (PH11).
#
# Pulls in:
#   - asyncio      (FR-IPC-5 prerequisite, used by picolet._dispatcher + picolet_ui._loop._lvgl_pump)
#   - os-path      (used by user code)
#   - picolet        (PH06+ IPC dispatcher; PH11 adds InProcessTransport)
#   - picolet_ui     (PH07 webview + PH11 lvgl shared facade)
#   - lv_binding_micropython (the LVGL C module + generated bindings)

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")

freeze("../python", "picolet")
freeze("../python", "picolet_ui")

# Register the LVGL binding as a C module through the manifest rather
# than the legacy USER_C_MODULES make-arg.  Path is relative to this
# manifest's directory (manifests/), matching the freeze() calls above.
c_module("../lib/lv_binding_micropython")
