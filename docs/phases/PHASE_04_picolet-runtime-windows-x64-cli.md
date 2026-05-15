# PH04 — picolet-runtime-windows-x64-cli + Windows build

## Plan

### Goal (restated)

Mirror PH01 and PH03 for the `windows-x64` target. Produce a single
self-contained Windows PE executable, `picolet-runtime-windows-x64-cli.exe`,
cross-compiled from WSL using `dockcross/windows-static-x64-posix`, and
demonstrate that `picolet build --target windows-x64` produces a working
`.exe` from a hello-cli app that runs under WSL interop.

The phase closes the following requirements from
[docs/v1-spec.md](../v1-spec.md):

| Spec id | Requirement |
|---|---|
| FR-CLI-3 | `picolet build [--target T]` emits a single executable at `target/<target>/<app>[.exe]`. |
| FR-CLI-4 | `picolet build` with no `--target` builds for the host platform. |
| FR-RT-1 | Each runtime artifact is a single executable embedding MicroPython, romfs ioctl machinery, and (for cli) no renderer modules. |
| FR-RT-3 | The `cli` variant has no window, no webview, no LVGL. |
| FR-RT-4 | `gc.add_heap()` is available. |
| FR-RT-5 | The `ffi` module is available. |
| FR-RT-6 | Embedded romfs is auto-mounted at `/rom` and prepended to `sys.path`. |
| FR-RT-7 | `main.py` or `main.mpy` in frozen modules or under `/rom/` is executed at startup. |
| FR-RT-8 | `sys.argv` is populated from the host command line. |
| NFR-1 | `picolet-runtime-windows-x64-cli.exe` ≤ 1 MB on disk. |
| NFR-9 | Windows artifacts run on Windows 10 21H2 and later. |

### Why this is more than a copy of PH01

PH01 produced a Linux ELF by invoking the unix port with a lean variant
config. PH04 involves four structurally different decisions:

**1. Trailer detection: `/proc/self/exe` does not exist on Windows.**
The PH03 `romfs_trailer.c` opens `/proc/self/exe` to read the binary's
own tail. On Windows the equivalent is `GetModuleFileNameA(NULL, buf,
MAX_PATH)`. Using `argv[0]` is an unreliable fallback on Windows because
it is the command string as typed, not a resolved absolute path, and
cannot be relied on when the binary is on `PATH`. The Windows trailer
code must use `GetModuleFileNameA`.

**2. Romfs ioctl lives in `vfs_rom_ioctl.c`, not in `main.c`.**
The Windows port's `mpconfigport.h` unconditionally sets
`MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1` (line 101), directing the build
to use `ports/windows/vfs_rom_ioctl.c` instead of inlining the ioctl
in `main.c`. The trailer-detection call in PH03 was spliced into
`unix/main.c`'s `load_romfs_image()` via an overlay `main.c`. For the
Windows variant the equivalent splice point is the `load_romfs_image()`
in `ports/windows/vfs_rom_ioctl.c`. PH04 must ship an overlay
`vfs_rom_ioctl.c` that adds the trailer call to that function.

**3. MinGW cross-compile mechanics via dockcross.**
PH01 built natively inside a `ubuntu:22.04` container. PH04 builds
inside `dockcross/windows-static-x64-posix:latest` using the MinGW-w64
cross toolchain (`x86_64-w64-mingw32.static.posix-`). The output is a
statically linked PE-COFF `.exe`, which means the libffi deplibs step
must run inside dockcross with the cross-compile flags forwarded.
The overall dockcross pattern is already proven by
`/home/anl/pydfu-win/scripts/build-windows.sh`.

**4. WSL interop is the test path.**
The produced `.exe` is tested by running it directly from WSL:
`./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe`.
WSL2's Windows interop forwards execution to the Windows kernel, and
stdout/stderr return to the WSL shell. No additional emulation is needed
per CLAUDE.md §"Build and test policy".

### Architecture decisions to be made during implementation

#### Decision 1: single `romfs_trailer.c` with `#ifdef _WIN32` vs two copies

**Recommendation: single file with `#ifdef _WIN32`.**

The only Windows-specific code is the binary-self-open path (the six
lines that open `/proc/self/exe` on Linux vs `GetModuleFileNameA` on
Windows). Everything else — the CRC32 table, the trailer struct parsing,
the fallback logic, the malloc/fread/fclose sequence — is identical. Two
copies would diverge over time as the trailer format evolves (e.g. PH13
may add SBOM fields to the trailer). A single file with a guarded block
keeps divergence zero:

```c
#ifdef _WIN32
#include <windows.h>
    char exe_path[MAX_PATH];
    DWORD len = GetModuleFileNameA(NULL, exe_path, MAX_PATH);
    if (len == 0 || len == MAX_PATH) {
        return false;
    }
    FILE *f = fopen(exe_path, "rb");
#else
    FILE *f = fopen("/proc/self/exe", "rb");
#endif
```

The file currently lives in the unix variant's overlay directory at
`overlay/ports/unix/variants/picolet-cli/romfs_trailer.{c,h}`. Because
the Windows build now needs the same file, it should be relocated to a
shared overlay location:

```
overlay/shared/romfs_trailer.c    ← moved from unix variant dir
overlay/shared/romfs_trailer.h    ← moved from unix variant dir
```

Each variant's `.mk` then references the shared location. Alternatively
the file can stay in the unix variant dir and the Windows variant's `.mk`
adds a `SRC_C` entry pointing at the `../../../unix/variants/picolet-cli/`
relative path. The shared location is cleaner for future variants.

The header comment in `romfs_trailer.h` on line 66–68 refers to
`/proc/self/exe` — update that to document both paths when the Windows
guard is added.

