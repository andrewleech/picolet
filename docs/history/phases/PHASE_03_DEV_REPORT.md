# picolet — Phase 03 Developer Report

**Feature:** picolet end-to-end build pipeline (Linux / cli)
**Phase:** 03 — End-to-end build: cli on linux-x64
**Date:** 2026-05-15
**Attempt:** 1
**Phase File:** docs/phases/PHASE_03_end-to-end-build-cli-linux.md

## Implementation Summary

Phase 03 wires together the MicroPython unix port with user application code to produce a single self-contained executable. The pipeline has three main layers: a C-level trailer detection mechanism embedded in the runtime binary, a Python `picolet build` subcommand that drives compilation and packaging, and a 14-gate SQE harness that validates every functional requirement.

The trailer mechanism uses an append-at-end strategy (Decision: `223380f`): the MicroPython romfs image is appended to the runtime binary, followed by a 24-byte trailer (magic `PYLT`, version 1, payload size, CRC32). At startup the runtime reads `/proc/self/exe`, seeks to EOF minus 24 bytes, validates the trailer, and if valid maps the preceding bytes as the romfs image. Five silent-or-loud fallback modes guard against miscorruption. The macro `MICROPY_VFS_ROM_TRAILER=1` gates this path cleanly so the stock runtime (no trailer) retains identical behaviour.

`picolet build` implements the full pipeline in ten steps: resolve runtime artifact, verify mpy-cross version compatibility, locate picolet.toml, infer app variant (cli vs webview), compile all `.py` sources to `.mpy`, copy `[romfs].include` directories, zero all mtimes for reproducibility (FR-BP-6), build the romfs image with `mpremote romfs build`, append the romfs + trailer to the runtime, and write the output artifact. All 16 checks in the SQE harness (14 gates, gate 6 split into 6a/6b) pass in a single run.

## Files Created

| File Path | Purpose | Lines |
|-----------|---------|-------|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.h` | C header: `picolet_trailer_t` packed struct, magic constant, `picolet_load_romfs_trailer()` declaration | 78 |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c` | CRC32 table + `picolet_load_romfs_trailer()` implementation (procfs, seek, validate, malloc) | 238 |
| `packages/picolet-runtime/overlay/ports/unix/main.c` | Full copy of integration-branch `main.c` with `#if MICROPY_VFS_ROM_TRAILER` patch applied | 1070 |
| `packages/picolet-cli/picolet/_trailer.py` | Python trailer struct constants and `pack_trailer()` / `unpack_trailer()` helpers | 55 |
| `packages/picolet-cli/picolet/runtime_resolver.py` | `resolve_runtime()` and `locate_mpy_cross()` — walk repo tree to find build artifacts | 84 |
| `packages/picolet-cli/picolet/build_cmd.py` | `picolet build` subcommand: full 10-step pipeline | 480 |
| `tests/phase-03/fixtures/hello-cli-with-assets/picolet.toml` | Gate 8 fixture manifest with `[romfs] include = ["assets"]` | 7 |
| `tests/phase-03/fixtures/hello-cli-with-assets/src/main.py` | Gate 8 fixture app: opens `/rom/assets/data.txt` at runtime | 5 |
| `tests/phase-03/fixtures/hello-cli-with-assets/assets/data.txt` | Gate 8 fixture asset file | 1 |
| `tests/phase-03/run.sh` | 14-gate SQE harness (bash) | 278 |

## Files Modified

| File Path | Changes Made | Reason |
|-----------|-------------|--------|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Added `#define MICROPY_VFS_ROM_TRAILER (1)` | Enables trailer detection for the cli variant only |
| `packages/picolet-runtime/scripts/build-runtime.sh` | (a) Default `TEST_ROMFS=""` → builds empty romfs. (b) Warm-cache skip for libffi `make deplibs`. (c) [7a] magic check changed to tail-byte `od` comparison. (d) [7b] `.version` sidecar written via `mpy-cross --version`. | Ship empty romfs by default; work around Ubuntu 22.04 libtool incompatibility; correct magic check; version sidecar for `_verify_mpy_cross_version()` |
| `packages/picolet-runtime/scripts/dockerfiles/linux-x64-build/Dockerfile` | Added `autoconf`, `automake`, `libtool` to apt-get install | libffi `autogen.sh` requires a modern libtool for `LT_SYS_SYMBOL_USCORE` |
| `packages/picolet-cli/picolet/__main__.py` | PEP-723 dep added (`mpremote`); `build_cmd` imported and registered; `NotImplementedError` caught in `main()` | Wire `picolet build` into CLI entry point |

## Build Status

- **Build command:** `bash packages/picolet-runtime/scripts/rebuild-integration.sh` (Docker-based cross-compile; produces `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`)
- **Result:** Pass
- **Warnings:** None (libffi warm-cache skip suppresses spurious deplibs rebuild on repeated runs)

## Deviations from Phase Plan

