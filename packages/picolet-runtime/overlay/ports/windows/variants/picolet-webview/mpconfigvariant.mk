# Lean variant for the picolet webview runtime (windows-x64, PH10).
# Forked from picolet-cli/mpconfigvariant.mk.  Deltas:
#   - frozen manifest pointer
#   - picolet_webview2 C overlay statically compiled in
#   - extra LDFLAGS for Win32 libs the overlay needs (user32, ole32, shell32)
#
# The WebView2Loader.dll is NOT linked here — it is bundled into the app
# romfs by `picolet build` (deliverable 18) and loaded via LoadLibraryW at
# runtime (AD1).  Gate 7 verifies the static-import set contains no
# WebView2Loader.dll.

MICROPY_PY_FFI = 1

# romfs_trailer.c and picolet_webview2.c both live alongside this .mk and
# are picked up automatically via $(wildcard $(VARIANT_DIR)/*.c) in the
# windows port's main Makefile.  We don't need an explicit SRC_C += line
# (and could not use one anyway — the port's Makefile sets SRC_C with `=`
# AFTER including this .mk, which would discard any append done here).
#
# The C overlay includes its companion header via "picolet_webview2.h" and
# the vendored WebView2 header subset via "include/WebView2_min.h"; both
# resolve relative to the .c file's directory, so no INC tweaks are needed.

# WebView2 SDK headers are MinGW-compat already (the subset under
# include/ is hand-written from the public SDK documentation rather
# than copied from the MSVC-flavoured Microsoft headers).
CFLAGS += -DPICOLET_WV2_MINGW=1

# Export the flat C API symbols even though they're inside the .exe so
# libffi.ffi.open(None)-equivalent can resolve them via the running
# process's symbol table.  -rdynamic / -Wl,--export-all-symbols is the
# MinGW idiom; we use -Wl,--export-all-symbols for the windows port.
LDFLAGS += -Wl,--export-all-symbols

# Win32 system libraries needed by the overlay (user32 for Create/Register
# WindowExW, ole32 for CoInitializeEx, shell32 for SHGetFolderPathW
# fallback in the loader-DLL extract path).  bcrypt and ws2_32 are
# already pulled in by the port Makefile.
LIB += -lole32 -loleaut32 -luser32 -lshell32 -lshlwapi

# Frozen manifest: windows-aware webview variant.  See manifest_webview_windows.py.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview_windows.py