**Decision log**: Record this as a `[PH04] Decision:` empty commit
before any code changes.

#### Decision 2: where the trailer-detection hook lives on Windows

The Windows port's `load_romfs_image()` in `ports/windows/vfs_rom_ioctl.c`
is the correct hook point. It mirrors the unix `main.c` pattern exactly:
it is called exactly once, is guarded by `MICROPY_VFS_ROM_IOCTL`, and
holds the `romfs_buf` / `romfs_size` globals. The amendment is:

```c
#if MICROPY_VFS_ROM_TRAILER
#include "romfs_trailer.h"
#endif

static void load_romfs_image(void) {
    if (romfs_buf != NULL) {
        return;
    }
    #if MICROPY_VFS_ROM_TRAILER
    if (picolet_load_romfs_trailer(&romfs_buf, &romfs_size)) {
        return;
    }
    #endif
    /* ... existing MICROPY_ROMFS_EMBEDDED / file-load fallback ... */
}
```

This requires an overlay `vfs_rom_ioctl.c` (path:
`overlay/ports/windows/vfs_rom_ioctl.c`) that is a lightly amended copy
of the integration branch's `ports/windows/vfs_rom_ioctl.c`. The
amendment is the `#if MICROPY_VFS_ROM_TRAILER` block above and the
`#include "romfs_trailer.h"` at the top. The `MICROPY_VFS_ROM_TRAILER`
guard means the stock Windows build (without the picolet-cli variant)
is unaffected.

Because `overlay/` is applied by `rebuild-integration.sh` via a flat
`cp`, the overlay `vfs_rom_ioctl.c` replaces the one in
`ports/windows/vfs_rom_ioctl.c` in the integration tree. This is the
same mechanism PH03 used for `overlay/ports/unix/main.c`.

#### Decision 3: Windows-equivalent overlay main.c vs amending vfs_rom_ioctl.c

**Recommendation: amend `vfs_rom_ioctl.c` only; no overlay `main.c`
for the Windows port.**