**Overlay carries full `main.c` instead of a patch header.** The phase plan proposed adding trailer detection via a patch applied to a header included from `main.c`. In practice, `rebuild-integration.sh`'s step [3/3] copies overlay files verbatim into the submodule working tree and then rejects runs when the submodule has uncommitted edits. Since `main.c` itself needed modification (not a standalone include), the only clean approach that satisfies the "no direct submodule edits" invariant was to carry the full patched `main.c` in the overlay tree (`overlay/ports/unix/main.c`). The content is identical to the integration-branch `main.c` plus the `#if MICROPY_VFS_ROM_TRAILER` block. Decision recorded in commit `e161c13`.

**Entry point compiled to `/rom/main.mpy` (auto-run location).** The phase plan described compiling the entry point to its natural path (e.g., `romfs/src/main.mpy`). The MicroPython unix port's startup sequence checks `/rom/main.mpy` specifically; it does not search subdirectories. `_compile_mpy()` therefore compiles the designated entry point a second time, writing the result to `romfs_root/main.mpy`. Decision recorded in commit `97df09c`.

**`[7a]` magic check uses tail bytes, not `strings | grep`.** The `romfs_trailer.c` source itself contains the string literal `"PYLT"` in `.rodata`, so a `strings` scan always finds the magic regardless of whether a trailer is present. The check was rewritten to compare the last four bytes of the binary against the little-endian encoding of the magic (`50 59 4c 54`). Decision recorded in commit `c6a6fc2`.

**libffi `make deplibs` skipped when warm cache detected.** Ubuntu 22.04's system libtool 2.4.6 lacks `LT_SYS_SYMBOL_USCORE`, causing libffi's `autogen.sh` to fail. The Dockerfile now installs `autoconf`, `automake`, `libtool` from apt so the Docker image has a compatible toolchain. Additionally, `build-runtime.sh` detects a warm build cache (`ffi.h` present, `configure` absent) and skips `make deplibs` entirely, touching required timestamps so subsequent `make` steps proceed without re-running autogen. Caveat recorded in commit `043d242`.

## Known Limitations

- `resolve_runtime()` hard-codes the path convention `packages/picolet-runtime/build/picolet-runtime-{target}-{variant}`. A `TODO(PH05)` comment flags this for the distribution packaging phase when artifacts will live elsewhere.
- `_host_target()` returns `"linux-x64"` only; other platforms raise `NotImplementedError`. This is intentional scope — PH03 is Linux-only.
- The libffi warm-cache workaround is fragile: it keys on the presence of `ffi.h` and absence of `configure`. If the libffi source layout changes in a future MicroPython pull, the heuristic may need adjustment.

## Key Decisions Made

**Append-at-end trailer (Option A).** Three options were considered: (A) append romfs after the ELF, (B) re-link with romfs baked in, (C) sibling file. Option A was chosen because it requires no re-link, works with dynamic-PIE ELFs (which tolerate trailing data), and keeps the build pipeline simple. Decision recorded in commit `223380f`.

**CRC32 polynomial 0xEDB88320 (zlib reflected).** This matches Python's `zlib.crc32()` exactly, enabling straightforward cross-validation between the C detection code and the Python packing code without a custom implementation.

**Empty romfs shipped by default.** `build-runtime.sh` previously embedded a test romfs image. The stock runtime must not carry application content (NFR-1 size gate, clean separability). The default is now an empty directory producing the 4-byte mpremote sentinel.

## Notes for SQE

- Gate 9 (trailer path) is confirmed by output matching: if the user's romfs is not loaded, `main.py` cannot print the expected string. This is an indirect but conclusive check.
- Gate 11 (stripped binary fallback) truncates the binary at `size - 24` bytes, removing the trailer entirely. The runtime should silently fall back to the empty embedded romfs and exit 0 with no output. Verify that no error is printed to stderr.
- Gate 12 (CRC mismatch warning) overwrites the last 4 bytes of the appended binary with `\x00\x00\x00\x00` to corrupt the CRC field. The runtime must emit a warning to stderr and still exit 0 (graceful fallback, not crash).
- Gate 10 (reproducibility) runs `picolet build` twice on the same source tree in separate staging directories and diffs the output binaries. Any timestamp or ordering non-determinism in `mpremote romfs build` will surface here.
- Gate 13b (NFR-8 / Ubuntu 22.04) runs the built binary inside `docker run --rm ubuntu:22.04`. The binary must not require a glibc version newer than 2.35.
- The `hello-cli-with-assets` fixture (gate 8) is the only test that exercises the `[romfs] include` path. SQE should also consider a fixture with multiple include directories and nested subdirectories.
- `picolet build --keep-staging` preserves the staging directory for inspection; this flag is useful when diagnosing romfs layout issues.

---

```
STATUS: Complete
ARTIFACT: docs/phases/PHASE_03_DEV_REPORT.md
SUMMARY: Phase 03 implements the full end-to-end Linux build pipeline: a 24-byte append-at-end romfs trailer in C (gated by MICROPY_VFS_ROM_TRAILER=1), the picolet build subcommand in Python (10-step pipeline: mpy compilation, romfs assembly, trailer append), and a 14-gate SQE harness. All 16 harness checks pass; the runtime artifact is 624944 bytes (NFR-1 satisfied) and runs on Ubuntu 22.04 (NFR-8 satisfied).
```
