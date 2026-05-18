/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Andrew Leech
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

// Shared macro set for all picolet unix-port variants (cli, webview, lvgl).
//
// This file is #included by each per-variant mpconfigvariant.h.  The
// variant file must NOT be included directly by the MicroPython build
// system; only the per-variant wrapper is.
//
// Strategy (mirrors the original per-variant comments):
//   1. Pre-define macros that mpconfigvariant_common.h guards with
//      #ifndef so they take effect before the include.
//   2. Set ROM level to EXTRA_FEATURES (same as 'standard') — CORE_FEATURES
//      strips features that the unix port's own C source depends on
//      (e.g. MICROPY_KBD_EXCEPTION used unconditionally in unix_mphal.c
//      when MICROPY_ASYNC_KBD_INTR=1), causing build failures.
//   3. After the include, use #undef + #define to override the macros that
//      the common header sets unconditionally (plain #define, no #ifndef).
//      This avoids the -Werror=macro-redefined build failure.
//
// Size reduction from CORE_FEATURES is therefore not taken; instead size
// is recovered by disabling the set of optional modules and features that
// mpconfigvariant_common.h enables on top of port defaults.
//
// Do NOT set MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL — the unix port bakes
// mp_vfs_rom_ioctl() directly into main.c; there is no vfs_rom_ioctl.c.

// --- Pre-empt #ifndef-guarded macros in mpconfigvariant_common.h ----

// Suppress debug printers before the common header sets them via #ifndef guard.
#define MICROPY_DEBUG_PRINTERS              (0)

// --- ROM feature level ---------------------------------------------------

// EXTRA_FEATURES is the same level 'standard' uses. CORE_FEATURES is not
// safe for the unix port: it drops MICROPY_KBD_EXCEPTION which unix_mphal.c
// references unconditionally when MICROPY_ASYNC_KBD_INTR=1 (i.e. when
// threading is enabled and MICROPY_PY_THREAD_GIL=0, which is the default).
#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES)

// --- GC split heap (FR-RT-4) -------------------------------------------

// Required by gc.add_heap() — PR #41 (pr/gc-add-heap).
#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)

// --- Pull in the unix-port common variant config ------------------------

#include "../mpconfigvariant_common.h"

// --- Override macros set unconditionally by mpconfigvariant_common.h ----
// The common header uses plain #define (no #ifndef) for these, so we must
// #undef first to avoid the -Werror=macro-redefined build failure.

// Use terse error reporting to save several KB of error strings.
#undef MICROPY_ERROR_REPORTING
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#undef MICROPY_WARNINGS
#define MICROPY_WARNINGS                    (0)
#undef MICROPY_PY_STR_BYTES_CMP_WARN
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

// Disable REPL conveniences — the picolet runtime variants don't host an
// interactive REPL; only -c and script execution are needed.
#undef MICROPY_REPL_EMACS_WORDS_MOVE
#define MICROPY_REPL_EMACS_WORDS_MOVE       (0)
#undef MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE
#define MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE (0)
#undef MICROPY_USE_READLINE_HISTORY
#define MICROPY_USE_READLINE_HISTORY        (0)

// Disable memory stats / debugging extras (save ~10 KB).
#undef MICROPY_MALLOC_USES_ALLOCATED_SIZE
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)
#undef MICROPY_MEM_STATS
#define MICROPY_MEM_STATS                   (0)

// Disable micropython.mem_info() — it uses mp_verbose_flag which is only
// defined when MICROPY_DEBUG_PRINTERS=1. We pre-empt MICROPY_DEBUG_PRINTERS
// with 0 via the #ifndef guard above, so MEM_INFO must also be off to avoid
// an undefined symbol link error in main.c:915.
#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)

// Disable sys extras not needed by the picolet runtime variants.
#undef MICROPY_PY_SYS_ATEXIT
#define MICROPY_PY_SYS_ATEXIT               (0)
// Keep MICROPY_PY_SYS_EXC_INFO=1 (asyncio uses it for traceback chaining).

// Disable machine module (hardware-control API, not relevant for picolet variants).
#undef MICROPY_PY_MACHINE
#define MICROPY_PY_MACHINE                  (0)
#undef MICROPY_PY_MACHINE_PULSE
#define MICROPY_PY_MACHINE_PULSE            (0)
#undef MICROPY_PY_MACHINE_PIN_BASE
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

// Disable websocket (no use case for the picolet runtime variants).
#undef MICROPY_PY_WEBSOCKET
#define MICROPY_PY_WEBSOCKET                (0)

// Disable builtins not used by the picolet runtime.
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

// Disable crypto/compression modules (not in the picolet baseline).
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

// Keep compiler ON — picolet variants need -c / eval() and the PH16 REPL drop-down.
// (MICROPY_ENABLE_COMPILER defaults to 1; explicit here for clarity.)
#define MICROPY_ENABLE_COMPILER             (1)

// Keep json, re, heapq, random ON:
//   json   — built-in C module, gate 8 verifies.
//   re     — os.path patterns; asyncio touches it occasionally.
//   heapq  — asyncio scheduler depends on it.
//   random — tiny; several micropython-lib modules expect it.

// Do NOT set MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL — unix port has the
// ioctl inline in main.c, not in a separate vfs_rom_ioctl.c.
// MICROPY_VFS_ROM and MICROPY_VFS_ROM_IOCTL are already 1 from the
// common header; leave them.

// Enable append-at-end romfs trailer detection (FR-BP-5, PH03).
// Causes load_romfs_image() to first call picolet_load_romfs_trailer()
// and fall back to the linked romfs only on miss.
// Implementation in variants/picolet-<variant>/romfs_trailer.{c,h}.
#define MICROPY_VFS_ROM_TRAILER (1)