PH03 shipped an overlay `ports/unix/main.c` because that is where the
unix port's `load_romfs_image()` lives. The Windows port's equivalent
is in `vfs_rom_ioctl.c`, which is a smaller file (~130 lines vs
~1 100 lines for `main.c`). Amending `vfs_rom_ioctl.c` is lower
risk than overlaying the full `main.c`. The Windows `main.c` shares
the unix `main.c` source (see the Windows Makefile `SRC_C` list: it
includes `ports/unix/main.c`). The overlay unix `main.c` already
handles the Linux path. There is no separate Windows `main.c` to
overlay; the Windows port uses the unix one. The only Windows-specific
romfs entry point is `vfs_rom_ioctl.c`.

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` completes 0 with the new Windows variant overlay applied. | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0; last step reports `picolet runtime: Apply downstream overlay`. |
| 2 | `build-runtime.sh --target windows-x64 --variant cli` exits 0 and produces `packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe`. | `test -f packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe` |
| 3 | FR-RT-3: cli variant has no window, no webview, no LVGL. | `strings packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \| grep -iE 'webview\|gtk\|sdl\|lvgl'` → no output. |
| 4 | FR-RT-4: `gc.add_heap()` is callable. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import gc; gc.add_heap(bytearray(4096)); print("heap-ok")'` → `heap-ok`. |
| 5 | FR-RT-5: `ffi` import succeeds. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import ffi; print("ffi-ok")'` → `ffi-ok`. |
| 6 | FR-RT-6 + FR-RT-7: romfs mounts at `/rom`, `main.mpy` auto-runs. | `picolet init win-test-gate6 --template hello-cli && cd win-test-gate6 && picolet build --target windows-x64 && ./target/windows-x64/win-test-gate6.exe` → `Hello from win-test-gate6` on stdout. |
| 7 | FR-RT-8: `sys.argv` is populated. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import sys; print(sys.argv[0])'` → non-empty path printed. |
| 8 | FR-RT-1: single executable, no renderer modules. | `ldd packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe` (or `objdump -p ... \| grep DLL`) shows no webview, no SDL, no GTK DLLs. Expected imports: only Windows system DLLs (kernel32, msvcrt, etc.) for a statically linked build. |
| 9 | Stock runtime (no appended trailer) starts cleanly, mounts empty linked romfs, no stderr. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import os; print(sorted(os.listdir("/rom")))'` → `[]` and stderr empty. |
| 10 | Trailer detection works on Windows: appended romfs is found and mounted. | Gate 6 implicitly verifies this — the hello-cli output depends on `/rom/main.mpy` being loaded via the trailer path. |
| 11 | Trailer fallback: truncating the trailer causes fallback to linked empty romfs. | `cp target/windows-x64/win-test-gate6.exe broken.exe && truncate -s -24 broken.exe && ./broken.exe` → exits 0 with no output (empty linked romfs, no user main). |
| 12 | NFR-1: `picolet-runtime-windows-x64-cli.exe` ≤ 1 MB. | `test "$(wc -c < packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe)" -le 1048576`. The build script checks this in step [8/8]. |
| 13 | NFR-1 also holds for the final app binary. | `test "$(wc -c < target/windows-x64/win-test-gate6.exe)" -le 1048576`. |
| 14 | NFR-9: the produced `.exe` runs under Windows 10 21H2 interop. | WSL interop constitutes a Windows execution environment. The static MinGW build targets `_WIN32_WINNT=0x0A00` (Windows 10) or lower. Confirm with: `objdump -p packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \| grep MajorOSVersion` → value ≤ 10. |
| 15 | FR-CLI-3: `picolet build --target windows-x64` inside a hello-cli app produces `target/windows-x64/<app>.exe`. | `picolet init win-test-gate15 --template hello-cli && cd win-test-gate15 && picolet build --target windows-x64 && test -f target/windows-x64/win-test-gate15.exe` |
| 16 | FR-CLI-4: `picolet build` with no `--target` on WSL host still resolves to `linux-x64`. | `picolet build` inside a hello-cli app on the WSL host → `target/linux-x64/<app>` (not `windows-x64`). Confirms `_host_target()` still returns `"linux-x64"` on Linux even after PH04's changes. |
| 17 | False-positive magic check: stock `.exe` last 4 bytes are not `PYLT`. | Asserted by build script step [7a] (same as PH01/PH03 check). |
| 18 | PE-COFF appended data is ignored by the Windows loader. | Gate 10 (trailer-appended exe runs correctly) is the direct proof. |
| 19 | `asyncio` import succeeds (from frozen manifest). | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import asyncio; print("aio-ok")'` → `aio-ok`. |
| 20 | `json` import succeeds (built-in C module). | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import json; print(json.dumps({"a":1}))'` → `{"a": 1}`. |
| 21 | `os.path` import succeeds (from frozen manifest). | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import os.path; print(os.path.join("a","b"))'` → `a\b` or `a/b`. |
| 22 | Build is idempotent: second `build-runtime.sh` call does not rebuild from scratch. | Second invocation completes without repeating the libffi configure step; `make` reports "Nothing to be done." for unchanged sources. |

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-CLI-{3,4}, FR-RT-{1,3,4,5,6,7,8}, NFR-{1,9} normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH04 | Goal, deliverables, exit gate, model tiers. |
| `/home/anl/picolet/CLAUDE.md` | Branch, commit, WSL interop test, dev-log policy. |
| `/home/anl/picolet/docs/phases/PHASE_01_picolet-runtime-linux-x64-cli.md` | Linux variant precedent: variant config shape, `mpconfigvariant.{h,mk}` patterns, `build-runtime.sh` step shape, MICROPY_GC_SPLIT_HEAP / FFI / STANDALONE / COMPILER decisions. |
| `/home/anl/picolet/docs/phases/PHASE_03_end-to-end-build-cli-linux.md` | Trailer architecture, fallback modes, PE-COFF appended-data safety discussion, `build_cmd.py` pipeline, `runtime_resolver.py`. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | The unix variant config in its final PH03 state — the Windows variant mirrors most of it. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.mk` | Unix variant Make config — Windows `.mk` mirrors the FFI and FROZEN_MANIFEST lines; STANDALONE is handled differently for Windows. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.{c,h}` | The Linux trailer code that PH04 must amend with `#ifdef _WIN32`. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/main.c` | How PH03 spliced the trailer call into `load_romfs_image()` — Windows uses the same pattern in `vfs_rom_ioctl.c` instead. |
| `/home/anl/picolet/packages/picolet-runtime/scripts/build-runtime.sh` | The build orchestrator PH04 extends with a `windows-x64/cli` branch. The script already has a `windows-x64/*` error stub at line 80. |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | `_host_target()` raises `NotImplementedError` for non-linux (line 232–236). Step 3 checks `target != "linux-x64"` (line 136). Both must be loosened for windows-x64. |
| `/home/anl/picolet/packages/picolet-cli/picolet/runtime_resolver.py` | `resolve_runtime()` is already target-agnostic (constructs `picolet-runtime-{target}-{variant}`). For windows-x64 the artifact name becomes `picolet-runtime-windows-x64-cli.exe` — the resolver needs an `.exe` suffix on the artifact name. |
| `/home/anl/pydfu-win/micropython/ports/windows/variants/pydfu/mpconfigvariant.h` | pydfu Windows variant precedent for macros. PH04's `picolet-cli` Windows variant has more overlap with the unix `picolet-cli` config than with pydfu, but the GC split heap, FFI, terse error, debug-printers, builtins-help disables all carry across. |
| `/home/anl/pydfu-win/micropython/ports/windows/variants/pydfu/mpconfigvariant.mk` | `MICROPY_PY_FFI=1 MICROPY_ENABLE_COMPILER=0`. PH04 keeps compiler on for the same reasons as PH01. |
| `/home/anl/pydfu-win/micropython/ports/windows/vfs_rom_ioctl.c` | The upstream `vfs_rom_ioctl.c` that PH04's overlay amends. Confirmed: `load_romfs_image()` at line 47 (embedded path) and line 60 (file-load path) are both amendment targets. |
| `/home/anl/pydfu-win/scripts/build-windows.sh` | Dockcross 5-step pipeline (mpy-cross, submodules, romfs, deplibs, port build). PH04's `build-runtime.sh` windows branch mirrors steps 1, 2, 4, 5 with the picolet-cli variant substituted for pydfu. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/windows/Makefile` | Confirmed: `vfs_rom_ioctl.c` is in `SRC_C` unconditionally (line 72); `$(wildcard $(VARIANT_DIR)/*.c)` also picks up any `.c` files in the variant dir. libffi deplibs rule uses `CROSS_COMPILE` forwarded from the build invocation. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/windows/mpconfigport.h` lines 94–102 | Confirmed: `MICROPY_VFS_ROM=1`, `MICROPY_VFS_ROM_IOCTL=1`, `MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1` are all `#ifndef`-guarded defaults in `mpconfigport.h`. The variant `.h` need not re-assert them. |

### Files to create

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli/mpconfigvariant.h` | Windows cli variant feature config. Mirrors the unix variant's macro set but omits unix-specific overrides and does not include `mpconfigvariant_common.h` (the Windows port has no such file). See "Variant config plan" below. |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli/mpconfigvariant.mk` | Windows cli variant Make config. Sets `MICROPY_PY_FFI=1`, points at the frozen manifest via `PICOLET_RUNTIME_ROOT`. Does NOT set `MICROPY_STANDALONE=1` — the Windows Makefile's deplibs rule handles libffi building via the CROSS_COMPILE path. See "Variant config plan" below. |
| `packages/picolet-runtime/overlay/ports/windows/vfs_rom_ioctl.c` | Amended `vfs_rom_ioctl.c` that adds the `#if MICROPY_VFS_ROM_TRAILER` block inside `load_romfs_image()`. Replaces the integration branch's `ports/windows/vfs_rom_ioctl.c` via the overlay cp mechanism. |
| `packages/picolet-runtime/tests/phase-04/run.sh` | Tester harness for gates 1–22. Mirrors `tests/phase-03/run.sh` structure. |

### Files to modify

| Path | Change |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c` | Add `#ifdef _WIN32` / `#else` / `#endif` block around the binary-self-open section. Windows path uses `GetModuleFileNameA(NULL, buf, MAX_PATH)` and `#include <windows.h>`. Linux path keeps `fopen("/proc/self/exe", "rb")`. See "romfs_trailer.c amendment" below. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.h` | Update comment in `picolet_load_romfs_trailer` doc block to mention both `/proc/self/exe` (Linux) and `GetModuleFileNameA` (Windows). |
| `packages/picolet-runtime/scripts/build-runtime.sh` | Replace the `windows-x64/*` error stub (lines 80–83) with a working Windows build branch. See "build-runtime.sh extension" below. |
| `packages/picolet-cli/picolet/build_cmd.py` | (a) `_host_target()`: extend to return `"windows-x64"` when `sys.platform == "win32"`. (b) The `target != "linux-x64"` guard at line 136: replace with an allow-list `{linux-x64, windows-x64}` check. No other changes — the rest of the pipeline is already target-agnostic (the `.exe` suffix at line 201 was pre-written in PH03). |
| `packages/picolet-cli/picolet/runtime_resolver.py` | `resolve_runtime()`: when `target == "windows-x64"`, append `.exe` to the artifact name before constructing the path. The current code constructs `picolet-runtime-windows-x64-cli` — the Windows build produces `picolet-runtime-windows-x64-cli.exe`. |

### Variant config plan (Windows picolet-cli)

The Windows port's `mpconfigport.h` is structurally different from the
unix port's. It includes `mpconfigvariant.h` at line 30 (before its own
defaults), which means the variant `.h` is read first. Macros set in the
variant `.h` without `#ifndef` take precedence over the port defaults.
There is no `mpconfigvariant_common.h` equivalent on the Windows port.

**`overlay/ports/windows/variants/picolet-cli/mpconfigvariant.h`**

Macros to set (rationale references the unix variant plan in PH01):

| Macro | Value | Rationale |
|---|---|---|
| `MICROPY_GC_SPLIT_HEAP` | `1` | FR-RT-4: gc.add_heap(). |
| `MICROPY_GC_SPLIT_HEAP_ADD` | `1` | FR-RT-4: the heap-add path. |
| `MICROPY_VFS_ROM_TRAILER` | `1` | Enable trailer detection (FR-BP-5). The Windows port's `vfs_rom_ioctl.c` overlay checks this macro. |
| `MICROPY_ERROR_REPORTING` | `MICROPY_ERROR_REPORTING_TERSE` | Size. `mpconfigport.h` does not set a default; we set terse explicitly. |
| `MICROPY_WARNINGS` | `0` | Size. |
| `MICROPY_PY_STR_BYTES_CMP_WARN` | `0` | Size. |
| `MICROPY_DEBUG_PRINTERS` | `0` | Size. `mpconfigport.h` line 63 guards with `#ifndef` so the variant pre-empts it. |
| `MICROPY_PY_MICROPYTHON_MEM_INFO` | `0` | Avoid undefined symbol link error when `MICROPY_DEBUG_PRINTERS=0` (same issue as unix variant). |
| `MICROPY_MEM_STATS` | `0` | Size. `mpconfigport.h` line 59 is `#ifndef`-guarded. |
| `MICROPY_MALLOC_USES_ALLOCATED_SIZE` | `0` | Size. `mpconfigport.h` line 55 is `#ifndef`-guarded. |
| `MICROPY_ENABLE_COMPILER` | `1` | Keep on — gate tests use `-c`; same rationale as unix variant. |
| `MICROPY_PY_BUILTINS_HELP` | `0` | Not needed for cli. `mpconfigport.h` line 135 is `#ifndef`-guarded. |
| `MICROPY_PY_BUILTINS_HELP_MODULES` | `0` | Same. |
| `MICROPY_PY_BUILTINS_INPUT` | `0` | Same. `mpconfigport.h` line 128 is `#ifndef`-guarded. |
| `MICROPY_PY_BUILTINS_NOTIMPLEMENTED` | `0` | Size. `mpconfigport.h` line 125 is `#ifndef`-guarded. |
| `MICROPY_PY_DEFLATE` | `0` | Not in cli baseline. |
| `MICROPY_PY_DEFLATE_COMPRESS` | `0` | Same. |
| `MICROPY_PY_HASHLIB` | `0` | Not in cli baseline. |
| `MICROPY_PY_MACHINE` | `0` | Hardware-control API; not applicable. |
| `MICROPY_PY_MACHINE_PULSE` | `0` | Same. |
| `MICROPY_PY_MACHINE_PIN_BASE` | `0` | Same. |
| `MICROPY_USE_READLINE_HISTORY` | `0` | cli runtime does not host interactive REPL. `mpconfigport.h` line 37 is `#ifndef`-guarded. |
| `MICROPY_PY_SYS_ATEXIT` | `0` | Size; not needed for cli. |

Do NOT set `MICROPY_VFS_ROM`, `MICROPY_VFS_ROM_IOCTL`, or
`MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL` — all three are already correctly
defaulted in `mpconfigport.h` via `#ifndef` guards.

Do NOT set `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS` — the Windows port pins
it to 0 in `mpconfigport.h` line 157 (not `#ifndef`-guarded); the
variant cannot override it.

**`overlay/ports/windows/variants/picolet-cli/mpconfigvariant.mk`**

```make
# Lean variant for the picolet cli runtime (windows-x64).
# Enables FFI (FR-RT-5) and the frozen manifest.

MICROPY_PY_FFI = 1

# Note: MICROPY_STANDALONE is a unix-port-specific variable that triggers
# the unix port's libffi-from-source build.  The Windows Makefile handles
# libffi unconditionally via its own deplibs rule and CROSS_COMPILE.
# Do not set MICROPY_STANDALONE here.

# Frozen manifest: resolved via PICOLET_RUNTIME_ROOT (exported by build-runtime.sh).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
```

### romfs_trailer.c amendment

The existing `overlay/ports/unix/variants/picolet-cli/romfs_trailer.c`
opens `/proc/self/exe` at line 141. The Windows amendment wraps that
with a `#ifdef _WIN32` block:

```c
bool picolet_load_romfs_trailer(const uint8_t **buf_out, size_t *size_out) {
#ifdef _WIN32
    // Windows: use GetModuleFileNameA to get the running exe path.
    // argv[0] is the command string as typed and is unreliable when the
    // binary is invoked from PATH or with a relative path.
    #include <windows.h>
    char exe_path[MAX_PATH];
    DWORD len = GetModuleFileNameA(NULL, exe_path, (DWORD)MAX_PATH);
    if (len == 0 || len >= (DWORD)MAX_PATH) {
        // Fallback 1 (Windows): cannot determine exe path — silent.
        return false;
    }
    FILE *f = fopen(exe_path, "rb");
#else
    // Linux: /proc/self/exe resolves to the running binary regardless of
    // how it was invoked.
    FILE *f = fopen("/proc/self/exe", "rb");
#endif
    if (!f) {
        // Fallback 1: cannot open the binary — silent.
        return false;
    }
    /* ... rest of the function unchanged ... */
}
```

Important: the `#include <windows.h>` must be placed at the top of the
file with the other includes, not inside the function body. The snippet
above is illustrative; the actual implementation puts the `#include` at
the top and uses `#ifdef _WIN32` only around the open-path code.

