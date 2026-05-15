# Lean variant for the picolet cli runtime (windows-x64).
# Enables FFI (FR-RT-5) and the frozen manifest.

# Enable the ffi module.  The Windows Makefile's deplibs rule builds libffi
# from source when MICROPY_PY_FFI=1, using CROSS_COMPILE forwarded from
# the build script.
MICROPY_PY_FFI = 1

# Note: MICROPY_STANDALONE is a unix-port-specific variable that triggers
# the unix port's libffi-from-source build.  The Windows Makefile handles
# libffi unconditionally via its own deplibs rule and CROSS_COMPILE.
# Do not set MICROPY_STANDALONE here.

# romfs_trailer.c is placed physically in this variant directory (see
# romfs_trailer.c alongside this file).  The Windows Makefile includes
# $(wildcard $(VARIANT_DIR)/*.c) in SRC_C, picking it up automatically.
# The .c file is a copy of overlay/ports/unix/variants/picolet-cli/romfs_trailer.c
# with #ifdef _WIN32 guards — same source, both platforms compile cleanly.

# Frozen manifest: resolved via PICOLET_RUNTIME_ROOT (exported by build-runtime.sh).
# The ?= allows the build script to override for test-romfs variants.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
