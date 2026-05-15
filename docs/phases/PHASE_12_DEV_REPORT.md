# picolet — Phase 12 Developer Report

**Feature:** picolet
**Phase:** 12 — LVGL renderer on Windows (windows-x64/lvgl) + NFR-3 remediation
**Date:** 2026-05-16
**Attempt:** 2 (NFR-3 remediation pass)
**Phase File:** /home/anl/picolet/docs/phases/PHASE_12_lvgl-renderer-windows.md

---

## Summary

PH12 lands the Windows LVGL runtime variant (`picolet-runtime-windows-x64-lvgl.exe`).
The NFR-3 remediation pass (Attempt 2) replaced the prebuilt MinGW SDL2 archive
with a from-source CMake build compiled with `-ffunction-sections -fdata-sections -Os`
and an overlay `micropython.rc` that removes the 170 KB upstream icon.  The final
binary is **2,078,208 bytes (1.98 MiB)**, meeting NFR-3's 2 MiB ceiling.

---

## Implementation Summary (Attempt 1)

PH12 created the Windows LVGL variant configuration files, a minimal frozen
manifest, and the `build-runtime.sh` build branch for `windows-x64/lvgl`.

Key findings during Attempt 1 that drove the NFR-3 deviation:

- The phase plan's `SRC_C +=` approach for `romfs_trailer.c` does not work
  because the Windows Makefile reassigns `SRC_C` via a simple assignment after
  including the variant `.mk`.  Fix: placed `romfs_trailer.c` directly in the
  variant directory where `$(wildcard $(VARIANT_DIR)/*.c)` picks it up.
- The binding's SDL2 pkg-config probe is gated on `$(notdir $(CURDIR)) == unix`,
  so `MICROPY_SDL=1` was never injected for the Windows port.  Fix: injected it
  via `LV_CFLAGS`.
- The prebuilt MinGW SDL2 archive was not compiled with `-ffunction-sections`,
  so `--gc-sections` could not eliminate unused functions.  Binary was 3.08 MiB.

## Implementation Summary (Attempt 2 — NFR-3 remediation)

The root cause of the NFR-3 violation was that the prebuilt SDL2 archive lacked
per-function sections.  The solution had two parts:

**Part 1 — from-source SDL2 build.**  SDL2 2.26.2 is fetched from the SDL
GitHub releases, verified by SHA-256, and built with MXE's cmake wrapper
(`x86_64-w64-mingw32.static.posix-cmake`) inside the dockcross container.
Build flags: `-ffunction-sections -fdata-sections -Os`.  Unused subsystems
disabled at configure time: audio, joystick, haptic, sensor, HIDAPI, power,
DirectX, D3D, OpenGL, locale, misc.  The output is installed into
`build/sdl2-win64-ffs/` and cached by source SHA-256 stamp.

**Part 2 — micropython.rc icon overlay.**  After all SDL2 pruning the binary
was still 2.14 MiB.  MicroPython's upstream `micropython.rc` embeds
`vector-logo-2.ico` (170 KB), which windres packs into the PE `.rsrc` section.
A new overlay file `overlay/ports/windows/micropython.rc` replaces the icon with
a minimal `VS_VERSION_INFO` resource (no icon), recovering 167 KB.

Size progression:
| Configuration | Size (bytes) | MiB |
|---|---|---|
| Prebuilt SDL2 (Attempt 1) | 3,233,792 | 3.08 |
| From-source SDL2, full config | 3,217,408 | 3.07 |
| + no audio/joystick/haptic/sensor/HIDAPI/power | 2,485,248 | 2.37 |
| + no DirectX/D3D | 2,335,232 | 2.23 |
| + no OpenGL/locale/misc | 2,248,704 | 2.14 |
| + micropython.rc overlay (no icon) | **2,078,208** | **1.98** ✓ |

---

## Files Created

| File Path | Purpose |
|-----------|---------|
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/mpconfigvariant.h` | Windows LVGL variant feature config |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/mpconfigvariant.mk` | Variant Make config — SDL2 paths, LV_CONF_PATH, gc-sections |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/romfs_trailer.c` | Windows GetModuleFileNameA-based romfs trailer |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/romfs_trailer.h` | romfs trailer header |
| `packages/picolet-runtime/overlay/ports/windows/micropython.rc` | Overlay rc: version-info only, no 170 KB icon (NFR-3 fix) |
| `packages/picolet-runtime/manifests/manifest_lvgl_windows.py` | Frozen manifest for windows-x64/lvgl |
| `tests/phase-12/run.sh` | PH12 exit gate harness |
| `tests/phase-12/fixtures/hello-lvgl-win-min/picolet.toml` | Gate 6 fixture |
| `tests/phase-12/fixtures/hello-lvgl-win-min/src/main.py` | Gate 6 fixture app |