The function body from the file-size stat onwards is identical on both
platforms (POSIX `fseek`/`ftell`/`fread` work on Windows with MinGW).

### vfs_rom_ioctl.c overlay amendment

Create `overlay/ports/windows/vfs_rom_ioctl.c` as a copy of the
integration branch's `packages/picolet-runtime/micropython/ports/windows/vfs_rom_ioctl.c`
with these additions:

At the top, after the existing `#include <stdio.h>`:
```c
#if MICROPY_VFS_ROM_TRAILER
#include "romfs_trailer.h"
#endif
```

Inside the `MICROPY_ROMFS_EMBEDDED` branch of `load_romfs_image()`,
after the early-return guard `if (romfs_buf != NULL) { return; }`:
```c
#if MICROPY_VFS_ROM_TRAILER
    if (picolet_load_romfs_trailer(&romfs_buf, &romfs_size)) {
        return;
    }
#endif
```

The `MICROPY_ROMFS_EMBEDDED` path is the one used when the build
embeds an empty romfs (i.e., the stock runtime as shipped by PH04).
The file-load path (`#else` branch) is a development convenience and
does not need the trailer check, but adding it there too costs nothing
and avoids surprises if a developer tests with `MICROPY_ROMFS_EMBEDDED=0`.

The `romfs_trailer.h` include resolves via the compiler's `-I$(VARIANT_DIR)`
flag (already present in the Windows `Makefile` at line 39). The
`romfs_trailer.c` itself is added to the variant's `.mk` via `SRC_C`
(see below).

