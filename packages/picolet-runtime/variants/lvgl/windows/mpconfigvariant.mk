# Lean variant for the picolet lvgl runtime (windows-x64, PH12).
#
# Forked from variants/lvgl/unix/mpconfigvariant.mk.
# Deltas from the Linux version:
#   - MICROPY_STANDALONE is not set (Windows Makefile handles libffi via deplibs).
#   - FROZEN_MANIFEST points at manifest_lvgl_windows.py.
#   - SDL2 cflags and ldflags are injected directly (MXE static path).
#   - LV_CONF_PATH reuses the unix lv_conf.h (content is platform-agnostic).

MXE_ROOT := /usr/src/mxe/usr/x86_64-w64-mingw32.static.posix

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

# SDL2 include path.  lv_conf.h uses #include <SDL2/SDL.h> (with the SDL2/
# prefix), so the include path must point at the directory *above* SDL2/,
# not at SDL2/ itself.
CFLAGS_EXTRA += -I$(MXE_ROOT)/include

# SDL2 static lib + Win32 dependencies SDL2 needs when statically linked.
# SDL2 on Windows calls into: user32 (window creation), winmm (timer),
# gdi32 (GDI surface), ole32 (COM init), imm32 (IME input), version
# (VerQueryValueW), setupapi (HID device enumeration).
# Additional dependencies from the MXE static build: oleaut32, uuid,
# advapi32 (registry / security), shell32.
LIB += $(MXE_ROOT)/lib/libSDL2.a
LIB += -luser32 -lwinmm -lgdi32 -lole32 -loleaut32 -limm32 -lversion
LIB += -lsetupapi -luuid -ladvapi32 -lshell32

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
            -I$(MXE_ROOT)/include

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
