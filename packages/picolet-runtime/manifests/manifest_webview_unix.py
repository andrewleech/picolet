# Frozen-manifest baseline for the picolet `webview` variant (PH07).
#
# Identical to manifest_cli.py plus the picolet_ui package (WebKitGTK 4.1
# bindings via pure libffi).  See the cli manifest for the rationale on
# include() vs require() etc.
#
# Pulls in:
#   - asyncio      (FR-IPC-5 prerequisite, used by picolet._dispatcher + picolet_ui._loop)
#   - os-path      (used by user code)
#   - picolet        (the runtime-side IPC dispatcher package; PH06+)
#   - picolet_ui     (PH07: GTK 3 + WebKitGTK 4.1 bindings + WebviewTransport)

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")
require("pathlib")
require("__future__")

freeze("../python", "picolet")
freeze("../python", "picolet_ui")
