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

# romfs_trailer.c is now in overlay/shared/ (shared/romfs_trailer.c after
# the overlay copy step).  The Windows Makefile appends it to SRC_C
# explicitly after the SRC_C = block, so no variant-level SRC_C += is
# needed or possible (the port Makefile's SRC_C = would discard it).

# Frozen manifest: resolved via PICOLET_RUNTIME_ROOT (exported by build-runtime.sh).
# The ?= allows the build script to override for test-romfs variants.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
