# Lean variant for the picolet lvgl runtime (windows-x64, PH12).
#
# Forked from variants/lvgl/unix/mpconfigvariant.mk.
# Deltas from the Linux version:
#   - MICROPY_STANDALONE is not set (Windows Makefile handles libffi via deplibs).
#   - FROZEN_MANIFEST points at manifest_lvgl_windows.py.
#   - SDL2 paths come from the official upstream MinGW binary release
#     (downloaded by build-runtime.sh [2b/8] and passed in as
#     SDL2_INCLUDE_DIR / SDL2_LIB_DIR Make variables).
#   - LV_CONF_PATH reuses the unix lv_conf.h (content is platform-agnostic).

# Enable the ffi module.  Do NOT set MICROPY_STANDALONE — Windows Makefile's
# deplibs rule handles libffi cross-build without the standalone bootstrap.
MICROPY_PY_FFI = 1

# Disable SSL — not in the lvgl baseline.
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: lvgl variant pulls in asyncio, os-path, picolet, and
# picolet_ui (the same Python surface as the Linux lvgl variant).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_lvgl_windows.py

# USER_C_MODULES points at the parent directory of lv_binding_micropython.
# MicroPython's py.mk walks subdirectories looking for
# $(USER_C_MODULES)/*/micropython.mk; the binding's micropython.mk lives
# at lv_binding_micropython/micropython.mk under that root.
USER_C_MODULES = $(PICOLET_RUNTIME_ROOT)/lib

# Override lv_binding_micropython's default lv_conf.h with the picolet
# hand-tuned copy (content is platform-agnostic pure C preprocessor directives).
LV_CONF_PATH = $(PICOLET_RUNTIME_ROOT)/variants/lvgl/unix/lv_conf.h

# Tell lv_conf.h to enable LV_USE_SDL (guarded on MICROPY_SDL there).
# This bypasses the binding's pkg-config SDL2 detection which is only
# active when CURDIR's basename is "unix" (AD2).
CFLAGS_EXTRA += -DMICROPY_SDL=1

