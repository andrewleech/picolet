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

// Lean variant for the picolet lvgl runtime (windows-x64, PH12).
//
// Forked from overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.h.
// Cannot include ../mpconfigvariant_common.h (Windows port has no such file).
// Macro set mirrors the unix lvgl variant's effective values.
//
// The Windows port's mpconfigport.h already sets:
//   MICROPY_VFS_ROM=1
//   MICROPY_VFS_ROM_IOCTL=1
//   MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1  (directs build to vfs_rom_ioctl.c)
// all guarded with #ifndef, so the variant need not re-assert them.

// --- GC split heap (FR-RT-4) -------------------------------------------

#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)

// --- Romfs trailer detection (FR-BP-5, PH04) ----------------------------

#define MICROPY_VFS_ROM_TRAILER             (1)

// --- Size-reduction overrides -------------------------------------------

#define MICROPY_DEBUG_PRINTERS              (0)
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_WARNINGS                    (0)
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)
#define MICROPY_MEM_STATS                   (0)
#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)

#define MICROPY_PY_SYS_ATEXIT               (0)

#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

// --- Module disables ----------------------------------------------------

// Keep compiler ON for parity with the cli variant and to support -c on
// the command line.  The -c path in unix/main.c is guarded by
// MICROPY_ENABLE_COMPILER; disabling it would break the gate harness.
// Binary size is managed via lv_conf.h and --gc-sections instead.
#define MICROPY_ENABLE_COMPILER             (1)

#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

// Required by asyncio/core.py ("import select").
#define MICROPY_PY_SELECT                   (1)

// _asyncio C module — supplies TaskQueue/Task natively, matching the
// frozen asyncio manifest's expectations.
#define MICROPY_PY_ASYNCIO                  (1)

// LVGL binding needs the scheduler to call mp_sched_schedule from C
// without holding the GIL.  This is on by default in
// MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES; confirm here so the
// binding's mp_sched_schedule path links cleanly.
#ifndef MICROPY_ENABLE_SCHEDULER
#define MICROPY_ENABLE_SCHEDULER            (1)
#endif
