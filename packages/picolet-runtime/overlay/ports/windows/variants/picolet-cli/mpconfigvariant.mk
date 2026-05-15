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

# Add romfs_trailer.c to the build.  The file lives in the unix variant
# directory (single canonical copy with #ifdef _WIN32 guards); reference it
# via the absolute PICOLET_RUNTIME_ROOT path exported by build-runtime.sh.
# The Windows Makefile's $(wildcard $(VARIANT_DIR)/*.c) only picks up files
# physically present in the variant directory, so an explicit SRC_C += with
# an absolute path is required for this out-of-variant source.
SRC_C += $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c

# Frozen manifest: resolved via PICOLET_RUNTIME_ROOT (exported by build-runtime.sh).
# The ?= allows the build script to override for test-romfs variants.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
