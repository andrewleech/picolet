# Lean variant for the picolet lvgl runtime (linux-x64, PH11).
#
# Forked from picolet-webview/mpconfigvariant.mk.  Three deltas:
#   - FROZEN_MANIFEST points at manifest_lvgl.py
#   - USER_C_MODULES points at the lv_binding_micropython overlay
#     submodule so the LVGL widget library + bindings build as a
#     USER_C_MODULE.
#   - LV_CONF_PATH points at the hand-tuned lv_conf.h colocated with
#     this file (overlay/ports/unix/variants/picolet-lvgl/lv_conf.h).
#
# libffi (MICROPY_PY_FFI=1) is kept on for symmetry with the webview
# variant.  picolet_ui's shared _loop / _toml machinery uses it; lvgl
# itself is a USER_C_MODULE and does NOT bind via ffi.  Size cost
# ~30 KB (within NFR-3 headroom).

# Enable the ffi module and build libffi from source (static link).
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Disable SSL — not in the lvgl baseline.
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: lvgl variant pulls in both picolet (PH06 dispatcher)
# and picolet_ui (PH11 lvgl bindings + InProcessTransport + shared
# _loop with _lvgl_pump).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_lvgl.py

# USER_C_MODULES points at the parent directory of the overlay
# submodule (overlay/lib).  MicroPython's py.mk walks subdirectories
# looking for $(USER_C_MODULES)/*/micropython.mk; the binding's
# micropython.mk lives at lv_binding_micropython/micropython.mk under
# that root.  The binding consumes USERMOD_DIR (set to its own
# directory by py.mk's foreach), finds the lvgl submodule under
# $(USERMOD_DIR)/lvgl, runs gen_mpy.py at make-time to produce
# lv_mpy.c, and links the result into the runtime binary.
USER_C_MODULES = $(PICOLET_RUNTIME_ROOT)/overlay/lib

# Override lv_binding_micropython's default lv_conf.h with our hand-
# tuned overlay copy.  micropython.mk reads $(LV_CONF_PATH) and emits
# -DLV_CONF_PATH="<file>" into the compiler invocation.  Without this
# override the build picks up the binding's default lv_conf.h (which
# enables most widgets and blows NFR-3).
LV_CONF_PATH = $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-lvgl/lv_conf.h

# Drop SDL2 build flags into CFLAGS_USERMOD / LDFLAGS_USERMOD.  We do
# this here rather than rely on the binding's pkg-config detection
# because the binding's micropython.mk runs the pkg-config probe only
# when CURDIR's basename is "unix" — which it is during our build, but
# being explicit guards against future relocations of the unix port
# Makefile.
# (The binding's micropython.mk already includes equivalent logic;
# this comment documents the path for readers.)
