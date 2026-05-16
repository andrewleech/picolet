/*
 * picolet_lvgl_png.c — minimal PNG encoder for the LVGL test snapshot API.
 *
 * PH17 (FR-TEST-2).  Encodes an RGB888 framebuffer to a valid PNG stream
 * using zlib (dlopen'd as libz.so.1 at first call) for DEFLATE compression.
 *
 * PNG format (RFC 2083):
 *   8-byte magic
 *   IHDR chunk  (13 bytes data)
 *   IDAT chunk  (zlib-deflated, filter-type 0 per scanline)
 *   IEND chunk  (0 bytes data)
 *
 * Filter type: we use type 0 (None) for every row — no prediction.
 * Simple, minimal code; compression ratio is lower than adaptive but the
 * output is valid.
 *
 * Dynamic dependency: libz.so.1 (zlib, LGPL-2.1+, runtime dlopen).
 * Static link is avoided to satisfy NFR-5.
 *
 * License: MIT (picolet code).
 */

#include "picolet_lvgl_png.h"

#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <dlfcn.h>   /* dlopen / dlsym */

/* ----- zlib types / constants we reference -------------------------------- */

#define Z_OK            0
#define Z_STREAM_END    1
#define Z_DEFAULT_COMPRESSION (-1)
#define Z_DEFLATED      8
#define Z_DEFAULT_STRATEGY 0
#define Z_FINISH        4
#define Z_NULL          0

typedef void *voidpf;
typedef unsigned long uLong;
typedef unsigned int  uInt;
typedef uint8_t       Byte;

typedef struct z_stream_s {
    const Byte *next_in;
    uInt        avail_in;
    uLong       total_in;
    Byte       *next_out;
    uInt        avail_out;
    uLong       total_out;
    char       *msg;
    void       *state;
    void       *zalloc;
    void       *zfree;
    voidpf      opaque;
    int         data_type;
    uLong       adler;
    uLong       reserved;
} z_stream;

typedef int (*pfn_deflateInit2)(z_stream *, int level, int method,
                                int windowBits, int memLevel,
                                int strategy, const char *version,
                                int stream_size);
typedef int (*pfn_deflate)(z_stream *, int flush);
typedef int (*pfn_deflateEnd)(z_stream *);
typedef uLong (*pfn_crc32)(uLong crc, const Byte *buf, uInt len);

/* ----- lazy-loaded zlib function pointers --------------------------------- */

static void       *g_zlib_handle  = NULL;
static pfn_deflateInit2 g_deflateInit2 = NULL;
static pfn_deflate      g_deflate      = NULL;
static pfn_deflateEnd   g_deflateEnd   = NULL;
static pfn_crc32        g_crc32        = NULL;

static int load_zlib(void) {
    if (g_zlib_handle != NULL) { return 0; }
    /* Try common sonames. */
    const char *names[] = { "libz.so.1", "libz.so", NULL };
    for (int i = 0; names[i]; i++) {
        g_zlib_handle = dlopen(names[i], RTLD_LAZY | RTLD_LOCAL);
        if (g_zlib_handle) break;
    }
    if (!g_zlib_handle) { return -1; }

    g_deflateInit2 = (pfn_deflateInit2)dlsym(g_zlib_handle, "deflateInit2_");
    g_deflate      = (pfn_deflate)     dlsym(g_zlib_handle, "deflate");
    g_deflateEnd   = (pfn_deflateEnd)  dlsym(g_zlib_handle, "deflateEnd");
    g_crc32        = (pfn_crc32)       dlsym(g_zlib_handle, "crc32");

    if (!g_deflateInit2 || !g_deflate || !g_deflateEnd || !g_crc32) {
        dlclose(g_zlib_handle);
        g_zlib_handle = NULL;
        return -1;
    }
    return 0;
}

/* ----- dynamic byte buffer ------------------------------------------------ */

typedef struct {
    uint8_t *data;
    size_t   len;
    size_t   cap;
} ByteBuf;

static int buf_init(ByteBuf *b, size_t initial) {
    b->data = (uint8_t *)malloc(initial);
    if (!b->data) return -1;
    b->len  = 0;
    b->cap  = initial;
    return 0;
}

static int buf_append(ByteBuf *b, const void *src, size_t n) {
    if (b->len + n > b->cap) {
        size_t newcap = b->cap * 2;
        while (newcap < b->len + n) newcap *= 2;
        uint8_t *p = (uint8_t *)realloc(b->data, newcap);
        if (!p) return -1;
        b->data = p;
        b->cap  = newcap;
    }
    memcpy(b->data + b->len, src, n);
    b->len += n;
    return 0;
}

static void buf_free(ByteBuf *b) { free(b->data); b->data = NULL; }

/* ----- PNG helpers -------------------------------------------------------- */

