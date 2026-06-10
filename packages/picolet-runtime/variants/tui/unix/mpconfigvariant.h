/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

// Per-variant config for the picolet tui runtime (linux-x64).
//
// The shared macro set is in ../../common/unix/mpconfigvariant_picolet_common.h.
// Overrides below are the build-flag gates from NFR-TUI-9: weakref +
// re-named-groups are off by default at EXTRA_FEATURES and need explicit
// enable for the picolet_tui Python surface (Phase 2b shims rely on
// WeakSet/WeakValueDictionary/WeakKeyDictionary; the trimmed Rich subset
// uses named-class re patterns).  select, deque, ordereddict, asyncio,
// io.IOBase, re, re.sub are already on at EXTRA_FEATURES.

#include "../../common/unix/mpconfigvariant_picolet_common.h"

#undef MICROPY_PY_WEAKREF
#define MICROPY_PY_WEAKREF                  (1)

#undef MICROPY_PY_RE_MATCH_GROUPS
#define MICROPY_PY_RE_MATCH_GROUPS          (1)

// match.span()/start()/end() — used by the Rich subset's wrap and
// markup scanners (_wrap.py words(), markup.py, text.py highlight).
#undef MICROPY_PY_RE_MATCH_SPAN_START_END
#define MICROPY_PY_RE_MATCH_SPAN_START_END  (1)

// fn.__code__ with co_argcount / co_kwonlyargcount / co_flags /
// co_varnames (argument names from the bytecode prelude).  The
// @widget decorator and Reactive watcher dispatch read positional
// arity at class-decoration time (FR-TUI-14, FR-TUI-20) through the
// picolet_tui._shims.callback oracle, which needs these attributes.
#undef MICROPY_PY_FUNCTION_ATTRS_CODE
#define MICROPY_PY_FUNCTION_ATTRS_CODE      (1)