### build-runtime.sh extension (windows-x64 branch)

Replace the `windows-x64/*` error stub in the `case "${TARGET}/${VARIANT}"` block
with a working branch that:

1. Sets `DOCKCROSS_IMAGE="dockcross/windows-static-x64-posix:latest"`.
2. Sets `CROSS=x86_64-w64-mingw32.static.posix-`.
3. Defines a `docker_windows()` helper mirroring `docker_linux()` but
   using the dockcross image.
4. Sets `WINDOWS_PORT="$SUBMODULE/ports/windows"`.
5. Sets `VARIANT_NAME="picolet-${VARIANT}"` (same as linux branch).
6. Sets `ARTIFACT_NAME="picolet-runtime-${TARGET}-${VARIANT}.exe"`.
7. Sets `VARIANT_BUILD="$WINDOWS_PORT/build-${VARIANT_NAME}"`.

Steps (parallel the 8-step Linux flow):

**Step [0/8] — Ensure dockcross image is present.**
```bash
if ! docker image inspect "$DOCKCROSS_IMAGE" >/dev/null 2>&1; then
    docker pull "$DOCKCROSS_IMAGE"
fi
```

**Step [1/8] — Ensure submodule is on integration branch** (same as
Linux; `rebuild-integration.sh` also applies the Windows overlay).

Also check that the Windows variant directory is present in the
integration tree:
```bash
if [[ ! -d "$WINDOWS_PORT/variants/${VARIANT_NAME}" ]]; then
    echo "  Windows overlay not applied; running rebuild-integration.sh"
    "$SCRIPT_DIR/rebuild-integration.sh"
fi
```

