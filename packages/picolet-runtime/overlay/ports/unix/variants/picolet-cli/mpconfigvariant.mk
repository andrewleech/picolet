# Lean variant for the picolet cli runtime (linux-x64).
# Enables FFI (FR-RT-5) and static libffi linking (FR-RT-1 / NFR-4).
# Points at the out-of-submodule frozen manifest.

# Enable the ffi module and build libffi from source (static link).
# The unix port's Makefile lines 170-194 wire the rest when these are set.
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Disable SSL — not in the cli baseline; also avoids the mbedtls size
# contribution (~300 KB) which would violate NFR-1.
# mpconfigport.mk enables SSL by default for unix; we override here.
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: resolved at build time via PICOLET_RUNTIME_ROOT,
# which build-runtime.sh exports before invoking make.
# The ?= allows the build script to override for test-romfs variants.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
