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

// Lean variant for the picolet cli runtime (windows-x64).
//
// Goals: single self-contained .exe ≤ 1 MB (NFR-1) with gc.add_heap
// (FR-RT-4), ffi (FR-RT-5), asyncio (via frozen manifest), romfs
// auto-mount with trailer detection (FR-RT-6/FR-RT-7), and the compiler
// on for -c / eval().
//
// The Windows port's mpconfigport.h already sets:
//   MICROPY_VFS_ROM=1
//   MICROPY_VFS_ROM_IOCTL=1
//   MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1  (directs build to vfs_rom_ioctl.c)
// all guarded with #ifndef, so the variant need not re-assert them.
//
// The Windows port includes mpconfigvariant.h at the top of mpconfigport.h
// (before port defaults), so macros defined here without #ifndef take
// precedence over port defaults.  There is no mpconfigvariant_common.h
// on the Windows port; all configuration is explicit below.
//
// Do NOT set MICROPY_PY_SYS_PATH_ARGV_DEFAULTS — the Windows port pins
// it to 0 in mpconfigport.h line 157 (not #ifndef-guarded); the variant
// cannot override it.

// --- GC split heap (FR-RT-4) -------------------------------------------

// Required by gc.add_heap() — PR #41 (pr/gc-add-heap).
#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)

// --- Romfs trailer detection (FR-BP-5, PH04) ----------------------------

// Enables trailer-detection hook in overlay/ports/windows/vfs_rom_ioctl.c.
// The overlay load_romfs_image() calls picolet_load_romfs_trailer() before
// falling back to the embedded empty sentinel.
#define MICROPY_VFS_ROM_TRAILER             (1)

// --- Size-reduction overrides -------------------------------------------

// Terse error reporting — mpconfigport.h has no default; we set explicitly.
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_WARNINGS                    (0)
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

// Debug printers disabled — mpconfigport.h line 62-64 is #ifndef-guarded.
#define MICROPY_DEBUG_PRINTERS              (0)

// micropython.mem_info() uses mp_verbose_flag which is only defined when
// MICROPY_DEBUG_PRINTERS=1. Disable to avoid undefined symbol link error.
#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)

// Memory stats disabled (saves ~4 KB) — mpconfigport.h line 59 is #ifndef-guarded.
#define MICROPY_MEM_STATS                   (0)

// malloc-size tracking disabled — mpconfigport.h line 55 is #ifndef-guarded.
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)

// REPL history not needed for cli runtime — mpconfigport.h line 37 is #ifndef-guarded.
#define MICROPY_USE_READLINE_HISTORY        (0)

// --- Module disables ---------------------------------------------------

// Keep compiler ON — cli needs -c / eval(); same rationale as unix variant.
// MICROPY_ENABLE_COMPILER defaults to 1; explicit here for clarity.
#define MICROPY_ENABLE_COMPILER             (1)

// Builtins not needed for cli.
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

// Crypto/compression modules not in the cli baseline.
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

// Machine module (hardware-control API) not applicable for cli.
#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

// sys.atexit not needed for cli.
#define MICROPY_PY_SYS_ATEXIT               (0)

// Keep json, re, heapq, random ON (asyncio and stdlib patterns depend on them).