**Step [2/8] — Verify submodule presence** (same asyncio + os-path
checks as Linux).

**Step [3/8] — Build mpy-cross inside dockcross.**
mpy-cross must be built by the same compiler as the runtime so the
bytecode format matches. For the Windows target this means building
mpy-cross inside dockcross:
```bash
docker_windows "$SUBMODULE/mpy-cross" make -j
```
The produced `mpy-cross/build/mpy-cross` is a Linux ELF (dockcross
containers include a Linux GCC alongside the MinGW cross-compiler;
mpy-cross is a host tool). The `build-windows.sh` pydfu precedent
confirms this pattern at its step [1/5].

**Step [4/8] — Fetch submodules (libffi).**
```bash
make -C "$WINDOWS_PORT" submodules VARIANT="${VARIANT_NAME}"
```
No `MICROPY_STANDALONE=1` needed here — the Windows Makefile's
`GIT_SUBMODULES += lib/libffi` in the `ifeq ($(MICROPY_PY_FFI),1)` block
handles it when `MICROPY_PY_FFI=1` is set in the variant `.mk`.

**Step [5/8] — Build empty embedded romfs** (same as Linux step 5;
uses host Python + mpremote to produce the 4-byte sentinel). The romfs
path must be absolute (same concern as Linux).

**Step [6/8] — Build libffi (deplibs) inside dockcross.**
```bash
docker_windows "$WINDOWS_PORT" make \
    -j \
    VARIANT="${VARIANT_NAME}" \
    CROSS_COMPILE="$CROSS" \
    PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
    deplibs
```
Skip if `ffi.h` already built (warm cache).

**Step [6b/8] — Build the Windows port variant inside dockcross.**
```bash
docker_windows "$WINDOWS_PORT" make \
    -j \
    VARIANT="${VARIANT_NAME}" \
    CROSS_COMPILE="$CROSS" \
    ROMFS_IMG="$ROMFS_IMG_REL" \
    PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"
```

**Step [7/8] — Strip and install artifact.**
The built binary is at `$VARIANT_BUILD/micropython.exe`.
```bash
BUILT_BINARY="$VARIANT_BUILD/micropython.exe"
mkdir -p "$BUILD_DIR"
cp "$BUILT_BINARY" "$ARTIFACT"
"${CROSS}strip" --strip-unneeded "$ARTIFACT" 2>/dev/null || \
    x86_64-w64-mingw32-strip --strip-unneeded "$ARTIFACT" || \
    true  # strip may not be available on host; dockcross has it
```
The strip step should also run inside dockcross if host `strip` is not
MinGW-aware:
```bash
docker_windows "$PKG_ROOT" "${CROSS}strip" --strip-unneeded "$ARTIFACT"
```

Step [7a]: Assert last 4 bytes of stock `.exe` are not `"PYLT"` (same
as Linux check).

Step [7b]: Write `.version` sidecar — same as Linux.

**Step [8/8] — NFR-1 size gate** (same as Linux: ≤ 1 048 576 bytes).

### build_cmd.py changes

**`_host_target()` extension:**
```python
def _host_target() -> str:
    machine = platform.machine().lower()
    system = sys.platform
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if system == "win32" and machine in ("x86_64", "amd64", "amd64"):
        return "windows-x64"
    raise NotImplementedError(
        f"host auto-detection: unsupported platform {sys.platform}/{platform.machine()}; "
        "use --target to specify explicitly. "
        "Supported targets: linux-x64, windows-x64."
    )
```

**Target guard change:**
```python
SUPPORTED_TARGETS = {"linux-x64", "windows-x64"}
if target not in SUPPORTED_TARGETS:
    raise NotImplementedError(
        f"--target {target!r} not implemented; "
        f"supported targets: {', '.join(sorted(SUPPORTED_TARGETS))}. "
        "webview targets land in PH09/PH10; lvgl in PH11/PH12."
    )
```

**FR-CLI-4 note:** `_host_target()` running on WSL returns `"linux-x64"`
(WSL is Linux). `_host_target()` returning `"windows-x64"` only triggers
when running natively on Windows (CPython on Win32). This is the correct
behaviour: the developer cross-compiles from WSL using `--target
windows-x64` explicitly; the host-default path is the Linux binary.

### runtime_resolver.py changes

`resolve_runtime()` must append `.exe` for Windows targets:

```python
artifact_name = f"picolet-runtime-{target}-{variant}"
if target == "windows-x64":
    artifact_name += ".exe"
```

No other changes — the path construction already uses the artifact name
verbatim.

### Sequence the developer follows

All commands from `/home/anl/picolet` on branch `dev`.

**1. Record the architecture decision as an empty commit.**
```bash
git commit --allow-empty -s -m "[PH04] Decision: single romfs_trailer.c with #ifdef _WIN32

The Windows-specific code (GetModuleFileNameA vs /proc/self/exe) is
isolated to 6 lines inside picolet_load_romfs_trailer().  Two copies
would diverge as the trailer format evolves.  Single file with a
guarded block is the correct shape.

Hook point for Windows: overlay/ports/windows/vfs_rom_ioctl.c
load_romfs_image(), guarded by MICROPY_VFS_ROM_TRAILER.  No overlay
main.c needed for the Windows port."
```

**2. Amend `romfs_trailer.c` with the `#ifdef _WIN32` block.**
Edit `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c`.
Add `#ifdef _WIN32` / `#else` / `#endif` around the binary-open lines.
Move the `#include <windows.h>` to the top of the file inside an
`#ifdef _WIN32` guard.
Update the comment in `romfs_trailer.h`'s `picolet_load_romfs_trailer`
doc block to mention Windows.

