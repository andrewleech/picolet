# Frozen-manifest baseline for the picolet `webview` variant on windows-x64 (PH10).
#
# Identical to the unix webview manifest (asyncio + os-path + picolet +
# picolet_ui).  The `picolet_ui` package picks the Win32 + WebView2 backend
# automatically at import time via sys.platform — the Linux GTK
# branches are dead code on Windows and vice versa.
#
# Both webview manifests freeze the same picolet_ui package; the runtime
# variant's mpconfigvariant determines which FFI surface is actually
# linkable (Linux pulls libwebkit2gtk-4.1 via libffi; Windows binds the
# in-process picolet_webview2 C overlay).

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")

freeze("../python", "picolet")
freeze("../python", "picolet_ui")