## Files Modified

| File Path | Changes |
|-----------|---------|
| `packages/picolet-runtime/scripts/build-runtime.sh` | [2b/8] block: prebuilt tarball → from-source CMake SDL2 build with `-ffunction-sections`; SDL2 subsystem pruning; SHA-256 stamp cache.  `finish_artifact()` windows-x64/lvgl ceiling restored to 2097152 (NFR-3). |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/mpconfigvariant.mk` | Added commented-out LTO block (tried, caused crash — see Caveats). |
| `tests/phase-12/run.sh` | B2 gate restored to 2 MiB NFR-3 ceiling; header comment updated. |
| `packages/picolet-runtime/micropython` | Submodule pointer updated after rebuild-integration (overlay files refreshed in integration branch). |

---

## Build Verification

**Build command:**
```
bash packages/picolet-runtime/scripts/build-runtime.sh --target windows-x64 --variant lvgl
```

**Result:** Pass

**Binary:** `packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe`
- Size: 2,078,208 bytes (99% of NFR-3 ceiling of 2,097,152 bytes)
- NFR-3: PASS

**Build times:**
- Cold SDL2 build (first run, no cache): ~55 seconds
- Warm build (SDL2 cached): ~13 seconds

**Section breakdown (objdump):**
| Section | Size |
|---------|------|
| `.text` | 1,380,552 bytes |
| `.rdata` | 428,384 bytes |
| `.pdata` | 98,316 bytes |
| `.xdata` | 77,428 bytes |
| `.data` | 39,056 bytes |
| `.idata` | 14,060 bytes |
| `.rsrc` | ~500 bytes |

---

## Test Results

```
bash tests/phase-12/run.sh
```

| Result | Count |
|--------|-------|
| PASS | 15 |
| FAIL | 0 |
| SKIP | 3 (D1 display, D2 display, E1 build-skip) |

Non-regression:
- PH11 (linux-x64/lvgl): PASS
- PH10 (windows webview): PASS

---

## Deviations from Phase Plan

**AD1 implementation (SDL2 acquisition):** The phase plan selected MXE
`make -C /usr/src/mxe sdl2` inside the ephemeral container.  The MXE-built
library lacked `-ffunction-sections` and the binary was 3.08 MiB, violating
NFR-3.  The implementation was revised to Option C (from-source CMake build
with `-ffunction-sections`).  The phase file AD1 table has been updated to
reflect this.

**micropython.rc overlay (not in original plan):** An overlay `micropython.rc`
was required to remove the 167 KB icon that windres embeds.  This file was not
planned but is a one-line overlay that has no effect on runtime behavior.

---

## Caveats

Three technical caveats are recorded as empty commits on the `dev` branch:

**LTO causes crash:** `-flto` added to CFLAGS_EXTRA/LDFLAGS produced a 2.07 MiB
binary (below NFR-3) but caused Windows initialization failure (exit code 5 /
STATUS_ACCESS_VIOLATION).  MinGW LTO corrupts Windows CRT startup under this
dockcross configuration.  Flags retained as commented-out lines in
`mpconfigvariant.mk`.

**Unwind tables mandatory:** Tried `-fno-asynchronous-unwind-tables --no-seh`
to reduce the 175 KB `.pdata`/`.xdata` overhead.  Both flags caused runtime
crash.  The Windows x64 ABI mandates SEH unwind metadata for all non-leaf
frames.  These sections are a fixed ~175 KB cost that cannot be eliminated.

**Icon removal:** The upstream `micropython.rc` icon (170 KB) was removed via
overlay as the final measure to meet NFR-3.

---

## Notes for SQE

- **Gates D1/D2 (display):** Require `--skip-display` to be removed and a
  Windows display accessible via WSL interop.  Headless CI cannot exercise these.
- **Gate B2 (NFR-3):** Reports `2078208 bytes (99% of NFR-3 ceiling)`.  NFR-3 is
  met.  No planner action required.
- **Static linkage (gate B4):** `objdump -p | grep DLL` shows only system DLLs:
  `bcrypt.dll`, `KERNEL32.dll`, `msvcrt.dll`, `user32.dll`, `winmm.dll`,
  `gdi32.dll`, `ole32.dll`, etc.  `SDL2.dll` is absent — static linkage confirmed.
- **romfs_trailer.c:** Uses `GetModuleFileNameA(NULL, ...)`.  Non-ASCII `.exe`
  paths may fail (limitation inherited from `picolet-cli`).