**3. Create the Windows overlay `vfs_rom_ioctl.c`.**
Copy `packages/picolet-runtime/micropython/ports/windows/vfs_rom_ioctl.c`
to `packages/picolet-runtime/overlay/ports/windows/vfs_rom_ioctl.c`.
Add the `MICROPY_VFS_ROM_TRAILER` block as described in "vfs_rom_ioctl.c
overlay amendment" above.

**4. Create the Windows variant directory and files.**
```bash
mkdir -p packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli
```
Write `mpconfigvariant.h` per "Variant config plan" above.
Write `mpconfigvariant.mk` per "Variant config plan" above.

The Windows variant `.mk` must also add `romfs_trailer.c` to `SRC_C`.
The Windows Makefile already includes `$(wildcard $(VARIANT_DIR)/*.c)`
in `SRC_C` (line 73), so any `.c` file dropped in the variant directory
is automatically compiled. `romfs_trailer.c` should live in the shared
location or be symlinked; the simplest approach for the overlay cp
mechanism is to keep a single canonical copy in:
`overlay/ports/unix/variants/picolet-cli/romfs_trailer.c`
and reference it from the Windows variant `.mk`:
```make
SRC_C += $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c
```
This avoids a second copy. The `#ifdef _WIN32` guards in the file ensure
correct behaviour on both builds. However, the Windows Makefile's
`$(wildcard $(VARIANT_DIR)/*.c)` uses the VARIANT_DIR as the base, so
an explicit `SRC_C +=` with an absolute path is the correct mechanism
for referencing an out-of-variant `.c` file.

Alternatively, place `romfs_trailer.c` and `romfs_trailer.h` in a
`overlay/shared/` directory and update both variants' `.mk` files to
reference `$(PICOLET_RUNTIME_ROOT)/overlay/shared/romfs_trailer.c`. Either
approach is acceptable; pick the one that feels cleanest during
implementation and log the choice as a `[PH04] Decision:` commit.

**5. Extend `build-runtime.sh` with the windows-x64 branch.**
Remove the error stub for `windows-x64/*` at lines 80–83.
Add the new Windows build path per "build-runtime.sh extension" above.
The windows path is a new `elif` branch in the main `case` statement
(or a separate function `build_windows_x64()` called from `main`).

**6. Extend `build_cmd.py` and `runtime_resolver.py`.**
Make the changes described in "build_cmd.py changes" and
"runtime_resolver.py changes" above.

**7. Commit the overlay and script changes together.**
```bash
git add packages/picolet-runtime/overlay/ports/windows/ \
        packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c \
        packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.h \
        packages/picolet-runtime/scripts/build-runtime.sh \
        packages/picolet-cli/picolet/build_cmd.py \
        packages/picolet-cli/picolet/runtime_resolver.py
git commit -s -m "[PH04] Add windows-x64 cli variant and extend build pipeline

Closes: FR-CLI-3, FR-CLI-4, FR-RT-{1,3,4,5,6,7,8}, NFR-1, NFR-9"
```

**8. Run `rebuild-integration.sh` to apply the new overlay.**
```bash
./packages/picolet-runtime/scripts/rebuild-integration.sh
```
Confirm the commit message in the integration branch shows the new
Windows files applied.

**9. Run the windows build.**
```bash
bash packages/picolet-runtime/scripts/build-runtime.sh \
    --target windows-x64 \
    --variant cli
```
Expect `=== Build complete: .../picolet-runtime-windows-x64-cli.exe ===`
and NFR-1 size gate passed.

**10. Smoke-test the runtime binary directly.**
```bash
./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \
    -c 'print("win-rt-ok")'
# expect: win-rt-ok

./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \
    -c 'import gc; gc.add_heap(bytearray(4096)); print("heap-ok")'
# expect: heap-ok

./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \
    -c 'import ffi; print("ffi-ok")'
# expect: ffi-ok

./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe \
    -c 'import asyncio; print("aio-ok")'
# expect: aio-ok
```

**11. End-to-end build test.**
```bash
cd /tmp
picolet init hello-win --template hello-cli
cd hello-win
picolet build --target windows-x64
./target/windows-x64/hello-win.exe
# expect: Hello from hello-win
```

**12. Write the test harness.**
Create `packages/picolet-runtime/tests/phase-04/run.sh` exercising all 22
exit gates. Mirrors `tests/phase-03/run.sh` structure.

**13. Commit test harness.**
```bash
git add packages/picolet-runtime/tests/phase-04/
git commit -s -m "[PH04] Add phase-04 test harness

Gates 1-22 covering FR-CLI-{3,4}, FR-RT-{1,3,4,5,6,7,8},
NFR-1, NFR-9."
```

### Foreseeable risks

**Risk 1: libffi cross-compile under dockcross.**
The PH01 `build-runtime.sh` already documents the autogen.sh /
configure regeneration issue for the Linux build (the warm-cache
timestamp-touch workaround at lines 252–265). A similar issue may
arise for the Windows deplibs build: if the integration submodule is
re-initialised, `configure` may be absent. The same mitigation applies:
detect `ffi.h` in the build cache, touch `configure` and Makefile
timestamps, skip re-autogen. The pydfu `build-windows.sh` does not have
this workaround, but pydfu's integration script does not reinitialise
submodules the same way. The developer should anticipate this and apply
the mitigation in the Windows deplibs step proactively.

