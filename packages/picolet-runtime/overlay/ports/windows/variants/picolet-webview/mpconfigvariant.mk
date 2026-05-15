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

# romfs_trailer.c is co-located in this variant directory and picked up
# automatically via $(wildcard $(VARIANT_DIR)/*.c) — same trick the cli
# variant uses.

# --- picolet_webview2 C overlay -------------------------------------------
#
# The overlay sources live one level up under
# overlay/modules/picolet_webview2/ — flattened into the windows port by
# rebuild-integration.sh into ports/windows/modules/picolet_webview2/.
# The overlay is windows-x64-webview-only; the cli variant does not
# compile it.

PICOLET_WEBVIEW2_DIR = modules/picolet_webview2
SRC_C += $(PICOLET_WEBVIEW2_DIR)/picolet_webview2.c
INC += -I$(PICOLET_WEBVIEW2_DIR) -I$(PICOLET_WEBVIEW2_DIR)/include

# WebView2 SDK headers vendored under modules/picolet_webview2/include/
# need a couple of MinGW-w64-compat shims to compile.  These are toggled
# via predefines so the overlay .c stays clean of MSVC-isms.
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
