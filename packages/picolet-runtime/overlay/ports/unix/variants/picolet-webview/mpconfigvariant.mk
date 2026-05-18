# Lean variant for the picolet webview runtime (linux-x64 + macOS, PH07/PH25).
#
# Forked from picolet-cli/mpconfigvariant.mk.  Delta is the frozen
# manifest pointer; everything else is identical.  libffi is enabled
# (MICROPY_PY_FFI=1) for the WebKitGTK 4.1 bindings (Linux) and the
# WKWebView bindings (macOS) frozen under packages/picolet-runtime/python/picolet_ui.

# Enable the ffi module and build libffi from source (static link).
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Disable SSL — not in the webview baseline.
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: webview variant pulls in both picolet (PH06 dispatcher)
# and picolet_ui (PH07 WebKitGTK bindings on Linux; PH25 WKWebView on macOS).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview_unix.py

# romfs_trailer.c lives in the shared overlay directory (shared/romfs_trailer.c
# after the overlay copy step).  The unix port's $(wildcard $(VARIANT_DIR)/*.c)
# no longer picks it up, so we add it explicitly here.
SRC_C += shared/romfs_trailer.c
INC += -I$(TOP)/shared

# ---------------------------------------------------------------------------
# Platform-specific source files and linker flags (PH25)
#
# On macOS (Darwin), include the WKWebView C overlay and link against the
# required Apple frameworks.  The C overlay exposes all picolet_wkwv_*
# symbols with __attribute__((visibility("default"))); -fvisibility=hidden
# hides everything else so ffi.open(None) does not collide with internal
# symbols.  -Wl,-export_dynamic re-exports the visible symbols from the
# Mach-O binary so dlopen(NULL) / ffi.open(None) can resolve them.
#
# On Linux, the existing WebKitGTK 4.1 path applies; no additional source
# or link flags are needed here (the GTK libraries are loaded dynamically
# at runtime via ffi.open in _gtk_ffi.py).
# ---------------------------------------------------------------------------

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
SRC_C += picolet_webview_mac.c
CFLAGS_EXTRA += -fvisibility=hidden
LDFLAGS_EXTRA += -Wl,-export_dynamic
LDFLAGS_EXTRA += -framework Cocoa -framework WebKit -framework Foundation
LDFLAGS_EXTRA += -framework CoreFoundation
endif
