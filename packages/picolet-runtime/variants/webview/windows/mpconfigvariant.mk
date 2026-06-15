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

# picolet_webview2.c lives alongside this .mk and is picked up automatically
# via $(wildcard $(VARIANT_DIR)/*.c) in the windows port's main Makefile.
# romfs_trailer.c has been consolidated to shared/romfs_trailer.c; the
# port Makefile appends it to SRC_C explicitly after the SRC_C = block.
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
# ws2_32 is needed by picolet_wv2_pick_test_port() (PH17) — WSAStartup/socket/
# getsockname/closesocket.  The port Makefile may already link ws2_32 but we
# add it explicitly so the dependency is captured here.
#
# picolet_winevents (user_c_modules/picolet_winevents/picolet_winevents.c) pulls in
# comctl32 (SetWindowSubclass), wtsapi32 (WTSRegisterSessionNotification) and
# powrprof (RegisterPowerSettingNotification).  shell32 is already listed for
# the loader.
# LIBS (not LIB): the windows port link rule is
#   $(CC) -o $@ $^ $(LIBS) $(LDFLAGS)   [py/mkrules.mk]
# and the port seeds it with `LIBS += -lws2_32`.  An earlier `LIB +=`
# here went into a variable nothing references, so ole32/comctl32/
# wtsapi32 never reached the linker and the COM + window-subclass +
# session-notification symbols failed to resolve.
LIBS += -lole32 -loleaut32 -luser32 -lshell32 -lshlwapi -lws2_32
LIBS += -lcomctl32 -lwtsapi32 -lpowrprof

# picolet_winevents: generic Win32 event hook (WM_DEVICECHANGE, WM_CLOSE,
# WM_POWERBROADCAST, …).  Statically linked into the .exe; reached from
# Python via ffi.open(None).func("picolet_winevents_*").  --export-all-symbols
# above makes its symbols visible without per-symbol --undefined retains.
#
# EXTRA_SRC_C (from pr/windows-extra-src-c) is the documented extension
# hook — variant SRC_C += is silently discarded by the port Makefile's
# plain SRC_C = reassignment.
EXTRA_SRC_C += $(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_winevents/picolet_winevents.c
INC         += -I$(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_winevents

# Frozen manifest: windows-aware webview variant.  See manifest_webview_windows.py.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview_windows.py
