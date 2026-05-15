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

// Lean variant for the picolet webview runtime (linux-x64).
//
// PH07.  Forked from picolet-cli/mpconfigvariant.h.  The only deltas
// versus the cli baseline are documentary; the macro set is identical.
// libffi (MICROPY_PY_FFI) is on for both variants and is required here
// for the WebKitGTK 4.1 bindings under packages/picolet-runtime/python/picolet_ui.
//
// Webview variant is Linux-only.  Windows webview lands in PH10 via
// WebView2 and is not covered here.

// --- Pre-empt #ifndef-guarded macros in mpconfigvariant_common.h ----

#define MICROPY_DEBUG_PRINTERS              (0)

// --- ROM feature level ---------------------------------------------------

#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES)

// --- GC split heap (FR-RT-4) -------------------------------------------

#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)

// --- Pull in the unix-port common variant config ------------------------

#include "../mpconfigvariant_common.h"

// --- Override macros set unconditionally by mpconfigvariant_common.h ----

#undef MICROPY_ERROR_REPORTING
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#undef MICROPY_WARNINGS
#define MICROPY_WARNINGS                    (0)
#undef MICROPY_PY_STR_BYTES_CMP_WARN
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

#undef MICROPY_REPL_EMACS_WORDS_MOVE
#define MICROPY_REPL_EMACS_WORDS_MOVE       (0)
#undef MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE
#define MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE (0)
#undef MICROPY_USE_READLINE_HISTORY
#define MICROPY_USE_READLINE_HISTORY        (0)

#undef MICROPY_MALLOC_USES_ALLOCATED_SIZE
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)
#undef MICROPY_MEM_STATS
#define MICROPY_MEM_STATS                   (0)

#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)

#undef MICROPY_PY_SYS_ATEXIT
#define MICROPY_PY_SYS_ATEXIT               (0)

#undef MICROPY_PY_MACHINE
#define MICROPY_PY_MACHINE                  (0)
#undef MICROPY_PY_MACHINE_PULSE
#define MICROPY_PY_MACHINE_PULSE            (0)
#undef MICROPY_PY_MACHINE_PIN_BASE
#define MICROPY_PY_MACHINE_PIN_BASE         (0)

#undef MICROPY_PY_WEBSOCKET
#define MICROPY_PY_WEBSOCKET                (0)

#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)

#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)

#define MICROPY_ENABLE_COMPILER             (1)

// Enable append-at-end romfs trailer detection (FR-BP-5, PH03/PH04).
#define MICROPY_VFS_ROM_TRAILER (1)