static void write_u32be(uint8_t *out, uint32_t v) {
    out[0] = (uint8_t)(v >> 24);
    out[1] = (uint8_t)(v >> 16);
    out[2] = (uint8_t)(v >>  8);
    out[3] = (uint8_t)(v      );
}

/* Append a PNG chunk: length(4) + type(4) + data(length) + crc32(4). */
static int append_chunk(ByteBuf *out, const char *type,
                         const uint8_t *data, uint32_t data_len) {
    uint8_t hdr[8];
    write_u32be(hdr,     data_len);
    memcpy(hdr + 4, type, 4);
    if (buf_append(out, hdr, 8) != 0) return -1;

    uLong crc = g_crc32(0, NULL, 0);
    crc = g_crc32(crc, (const Byte *)type, 4);
    if (data_len > 0 && data) {
        if (buf_append(out, data, data_len) != 0) return -1;
        crc = g_crc32(crc, data, data_len);
    }
    uint8_t crc_bytes[4];
    write_u32be(crc_bytes, (uint32_t)crc);
    return buf_append(out, crc_bytes, 4);
}

/* ----- Public API --------------------------------------------------------- */

int32_t picolet_lvgl_png_encode(const uint8_t *rgb888,
                              int32_t width, int32_t height,
                              uint8_t **out_bytes, size_t *out_size) {
    *out_bytes = NULL;
    *out_size  = 0;

    if (!rgb888 || width <= 0 || height <= 0) return -1;
    if (load_zlib() != 0) return -1;

    /* Build the raw scanline data with filter byte 0 (None) prepended to
     * each row:  [0x00, R, G, B, R, G, B, ...]  per row. */
    size_t row_bytes = (size_t)width * 3;
    size_t raw_len   = (size_t)height * (1 + row_bytes);
    uint8_t *raw     = (uint8_t *)malloc(raw_len);
    if (!raw) return -1;

    for (int y = 0; y < height; y++) {
        size_t out_off = (size_t)y * (1 + row_bytes);
        raw[out_off] = 0x00;  /* filter type None */
        memcpy(raw + out_off + 1, rgb888 + (size_t)y * row_bytes, row_bytes);
    }

    /* DEFLATE the scanline data. */
    size_t def_max = raw_len + (raw_len / 1000 + 1) + 32;
    uint8_t *deflated = (uint8_t *)malloc(def_max);
    if (!deflated) { free(raw); return -1; }

    z_stream zs;
    memset(&zs, 0, sizeof(zs));
    /* deflateInit2_: windowBits=15 (zlib header), memLevel=8 */
    int zr = g_deflateInit2(&zs, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                             15, 8, Z_DEFAULT_STRATEGY,
                             "1.2.11",   /* version — checked by zlib; any recent ver */
                             (int)sizeof(z_stream));
    if (zr != Z_OK) { free(raw); free(deflated); return -1; }

    zs.next_in  = (Byte *)raw;
    zs.avail_in = (uInt)raw_len;
    zs.next_out = deflated;
    zs.avail_out = (uInt)def_max;

    zr = g_deflate(&zs, Z_FINISH);
    g_deflateEnd(&zs);
    free(raw);

    if (zr != Z_STREAM_END) { free(deflated); return -1; }
    size_t def_len = (size_t)zs.total_out;

    /* Assemble PNG output. */
    ByteBuf out;
    if (buf_init(&out, 64 + def_len + 128) != 0) { free(deflated); return -1; }

    /* PNG magic */
    static const uint8_t png_magic[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    if (buf_append(&out, png_magic, 8) != 0) goto fail;

    /* IHDR: width(4) height(4) bitdepth(1) colortype(1=RGB) compress(1)
     *       filter(1) interlace(1) */
    {
        uint8_t ihdr[13];
        write_u32be(ihdr + 0, (uint32_t)width);
        write_u32be(ihdr + 4, (uint32_t)height);
        ihdr[8]  = 8;   /* 8 bits per channel */
        ihdr[9]  = 2;   /* RGB (truecolour) */
        ihdr[10] = 0;   /* deflate/inflate */
        ihdr[11] = 0;   /* adaptive filtering */
        ihdr[12] = 0;   /* no interlace */
        if (append_chunk(&out, "IHDR", ihdr, 13) != 0) goto fail;
    }

    /* IDAT */
    if (append_chunk(&out, "IDAT", deflated, (uint32_t)def_len) != 0) goto fail;

    /* IEND */
    if (append_chunk(&out, "IEND", NULL, 0) != 0) goto fail;

    free(deflated);
    *out_bytes = out.data;
    *out_size  = out.len;
    return 0;

fail:
    buf_free(&out);
    free(deflated);
    return -1;
}

void picolet_lvgl_png_free(uint8_t *bytes) {
    free(bytes);
}
