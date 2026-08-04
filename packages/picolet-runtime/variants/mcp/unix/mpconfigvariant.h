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

// Per-variant config for the picolet mcp runtime (linux-x64).
// The shared macro set is in ../../common/unix/mpconfigvariant_picolet_common.h.
// This is the cli baseline with SSL enabled via mpconfigvariant.mk
// (MICROPY_PY_SSL=1, MICROPY_SSL_MBEDTLS=1); no C-level overrides are
// needed for TLS itself since MICROPY_PY_SSL only gates the tls module
// registration and mbedtls source inclusion at the .mk/extmod.mk level.
#include "../../common/unix/mpconfigvariant_picolet_common.h"

// hashlib.sha1 (Q3 DECISION, P2 mcp-variant ticket): enabled for
// Sec-WebSocket-Accept verification during the WebSocket TLS handshake.
// The shared picolet common header forces MICROPY_PY_HASHLIB off
// unconditionally, so re-enabling it here requires an #undef first.
// MD5 and SHA256 stay off — SHA1 is the only digest the WS handshake
// needs, and both would otherwise default on via mpconfig.h's
// MICROPY_PY_SSL-gated defaults now that SSL is enabled.
#undef MICROPY_PY_HASHLIB
#define MICROPY_PY_HASHLIB                  (1)
#define MICROPY_PY_HASHLIB_SHA1             (1)
#define MICROPY_PY_HASHLIB_MD5              (0)
#define MICROPY_PY_HASHLIB_SHA256           (0)

// Idle-CPU behaviour for the asyncio-over-TLS workload. The hub connection is a
// wss:// (SSL) socket whose readiness is derived (decrypted application bytes
// available / a buffered TLS record), not a plain kernel fd edge.
//
// MICROPY_PY_SELECT_EVENT_SOURCE registers the TLS socket as a composed poll
// entry: fd-backed (its underlying socket fd is in the kernel pollfd set, so new
// data wakes poll() directly) AND ioctl-consulted (readiness comes from
// MP_STREAM_POLL / mbedtls check_pending, resolved before the loop blocks so a
// decrypted-but-unread record is not stranded behind the sleep). The event loop
// then sleeps to the asyncio deadline rather than periodic probing, so an idle
// TLS connection costs ~0% of a core (measured 0.000% steady state, ~57 poll/30s
// deadline-sleep, linux-x64) while inbound hub data still wakes it immediately.
//
// IOCTL_CALL_PERIOD_MS is the fallback cap for a pollable that is genuinely
// non-fd (no fd to wake on); with the event-source mechanism the SSL entry no
// longer reaches it. Kept at 50ms as a bounded floor for any such entry.
// Requires the integration-branch #ifndef guard on this macro
// (extmod/modselect.c).
#define MICROPY_PY_SELECT_IOCTL_CALL_PERIOD_MS (50)
#define MICROPY_PY_SELECT_EVENT_SOURCE (1)
