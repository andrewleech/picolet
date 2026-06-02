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

// Per-variant config for the picolet tui runtime (windows-x64).
//
// The shared macro set is in ../../common/windows/mpconfigvariant_picolet_common.h.
// Overrides below mirror the Unix tui variant: the picolet_tui Python
// surface (Phase 2b shims) uses WeakSet/WeakValueDictionary/WeakKeyDictionary
// and the trimmed Rich subset relies on re named-class patterns (NFR-TUI-9).
// Neither is on at the Windows port's default ROM-level, so they need
// explicit enable here.

#include "../../common/windows/mpconfigvariant_picolet_common.h"

#undef MICROPY_PY_WEAKREF
#define MICROPY_PY_WEAKREF                  (1)

#undef MICROPY_PY_RE_MATCH_GROUPS
#define MICROPY_PY_RE_MATCH_GROUPS          (1)
