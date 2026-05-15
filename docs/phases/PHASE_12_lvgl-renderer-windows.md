# PH12 — LVGL renderer on Windows (SDL2 backend)

## Plan

### Goal (restated)

Mirror PH11 on Windows. Produce `picolet-runtime-windows-x64-lvgl.exe`
cross-compiled via dockcross/windows-static-x64-posix (MXE MinGW-w64),
linking LVGL statically and SDL2 statically via the MXE toolchain's
prebuilt package system. The produced binary opens an SDL2 desktop
window under WSL interop when run on Windows. User frozen Python can
`import lvgl as lv` and build a widget tree. `picolet.invoke` /
`picolet.emit` work through the same `InProcessTransport` pair wired in
PH11.

PH11 established the overlay submodule (`overlay/lib/lv_binding_micropython`),
the tuned `lv_conf.h`, the Python facade (`picolet_ui._lvgl.py`), the
`_lvgl_pump` coroutine in `_loop.py`, and the `InProcessTransport`. PH12
reuses all of these; it adds only the Windows-port variant config, the
SDL2 acquisition step, the `SDL2.dll` distribution mechanism, and the
`build-runtime.sh` branch for `windows-x64/lvgl`.

The phase closes the following requirements from
[docs/v1-spec.md](../v1-spec.md):

| Spec id | Requirement |
|---|---|
| FR-LV-1 (Windows half) | On Windows the LVGL backend uses SDL2 for a desktop window. |
| FR-LV-2 | Display size comes from `[window]` in `picolet.toml`. (Windows half.) |
| FR-LV-3 | `import lvgl as lv` works inside the app's frozen Python. (Windows half.) |
| FR-LV-4 | `picolet.invoke` / `picolet.emit` work in the LVGL variant. (Windows half.) |
| FR-RT-2 | Three runtime variants per target: `lvgl` is one of them. PH12 lands the Windows build. |
| NFR-3 | `picolet-runtime-windows-x64-lvgl.exe` ≤ 2 MiB. |

### Architecture decisions

#### AD1 — SDL2 acquisition: MXE prebuilt package via `make -C /usr/src/mxe sdl2`

Three options were examined:

| Option | Description | Verdict |
|---|---|---|
| **A: MXE build inside dockcross (chosen)** | The dockcross/windows-static-x64-posix image ships MXE at `/usr/src/mxe` with a complete `sdl2.mk` recipe (SDL2 2.26.2). Running `make -C /usr/src/mxe sdl2 MXE_TARGETS=x86_64-w64-mingw32.static.posix` inside the container builds SDL2 once and deposits headers at `/usr/src/mxe/usr/x86_64-w64-mingw32.static.posix/include/SDL2/` and a static archive at `.../lib/libSDL2.a`. The output persists across container runs because `build-runtime.sh` bind-mounts the repo but `/usr/src/mxe` is inside the container — so the MXE SDL2 build is **one-time per fresh container pull** and is gated by checking whether `libSDL2.a` exists before re-running. | **Selected.** All toolchain state is self-contained inside the dockcross image family. No vendored blobs in the repo. Offline-capable once the container is pulled. The `--user` flag on the dockcross run normally drops write access to `/usr/src/mxe`; PH12's `build-runtime.sh` drops the `--user` override only for the one-time MXE build step by running it as root inside the container. |
| **B: Download SDL2-devel-2.x.x-mingw.tar.gz** | Download the official MinGW prebuilt archive from `github.com/libsdl-org/SDL/releases`. Extract headers and `libSDL2.a` into `$PKG_ROOT/build/sdl2-win64/`. The archive ships `x86_64-w64-mingw32/lib/libSDL2.a` (static) and `bin/SDL2.dll` (runtime). | **Fallback.** Works without MXE, but adds a network fetch with a pinned hash to the build script, or requires the developer to pre-place the archive. Option A is cleaner for CI reproducibility since the container already has the recipe. |
| **C: Build SDL2 from source in dockcross** | Fetch SDL2 source tarball, configure with `--host=x86_64-w64-mingw32.static.posix`, `make install` into `$PKG_ROOT/build/sdl2-win64/`. Matches how libffi is built for the cli variant. | **Rejected.** SDL2's autoconf chain is more complex than libffi's and has historically needed patches in cross-compile environments. Option A is strictly better (MXE handles the patches). |

**Static vs dynamic linkage.** SDL2 is zlib-licensed; static linkage does not
violate NFR-5. On Windows the dynamic-link path requires shipping `SDL2.dll`
alongside the `.exe` — manageable but adds complexity. Static link (`libSDL2.a`
from MXE) produces a single self-contained `.exe` with no DLL dependency on
SDL2, which is simpler and more consistent with the rest of the Windows build
(libffi and libmicropython are also statically linked). The MXE recipe builds
SDL2 in static mode by default for the `.static.posix` MXE target. **PH12
statically links SDL2.**

NFR-5 is satisfied: SDL2 is zlib-licensed, statically linked. NFR-9 is
satisfied: static MinGW build targets Windows 10+.

