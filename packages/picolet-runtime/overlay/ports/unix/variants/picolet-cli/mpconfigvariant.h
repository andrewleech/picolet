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

// Lean variant for the picolet cli runtime (linux-x64).
//
// Goals: single self-contained ELF ≤ 1 MB (NFR-1) with gc.add_heap
// (FR-RT-4), ffi (FR-RT-5), asyncio (via frozen manifest), romfs
// auto-mount (FR-RT-6/FR-RT-7), and the compiler on for -c / eval().
//
// Strategy:
//   1. Pre-define macros that mpconfigvariant_common.h guards with
//      #ifndef so they take effect before the include.
//   2. Set ROM level to CORE_FEATURES (same as mpconfigport.h default)
//      rather than EXTRA_FEATURES that "standard" uses — strips optional
//      builtins at the feature-level layer.
//   3. After the include, override the macros the common header forces
//      on with plain #define (which wins over a prior #define).
//
// Do NOT set MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL — the unix port bakes
// mp_vfs_rom_ioctl() directly into main.c; there is no vfs_rom_ioctl.c.

// --- Pre-empt #ifndef-guarded macros in mpconfigvariant_common.h ----

// Suppress debug printers before the common header sets them.
#define MICROPY_DEBUG_PRINTERS              (0)

// Use single-precision float to save code size (double is the common default).
// asyncio does not require double; leave as double if this causes issues.
// Actually keep double for compatibility — most library code expects it.
// (MICROPY_FLOAT_IMPL uses #ifndef guard so we can override here.)
// Leave at default (double) by not defining it here.

// --- ROM feature level ---------------------------------------------------

// CORE_FEATURES gives us the essential Python subset without the heavy
// optional extras that EXTRA_FEATURES enables (e.g. descriptors extras,
// reverse special methods, etc.).  mpconfigport.h already defaults to
// CORE_FEATURES; we make it explicit here so a future change to the
// port default doesn't silently inflate this variant.
#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_CORE_FEATURES)

// --- GC split heap (FR-RT-4) -------------------------------------------

// Required by gc.add_heap() — PR #41 (pr/gc-add-heap).
#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)

// --- Pull in the unix-port common variant config ------------------------

#include "../mpconfigvariant_common.h"

// --- Override macros set unconditionally by mpconfigvariant_common.h ----
// (The common header uses plain #define for these, so we re-define after.)

// Use terse error reporting to save several KB of error strings.
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_WARNINGS                    (0)
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

// Disable REPL conveniences — the cli runtime doesn't host an interactive
// REPL; only -c and script execution are needed.
#define MICROPY_REPL_EMACS_WORDS_MOVE       (0)
#define MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE (0)
#define MICROPY_USE_READLINE_HISTORY        (0)

// Disable memory stats / debugging extras (save ~10 KB).
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)
#define MICROPY_MEM_STATS                   (0)

// Disable sys extras not needed by the cli variant.
#define MICROPY_PY_SYS_ATEXIT               (0)
// Keep MICROPY_PY_SYS_EXC_INFO=1 (asyncio uses it for traceback chaining).

// Disable machine module (hardware-control API, not relevant for cli).
#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

// Disable websocket (no use case for the cli baseline).
#define MICROPY_PY_WEBSOCKET                (0)

// Disable builtins not used by the cli runtime.
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

// Disable crypto/compression modules (not in the cli baseline).
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

// Keep compiler ON — cli needs -c / eval() and PH16 REPL drop-down.
// (MICROPY_ENABLE_COMPILER defaults to 1; explicit here for clarity.)
#define MICROPY_ENABLE_COMPILER             (1)

// Keep json, re, heapq, random ON:
//   json   — built-in C module, gate 8 verifies.
//   re     — os.path patterns; also asyncio touches it occasionally.
//   heapq  — asyncio scheduler depends on it.
//   random — tiny; several micropython-lib modules expect it.

// Do NOT set MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL — unix port has the
// ioctl inline in main.c, not in a separate vfs_rom_ioctl.c.
// MICROPY_VFS_ROM and MICROPY_VFS_ROM_IOCTL are already 1 from the
// common header (lines 119–120); leave them.
