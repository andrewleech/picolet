# cli-baseline variant for the picolet mcp runtime (linux-x64) with TLS
# enabled (FR-RT-3..5 plus the P2 mcp-variant ticket).  Same FFI/romfs
# wiring as the cli variant; SSL is turned on for the plugin's WSS hub
# transport (Q1 DECISION: dedicated variant rather than re-enabling TLS
# in the shared cli variant).

# Enable the ffi module and build libffi from source (static link).
# The unix port's Makefile lines 170-194 wire the rest when these are set.
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Enable SSL via mbedtls, statically linked (NFR-5: mbedtls is Apache-2.0,
# not GPL/AGPL, so static linking is permitted).  mpconfigport.mk enables
# SSL by default for unix; this pins it explicitly rather than relying on
# the port default so the variant's intent is visible at this file.
MICROPY_PY_SSL = 1
MICROPY_SSL_MBEDTLS = 1
MICROPY_SSL_AXTLS = 0

# Frozen manifest: resolved at build time via PICOLET_RUNTIME_ROOT,
# which build-runtime.sh exports before invoking make.
# The ?= allows the build script to override for test-romfs variants.
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_mcp.py

# romfs_trailer.c lives in variants/common/ (out-of-tree).  The unix port's
# $(wildcard $(VARIANT_DIR)/*.c) does not pick it up from outside VARIANT_DIR,
# so we add it explicitly using an absolute path via PICOLET_RUNTIME_ROOT.
SRC_C += $(PICOLET_RUNTIME_ROOT)/variants/common/romfs_trailer.c
INC += -I$(PICOLET_RUNTIME_ROOT)/variants/common
