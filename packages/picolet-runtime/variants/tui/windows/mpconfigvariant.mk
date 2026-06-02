# Lean variant for the picolet tui runtime (windows-x64).
# Forked from variants/tui/unix/mpconfigvariant.mk; mirrors the cli windows
# variant for the libffi / FROZEN_MANIFEST plumbing.
#
# Deltas from the Unix tui variant:
#   - MICROPY_STANDALONE is not set (Windows Makefile handles libffi via
#     its own deplibs rule + forwarded CROSS_COMPILE).
#   - MICROPY_PY_THREAD is left alone — the Windows port's mpconfigport.h
#     already defaults the threading module off, so there is no make-time
#     knob to override (the .h-level NFR-TUI-11 / D6 single-thread invariant
#     is preserved by the port default).  The Unix variant has to override
#     because ports/unix/mpconfigport.mk turns _thread on by default.
#   - FROZEN_MANIFEST points at manifest_tui_windows.py.
#   - The Windows backend of picolet_tuiterm lives in a separate .c
#     (picolet_tuiterm_win.c) — both halves implement the same public
#     surface, so the Windows variant adds the _win.c file only.  Building
#     the Unix sibling here would pull in <termios.h>/<poll.h> and fail
#     under MinGW.
#   - Symbol retention uses --export-all-symbols (MinGW idiom matching the
#     webview/lvgl windows variants) instead of the ELF --export-dynamic +
#     per-symbol --undefined= dance; combined with __declspec(dllexport) on
#     every public declaration in picolet_tuiterm.h that is sufficient to
#     keep the symbols in the PE export table where ffi.open(None) finds
#     them.

# Enable the ffi module.  The Windows Makefile's deplibs rule builds libffi
# from source when MICROPY_PY_FFI=1, using CROSS_COMPILE forwarded from
# the build script.
MICROPY_PY_FFI = 1

# Disable SSL — not in the tui baseline (also saves ~300 KB).
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: tui variant pulls in asyncio, os-path, picolet, and
# picolet_tui (Phase 2b lands the Python surface).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_tui_windows.py

# picolet_tuiterm: conhost VT-mode + ReadConsoleInputW shim reached from
# frozen Python via ffi.open(None).func("picolet_tuiterm_*").  See research
# doc 04 §2.
#
# EXTRA_SRC_C (from pr/windows-extra-src-c) is the documented extension
# hook — variant SRC_C += is silently discarded by the port Makefile's
# plain SRC_C = reassignment.
EXTRA_SRC_C += $(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_tuiterm/picolet_tuiterm_win.c
INC         += -I$(PICOLET_RUNTIME_ROOT)/user_c_modules/picolet_tuiterm

# Export the flat C API symbols even though they're inside the .exe so
# libffi's ffi.open(None) can resolve them via the running process's PE
# export table.  --export-all-symbols is the MinGW idiom (the ELF
# --export-dynamic / --export-dynamic-symbol pair is not honoured by
# MinGW ld).  Combined with __declspec(dllexport) on the public
# declarations in picolet_tuiterm.h, the picolet_tuiterm_* symbols
# survive linker --gc-sections and end up reachable.
LDFLAGS += -Wl,--export-all-symbols

# Win32 libraries.  kernel32 is implicit (linked by every MinGW PE binary).
# user32 is needed for MessageBoxW-style fallbacks if the conhost path
# is ever extended; picolet_tuiterm_win.c itself only calls conhost APIs
# (kernel32) but linking user32 is harmless and matches the webview /
# lvgl windows variants for forward compatibility with any UI fallback.
LIB += -luser32
