/*
 * picolet_lvgl_png.h — encode RGB888 framebuffer data to PNG bytes.
 *
 * PH17 (FR-TEST-2, D5).  Used by picolet._test.snapshot() on the LVGL
 * variant to convert the lv_snapshot_take() buffer to a PNG that the
 * AppHarness can assert on.
 *
 * The encoder uses system libz (zlib) for DEFLATE compression.  libz is
 * LGPL-2.1+ and is dynamically linked — dlopen("libz.so.1") at runtime
 * inside the shim.  This satisfies NFR-5 (no static GPL/LGPL linking).
 *
 * License: MIT (picolet code).
 */

#ifndef PICOLET_LVGL_PNG_H
#define PICOLET_LVGL_PNG_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Encode RGB888 data (width * height * 3 bytes, row-major) to a PNG byte
 * stream.  The output buffer is malloc'd by the encoder; the caller must
 * free it with picolet_lvgl_png_free().
 *
 * Returns 0 on success, -1 on failure (malloc OOM, zlib error, etc).
 *
 * Thread safety: not thread-safe (global libz dlopen handle).
 */
int32_t picolet_lvgl_png_encode(const uint8_t *rgb888,
                              int32_t width, int32_t height,
                              uint8_t **out_bytes, size_t *out_size);

/* Free a buffer returned by picolet_lvgl_png_encode. */
void picolet_lvgl_png_free(uint8_t *bytes);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_LVGL_PNG_H */