**SDL2.dll distribution.** Because SDL2 is statically linked into the runtime
binary, there is no runtime DLL to distribute. The `picolet build` pipeline
(PH03/PH04) requires no change. This is a deliberate divergence from PH11's
dynamic-link approach on Linux: on Linux, dynamic link is preferred because
SDL2 dlopens audio/video backends (PulseAudio, ALSA, X11) at runtime and
static linkage brings those in redundantly. On Windows, SDL2's backends
(DirectX, WinAPI windowing) are system DLLs already present, and static link
of the SDL2 glue layer itself is the correct idiom for a redistributable
binary. A `NOTE` commit records this divergence.

#### AD2 — `lv_binding_micropython` MinGW compatibility

The binding's `micropython.mk` has an SDL2-detection block guarded by
`ifeq ($(notdir $(CURDIR)),unix)` — it only runs when `make` is invoked
from the unix port directory. For the Windows port, `CURDIR` is the windows
port directory, so `MICROPY_SDL` is never set and `LV_USE_SDL` defaults to 0.
PH12 must bypass this guard.

Three approaches:

| Option | Description | Verdict |
|---|---|---|
| **A: Inject SDL2 flags via `mpconfigvariant.mk` (chosen)** | Set `LV_CFLAGS` and `LDFLAGS_USERMOD` in the Windows variant `.mk` directly, mirroring what `micropython.mk`'s pkg-config block would have produced. Also set `MICROPY_SDL=1` via `CFLAGS_EXTRA` so `lv_conf.h`'s `#ifdef MICROPY_SDL` enables `LV_USE_SDL=1`. | **Selected.** Explicit, readable, no patching of third-party files. SDL2 paths are computed from MXE output dir which is a known constant inside dockcross. |
| **B: Patch `micropython.mk` to detect MinGW** | Add a `else ifeq ($(notdir $(CURDIR)),windows)` branch. | **Rejected.** Modifies the pinned submodule which complicates SHA pinning. |
| **C: Wrap `micropython.mk` with a new make target** | Write a shim Makefile that sets the right vars then `include`s the binding's `.mk`. | **Rejected.** Overengineered; the variant `.mk` already has the right injection point. |