**Risk 2: `GetModuleFileNameA` reliability.**
`GetModuleFileNameA(NULL, buf, MAX_PATH)` returns the full path only if
`MAX_PATH` is sufficient. On long paths (> 260 characters) with the
Windows LongPath setting enabled, this may truncate. Mitigation: treat
`len == MAX_PATH` (potential truncation) as a failure and fall back
silently, as specified in the "romfs_trailer.c amendment" section above.
A more robust approach would use `GetModuleFileNameW` + WideChar, but
that adds complexity; the 260-character limit covers all practical
installation paths for a CLI dev tool.

**Risk 3: mpy-cross version skew.**
When `picolet build --target windows-x64` runs on WSL, it uses the
in-tree `mpy-cross` (a Linux binary built in step [3/8] of
`build-runtime.sh`). The mpy-cross runs on the host (Linux), not on
Windows. This is correct: mpy-cross is a host tool that produces
bytecode consumed by the Windows runtime. The `.version` sidecar written
in step [7b] must match the `mpy-cross --version` output. Since both
artifacts come from the same integration branch commit, they are
version-matched by construction. If the developer builds the Windows
runtime in a separate session after rebuilding the Linux runtime, the
`mpy-cross` binary is refreshed and the `.version` sidecars for both
targets stay in sync.

**Risk 4: PE-COFF appended-data tolerance.**
NSIS, makeself, and Inno Setup all append data to PE-COFF executables
and this is a well-established pattern. The Windows PE loader reads the
image by walking the PE Optional Header fields (`SizeOfImage`,
`SizeOfHeaders`), not by reading to EOF. Trailing bytes are ignored.
However, if `MICROPY_ROMFS_EMBEDDED=1` is set and the `.exe` is
processed by `upx` or a code-signing tool that re-writes the PE
structure, trailing data may be stripped. PH04 does not code-sign
(NFR scope note: "Code signing" is out of scope per v1-spec.md §"Out
of scope"). Logged as a caveat for future phases.

**Risk 5: mpremote romfs filename ASCII limit.**
Confirmed caveat from PH03: mpremote's romfs builder silently omits
files with non-ASCII names. This persists on Windows. The error manifests
as a `UnicodeEncodeError` in `build_cmd.py` when the file path contains
non-ASCII characters. The PH03 fix (`.encode('utf-8', errors='replace')`
or early validation) carries over.

**Risk 6: Windows `MICROPY_PY_MACHINE` macro and possible port
dependencies.**
The unix port guards some of its code on `MICROPY_ASYNC_KBD_INTR` /
`MICROPY_KBD_EXCEPTION` which PH01 avoided by keeping `EXTRA_FEATURES`.
The Windows port has a different set of port-specific guards. If
disabling `MICROPY_PY_MACHINE` causes a compile error on the Windows
port (unlikely — the Windows port's `mpconfigport.h` does not set
`MICROPY_PY_MACHINE` by default), the variant `.h` should guard the
define: `#ifndef MICROPY_PY_MACHINE` / `#define MICROPY_PY_MACHINE (0)`.
Verify the build produces no errors for each disabled macro.

**Risk 7: variant `.mk` SRC_C path for romfs_trailer.c.**
The Windows Makefile's `$(wildcard $(VARIANT_DIR)/*.c)` pattern picks up
`.c` files physically present in the variant directory. If `romfs_trailer.c`
is kept in the unix variant directory and referenced via `SRC_C +=` in
the Windows `.mk`, the path must be absolute or relative to the Makefile's
working directory (the Windows port directory). Using
`$(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c`
(absolute, via `PICOLET_RUNTIME_ROOT` exported by the build script) is
the safest approach.

### Out of scope for PH04

- webview and lvgl variants on Windows (PH10, PH12).
- Runtime artifact distribution / caching (PH05).
- macOS and ARM targets.
- Code signing or VERSIONINFO resources.
- `picolet dev` subcommand (PH16).
- SBOM emission (PH13).

### Spec traceability

| Spec id | Where closed in PH04 |
|---|---|
| FR-CLI-3 | `build_cmd.py` step 10 writes `target/windows-x64/<app>.exe`; `.exe` suffix added at line 201 (pre-written in PH03, now exercised). Exit gate 15. |
| FR-CLI-4 | `_host_target()` extension returns `"windows-x64"` on `win32`; keeps `"linux-x64"` on Linux. Exit gate 16. |
| FR-RT-1 | Single PE-COFF `.exe` produced by the Windows port build with no separate DLL dependencies. Exit gate 8. |
| FR-RT-3 | No renderer modules in the cli variant config. Exit gates 3, 8. |
| FR-RT-4 | `MICROPY_GC_SPLIT_HEAP=1` / `MICROPY_GC_SPLIT_HEAP_ADD=1` in Windows variant `.h`. Exit gate 4. |
| FR-RT-5 | `MICROPY_PY_FFI=1` in Windows variant `.mk`; Windows Makefile builds libffi via deplibs. Exit gate 5. |
| FR-RT-6 | Windows port's `mpconfigport.h` sets `MICROPY_VFS_ROM=1` and `MICROPY_VFS_ROM_IOCTL=1` by default; the variant inherits both. romfs auto-mount runs in the port's `main.c` (shared with unix). Exit gate 6. |
| FR-RT-7 | Same `main.c` auto-run logic (frozen then `/rom/main.mpy`) applies. Variant enables `MICROPY_MODULE_FROZEN_MPY` (on by default in the port). Exit gate 6. |
| FR-RT-8 | `main.c` populates `sys.argv`. `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS=0` pinned in `mpconfigport.h:157`. Exit gate 7. |
| NFR-1 | Build script step [8/8] checks `wc -c ≤ 1 048 576`. Exit gates 12, 13. |
| NFR-9 | Static MinGW build; dockcross targets Win10 (NT 6.2+ default). Exit gate 14. |
