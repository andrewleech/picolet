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

// Shared macro set for all picolet windows-port variants (cli, webview, lvgl).
//
// This file is #included by each per-variant mpconfigvariant.h.  The
// variant file must NOT be included directly by the MicroPython build
// system; only the per-variant wrapper is.
//
// The Windows port's mpconfigport.h already sets:
//   MICROPY_VFS_ROM=1
//   MICROPY_VFS_ROM_IOCTL=1
//   MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1  (directs build to vfs_rom_ioctl.c)
// all guarded with #ifndef, so the variant need not re-assert them.
//
// The Windows port includes mpconfigvariant.h at the top of mpconfigport.h
// (before port defaults), so macros defined here without #ifndef take
// precedence over port defaults.  There is no upstream mpconfigvariant_common.h
// on the Windows port; all configuration is explicit here.
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

// REPL history not needed for picolet runtime variants — mpconfigport.h line 37 is
// #ifndef-guarded.
#define MICROPY_USE_READLINE_HISTORY        (0)

// --- Module disables ----------------------------------------------------

// Keep compiler ON — picolet variants need -c / eval(); same rationale as unix variants.
// MICROPY_ENABLE_COMPILER defaults to 1; explicit here for clarity.
#define MICROPY_ENABLE_COMPILER             (1)

// Builtins not needed for picolet runtime.
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

// Crypto/compression modules not in the picolet baseline.
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

// Machine module (hardware-control API) not applicable for picolet variants.
#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

// sys.atexit not needed for picolet runtime.
#define MICROPY_PY_SYS_ATEXIT               (0)

// Keep json, re, heapq, random ON (asyncio and stdlib patterns depend on them).

// Two-argument next(iterator, default): a BASIC_FEATURES builtin the
// CORE_FEATURES baseline omits.  The picolet_tui Rich subset uses it
// (style.py parse, segment.py divide); plain Python code reasonably
// expects it everywhere.
#define MICROPY_PY_BUILTINS_NEXT2           (1)

// select module: required by asyncio/core.py ("import select").
// The Windows port defaults to MICROPY_CONFIG_ROM_LEVEL_CORE_FEATURES which
// does not include select (it needs AT_LEAST_EXTRA_FEATURES).  Enable explicitly.
#define MICROPY_PY_SELECT                   (1)

// asyncio C module (_asyncio): provides TaskQueue and Task natively.
// The frozen asyncio manifest (extmod/asyncio/manifest.py) omits task.py
// because it assumes the C module is present.  Without this, asyncio/core.py
// falls back to "from .task import ..." which fails since task.py is not frozen.
#define MICROPY_PY_ASYNCIO                  (1)

// App-runner mode: skip pre_process_options() and forward argv[1..] verbatim
// to sys.argv.  All picolet variants embed an application (via romfs or frozen
// modules) so none of -h/--version/-c/-m/-O/-X/-i should be intercepted by
// the interpreter.
#define MICROPY_APP_RUNNER (1)