**gen_mpy.py (code generation step).** PH11 encountered a regression
where `gen_mpy.py` preprocesses `lvgl_private.h` (not `lvgl.h`) and
misses SDL-exposed functions. PH11's workaround — `LV_CFLAGS = -include
$(PICOLET_RUNTIME_ROOT)/overlay/lib/lv_binding_micropython/lvgl/src/drivers/lv_drivers.h`
— is inherited unchanged in PH12's Windows variant `.mk`. The same
workaround applies regardless of target platform.

The `CPP` invoked during `gen_mpy.py` is the MXE cross-preprocessor
(`x86_64-w64-mingw32.static.posix-cpp`). The LVGL headers are
platform-agnostic C; the preprocessor handles them without issue under
MinGW. `pycparser`'s fake-libc stubs cover `<stdint.h>` etc.; the MinGW
cross-preprocessor's system includes do not interfere because the binding
uses `-I $(LVGL_BINDING_DIR)/pycparser/utils/fake_libc_include` to
shadow them.

#### AD3 — `picolet_ui_win` restructuring vs extension

`picolet_ui_win` (PH10, Windows-only) is the Windows-specific UI package
for the webview variant. It mirrors `picolet_ui` for Linux. For the LVGL
variant on Windows there are two options:

| Option | Description | Verdict |
|---|---|---|
| **A: Reuse `picolet_ui` unchanged (chosen)** | `picolet_ui._lvgl.py` (from PH11) is already platform-agnostic: it calls `lv.init()`, `lv.sdl_window_create()`, and the `_lvgl_pump` coroutine, none of which are Linux-specific. The `lv_binding_micropython` SDL2 driver is cross-platform. The Windows LVGL variant freezes `picolet_ui` (not `picolet_ui_win`), exactly as the Linux LVGL variant does. | **Selected.** No new module, no code duplication. `manifest_lvgl_windows.py` freezes `picolet_ui` — same as `manifest_lvgl.py` on Linux. |
| **B: Add `picolet_ui_win/_lvgl.py`** | Mirror `picolet_ui/_lvgl.py` in `picolet_ui_win/` so Windows LVGL uses `picolet_ui_win` consistently with the webview variant on Windows. | **Rejected.** The `_lvgl.py` code is already clean of platform-isms. A mirror would be byte-for-byte identical or a trivial re-import. Any future divergence is better handled with `sys.platform` guards inside the shared module than by forking the package. |

**Manifest.** Create `manifests/manifest_lvgl_windows.py`. It is identical to
`manifests/manifest_lvgl.py` except the comment header is Windows-specific.
The `freeze("../python", "picolet_ui")` line is the same — no `picolet_ui_win`.
The `lv` C module enters via `USER_C_MODULES`, same as Linux.

#### AD4 — `lv_conf.h` for Windows (NFR-3, same file)

The tuned `lv_conf.h` at
`overlay/ports/unix/variants/picolet-lvgl/lv_conf.h` is Linux-specific
only in its path location; the content is pure C preprocessor directives.
The Windows variant `.mk` points `LV_CONF_PATH` at the same file. No
separate `lv_conf.h` for Windows; the same tuning applies. If the
Windows binary's size differs materially from the Linux binary (expected
to be slightly larger due to PE-COFF overhead and any MinGW-specific
runtime support code), the existing size lever (`LV_USE_*` disables) is
iterated. The 2 MiB ceiling applies to the stripped Windows `.exe` as
required by NFR-3.

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` exits 0 with Windows lvgl overlay applied. | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0. Windows variant directory present in integration tree. |
| 2 | `build-runtime.sh --target windows-x64 --variant lvgl` exits 0 and produces `picolet-runtime-windows-x64-lvgl.exe`. **FR-RT-2.** | `test -f packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe` |
| 3 | **FR-LV-3**: `import lvgl as lv` succeeds in the Windows lvgl runtime. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe -c 'import lvgl as lv; print("ok")'` → `ok`. (Does not call `lv.init()`; no display required.) |
| 4 | **NFR-3**: Windows lvgl variant ≤ 2 MiB. | `wc -c packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe` → ≤ 2 097 152 bytes. Print actual size + percentage of ceiling. |
| 5 | **FR-LV-1** (Windows) + **FR-LV-2**: SDL2 desktop window opens with size from `[window]`. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe -c "import picolet_ui._test as t; t.run_lvgl_sanity_test()"` run via WSL interop with Windows display available. Stdout: `PICOLET_LV_SANITY_OK size=800x600 label=Hello,World`. |
| 6 | `hello-lvgl` end-to-end build produces a working Windows binary. | `picolet build --target windows-x64` against `tests/phase-12/fixtures/hello-lvgl-win-min/` (same layout as PH11's `hello-lvgl-min-e2e` with `[ui] renderer="lvgl"`). Run the `.exe` via WSL interop. Stdout contains `PICOLET_LV_SANITY_OK`. |
| 7 | **FR-LV-4**: `picolet.invoke` round-trips in-process on Windows. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe -c "import picolet_ui._test as t; t.run_ipc_probe()"` via WSL interop. Stdout: `PICOLET_LV_IPC_OK greet=hello,world`. |
| 8 | No SDL2.dll in the binary's import table (static linkage verified). **NFR-5.** | `objdump -p packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \| grep DLL` — output must not contain `SDL2.dll`. LVGL is MIT; SDL2 statically linked is zlib — no GPL/AGPL. |
| 9 | Linux LVGL variant is not regressed. | `./packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl -c 'import lvgl as lv; print("ok")'` → `ok`. |
| 10 | Windows cli and webview variants still build and their gates pass. | `build-runtime.sh --target windows-x64 --variant cli` exits 0; `build-runtime.sh --target windows-x64 --variant webview` exits 0; `bash tests/phase-10/run.sh --skip-build` exits 0. |
| 11 | Frozen manifest for Windows lvgl is unique — freezes `picolet_ui`, not `picolet_ui_win`. | `cat packages/picolet-runtime/manifests/manifest_lvgl_windows.py` — contains `freeze("../python", "picolet_ui")`. Does not contain `picolet_ui_win`. |
| 12 | `asyncio` import succeeds. | `./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe -c 'import asyncio; print("aio-ok")'` → `aio-ok`. |
| 13 | MXE SDL2 cache gate: second build invocation skips SDL2 rebuild. | Second `build-runtime.sh --target windows-x64 --variant lvgl` completes without re-running `make -C /usr/src/mxe sdl2`. Log shows `sdl2: MXE build cached; skipping`. |
| 14 | The Windows LVGL binary contains no LVGL debug symbols (`LV_USE_LOG=0`). | `strings packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \| grep -c "lv_log"` → 0. (PICOLET_LVGL_DEBUG=1 build path tested separately if needed.) |
| 15 | `lv_conf.h` used is the picolet overlay copy (PICOLET_LVGL_CONFIG token). | Build log or `strings` shows `PICOLET_LVGL_CONFIG` in the binary, OR build output prints `LV_CONF_PATH is .../overlay/ports/unix/variants/picolet-lvgl/lv_conf.h` (emitted by the binding's `$(info ...)` in `micropython.mk`). |

Gates 2, 3, 5–7 close FR-LV-{1,2,3,4} (Windows half) and FR-RT-2
(Windows lvgl). Gate 4 closes NFR-3. Gate 8 closes NFR-5. Gates 9, 10
protect PH11, PH04, and PH10 from regression.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-LV-{1,2,3,4}, FR-RT-2, NFR-3, NFR-5, NFR-9 normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH12 | Deliverables, exit gate, model tiers. |
| `/home/anl/picolet/CLAUDE.md` | Branch, commit, dev-log, escalation policy. |
| `/home/anl/picolet/docs/phases/PHASE_11_lvgl-renderer-linux.md` | All design decisions and implementation; PH12 inherits the submodule, lv_conf.h, Python facade, pump, InProcessTransport, and manifest shape. |
| `/home/anl/picolet/docs/phases/PHASE_04_picolet-runtime-windows-x64-cli.md` | Windows port structure, vfs_rom_ioctl.c hook, dockcross build flow, `build_windows_x64()` shape. |
| `/home/anl/picolet/docs/phases/PHASE_10_webview-renderer-windows.md` | Windows variant config pattern, `picolet_ui_win` structure, `manifest_webview_windows.py` shape (for the manifest analogue in PH12). |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.{h,mk}` | Direct model for the Windows variant; PH12 forks both files with minimal changes. |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-webview/mpconfigvariant.mk` | Windows-port mk pattern: `SRC_C` handling, `LDFLAGS`, `LIB`, Windows-specific linker flags. |
| `packages/picolet-runtime/overlay/lib/lv_binding_micropython/micropython.mk` | Confirmed: SDL2 detection is gated on `$(notdir $(CURDIR)) == unix`; PH12 injects SDL2 flags directly in the variant `.mk` (AD2). |
| `packages/picolet-runtime/scripts/build-runtime.sh` | `build_windows_x64()` function as the model; PH12 extends the `windows-x64/lvgl` branch stub at line 103. |
| `packages/picolet-runtime/manifests/manifest_lvgl.py` | PH12's `manifest_lvgl_windows.py` is identical with a Windows comment header. |
| `packages/picolet-runtime/python/picolet_ui/_lvgl.py` | Confirmed: calls only `lv.init()`, `lv.sdl_window_create()`, `sys.stderr.write()` — all platform-agnostic. |
| `dockcross/windows-static-x64-posix` container | Inspected: MXE at `/usr/src/mxe`, SDL2 recipe at `/usr/src/mxe/src/sdl2.mk` (version 2.26.2), no pre-built SDL2 in the container image. MXE target: `x86_64-w64-mingw32.static.posix`. |

### Files to create

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/mpconfigvariant.h` | Windows lvgl variant feature config. Forked from the unix lvgl `mpconfigvariant.h`. The macro set is identical to the Windows cli/webview variants. Cannot include `../mpconfigvariant_common.h` (Windows port has no such file). Manually lists all macros from the unix file as direct `#define` statements. Adds `MICROPY_VFS_ROM_TRAILER`, `MICROPY_ENABLE_SCHEDULER`, size-reduction macros. Omits unix-only macros (`MICROPY_USE_READLINE_HISTORY` etc. as guarded in PH04's config plan). |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/mpconfigvariant.mk` | Windows lvgl variant Make config. Sets `MICROPY_PY_FFI=1`. Points `FROZEN_MANIFEST` at `manifest_lvgl_windows.py`. Sets `USER_C_MODULES = $(PICOLET_RUNTIME_ROOT)/overlay/lib`. Sets `LV_CONF_PATH = $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-lvgl/lv_conf.h` (reusing the Linux lv_conf.h). Injects SDL2 headers/libs from MXE (see "mpconfigvariant.mk detail" below). Sets `MICROPY_SDL=1` via `CFLAGS_EXTRA` so `lv_conf.h` enables `LV_USE_SDL`. Carries forward `LV_CFLAGS = -include .../lv_drivers.h` from PH11. |
| `packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/romfs_trailer.c` | Symlink (or `SRC_C +=` reference) to the shared trailer. The Windows Makefile's `$(wildcard $(VARIANT_DIR)/*.c)` picks up `.c` files in the variant dir. The cleanest approach is `SRC_C += $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c` in the variant `.mk` (same pattern as PH04). Do not copy the file. |
| `packages/picolet-runtime/manifests/manifest_lvgl_windows.py` | New frozen manifest for the Windows lvgl variant. Identical content to `manifest_lvgl.py` — freezes `picolet` and `picolet_ui`. Comment header notes it is for `windows-x64`. The `lv` C module enters via `USER_C_MODULES`. |
| `tests/phase-12/fixtures/hello-lvgl-win-min/picolet.toml` | Gate 6 fixture: `[app]`, `[ui] renderer="lvgl"`, `[window] title="PH12 Sanity" size=[800,600]`. Same shape as PH11's `hello-lvgl-min-e2e/picolet.toml`. |
| `tests/phase-12/fixtures/hello-lvgl-win-min/src/main.py` | Gate 6 fixture app: `import picolet_ui; picolet_ui.run()`. Self-terminating after 30 ticks (same as PH11). Prints `PICOLET_LV_SANITY_OK` on exit. |
| `tests/phase-12/run.sh` | Tester harness covering gates 1–15. Mirrors `tests/phase-11/run.sh`. |

### Files to modify

| Path | Change |
|---|---|
| `packages/picolet-runtime/scripts/build-runtime.sh` | Replace the `windows-x64/lvgl` error stub (line 103–105) with a real branch that calls `build_windows_x64` (the existing function — `VARIANT=lvgl` is already set from `--variant lvgl`). Add an MXE SDL2 build step inside `build_windows_x64` before the port build: check for `/usr/src/mxe/usr/x86_64-w64-mingw32.static.posix/lib/libSDL2.a`; if absent, run `docker run --rm -v /usr/src/mxe:/usr/src/mxe "$DOCKCROSS_IMAGE" make -C /usr/src/mxe sdl2 MXE_TARGETS=x86_64-w64-mingw32.static.posix` without the `--user` flag (MXE writes to `/usr/src/mxe` inside the container, which requires root in the container but does not affect the host). Log `[PH12] sdl2 MXE build complete`. The MXE state persists inside the container layer; a fresh container pull resets it. |
| `packages/picolet-runtime/overlay/ports/windows/vfs_rom_ioctl.c` | No change needed. PH04 already added the `MICROPY_VFS_ROM_TRAILER` block. |
| `packages/picolet-cli/picolet/build_cmd.py` | Extend the renderer→variant allowlist to include `lvgl` for `windows-x64` if it currently rejects it. PH04 already expanded the target guard to `{linux-x64, windows-x64}`; PH10 added webview. PH12 ensures `lvgl` is not explicitly excluded for Windows. One-line change or already covered — verify and widen if needed. |

### `mpconfigvariant.mk` detail (Windows lvgl)

```make
# Lean variant for the picolet lvgl runtime (windows-x64, PH12).
#
# Forked from overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.mk.
# Deltas from the Linux version:
#   - MICROPY_STANDALONE is not set (Windows Makefile handles libffi via deplibs).
#   - FROZEN_MANIFEST points at manifest_lvgl_windows.py.
#   - SDL2 cflags and ldflags are injected directly (MXE static path).
#   - LV_CONF_PATH reuses the Linux lv_conf.h (content is platform-agnostic).

MXE_ROOT := /usr/src/mxe/usr/x86_64-w64-mingw32.static.posix

MICROPY_PY_FFI = 1
# Do NOT set MICROPY_STANDALONE — Windows Makefile's deplibs rule handles libffi.

FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_lvgl_windows.py

USER_C_MODULES = $(PICOLET_RUNTIME_ROOT)/overlay/lib

LV_CONF_PATH = $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-lvgl/lv_conf.h

# Tell lv_conf.h to enable LV_USE_SDL (guarded on MICROPY_SDL there).
CFLAGS_EXTRA += -DMICROPY_SDL=1

# SDL2 include path from MXE static build.
CFLAGS_EXTRA += -I$(MXE_ROOT)/include/SDL2

# SDL2 static lib + Win32 dependencies SDL2 needs.
# SDL2 on Windows calls into user32 (window creation), winmm (timer), ole32
# (COM init), imm32 (IME input), version, setupapi.
LIB += $(MXE_ROOT)/lib/libSDL2.a
LIB += -luser32 -lwinmm -lgdi32 -lole32 -limm32 -lversion -lsetupapi

# LV_CFLAGS: force the binding's CPP preprocessing step to include
# lv_drivers.h so SDL window/input functions appear in the generated
# lv_mpy.c (PH11 workaround, same as Linux).
LV_CFLAGS = -include $(PICOLET_RUNTIME_ROOT)/overlay/lib/lv_binding_micropython/lvgl/src/drivers/lv_drivers.h

# romfs_trailer.c is in the unix cli variant directory; reference it explicitly
# (Windows Makefile's $(wildcard $(VARIANT_DIR)/*.c) won't find it there).
SRC_C += $(PICOLET_RUNTIME_ROOT)/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c
```

### `mpconfigvariant.h` detail (Windows lvgl)

Forked from `overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.h`.
The Windows port has no `mpconfigvariant_common.h`, so all macros are
inlined directly. Match the macro set from PH04's Windows cli variant
but add the LVGL-specific additions:

```c
// Lean variant for the picolet lvgl runtime (windows-x64, PH12).
// Forked from overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.h.
// Cannot include ../mpconfigvariant_common.h (Windows port has no such file).
// Macro set mirrors the unix lvgl variant's effective values.

#define MICROPY_GC_SPLIT_HEAP               (1)
#define MICROPY_GC_SPLIT_HEAP_ADD           (1)
#define MICROPY_VFS_ROM_TRAILER             (1)
#define MICROPY_DEBUG_PRINTERS              (0)
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_WARNINGS                    (0)
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)
#define MICROPY_MEM_STATS                   (0)
#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)
#define MICROPY_PY_SYS_ATEXIT               (0)
#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)
#define MICROPY_ENABLE_COMPILER             (1)
// LVGL binding needs the scheduler; on by default in EXTRA_FEATURES
// but confirmed here for explicitness.
#ifndef MICROPY_ENABLE_SCHEDULER
#define MICROPY_ENABLE_SCHEDULER            (1)
#endif
```

### Sequence the developer follows

All commands from `/home/anl/picolet` on branch `dev`.

**1. Record architecture decisions.**
```bash
git commit --allow-empty -s -m "[PH12] Decision: MXE sdl2 build for Windows static link"
# Body covers AD1: MXE vs download vs from-source, static vs dynamic.
git commit --allow-empty -s -m "[PH12] Decision: reuse picolet_ui unchanged; manifest_lvgl_windows.py"
# Body covers AD3: no picolet_ui_win/_lvgl.py needed.
```

**2. Create the Windows variant directory and files.**
```bash
mkdir -p packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl
```
Write `mpconfigvariant.h` per the detail above.
Write `mpconfigvariant.mk` per the detail above.
Do NOT create a `romfs_trailer.c` in this directory — reference the
unix one via `SRC_C +=` in the `.mk`.

**3. Create `manifest_lvgl_windows.py`.**
Copy `manifests/manifest_lvgl.py` with a comment header noting
`windows-x64, PH12`. Content is identical.

**4. Extend `build-runtime.sh`.**
Replace the `windows-x64/lvgl` error stub (line ~103) with:
```bash
windows-x64/lvgl)
    # PH12: SDL2 Windows backend via MXE.
    ;;
```
(The existing `windows-x64/*` → `build_windows_x64` dispatch at the
bottom of the script already routes to `build_windows_x64` when
`$TARGET == windows-x64`. The stub was blocking entry to the case block;
removing it unblocks the flow to `build_windows_x64`.)

Inside `build_windows_x64`, before step `[6/8]`, add an MXE SDL2 step:
```bash
echo "[5b/8] Ensuring MXE SDL2 static library"
MXE_SDL2_LIB="/usr/src/mxe/usr/x86_64-w64-mingw32.static.posix/lib/libSDL2.a"
if [[ "$VARIANT" == "lvgl" ]] && [[ ! -f "$MXE_SDL2_LIB" ]]; then
    echo "  SDL2 not present in MXE; building via 'make sdl2' inside dockcross"
    echo "  (first-time build; takes ~5 min; result cached in container layer)"
    docker run --rm \
        -v /usr/src/mxe:/usr/src/mxe \
        "$DOCKCROSS_IMAGE" \
        make -C /usr/src/mxe sdl2 \
            MXE_TARGETS="x86_64-w64-mingw32.static.posix" \
            -j "$(nproc)"
    if [[ ! -f "$MXE_SDL2_LIB" ]]; then
        echo "error: MXE SDL2 build failed; $MXE_SDL2_LIB not found" >&2
        exit 1
    fi
    echo "  SDL2 MXE build complete"
else
    echo "  sdl2: MXE build cached; skipping"
fi
```

**5. Rebuild the integration branch.**
```bash
./packages/picolet-runtime/scripts/rebuild-integration.sh
```
Verify the Windows lvgl variant directory is present in the integration
branch's `ports/windows/variants/picolet-lvgl/`.

**6. Build the Windows lvgl runtime.**
```bash
bash packages/picolet-runtime/scripts/build-runtime.sh \
    --target windows-x64 \
    --variant lvgl
```
The first run will trigger the MXE SDL2 build (step [5b/8], ~5 min).
Subsequent runs skip it.

Expected final output:
```
=== Build complete: .../picolet-runtime-windows-x64-lvgl.exe ===
  size: NNNNN bytes (XX% of NFR-3 ceiling of 2097152 bytes)
```

**7. Verify gate 3 (`import lvgl as lv`).**
```bash
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import lvgl as lv; print("ok")'
# expect: ok
```
This is the first hard proof that USER_C_MODULES linked correctly under
MinGW. If it fails, the most likely cause is a missing `MICROPY_SDL=1`
define or wrong SDL2 include path.

**8. Verify gate 4 (NFR-3 size).**
```bash
wc -c packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe
```
If size > 2 097 152 bytes, iterate on `lv_conf.h` (same levers as
PH11: disable unused widgets, disable animations, disable logging).
Each tuning step is a commit:
```
[PH12] Note: lv_conf.h: disabled LV_USE_<X> for Windows; size <Y>kB -> <Z>kB.
```

**9. Gate 8 (no SDL2.dll in import table).**
```bash
objdump -p packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    | grep DLL
```
Expected: only `bcrypt.dll`, `KERNEL32.dll`, `msvcrt.dll`, and
potentially `user32.dll`, `winmm.dll`, `gdi32.dll` (these are system
DLLs pulled in by SDL2's static glue layer). `SDL2.dll` must NOT appear.

**10. Gate 5 (SDL2 window opens under WSL interop).**
Run the test with Windows display available:
```bash
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import picolet_ui._test as t; t.run_lvgl_sanity_test()'
```
Expected stdout: `PICOLET_LV_SANITY_OK size=800x600 label=Hello,World`.
This requires running under WSL interop with a Windows host display
(or via Windows Terminal / WSLg). Unlike Linux, there is no `xvfb`
fallback — the test runs on the real Windows display.

**11. Gate 7 (IPC probe).**
```bash
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import picolet_ui._test as t; t.run_ipc_probe()'
```
Expected stdout: `PICOLET_LV_IPC_OK greet=hello,world`.

**12. Gate 6 (end-to-end build).**
```bash
mkdir -p tests/phase-12/fixtures/hello-lvgl-win-min/src
# Write picolet.toml and src/main.py as per the fixture spec above.
picolet build --target windows-x64 \
    tests/phase-12/fixtures/hello-lvgl-win-min/
./tests/phase-12/fixtures/hello-lvgl-win-min/target/windows-x64/hello-lvgl-win-min.exe
```
Expected stdout: `PICOLET_LV_SANITY_OK`.

**13. Non-regression checks.**
```bash
./packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl \
    -c 'import lvgl as lv; print("ok")'
bash tests/phase-11/run.sh --skip-rebuild
bash tests/phase-10/run.sh --skip-build
bash packages/picolet-runtime/scripts/build-runtime.sh \
    --target windows-x64 --variant cli
```
All must exit 0.

**14. Write and run the test harness.**
Create `tests/phase-12/run.sh`. Mirror `tests/phase-11/run.sh` with
Windows-specific gate verifications. Include the `--skip-build` flag
for the tester (runtime artifact already present).

**15. Commit.**
```bash
git add packages/picolet-runtime/overlay/ports/windows/variants/picolet-lvgl/ \
        packages/picolet-runtime/manifests/manifest_lvgl_windows.py \
        packages/picolet-runtime/scripts/build-runtime.sh \
        tests/phase-12/
git commit -s -m "[PH12] Add windows-x64 lvgl variant and SDL2 via MXE

Closes: FR-LV-1 (Windows), FR-LV-2 (Windows), FR-LV-3 (Windows),
FR-LV-4 (Windows), FR-RT-2 (Windows lvgl), NFR-3 (Windows)."
```

### Verification commands (tester)

```bash
# Build the runtime (skip if artifact already present)
bash packages/picolet-runtime/scripts/build-runtime.sh \
    --target windows-x64 --variant lvgl

# Gate 2: artifact present
test -f packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe

# Gate 3: import lvgl
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import lvgl as lv; print("ok")'

# Gate 4: NFR-3 size
wc -c packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe

# Gate 8: no SDL2.dll import (static linkage)
objdump -p packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    | grep DLL | grep -iv sdl2 || true

# Gate 5: SDL2 window (requires Windows host display via WSL interop)
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import picolet_ui._test as t; t.run_lvgl_sanity_test()'

# Gate 7: IPC probe
./packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe \
    -c 'import picolet_ui._test as t; t.run_ipc_probe()'

# Non-regression
./packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl \
    -c 'import lvgl as lv; print("ok")'

# Full gate harness
bash tests/phase-12/run.sh
```

### Foreseeable risks

**Risk 1: MXE SDL2 build requires network / long build time.**
The one-time MXE `make sdl2` step requires network access to download
the SDL2 tarball (~4 MB) and build time of roughly 3–5 minutes. The
build output is written inside the container's `/usr/src/mxe` filesystem
layer and does not persist when the container image is pulled fresh.

Mitigation: the build step is gated on `[[ ! -f $MXE_SDL2_LIB ]]`.
A warm container from a prior session re-uses the built library without
re-downloading. For fully offline CI, Option B (pre-downloaded
SDL2-devel-mingw tarball) is the documented fallback; the build script
can be switched to Option B by replacing the MXE step with an archive
extraction from a vendored location. Record this as a `[PH12] Caveat:`
commit for the CI team.

**Risk 2: SDL2 static Win32 dependency list is incomplete.**
SDL2 statically linked on Windows needs several Win32 DLLs that may not
all be listed in the variant `.mk`'s `LIB +=` line. The full set
(from upstream SDL2 CMake and pkg-config output) is typically:
`user32`, `gdi32`, `winmm`, `imm32`, `ole32`, `oleaut32`, `version`,
`uuid`, `advapi32`, `setupapi`, `shell32`. Missing entries produce
undefined-symbol linker errors with MinGW's static linker.

Mitigation: start with the set documented in AD1 above. If the build
produces `undefined reference to ...` errors, add the missing `-l` flag.
Each addition is logged as a `[PH12] Note: add -l<lib> for SDL2 static deps`.
The `strings` / `objdump` verification confirms only system DLLs appear
in the import table.

**Risk 3: `lv_binding_micropython` CPP cross-preprocessor mismatch.**
The `gen_mpy.py` code-gen step uses `$(CPP)` which inside dockcross is
`x86_64-w64-mingw32.static.posix-cpp`. The `-include lv_drivers.h`
workaround (PH11 Risk 1 and Finding 1) must still work with the MinGW
CPP. MinGW's CPP is GCC-based and accepts `-include`; no change is
expected. However, `__MINGW32__` being defined by the MinGW preprocessor
may activate Windows-specific code paths inside LVGL headers (e.g.
`#ifdef _WIN32` guards in `lv_types.h`) that pycparser's fake-libc stubs
do not cover.

Mitigation: if `gen_mpy.py` fails with a parse error, add the needed
macro to the fake-libc stub or to the `-D` flags passed to the CPP step.
The `pycparser/utils/fake_libc_include` directory already handles most
stdint/stddef; MinGW-specific types like `__int64` may need a stub.
This is the single most uncertain compile-time risk.

**Risk 4: NFR-3 size budget on Windows.**
The Windows binary is typically 50–150 KB larger than an equivalent Linux
binary due to PE-COFF overhead, Windows C runtime startup, and MinGW
runtime support code. PH11's Linux lvgl binary was 1,646,952 bytes (78%
of the 2 MiB ceiling). Adding ~100 KB would bring the Windows binary to
roughly 1,750 KB (83% of ceiling), still within budget.

Mitigation: same levers as PH11 (disable unused `LV_USE_*`). The 2 MiB
ceiling in `build-runtime.sh`'s `CEILING` table is already correct for
`lvgl`; the gate fires automatically.

**Risk 5: SDL2 static library symbol conflicts with MinGW CRT.**
SDL2 uses `_beginthreadex` and other MSVCRT functions. The MXE build
links against MinGW's static CRT, which includes these. Duplicated symbol
errors (`multiple definition of...`) can occur if the port Makefile also
links the CRT statically.

Mitigation: inspect the dockcross/windows-static-x64-posix port Makefile
for `LDFLAGS` and ensure there is no double-CRT linkage. The webview
variant (PH10) already links COM / Win32 DLLs successfully under the
same toolchain, providing confidence that the toolchain handles Windows
library linkage correctly.

**Risk 6: `run_lvgl_sanity_test` under WSL interop requires a display.**
Unlike PH11's `xvfb-run` path, the Windows `.exe` runs natively and
needs a real Windows display (X server or WSLg). If the test host has no
display (headless CI), gate 5 cannot be run interactively.

Mitigation: gate 5 is a mandatory manual gate (developer or tester runs
it on their workstation). A note in `run.sh` marks it `[MANUAL]` if no
Windows display is detected (check `DISPLAY` env or Windows-specific
SDL2 init error). Gates 3 (import lvgl), 4 (size), 7 (IPC probe), and 8
(static linkage) are fully headless and cover the critical paths.

### Out of scope for PH12

- **Linux LVGL changes** — PH11 is closed. PH12 must not regress it
  (gate 9).
- **SDL2.dll distribution** — not needed; SDL2 is statically linked.
  If a future phase switches to dynamic linkage (unlikely given AD1),
  the `picolet build` pipeline would need a DLL-copy step analogous to
  PH10's WebView2Loader.dll bundling.
- **SBOM** — PH13. SDL2 (zlib), LVGL (MIT), and lv_binding_micropython
  (MIT) are all accounted for in the SBOM design; PH12 adds a note
  that SDL2 is statically linked on Windows.
- **CI release pipeline** — PH15. The `build-runtime.sh` command in
  PH12 is the same command PH15's matrix uses.
- **`hello-lvgl` template registration in `picolet init`** — PH14.
- **Windows LVGL multi-window** — out of v1 scope.
- **LV_USE_WINDOWS native driver** — LVGL ships a Win32 GDI-based
  `lv_windows` driver (`lvgl/src/drivers/windows/`). This is out of
  scope; SDL2 is the cross-platform path per FR-LV-1 and PH11's AD2.

### Spec traceability

| Spec id | Gate(s) closing it | Notes |
|---|---|---|
| FR-LV-1 (Windows) | 5 | SDL2 desktop window opens on Windows via WSL interop. Static SDL2 from MXE. |
| FR-LV-2 (Windows) | 5, 6 | `[window]` from `picolet.toml` configures SDL2 display size + title; e2e build proves the path. |
| FR-LV-3 (Windows) | 3 | `import lvgl as lv` succeeds in the Windows lvgl runtime. |
| FR-LV-4 (Windows) | 7 | `InProcessTransport.pair()` round-trips `picolet.invoke` inside the Windows runtime. Same dispatcher as Linux (PH11). |
| FR-RT-2 (Windows lvgl) | 2, 10 | Build script grows a real branch; Windows cli + webview variants still build. |
| NFR-3 (Windows) | 4 | 2 MiB ceiling enforced by the build script's `CEILING` table. |
| NFR-5 | 8 | SDL2 (zlib) statically linked; no GPL/AGPL in link set. |
| NFR-9 | 8 | Static MinGW build; Windows system DLLs only in import table. |
| FR-BP-1 | 6 | `[ui] renderer = "lvgl"` resolves to `windows-x64/lvgl` runtime at build time. |
| FR-BP-6 | 6 | Submodule pinned by SHA; SDL2 from MXE at pinned version 2.26.2. |

PH12 closes FR-LV-1's Windows half (the Linux half closed in PH11).
PH12 does not add new FR-IPC-* coverage — those are exercised by the
`InProcessTransport` which is unchanged from PH11. PH12 does not touch
FR-WV-*, FR-CLI-*, FR-SBOM-*, NFR-1, NFR-2, NFR-4, NFR-6, NFR-7, NFR-8.
Gates 9, 10 protect PH11, PH10, and PH04 from regression.