# SDL2 paths come from build-runtime.sh which downloads the official
# libsdl-org MinGW binary release (SDL2-devel-2.30.10-mingw.tar.gz) and
# passes SDL2_INCLUDE_DIR and SDL2_LIB_DIR as Make variables.  Fail the
# build with a clear message if either is unset rather than producing a
# confusing missing-header error later.
ifeq ($(SDL2_INCLUDE_DIR),)
$(error SDL2_INCLUDE_DIR not set — build-runtime.sh should pass it in. \
        If invoking make directly, point it at the upstream MinGW \
        release's x86_64-w64-mingw32/include directory)
endif
ifeq ($(SDL2_LIB_DIR),)
$(error SDL2_LIB_DIR not set — see comment above)
endif

# lv_conf.h uses #include <SDL2/SDL.h>, so the include path must point at
# the directory *above* SDL2/ — exactly what SDL2_INCLUDE_DIR contains.
CFLAGS_EXTRA += -I$(SDL2_INCLUDE_DIR)

# Static linkage against libSDL2.a from the official upstream MinGW
# binary release.  The "one binary, no runtime deps" property is core to
# the project's value proposition; dynamic linkage against SDL2.dll is
# NOT acceptable here.  The upstream release ships both libSDL2.a and
# libSDL2.dll.a — ld picks the import lib first for plain `-lSDL2`, so
# we reference the archive by explicit path to force static linkage.
#
# Win32 sub-libs SDL2 itself calls into (from sdl2.pc Libs.private):
#   - dinput8 / dxguid / dxerr8  DirectInput
#   - user32                     window creation
#   - winmm                      multimedia timer
#   - gdi32                      GDI surface
#   - ole32 / oleaut32 / uuid    COM
#   - imm32                      IME input
#   - version                    VerQueryValueW
#   - setupapi                   HID device enumeration
#   - shell32                    shell APIs
LIB += $(SDL2_LIB_DIR)/libSDL2.a
LIB += -ldinput8 -ldxguid -ldxerr8
LIB += -luser32 -lwinmm -lgdi32 -lole32 -loleaut32 -limm32 -lversion
LIB += -lsetupapi -luuid -lshell32

# LV_CFLAGS: force the binding's CPP preprocessing step to include
# lv_drivers.h so SDL window/input functions appear in the generated
# lv_mpy.c.  The binding's micropython.mk preprocesses lvgl_private.h
# (not lvgl.h), which misses SDL-exposed functions without this -include.
# (PH11 workaround, unchanged for the Windows cross-compiler.)
#
# IMPORTANT: The binding's micropython.mk only adds -DMICROPY_SDL=1 to
# CFLAGS_USERMOD when CURDIR's basename is "unix" (via pkg-config probe).
# For the Windows port (basename "windows") the probe is skipped, so
# MICROPY_SDL never reaches the CPP step unless we inject it via LV_CFLAGS.
# LV_CFLAGS is forwarded into CFLAGS_USERMOD by the binding's own
#   CFLAGS_USERMOD += $(LV_CFLAGS)
# so this is the correct vector to reach the CPP invocation.
# Also include the SDL2 headers so the CPP can resolve <SDL2/SDL.h>
# during preprocessing (gen_mpy.py only needs type information, not
# the actual SDL symbols — those come at link time from libSDL2.a).
LV_CFLAGS = -include $(PICOLET_RUNTIME_ROOT)/lib/lv_binding_micropython/lvgl/src/drivers/lv_drivers.h \
            -DMICROPY_SDL=1 \
            -I$(SDL2_INCLUDE_DIR)

# Dead-code elimination: put each function/data item in its own section
# so the linker can garbage-collect unreachable sections from SDL2's static
# archive (SDL2 ships many backends — DirectX, audio, haptics — we only use
# the window/input core).  --gc-sections on the link step drops unused sections.
CFLAGS_EXTRA += -ffunction-sections -fdata-sections
LDFLAGS += -Wl,--gc-sections
# Note: --export-all-symbols is intentionally absent.  The LVGL variant
# does not use ffi to dlopen(None) and resolve runtime symbols — LVGL enters
# via USER_C_MODULES (C module), not via ffi.  Including --export-all-symbols
# would add ~500 KB to the PE export table for no benefit.
#
# Link-time optimisation: allows the linker to inline and eliminate dead code
# across all compilation units (MP, LVGL, port glue).  The SDL2 archive is
# built separately with -ffunction-sections/-fdata-sections; those complement
# LTO (LTO operates on IR, gc-sections on ELF sections).
# NOTE: LTO disabled — the MicroPython Windows port's Makefile uses COPT
# which gets prepended before CFLAGS_EXTRA; the LTO IR is not compatible with
# the LTRANS invocation inside dockcross when the lto-wrapper path differs.
# The -ffunction-sections + --gc-sections combination is the primary dead-code
# elimination mechanism for SDL2; LTO provides diminishing returns here.
#CFLAGS_EXTRA += -flto
#LDFLAGS += -flto

# romfs_trailer.c is in variants/common/ (out-of-tree).
# The Windows port Makefile appends it to SRC_C after the SRC_C = block;
# that path reference is updated in the Makefile separately.

# picolet_winevents: generic Win32 event hook (WM_DEVICECHANGE, WM_CLOSE,
# WM_POWERBROADCAST, …).  Compiled into the .exe and reached from Python
# via ffi.open(None).func("picolet_winevents_*").
#
# Unlike the webview variant, this variant does NOT use --export-all-symbols
# (PE export table size), so the FFI-visible symbols are pinned individually
# via -Wl,--undefined= below to survive --gc-sections.
#
# EXTRA_SRC_C (from pr/windows-extra-src-c) is the documented extension
# hook — variant SRC_C += is silently discarded by the port Makefile's
# plain SRC_C = reassignment.
EXTRA_SRC_C += $(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_winevents/picolet_winevents.c
INC         += -I$(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_winevents

# Win32 libs that picolet_winevents.c needs in addition to what SDL2/lvgl pull in.
# user32, ole32, shell32, oleaut32, advapi32 are already in LIB above (SDL2).
LIB += -lcomctl32 -lwtsapi32 -lpowrprof

# Retain picolet_winevents_* symbols against --gc-sections.
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_attach
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_detach
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_subscribe
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_unsubscribe
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_poll_json
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_free
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_overflow_count
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_watch_device_interface
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_watch_power
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_watch_session
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_watch_clipboard
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_accept_drop_files
LDFLAGS_EXTRA += -Wl,--undefined=picolet_winevents_last_error
# The LVGL variant doesn't --export-all-symbols either, so symbols listed via
# --undefined must also appear in the export table for ffi.open(None) to
# resolve them at runtime.  -Wl,--export-dynamic-symbol takes a single name
# at a time and is supported by MinGW ld since 2.35.
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_attach
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_detach
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_subscribe
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_unsubscribe
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_poll_json
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_free
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_overflow_count
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_watch_device_interface
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_watch_power
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_watch_session
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_watch_clipboard
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_accept_drop_files
LDFLAGS_EXTRA += -Wl,--export-dynamic-symbol=picolet_winevents_last_error
