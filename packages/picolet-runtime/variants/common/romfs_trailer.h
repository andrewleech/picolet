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

#ifndef PICOLET_ROMFS_TRAILER_H
#define PICOLET_ROMFS_TRAILER_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

// Picolet append-at-end romfs trailer format (FR-BP-5).
//
// Layout (24 bytes, little-endian, at end of file):
//   bytes  0..3   magic        "PYLT"
//   bytes  4..5   version      u16 = 1
//   bytes  6..7   flags        u16 = 0  (reserved)
//   bytes  8..15  payload_size u64 = N  (bytes of romfs payload)
//   bytes 16..19  payload_crc32 u32     (zlib CRC32 of payload)
//   bytes 20..23  pad          u32 = 0  (reserved)
//
// The romfs payload immediately precedes the trailer in the file:
//   [ELF/PE runtime bytes][romfs payload N bytes][trailer 24 bytes]
//
// Detection: open the running binary (Linux: /proc/self/exe;
// Windows: GetModuleFileNameW(NULL,...) + _wfopen()), seek to
// file_size-24, read trailer.
// Magic mismatch -> silent fallback to linked empty romfs.
// CRC mismatch   -> loud fallback (stderr warning).

#define PICOLET_TRAILER_MAGIC     "PYLT"
#define PICOLET_TRAILER_VERSION   1
#define PICOLET_TRAILER_SIZE      24

// Packed trailer struct (little-endian on all supported targets).
// Compiler packs this correctly on all gcc/clang x86-64 targets.
typedef struct __attribute__((packed)) {
    uint8_t  magic[4];         // "PYLT"
    uint16_t version;          // must be 1
    uint16_t flags;            // reserved, 0
    uint64_t payload_size;     // bytes of romfs payload
    uint32_t payload_crc32;    // zlib CRC32 of payload bytes
    uint32_t pad;              // reserved, 0
} picolet_trailer_t;

// Attempt to load a romfs image from the trailer appended to the running
// binary (/proc/self/exe on Linux; GetModuleFileNameW(NULL,...) on Windows).
//
// On success: *buf_out and *size_out are set to the malloced payload buffer
//             and its size; returns true. Caller owns the buffer.
// On failure: returns false; *buf_out and *size_out are unchanged.
//             Fallback modes are enumerated in romfs_trailer.c.
//
// This function is only compiled when MICROPY_VFS_ROM_TRAILER=1, which is
// defined in each picolet variant's mpconfigvariant.h.
bool picolet_load_romfs_trailer(const uint8_t **buf_out, size_t *size_out);

#endif // PICOLET_ROMFS_TRAILER_H
