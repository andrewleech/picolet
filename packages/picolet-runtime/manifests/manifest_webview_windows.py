# Frozen-manifest baseline for the picolet `webview` variant on windows-x64 (PH10).
#
# Identical to the unix webview manifest (asyncio + os-path + picolet)
# plus the Windows-specific picolet_ui_win package (Win32 + WebView2 via
# the picolet_webview2 C overlay).  PH07's picolet_ui (GTK/WebKitGTK) is
# NOT frozen here — that package is unix-only and would fail to import
# on Windows for lack of libwebkit2gtk-4.1.so.0.
#
# Symmetry with manifest_webview_unix.py: each manifest freezes exactly
# one of (picolet_ui, picolet_ui_win), keeping the runtime's behaviour
# trivially platform-clean.  Gate 15 exercises this: each runtime can
# only import the package built for its platform.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

include("$(MPY_DIR)/extmod/asyncio")

require("os-path")

freeze("../python", "picolet")
freeze("../python", "picolet_ui_win")
