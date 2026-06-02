# Lean variant for the picolet tui runtime (linux-x64).
# Forked from variants/cli/unix/mpconfigvariant.mk.
#
# Deltas from the cli variant:
#   - MICROPY_PY_THREAD disabled (NFR-TUI-20 / D6 — single-thread runtime).
#   - FROZEN_MANIFEST points at manifest_tui_unix.py (populated in Phase 2b).
#   - picolet_tuiterm C shim added to SRC_C with --export-dynamic so
#     ffi.open(None) can resolve picolet_tuiterm_* symbols at runtime.
#
# Per-feature MicroPython build flags required by picolet-tui (NFR-TUI-9):
#   MICROPY_PY_WEAKREF, MICROPY_PY_RE_MATCH_GROUPS — turned on in the
#   variant header (.h), not here, because the .mk only sets the make-time
#   knobs (libffi build, threading port flag, etc.).

# Enable the ffi module and build libffi from source (static link).
# picolet_tuiterm symbols are reached via ffi.open(None).func(...).
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Disable SSL — not in the tui baseline (also saves ~300 KB).
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Single-thread runtime (NFR-TUI-11, NFR-TUI-20, D6).  The unix port's
# mpconfigport.mk sets MICROPY_PY_THREAD = 1 by default; override here so
# _thread is unavailable from Python and the shim pack synthesises
# threading.Lock/Event as no-op wrappers.
MICROPY_PY_THREAD = 0

# Frozen manifest: tui variant pulls in asyncio, os-path, picolet, and
# picolet_tui (Phase 2b lands the Python surface).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_tui_unix.py

# romfs_trailer.c lives in variants/common/ (out-of-tree).
SRC_C += $(PICOLET_RUNTIME_ROOT)/variants/common/romfs_trailer.c
INC += -I$(PICOLET_RUNTIME_ROOT)/variants/common

# picolet_tuiterm: termios/poll/SIGWINCH shim reached from frozen Python
# via ffi.open(None).func("picolet_tuiterm_*").  See research doc 04 §1.
#
# Linker retention: --export-dynamic keeps non-static symbols in the
# dynamic symbol table so dlsym(RTLD_DEFAULT) — which libffi uses
# internally for ffi.open(None) lookups — can find them at runtime.
# Per-symbol --undefined= synthesises a reference each function name so
# --gc-sections cannot drop the section.  The visibility("default")
# attribute on the public declarations is the source-side complement.
SRC_C += $(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_tuiterm/picolet_tuiterm.c
INC += -I$(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_tuiterm

LDFLAGS_EXTRA += -Wl,--export-dynamic
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_enable
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_disable
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_size
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_resize_pending
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_is_tty
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_read_input
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_write
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_capabilities
LDFLAGS_EXTRA += -Wl,--undefined=picolet_tuiterm_last_error
