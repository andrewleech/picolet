# PH01 — picolet-runtime-linux-x64-cli

## Plan

### Goal (restated)

Produce the first downstream picolet runtime artifact: a single
self-contained Linux x86-64 executable, `picolet-runtime-linux-x64-cli`,
that

- embeds MicroPython composed from the PH00 integration branch;
- is built from a new `picolet-cli` variant of the **unix** port that
  strips it to roughly the same lean profile as `pydfu-win`;
- ships a frozen `manifest_cli.py` baseline (`asyncio`, `os-path`,
  `json`);
- mounts an embedded romfs at `/rom`, prepends `/rom` and `/rom/lib`
  to `sys.path`, runs a frozen `main.py` (or `/rom/main.py`) at
  startup, and exits with that script's exit code; and
- weighs no more than 1 MB on disk.

The artifact is produced by a new `packages/picolet-runtime/scripts/
build-runtime.sh` parameterised on target × variant so that PH04 can
extend it to Windows without restructuring.

PH01 is Linux-only. PH04 mirrors this onto windows-x64. PH02 and PH03
add the `picolet` CLI and the romfs/binary-glue pipeline that consumes
this artifact.

### Exit-gate-relevant requirements

| Spec id | What it requires | Where PH01 satisfies it |
|---|---|---|
| FR-RT-1 | Single executable embedding MicroPython, renderer modules for its variant, and romfs ioctl machinery. | `picolet-runtime-linux-x64-cli` is a single statically-linked unix-port binary with libffi linked in, the in-`main.c` romfs ioctl from PR #43, and (for `cli`) **no** renderer modules — the variant explicitly has none, per FR-RT-3. |
| FR-RT-3 | `cli` variant has no window, no webview, no LVGL. | The `picolet-cli` variant adds no native modules, no SDL, no GTK, no WebKit, no WebView2. Only frozen Python + libffi. |
| FR-RT-4 | `gc.add_heap()` is available in every variant. | `mpconfigvariant.h` sets `MICROPY_GC_SPLIT_HEAP=1` and `MICROPY_GC_SPLIT_HEAP_ADD=1`. PR #41 (`pr/gc-add-heap`) provides the implementation. |
| FR-RT-5 | `ffi` module is available in every variant. | `mpconfigvariant.mk` sets `MICROPY_PY_FFI=1` and `MICROPY_STANDALONE=1` so libffi is built from source and statically linked. The unix port already wires this in its `Makefile` at lines 170–194. |
| FR-RT-6 | Embedded romfs is auto-mounted at `/rom` and prepended to `sys.path`. | The integration branch (PR #43) does this in `ports/unix/main.c` lines 584–588 when `MICROPY_VFS_ROM && MICROPY_VFS_ROM_IOCTL` are on; both are on by default for non-minimal variants via `mpconfigvariant_common.h` lines 119–120. The variant inherits these. |
| FR-RT-7 | `main.py` or `main.mpy` in frozen modules or under `/rom/` is executed at startup. | Same PR #43 logic in `ports/unix/main.c` lines 620–681: frozen `main.py` first, then `/rom/main.py`, then `/rom/main.mpy`. Variant just needs to enable `MICROPY_MODULE_FROZEN_MPY` (already on in non-minimal variants) and not disable it. |
| FR-RT-8 | `sys.argv` is populated from the host command line. | The unix port already does this in `main.c` (look for `mp_obj_list_init(MP_OBJ_TO_PTR(mp_sys_argv), 0)` then the argv-append loop later) — but with `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS=0` pinned in `mpconfigport.h:155`. No variant change needed; just verify with a test that drops args. |
| NFR-1 | `picolet-runtime-{target}-cli` ≤ 1 MB on disk. | Stripped, `-Os`, terse error reporting, lean variant config. Reference: stock unix port is 866 KB on this host, pydfu-win windows binary is 654 KB with FFI on. The 1 MB ceiling is tight but achievable; we leave headroom by disabling unneeded modules and `MICROPY_ERROR_REPORTING_TERSE`. |

### Exit gate

| # | Condition | Verification method |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` still exits 0 after the overlay is added. | Re-run on a freshly cleaned submodule clone; expect 0 exit, last `Apply picolet overlay` step replaces the previous "overlay directory is empty — skipping" branch. |
| 2 | The variant builds with `make -C ports/unix -j VARIANT=picolet-cli`. | `make -j` completes 0 from `packages/picolet-runtime/micropython` after `scripts/build-runtime.sh --target linux-x64 --variant cli` is run. Produces `ports/unix/build-picolet-cli/micropython`. |
| 3 | The build script produces `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` and that file is executable. | `test -x packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`. |
| 4 | The binary, run with no arguments, executes a frozen `main.py` from a test romfs and prints `ok`. | The build script also assembles a tiny test romfs containing a `main.py` that calls `print("ok"); sys.exit(0)` and links it in via `ROMFS_IMG=`. `./picolet-runtime-linux-x64-cli` returns `ok` on stdout and exit 0. |
| 5 | `gc.add_heap` is callable. | `./picolet-runtime-linux-x64-cli -c 'import gc; gc.add_heap(bytearray(4096)); print("heap-ok")'` prints `heap-ok`. (`-c` flag still works because `MICROPY_ENABLE_COMPILER` stays on for the cli variant — see "Variant config plan" below for the rationale.) |
| 6 | `ffi` import succeeds. | `./picolet-runtime-linux-x64-cli -c 'import ffi; print("ffi-ok")'` prints `ffi-ok`. |
| 7 | `asyncio` import succeeds (from frozen manifest). | `./picolet-runtime-linux-x64-cli -c 'import asyncio; print("aio-ok")'` prints `aio-ok`. |
| 8 | `json` import succeeds (from C built-in, not from manifest — see below). | `./picolet-runtime-linux-x64-cli -c 'import json; print(json.dumps({"a":1}))'` prints `{"a": 1}`. |
| 9 | `os.path` import succeeds (from frozen manifest). | `./picolet-runtime-linux-x64-cli -c 'import os.path; print(os.path.join("a","b"))'` prints `a/b`. |
| 10 | `sys.argv` round-trip works. | `./picolet-runtime-linux-x64-cli foo bar -c 'import sys; print(sys.argv)'` — handle `-c` precedence per `main.c` arg parsing; the deterministic shape of `sys.argv` for the cli variant gets locked in here by an SQE-authored fixture, not by FR text. |
| 11 | Romfs auto-mount: `/rom/main.py` runs when no frozen `main.py` exists. | A second test romfs without the frozen `main.py` runs `/rom/main.py` instead and prints `ok-from-rom`. This proves the FR-RT-7 fallback path, not just the frozen-first path. |
| 12 | NFR-1: binary size ≤ 1 MB stripped. | `test "$(wc -c < packages/picolet-runtime/build/picolet-runtime-linux-x64-cli)" -le 1048576`. The build script runs `strip` on the final artifact before the size check. |
| 13 | The binary depends only on Ubuntu 22.04 system libraries (NFR-8). | `ldd packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` lists only `libc`, `libm`, `libpthread`, `libdl`, and the dynamic linker. No `libwebkit2gtk`, no `libSDL2`, no `libffi` (because we statically link via `MICROPY_STANDALONE=1`). |
| 14 | Re-running `scripts/build-runtime.sh` is idempotent. | Two consecutive runs from a clean tree produce byte-identical output (modulo timestamps embedded by linker). At minimum, the second run completes without rebuilding everything from scratch. |

Gates 2–13 cover FR-RT-{1,3,4,5,6,7,8} and NFR-1 from the plan's
declared exit gate. Gates 1 and 14 protect the build pipeline itself so
PH02+ don't immediately regress what PH01 just landed.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-RT-{1,3,4,5,6,7,8} and NFR-1 normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH01 | Goal, deliverables, exit gate, model tiers. |
| `/home/anl/picolet/docs/architecture.md` | Runtime composition model, overlay layout, three-variant-per-platform decision (D4). |
| `/home/anl/picolet/CLAUDE.md` | Branch + commit + investigation-log conventions; build-and-test policy. |
| `/home/anl/picolet/docs/phases/PHASE_00_verify-mbm-baseline.md` | What PH00 delivered: integration branch + rerere cache, stock unix and windows ports green, `gc.add_heap`/`ffi` confirmed off in stock builds (gated on macros PH01 turns on). |
| `/home/anl/picolet/packages/picolet-runtime/README.md` | Overlay tree shape: `overlay/ports/unix/variants/picolet-cli/...`, `overlay/manifests/...`. |
| `/home/anl/picolet/packages/picolet-runtime/scripts/rebuild-integration.sh` lines 165–187 | Overlay-apply step: `find . -type f` from `overlay/` and `cp` into the submodule tree, then commit on the integration branch. PH01's overlay simply has to land in `overlay/` for this to pick it up. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/Makefile` lines 170–194, 257–275 | How `MICROPY_PY_FFI=1`, `MICROPY_STANDALONE=1`, and `ROMFS_IMG=` already wire through the unix port; nothing in PH01's variant needs to re-implement these. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/mpconfigport.h` | Baseline port config: `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS=0` is pinned here (line 155), so the variant cannot/should not flip it — unix's `main.c` populates `sys.argv` and `sys.path` directly. Confirms FR-RT-8 is satisfied without variant intervention. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/main.c` lines 584–681, 964–1057 | Romfs auto-mount, frozen-then-romfs main resolution, and the in-`main.c` `mp_vfs_rom_ioctl()` body that the unix port uses (no separate `vfs_rom_ioctl.c`, unlike windows). The variant MUST NOT set `MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1`. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/variants/mpconfigvariant_common.h` | Common variant baseline that the new variant `#include`s. Already turns on `MICROPY_VFS_ROM=1`, `MICROPY_VFS_ROM_IOCTL=1`, `MICROPY_PY_MACHINE=1`, `MICROPY_PY_WEBSOCKET=1`, `MICROPY_REPL_EMACS_WORDS_MOVE=1`, etc. — PH01 turns off the ones the cli baseline does not need. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/variants/standard/mpconfigvariant.{h,mk}` | The "everything on, frozen manifest = ssl + mip-cmdline" baseline; reference for the file shape and the `FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py` pattern. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/variants/minimal/mpconfigvariant.{h,mk}` | The "everything off, smallest possible" baseline; reference for which `MICROPY_PY_*` defaults are off in `mpconfigport.h` so the cli variant doesn't redundantly re-set them. The minimal variant also confirms the `mpconfigvariant.mk` knobs are real Make variables, not just `#define`s. |
| `/home/anl/pydfu-win/micropython/ports/windows/variants/pydfu/mpconfigvariant.h` | The lean-profile precedent. Two-thirds of its `#define`s carry across; the rest are unix-port specifics (e.g. unix already pins `MICROPY_ERROR_REPORTING_DETAILED` in `mpconfigvariant_common.h`, so the unix `picolet-cli` variant must override there, not in `mpconfigport.h`). |
| `/home/anl/pydfu-win/micropython/ports/windows/variants/pydfu/mpconfigvariant.mk` | `MICROPY_PY_FFI=1 MICROPY_ENABLE_COMPILER=0` precedent. The unix variant flips compiler back **on** — see "Variant config plan / compiler" below for the rationale departure. |
| `/home/anl/pydfu-win/micropython/tools/pydfu_app/manifest_frozen.py` | Frozen-manifest precedent. The picolet baseline manifest copies its `add_library("unix-ffi", "$(MPY_LIB_DIR)/unix-ffi")` + `require()` shape but lists `asyncio`, `os-path`, `json` instead of pydfu's `argparse`, `os-path`, `pydfu` etc. |
| `/home/anl/pydfu-win/micropython/tools/pydfu_app/main.py` | Heap-grow + romfs DLL extraction reference. The test `main.py` PH01 ships is much simpler — just enough to prove `print("ok")` runs and `sys.exit(N)` propagates. |
| `/home/anl/pydfu-win/scripts/build-windows.sh` | Dockcross build orchestration. PH01 is Linux-native (no docker needed), but the **shape** — separate steps for mpy-cross, submodules, optional romfs, then port build — carries through. PH04 will extend `build-runtime.sh` to add the dockcross-wrapped Windows branch. |
| `/home/anl/picolet/packages/picolet-runtime/manifests/` | Currently absent. PH01 creates this directory with `manifest_cli.py`. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/` | Currently absent. PH01 creates `overlay/ports/unix/variants/picolet-cli/` with the variant config files, and (later) `overlay/manifests/` if we decide to symlink — see "Manifest placement" below. |

### Files / scripts the developer will create or modify

#### New files

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Variant feature config: turn on `MICROPY_GC_SPLIT_HEAP`, `MICROPY_GC_SPLIT_HEAP_ADD`; turn off the unix common-variant's heavy default modules; pin terse error reporting. See "Variant config plan" below for the full list. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.mk` | Variant Make config: `MICROPY_PY_FFI=1`, `MICROPY_STANDALONE=1`, `FROZEN_MANIFEST=...`. The frozen-manifest path resolves to the in-overlay copy — see "Manifest placement". |
| `packages/picolet-runtime/manifests/manifest_cli.py` | Frozen manifest: `asyncio`, `os-path`. (No `json` — it's a built-in C module on the unix port; the manifest only needs to pull in non-built-ins.) |
| `packages/picolet-runtime/scripts/build-runtime.sh` | Top-level build orchestrator parameterised on `--target` × `--variant`. PH01 implements `--target linux-x64 --variant cli`; PH04 extends to `--target windows-x64`. |
| `packages/picolet-runtime/tests/phase-01/test_romfs/main.py` | Tiny test fixture: `print("ok"); import sys; sys.exit(0)`. Built into `romfs.img` by the build script's gate-4 test path. Lives outside `overlay/` so the integration branch is not polluted with test data. |
| `packages/picolet-runtime/tests/phase-01/test_romfs_no_frozen/main.py` | Second fixture: `print("ok-from-rom")` — exercises the `/rom/main.py` fallback path when no frozen main is present. Used by SQE for gate 11. |
| `packages/picolet-runtime/tests/phase-01/run-smoke.sh` | SQE-authored harness that runs gates 4–13 against the built binary. (Listed here for completeness; SQE role writes the body in the `## Tests` section.) |

#### Modified files

| Path | Change |
|---|---|
| `packages/picolet-runtime/scripts/rebuild-integration.sh` | None expected. The script already detects a populated `overlay/` and applies it (lines 165–187). PH01 just needs the overlay tree to exist. Listed here because if the SQE finds the cp loop mangles `mpconfigvariant.mk`'s tabs (it shouldn't — `cp -p` preserves bytes), the developer fixes it here. |
| `packages/picolet-runtime/micropython` submodule pointer | Bumped after `rebuild-integration.sh` lays the overlay commit on top of the integration branch. Committed in the parent repo with `git add packages/picolet-runtime/micropython && git commit -s`. |

#### Out-of-tree (no new files)

- The variant's C source lives entirely inside the existing unix port:
  `ports/unix/main.c`, `ports/unix/mpconfigport.h`, et al., which the
  PR #43 integration branch already configures correctly. **PH01 does
  not edit any `ports/unix/` C source.** Every PH01 customisation
  lives in the variant directory or the manifest.

- No `overlay/modules/picolet_*/` directories yet — those land in PH06
  (ipc) and PH07/PH11 (renderer modules). The cli variant ships none
  of them.

### Variant config plan

The unix port's defaults differ from the windows port's. The most
important deltas, with bearing on the cli variant:

| Macro | Unix port baseline | Windows port baseline (pre-PR-#44) | picolet-cli unix variant |
|---|---|---|---|
| `MICROPY_VFS_ROM` | 1 (in `mpconfigvariant_common.h:119`) | 0 (off, enabled per-variant) | leave at 1. |
| `MICROPY_VFS_ROM_IOCTL` | 1 (in `mpconfigvariant_common.h:120`) | enabled per-variant | leave at 1. |
| `MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL` | undefined → ioctl is in `main.c` | 1 → ioctl is in `vfs_rom_ioctl.c` | **leave undefined.** Setting it 1 breaks the unix build because `vfs_rom_ioctl.c` does not exist in the unix port tree. This is the primary divergence from the pydfu Windows precedent. |
| `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS` | 0 (pinned in `mpconfigport.h:155`) | 0 (pinned in `mpconfigport.h`) | leave at 0. Unix `main.c` already populates `sys.argv` and `sys.path` itself; the macro only matters on ports that don't have a `main.c`-side initialiser. FR-RT-8 is satisfied without it. |
| `MICROPY_GC_SPLIT_HEAP` | 0 default | 0 default | **1**. Required by `gc.add_heap()` (PR #41). |
| `MICROPY_GC_SPLIT_HEAP_ADD` | 0 default | 0 default | **1**. The runtime-add path. |
| `MICROPY_PY_FFI` | 0 default | 0 default | **1** (set in `.mk`, the unix port's `Makefile` lines 170–194 wires the rest). |
| `MICROPY_STANDALONE` | 0 default | n/a (windows always builds its own libffi) | **1** (set in `.mk`). Triggers libffi-from-source so the binary doesn't depend on system libffi at runtime, satisfying the "single executable" half of FR-RT-1 plus NFR-4. |
| `MICROPY_ENABLE_COMPILER` | 1 default | pydfu sets 0 | **1** (leave on). Departure from pydfu precedent: keeping the compiler on costs ~50–80 KB but enables `-c '<expr>'` and `eval()` in the cli, which the gate-4/5/6/7 smoke tests rely on, and which is useful for `picolet dev`'s REPL drop-down in PH16. We verify NFR-1 still holds with compiler on (stock unix is 866 KB → with libffi static and modules trimmed we have headroom). If size measurement after first build fails NFR-1 we revisit. |
| `MICROPY_ERROR_REPORTING` | DETAILED (in `mpconfigvariant_common.h:86`) | TERSE for pydfu variant | **TERSE**. The common header sets DETAILED; the variant overrides. Saves several KB of error message strings. |
| `MICROPY_WARNINGS` | 1 (common header line 87) | 0 for pydfu | **0**. |
| `MICROPY_PY_STR_BYTES_CMP_WARN` | 1 (common header line 88) | 0 for pydfu | **0**. |
| `MICROPY_REPL_EMACS_WORDS_MOVE` | 1 (common header line 58) | n/a | **0**. cli runtime won't host an interactive REPL beyond `-c`. |
| `MICROPY_REPL_EMACS_EXTRA_WORDS_MOVE` | 1 (line 59) | n/a | **0**. |
| `MICROPY_USE_READLINE_HISTORY` | 1 (line 60) | n/a | **0**. |
| `MICROPY_REPL_EMACS_KEYS` | (windows-specific) | 0 | leave at port default. |
| `MICROPY_PY_MACHINE` | 1 (common header line 115) | 0 for pydfu | **0**. |
| `MICROPY_PY_MACHINE_PULSE` | 1 (line 116) | 0 for pydfu | **0**. |
| `MICROPY_PY_MACHINE_PIN_BASE` | 1 (line 117) | 0 for pydfu | **0**. |
| `MICROPY_PY_WEBSOCKET` | 1 (line 112) | n/a | **0**. cli baseline has no websocket use case. |
| `MICROPY_PY_SYS_ATEXIT` | 1 (line 91) | 0 for pydfu | **0**. |
| `MICROPY_PY_SYS_EXC_INFO` | 1 (line 92) | n/a | **1** (keep — asyncio uses it for traceback chaining). |
| `MICROPY_MEM_STATS` | 1 (line 77) | 0 for pydfu | **0**. |
| `MICROPY_MALLOC_USES_ALLOCATED_SIZE` | 1 (line 76) | 0 for pydfu | **0**. |
| `MICROPY_DEBUG_PRINTERS` | 1 (line 36 of common, guarded by `#ifndef`) | 0 for pydfu | **0** (set before include of common to suppress). |
| `MICROPY_OPT_COMPUTED_GOTO` | 1 (line 80) | typically 0 on size-tuned | **1** (keep — perf > size here, the cost is single-digit KB). |
| `MICROPY_PY_BUILTINS_HELP` | (pdpfu sets 0) | 0 | **0** in variant. |
| `MICROPY_PY_BUILTINS_HELP_MODULES` | (pdpfu sets 0) | 0 | **0**. |
| `MICROPY_PY_BUILTINS_INPUT` | (pdpfu sets 0) | 0 | **0**. |
| `MICROPY_PY_BUILTINS_NOTIMPLEMENTED` | n/a | 0 | **0**. |
| `MICROPY_PY_DEFLATE`, `_DEFLATE_COMPRESS` | typically off in non-extra-features ports | 0 | **0**. |
| `MICROPY_PY_HASHLIB` | on in some configs | 0 for pydfu | **0**. We rely on the host (or future ssl frozen lib) for hashing; cli baseline doesn't need it. |
| `MICROPY_PY_RANDOM` | 1 (default in `extmod`) | 0 for pydfu | **leave on**. asyncio's `wait_for` doesn't use random, but it's tiny and several micropython-lib modules expect it. Could be disabled if size pressure demands. |
| `MICROPY_PY_RE` | on | 0 for pydfu | **leave on**. `os.path` patterns and asyncio occasionally touch it. (If size pressure: disable and re-measure.) |
| `MICROPY_PY_JSON` | on (built-in) | 0 for pydfu | **leave on**. FR — gate 8 imports `json`. |
| `MICROPY_PY_HEAPQ` | on | 0 for pydfu | **leave on**. asyncio uses `heapq` for its scheduler. Disabling breaks FR-RT-5 contract by extension because asyncio imports fail. |

The variant `.h` therefore:

1. **Pre-empts** the common header's settings by defining the relevant
   macros (with `#define`, not `#ifndef`-guard) **before** the
   `#include "../mpconfigvariant_common.h"` line — exactly the way the
   `standard` variant doesn't (it relies on defaults) and the way pydfu
   does (no include of common at all). The unix common header uses
   `#ifndef` for a handful of macros (`MICROPY_DEBUG_PRINTERS`,
   `MICROPY_FLOAT_IMPL`, etc.) so the variant can pre-empt those; the
   rest the variant overrides by re-`#define`-ing after the include.
2. **Sets `MICROPY_CONFIG_ROM_LEVEL`** to
   `MICROPY_CONFIG_ROM_LEVEL_CORE_FEATURES` (the bare-metal default in
   `mpconfigport.h:42`) rather than `EXTRA_FEATURES` which `standard`
   uses. That alone strips a notable chunk of optional builtins.
3. **Defines `MICROPY_GC_SPLIT_HEAP=1` and `MICROPY_GC_SPLIT_HEAP_ADD=1`**
   so the gc.add_heap symbol is built.

The variant `.mk`:

```make
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py
```

`PICOLET_RUNTIME_ROOT` is set by `build-runtime.sh` before invoking
`make`; it points at `packages/picolet-runtime` so the manifest path
resolves regardless of where the user invokes the build from.

**Why we don't disable the compiler.** pydfu does. The cost is
~50–80 KB and the benefit is that:
- The gate test fixtures (`-c '<expr>'`) work without a special host
  build, keeping CI simple.
- PH16 (`picolet dev`) wants a REPL or `-c` path for diagnostic dumps.
- Frozen-only builds prevent any future `import some_user_dyn_module`
  pattern from working on the cli artifact, which would surprise an
  end user. The cli variant is small enough without this knob being
  flipped.

If first measurement shows NFR-1 will not hold with compiler on, the
fallback is: disable compiler in the variant, and write the gate tests
to load pre-compiled `.mpy` test fixtures via the frozen manifest
instead of `-c`. Logged as a contingency, not the primary plan.

### Frozen manifest plan

`manifest_cli.py`:

```python
# Minimal frozen-manifest baseline for the picolet `cli` variant.
# Pulls in: asyncio (FR-IPC-5 prerequisite), os-path (used by user code).
# Does NOT pull in json — the unix port has it as a built-in C module.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

require("asyncio")   # FR-IPC-5: asyncio is the Python-side scheduler.
require("os-path")   # os.path module; consumed by user code routinely.
```

**Per-module justification:**

| Module | Why it's in the baseline | Could it be cut? |
|---|---|---|
| `asyncio` | Spec FR-IPC-5 makes asyncio "the Python-side scheduler" — every renderer variant (and the cli variant for parity) ships it. PH06's `picolet_ipc` will hook into it. cli apps that don't need it pay zero startup cost beyond the frozen bytes. | Technically yes for pure-cli apps that have no IPC, but parity across variants is a deliberate consequence of D3 in `architecture.md` and pulling it from the manifest later would be a breaking change for app authors. Keep. |
| `os-path` | Most apps assume `os.path.join`, `os.path.dirname`, etc. work. The unix port has `os` as a built-in but **not** `os.path` (it's a Python wrapper in `micropython-lib/python-stdlib/os-path/`). | Yes — but the cost is ~3 KB frozen bytes and we'd surprise users. Keep. |
| `json` | Spec doesn't require us to **freeze** json. The unix port has `json` as a built-in C module via `extmod/modjson.c` (gate 8 will confirm). | Don't include in the manifest. The v1-plan's mention of `json` in the deliverables list refers to "json is available", which is satisfied by the C built-in. Including it via micropython-lib would be redundant and increase frozen size for no functional gain. |

`add_library` calls register both `python-stdlib` and `python-ecosys`
because asyncio lives under `python-stdlib/asyncio` and we anticipate
future entries in this manifest needing `python-ecosys` (e.g. when the
SBOM phase wants a tomli-w shim). Adding both up front costs nothing
at build time and prevents PH02+ from having to edit this file just to
add a `require()`. If the SQE prefers to keep it minimal, drop the
`python-ecosys` line until PH06 needs it.

**No `unix-ffi` library here.** pydfu's manifest uses
`add_library("unix-ffi", ...)` because pyusb is a frozen
`unix-ffi`-style package. The cli baseline has no `ffi.*`-based frozen
modules, so the unix-ffi library reference is omitted. Apps that need
it can add it via their own app-level frozen extension at build time
(PH03 wires that path).

### Manifest placement

The picolet manifest lives at `packages/picolet-runtime/manifests/manifest_cli.py`,
not inside the submodule tree. This is deliberate:

- The manifest is downstream-only; we don't want it inside `overlay/`
  (which is a git-tracked drop-in to the submodule tree) because it
  would then get committed as a chunk of the integration branch every
  time `rebuild-integration.sh` runs. Manifests don't need to live on
  the submodule's git history.
- The `.mk` file references it via an absolute path resolved at build
  time. The build script exports `PICOLET_RUNTIME_ROOT=$(realpath
  packages/picolet-runtime)` before invoking `make`, and the `.mk` does
  `FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_cli.py`.
  This keeps the manifest a first-class picolet artifact, editable
  outside the submodule, while still readable to MicroPython's
  manifest discovery.

If during implementation this proves clunky (e.g. relative-path issues
inside the manifest's `module()` calls), the fallback is to put a
symlink inside `overlay/ports/unix/variants/picolet-cli/manifest.py`
pointing at the canonical file. The variant `.mk` then drops its
`FROZEN_MANIFEST` line and lets the port's default
(`FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py`) take over. Logged as
contingency.

### `build-runtime.sh` design

The script takes flags `--target`, `--variant`, and `--clean`, and:

1. Resolves repo root and `PICOLET_RUNTIME_ROOT`. Exports
   `PICOLET_RUNTIME_ROOT` for the manifest.
2. Validates `--target ∈ {linux-x64, windows-x64}` and
   `--variant ∈ {cli, webview, lvgl}`. PH01 only **implements**
   `--target linux-x64 --variant cli`; the other combos exit with
   "not implemented in PH01, see PH04/PH07/PH11" so PH04 only has to
   add a branch, not redesign the contract.
3. Ensures the submodule is on the integration branch and the rerere
   cache is in place (calls `rebuild-integration.sh` if `integration`
   does not exist or the user passed `--clean`). For idempotent
   re-runs in a warm tree, the rebuild is skipped (matches PH00's
   16-second idempotent warm-cache behaviour).
4. Builds mpy-cross natively for Linux:
   `make -C packages/picolet-runtime/micropython/mpy-cross -j`. (PH04
   will wrap this in `docker run dockcross/...` for the Windows
   target.)
5. Runs `make -C ports/unix submodules VARIANT=picolet-cli` to fetch
   libffi as a submodule of the integration branch (driven by
   `MICROPY_STANDALONE=1`).
6. Builds the test romfs (gate 4 fixture) into a temporary file:
   `python3 -m mpremote romfs build packages/picolet-runtime/tests/phase-01/test_romfs`
   and moves the resulting `.romfs` to a known build-tree path.
7. Builds the variant:
   ```
   make -C packages/picolet-runtime/micropython/ports/unix \
        -j \
        VARIANT=picolet-cli \
        ROMFS_IMG=$(realpath /path/to/test.romfs) \
        PICOLET_RUNTIME_ROOT=$(realpath packages/picolet-runtime)
   ```
   The port's existing Makefile turns the `ROMFS_IMG=` into a
   `MICROPY_ROMFS_EMBEDDED=1` define plus an objcopy of the .romfs
   payload into a `romfs_data.o` linked into the final binary.
8. Strips the resulting binary:
   `strip --strip-unneeded ports/unix/build-picolet-cli/micropython`.
9. Copies it to the canonical output path:
   `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`.
10. Runs gate 12 (size check) right there: fail loudly if NFR-1 is
    violated, so the developer sees the failure before the SQE/tester
    does. `wc -c` ≤ 1048576 (1 MiB; 1024×1024). If we want decimal MB
    (1,000,000), the spec ambiguity is resolved here in favour of MiB
    (binary MB — matches typical ELF size reporting).
11. Prints the size and exits 0.

The script is bash, `set -euo pipefail`, no parallelism beyond `make
-j`, no docker (Linux-native). PH04's Windows mirror will introduce a
docker-wrapped `make` invocation and a separate `objcopy` flag block
mirroring `pydfu-win/scripts/build-windows.sh`'s pattern; the cli
variant's `mpconfigvariant.h` is reused unchanged (windows port has
its own `picolet-cli` variant directory PH04 creates).

**Test-romfs handling.** The cli variant's exit-gate tests need a
romfs containing `main.py`. The build script bakes a tiny test romfs
in by default so the artifact PH02 inherits is testable on its own.
**This is a PH01 quirk** — once PH03 has the romfs-build pipeline,
the picolet-runtime artifact is meant to be shipped **without** an
embedded romfs (the romfs gets appended later by `picolet build`).
PH01 uses the embedded-romfs path because PH03 doesn't exist yet; the
tester confirms the artifact runs end-to-end without needing further
build glue. Logged in the phase notes for PH03 to undo.

### Sequence the developer follows

All from `/home/anl/picolet` on `dev`.

1. **Confirm PH00 still green.** Re-run
   `./packages/picolet-runtime/scripts/rebuild-integration.sh` once,
   verify it ends with `Integration rebuilt at <sha>` and overlay
   step says `(overlay directory is empty — skipping)`. If anything
   regressed, stop and surface — PH01 cannot proceed without PH00's
   green floor.

2. **Lay down the overlay tree:**
   ```
   mkdir -p packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli
   ```
   Write `mpconfigvariant.h` and `mpconfigvariant.mk` per the table in
   "Variant config plan" above.

3. **Lay down the manifest:**
   ```
   mkdir -p packages/picolet-runtime/manifests
   ```
   Write `manifest_cli.py` per the "Frozen manifest plan" above.

4. **Lay down the test fixtures:**
   ```
   mkdir -p packages/picolet-runtime/tests/phase-01/test_romfs
   mkdir -p packages/picolet-runtime/tests/phase-01/test_romfs_no_frozen
   ```
   Write `main.py` in each (single-line `print("ok")` /
   `print("ok-from-rom")` respectively).

5. **Write `scripts/build-runtime.sh`** per the design above.
   `chmod +x` it.

6. **Re-run `rebuild-integration.sh`.** Confirm it now picks up the
   overlay (step `[3/3] Apply picolet overlay` should commit the
   variant files onto the integration branch). Bump the submodule
   pointer in the parent repo.

7. **Run `./packages/picolet-runtime/scripts/build-runtime.sh
   --target linux-x64 --variant cli`.** Walk the build log for
   surprises (libffi configure output is verbose but expected).
   Confirm the artifact lands at
   `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`.

8. **Spot-check the gates locally:**
   ```
   ./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
   # expect: ok
   ./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import ffi; import gc; import asyncio; print("ok")'
   # expect: ok
   wc -c packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
   # expect: ≤ 1048576
   ```

9. **Commit on `dev` in two or three small commits** per CLAUDE.md:
   - `[PH01] Add picolet-cli unix variant baseline.` (overlay tree +
     manifest)
   - `[PH01] Add build-runtime.sh for linux-x64/cli.` (build script +
     test fixtures)
   - `[PH01] Bump picolet-runtime submodule pointer.` (after rebuild)

   Each commit signed with `-s`, body cites the FR ids closed.

10. **If size exceeds 1 MB,** disable the compiler in the variant
    `.mk` (`MICROPY_ENABLE_COMPILER = 0`) and refactor the gate
    fixtures to use frozen `.mpy` test bytecode instead of `-c`.
    Log the size-vs-features trade as a `[PH01] Decision:` empty
    commit before adjusting.

### Verification commands the SQE / tester will run

Gates 1, 14 — pipeline reproducibility:

```
git -C packages/picolet-runtime/micropython clean -xfd
./packages/picolet-runtime/scripts/rebuild-integration.sh
./packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli
./packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli  # second run
```

Gates 2, 3:

```
test -x packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
file packages/picolet-runtime/build/picolet-runtime-linux-x64-cli | grep -q "ELF 64-bit"
```

Gate 4 — frozen main.py runs and exits 0:

```
test "$(./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli)" = "ok"
```

Gates 5, 6, 7, 8, 9:

```
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import gc; gc.add_heap(bytearray(4096)); print("heap-ok")'
# expect: heap-ok
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import ffi; print("ffi-ok")'
# expect: ffi-ok
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import asyncio; print("aio-ok")'
# expect: aio-ok
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import json; print(json.dumps({"a":1}))'
# expect: {"a": 1}
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import os.path; print(os.path.join("a","b"))'
# expect: a/b
```

Gate 10 — argv (uses a fixture with a flag-less arg pattern):

```
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import sys; print(sys.argv)' arg1 arg2
# expect: ['-c', 'arg1', 'arg2']    (per unix port main.c argv handling)
```

Gate 11 — romfs-main fallback. Re-build with the `test_romfs_no_frozen`
fixture as `ROMFS_IMG` source:

```
./packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli --test-romfs test_romfs_no_frozen
test "$(./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli)" = "ok-from-rom"
```

Gate 12 — NFR-1 size:

```
size=$(wc -c < packages/picolet-runtime/build/picolet-runtime-linux-x64-cli)
test "$size" -le 1048576
echo "size: $size bytes ($(( size * 100 / 1048576 ))% of NFR-1 ceiling)"
```

Gate 13 — Ubuntu 22.04 NFR-8 dependency profile:

```
ldd packages/picolet-runtime/build/picolet-runtime-linux-x64-cli | \
    grep -vE 'linux-vdso|libc\.so|libm\.so|libpthread\.so|libdl\.so|libgcc_s\.so|ld-linux'
# expect: no output (everything else dynamic-linked is forbidden for the cli variant)
```

The tester re-runs all the above on a fresh checkout, and additionally
performs a containerised Ubuntu 22.04 cross-check by mounting the
artifact into a vanilla `ubuntu:22.04` image and re-running gate 4 —
this protects NFR-8 explicitly:

```
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" --user "$(id -u):$(id -g)" \
    ubuntu:22.04 \
    ./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
# expect: ok
```

(The CLAUDE.md global rule for ephemeral docker containers is followed
here — `--user`, `-v`, `-w` shape.)

### Foreseeable risks

| Risk | Likelihood | Impact | Mitigation / response |
|---|---|---|---|
| **The unix port has the romfs ioctl baked into `main.c`, not a separate `vfs_rom_ioctl.c`.** A developer copying the pydfu variant pattern verbatim will set `MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL=1` and break the build. | high — direct copy is the natural mistake. | high — link error, easy to misdiagnose as a romfs config error rather than a port-vs-port-vs-port-difference. | Called out explicitly in the variant config table above. Phase test gate 2 (build succeeds with `VARIANT=picolet-cli`) catches it within seconds. |
| **NFR-1 is tight.** Stock unix port = 866 KB. Adding static libffi (~150 KB typical) gives 1016 KB — within 1 MB but only just. Any future PR that bumps text size by ~10 KB tips it over. | high — the budget is already mostly consumed before we trim. | high — NFR-1 is a numbered requirement; failure is a gate failure not just a warning. | Variant disables ~15 KB of error reporting + ~20 KB of repl + ~30 KB of machine module = ~65 KB of headroom. If first measurement still exceeds 1 MB, fallback is `MICROPY_ENABLE_COMPILER=0` per "Variant config plan / compiler" note. Final ceiling: 1 MiB binary == 1048576 bytes — make that explicit in the build-script gate so we don't have a decimal-vs-binary-MB argument. |
| **`MICROPY_ENABLE_COMPILER=0` breaks the gate test fixtures.** They use `-c '<expr>'`. | medium — only triggers if we hit the compiler-off fallback. | medium — gate redesign needed mid-phase. | Contingency: gate tests use pre-compiled `.mpy` fixtures loaded via the frozen manifest. The SQE owns the rework. Better outcome is staying compiler-on and hitting the size budget. |
| **The frozen manifest's `add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")` requires `MPY_LIB_DIR` to be set.** The unix port's `mkrules` sets it to `$(MICROPY_LIB_DIR)` which defaults to `$(TOP)/lib/micropython-lib`; the integration branch's PR #38 adjusts the micropython-lib pointer. If the submodule is not initialised recursively the path doesn't exist. | low — `rebuild-integration.sh` already runs `git submodule update --init --recursive`. | high if it triggers — build error at manifest-freeze time. | The build script verifies `lib/micropython-lib/python-stdlib/asyncio/` exists before invoking make; fails fast with a clear error if not. |
| **Frozen manifest + embedded-romfs main.py priority.** Spec says either frozen `main.py` or `/rom/main.py` can be the startup entry. The unix port's `main.c` (PR #43) tries frozen first; PH01's gate 11 explicitly exercises the `/rom/` fallback. If the test fixture for gate 4 unintentionally freezes a `main.py` via the manifest, gate 11's `/rom/main.py` is never reached. | medium — easy to miss. | medium — gate 11 fails silently. | The gate-4 test romfs contains `main.py`; the manifest does **not** `module("main.py", ...)` it. The gate-11 fixture omits frozen-main entirely. The phase fixture layout makes this explicit. |
| **Asyncio in micropython-lib's python-stdlib has its own sub-tree (`asyncio/__init__.py`, `asyncio/core.py`, etc.).** `require("asyncio")` pulls in the whole sub-tree — small in frozen bytes but it's the largest single contributor to the manifest. | low — `require()` handles this; the only failure mode is a manifest typo. | low. | Verified via gate 7. |
| **`json` is a built-in C module on unix.** Confirmed via inspection of `extmod/modjson.c` and the unix `mpconfigport`. Variant must not disable it (gate 8 imports it). | low — disabling json is a deliberate `#define MICROPY_PY_JSON 0`, not a default. | low. | The variant config table above explicitly says "leave on" for `MICROPY_PY_JSON`. |
| **`MICROPY_PY_SYS_PATH_ARGV_DEFAULTS` is pinned to 0 in `ports/unix/mpconfigport.h:155`** — variant cannot turn it on. If a future iteration assumes the macro controls argv population on unix, it will break silently. | low for PH01 (FR-RT-8 satisfied via main.c-side init). | medium for later phases. | Documented in variant config table. PH04 may need a similar note for the windows port behaviour, which differs (PR #44 wrapped it with `#ifndef`). |
| **`scripts/build-runtime.sh` calls `python3 -m mpremote romfs build`.** mpremote must be installed on the host. PH00 listed it in prerequisites. CI on a fresh runner without mpremote installed will fail at gate 6 of the build sequence. | medium — present on dev host, not guaranteed in CI. | low — quick fix via `pip install mpremote`. | Build script checks `command -v mpremote` and prints an actionable error if missing. PH15 (CI) bakes mpremote into the runner setup. |
| **The unix port's `Makefile`'s `MICROPY_STANDALONE=1` triggers a libffi build from a sub-submodule (`lib/libffi`).** First run is slow (libffi autoconf, ~2–3 min on this host). | low — expected behaviour, not a defect. | low — surfaces as long first-build wall time. | Document in the phase. Subsequent builds re-use the libffi static lib from the build dir. |
| **`overlay/` directory committed onto the integration branch creates a commit that doesn't exist on `andrewleech/micropython`.** `rebuild-integration.sh` always reapplies the overlay so the integration branch's tip differs from any pushed branch. Force-push protection in `andrewleech/micropython` could refuse if the script ever tries to push integration upstream. | very low — script doesn't push integration; `[2/3] Promote integration_update -> integration` is local only. | very low. | Documented in the rebuild script header. Mentioned here so PH04/PH15 reviewers don't propose pushing the integration branch as a release tag. |
| **NFR-5 (no GPL/AGPL statically linked).** libffi is MIT-like (BSD-ish), python-stdlib's asyncio is MIT, MicroPython is MIT. cli variant has no other native deps. | very low — established licences. | low. | Note in the commit body: PH13 will formally enumerate via SBOM; PH13 confirms no new dependency outside the integration branch + libffi. |
| **NFR-4 (no system Python).** The runtime is a self-contained C binary. The build host needs python3 for mpy-cross and mpremote, but the artifact does not. | very low. | low. | gate 13's `ldd` check directly demonstrates no python dynamic linkage. |
| **The `[ui]` section absence vs cli variant selection** is a PH02 / PH03 concern (FR-BP-1). PH01 just produces the cli artifact; how `picolet build` selects it is later. | n/a for PH01. | n/a for PH01. | Out of scope, noted only to keep the planner from over-reaching. |

### Out of scope for PH01

- **Windows target.** No `overlay/ports/windows/variants/picolet-cli/`,
  no dockcross invocation, no `.exe` output. PH04 mirrors PH01 for
  windows-x64.
- **`picolet` CLI tool.** PH02 introduces `picolet init`, `picolet
  --version`, TOML validation. PH01's artifact is consumed by PH02/PH03
  but PH01 itself does not depend on `picolet-cli` and does not
  produce / configure it.
- **End-to-end `picolet build` pipeline.** PH03 wires user `.py` →
  `.mpy` → romfs → final binary. PH01's `build-runtime.sh` is the
  runtime-only half; it bakes a test romfs only because the artifact
  needs a frozen entry to demonstrate FR-RT-7 in isolation.
- **`picolet build --from-source`.** That flag lives in `picolet-cli`
  (PH02–PH05). PH01's `build-runtime.sh` is the script
  `--from-source` will eventually delegate to, but the flag plumbing
  itself is PH05's deliverable.
- **Pre-built runtime artifact distribution.** Caching, download
  resolver, release URLs — PH05.
- **Webview, LVGL, and IPC.** Those variants land in PH06–PH12.
- **SBOM emission.** PH13. No `runtime.toml` declaration of libffi
  yet, only a note in the commit body that PH13 will add it.
- **App icon, code signing, installer formats, auto-update.** All
  marked "out of scope for v1" in `v1-spec.md`.
- **`picolet dev` watch loop.** PH16.
- **Variant for macos, arm, or 32-bit.** Out of scope per
  `v1-spec.md` § Targets.
- **`json` from micropython-lib in the manifest.** Built-in suffices;
  including it would be redundant.
- **An `overlay/manifests/manifest_cli.py` symlink.** Only the
  canonical `packages/picolet-runtime/manifests/manifest_cli.py`
  exists; symlink fallback only adopted if the absolute-path approach
  proves clunky in practice.

### Spec traceability

| FR / NFR id | PH01 deliverable that closes it | Gate # |
|---|---|---|
| FR-RT-1 (single executable embedding MicroPython + renderer modules for its variant + romfs ioctl machinery) | The artifact is a single statically-linked ELF; for the cli variant, "renderer modules" is the empty set per FR-RT-3. Romfs ioctl is the in-`main.c` PR #43 implementation, included by `MICROPY_VFS_ROM_IOCTL=1` (inherited from common). | 2, 3, 4, 11, 13 |
| FR-RT-3 (cli variant has no window, no webview, no LVGL) | The variant adds zero native renderer modules. `ldd` confirms no GTK / SDL / WebKit linkage. | 13 |
| FR-RT-4 (`gc.add_heap()` available in every variant) | `MICROPY_GC_SPLIT_HEAP=1`, `MICROPY_GC_SPLIT_HEAP_ADD=1` in variant `.h`; PR #41 provides the impl. | 5 |
| FR-RT-5 (`ffi` available in every variant) | `MICROPY_PY_FFI=1`, `MICROPY_STANDALONE=1` in variant `.mk`; unix port Makefile already wires libffi. | 6 |
| FR-RT-6 (embedded romfs auto-mounted at `/rom`, prepended to `sys.path`) | `MICROPY_VFS_ROM=1`, `MICROPY_VFS_ROM_IOCTL=1` (inherited from common); `ROMFS_IMG=` build flag triggers `MICROPY_ROMFS_EMBEDDED=1` via the port Makefile; `main.c` lines 584–588 do the mount + sys.path append. | 4, 11 |
| FR-RT-7 (frozen `main.py`/`.mpy` or `/rom/main.py`/`.mpy` executes at startup) | `MICROPY_MODULE_FROZEN_MPY` is on by default in non-minimal variants. `main.c` lines 620–681 prioritise frozen > rom. | 4, 11 |
| FR-RT-8 (`sys.argv` populated from host command line) | Unix port `main.c` already populates `sys.argv` from argv before `pyexec` runs. Variant changes nothing here. | 10 |
| NFR-1 (`picolet-runtime-{target}-cli` ≤ 1 MB) | Variant strips ~65 KB of unused features; stock unix is 866 KB → cli with libffi static is projected ≤ 1 MB. Build script enforces gate. | 12 |
| NFR-4 (no system Python) | Build script produces a fully self-contained ELF; verified by `ldd`. | 13 |
| NFR-5 (no GPL/AGPL statically linked) | libffi is MIT-like, MicroPython is MIT, asyncio is MIT. PH13 ratifies via SBOM; PH01 commit body declares. | — (out of explicit gate; PH13 verifies) |
| NFR-8 (Linux artifacts run on Ubuntu 22.04 with no extra packages) | Statically linked except glibc + ld; verified by `ldd` and by running inside `ubuntu:22.04` container. | 13 |

Spec items the v1-plan exit gate for PH01 does **not** mention but
that PH01 incidentally satisfies (for the cli variant only):

- FR-RT-2 (three variants per target) is **partially** progressed —
  one of three variants per target now exists for linux-x64. PH04 +
  PH07/PH11/etc. complete it.

Spec items PH01 explicitly does not close:

- FR-RT-2 fully (needs webview + lvgl variants too).
- FR-CLI-* (PH02 onwards).
- FR-WV-*, FR-LV-*, FR-IPC-*, FR-BP-*, FR-SBOM-* (later phases).
- NFR-2, NFR-3 (webview / lvgl size budgets), NFR-6, NFR-7, NFR-9.

## Implementation

### Files created

| File | Notes |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Variant feature config. Uses EXTRA_FEATURES base (CORE_FEATURES breaks unix_mphal.c — see caveat commit). #undef+#define pattern for post-include overrides (avoids -Werror=redefined). Disables machine, websocket, REPL extras, mem stats, hashlib, deflate, ssl, micropython.mem_info. Enables GC split heap. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.mk` | Enables MICROPY_PY_FFI=1, MICROPY_STANDALONE=1. Disables SSL (mpconfigport.mk enables it by default). Points FROZEN_MANIFEST at PICOLET_RUNTIME_ROOT/manifests/manifest_cli.py. |
| `packages/picolet-runtime/manifests/manifest_cli.py` | Frozen manifest. Uses include("$(MPY_DIR)/extmod/asyncio") — asyncio is in extmod not micropython-lib in this MicroPython version. Uses require("os-path") from micropython-lib/python-stdlib. |
| `packages/picolet-runtime/scripts/build-runtime.sh` | Parameterised build orchestrator. Separate deplibs make call before main build (LIBFFI_CFLAGS is evaluated at make parse time). Copies romfs to /tmp with hyphen-free path to avoid objcopy symbol rename mismatch. |
| `packages/picolet-runtime/tests/phase-01/test_romfs/main.py` | Gate-4 fixture: `print("ok"); sys.exit(0)`. |
| `packages/picolet-runtime/tests/phase-01/test_romfs_no_frozen/main.py` | Gate-11 fixture: `print("ok-from-rom"); sys.exit(0)`. |

### Deviations from plan

| Item | Deviation | Rationale |
|---|---|---|
| ROM level | Plan says CORE_FEATURES; implementation uses EXTRA_FEATURES. | CORE_FEATURES disables MICROPY_KBD_EXCEPTION, which unix_mphal.c depends on unconditionally when MICROPY_ASYNC_KBD_INTR=1. See `[PH01] Caveat: CORE_FEATURES breaks unix port builds` commit. |
| asyncio manifest | Plan uses `require("asyncio")`; implementation uses `include("$(MPY_DIR)/extmod/asyncio")`. | asyncio is in extmod in this MicroPython version, not micropython-lib. See `[PH01] Note: asyncio is in extmod` commit. |
| MICROPY_PY_SSL | Not listed in plan; added to mpconfigvariant.mk. | mpconfigport.mk enables SSL by default; it pulls in mbedtls which fails to compile at EXTRA_FEATURES (mp_obj_memoryview_init usage). |
| MICROPY_PY_MICROPYTHON_MEM_INFO | Not listed in plan; set to 0 in variant header. | main.c:915 uses mp_verbose_flag inside this guard; mp_verbose_flag is only defined with MICROPY_DEBUG_PRINTERS=1, which we disable. |
| deplibs make step | Not in plan; added as a separate make invocation before main build. | LIBFFI_CFLAGS evaluated at make parse time; libffi must be built before make starts the compile step. |
| romfs staging in /tmp | Not in plan. | Hyphenated `picolet-runtime` in the path causes objcopy symbol rename to fail silently. See caveat commit. |

### Gate 5 API note

The phase plan's gate-5 verification command uses `gc.add_heap(bytearray(4096))`. PR #41 implements `gc.add_heap(nbytes: int)` — it allocates from the system and adds to the GC. The correct call is `gc.add_heap(4096)`. SQE test fixtures should use the integer API. See `[PH01] Note: gc.add_heap API takes int` commit.

### Build result

- Binary: `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`
- Size: 620,848 bytes stripped (59% of NFR-1 ceiling of 1,048,576 bytes)
- Compiler kept ON (no NFR-1 pressure at 59% usage)
- All gates 2–13 pass except gate-5 requires the corrected integer API

## Tests

(scrum-sqe fills in.)

## Verification

**Verdict: FAIL**

**Blocking finding: NFR-8 violated — binary requires GLIBC_2.38, Ubuntu 22.04 provides GLIBC_2.35.**

### Environment

- Host: WSL2, Ubuntu 24.04, GCC 13.3.0, glibc 2.39
- Build: warm tree rebuild (object cache intact, link step only)
- Build time (warm): 2.5 s
- Test time: 1.8 s wall

### Build verification

Deleted `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` and
`packages/picolet-runtime/build/romfs_staging/` before running the build
script. Result:

```
=== Build complete: packages/picolet-runtime/build/picolet-runtime-linux-x64-cli ===
size: 620848 bytes (59% of NFR-1 ceiling of 1048576 bytes)
```

Build exits 0. Submodule is clean and on the integration branch
(`6af0008eec`). The parent-repo gitlink matches that SHA — reproducible
from a fresh clone.

Second consecutive run also exits 0 at 620,848 bytes (link step
re-runs each time due to the ROMFS_IMG touch; object compilation is
skipped). Idempotency gate 14: met at the "no full rebuild" level;
byte-identical output also holds.

### Test suite results

```
bash tests/phase-01/run.sh
=== Results: 21 passed, 0 failed, 1 skipped / 22 total ===
wall time: 1816 ms
```

All 21 active subtests pass. B3 SKIP is genuine (confirmed independently:
a positional script-path argument to the binary causes the embedded romfs
`main.py` to run and call `sys.exit(0)` before the script is reached —
not a test-setup error).

### Requirements coverage matrix

| # | Source | Requirement | Implemented? | Evidence | Test coverage | Notes |
|---|---|---|---|---|---|---|
| 1 | Spec | FR-RT-1: single executable embedding MicroPython + romfs ioctl | Yes | ELF 64-bit, statically links libffi, romfs ioctl in `main.c` | A1, A2, C1–C4, D1 | |
| 2 | Spec | FR-RT-3: cli has no window/webview/LVGL | Yes | `ldd` shows only libc/libm/ld-linux; no GTK/SDL/WebKit symbols | A4 | |
| 3 | Spec | FR-RT-4: gc.add_heap() available | Yes | `mpconfigvariant.h`: `MICROPY_GC_SPLIT_HEAP=1`, `MICROPY_GC_SPLIT_HEAP_ADD=1` | B4, B5, B6 | API is int not bytearray; plan's gate-5 example was wrong, test suite uses correct form |
| 4 | Spec | FR-RT-5: ffi module available | Yes | `mpconfigvariant.mk`: `MICROPY_PY_FFI=1`, `MICROPY_STANDALONE=1` | B7 | |
| 5 | Spec | FR-RT-6: romfs auto-mounted at /rom, prepended to sys.path | Yes | `/rom` and `/rom/lib` both in sys.path at runtime | C1, C2, C3 | |
| 6 | Spec | FR-RT-7: main.py/mpy in frozen or /rom/ executed at startup | Yes | Both paths exercised: frozen romfs `main.mpy` (test_romfs) and `/rom/main.mpy` fallback (test_romfs_no_frozen) | C4, C5, D1, D2 | |
| 7 | Spec | FR-RT-8: sys.argv populated from host command line | Yes | `-c` path: `['-c', 'arg1', 'arg2']` confirmed | B2 | Script-path argv path untestable with current fixtures (B3 SKIP) — not a defect |
| 8 | Spec | NFR-1: ≤ 1 MiB | Yes | 620,848 bytes (59% of ceiling) | A3 | |
| 9 | Spec | NFR-4: no system Python required | Yes | Binary is self-contained C ELF; `ldd` shows no libpython | A4 | |
| 10 | Spec | NFR-8: runs on Ubuntu 22.04 with no extra packages | **No** | Binary requires `GLIBC_2.38`; Ubuntu 22.04 provides `GLIBC_2.35` | — | **FAIL** — see Blockers |

### Independent spot-checks

All of the following were verified directly against the binary:

- `print("ok")` round-trip: pass
- `gc.add_heap(4096)` returns `int` (value 2076800): pass
- `gc.add_heap(1)` raises `ValueError: heap size too small`: pass
- `import ffi; ffi.open` exists (is a function): pass
- `import asyncio; asyncio.run(<coro>)` returns coroutine value: pass
- `os.stat("/rom")` succeeds: pass
- `/rom` and `/rom/lib` both in `sys.path`: pass
- `ldd` output — only `linux-vdso`, `libm.so.6`, `libc.so.6`, `ld-linux-x86-64.so.2`: pass

### NFR-8 / Ubuntu 22.04 containerised check

```
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" --user "$(id -u):$(id -g)" \
    ubuntu:22.04 \
    packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
```

Result (exit 1):

```
/lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
/lib/x86_64-linux-gnu/libm.so.6: version `GLIBC_2.38' not found
```

The two versioned symbols requiring GLIBC_2.38:

```
__isoc23_sscanf    (GLIBC_2.38)
fmod               (GLIBC_2.38)
```

Root cause: the binary is compiled on Ubuntu 24.04 with GCC 13.3.0,
which emits `__isoc23_sscanf` (a C23 scanf hardening change introduced
in glibc 2.38) and a `fmod` at the 2.38 symbol version. Ubuntu 22.04
ships glibc 2.35 and does not provide these versioned symbols.

Gate 13's `ldd`-name filter only checks for *forbidden library names*;
it does not detect a *versioned symbol* requirement that exceeds the
target OS's glibc version. Both the phase plan's gate 13 definition and
the SQE's A4 subtest share this gap.

### Ldd output (full)

```
linux-vdso.so.1
libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
/lib64/ld-linux-x86-64.so.2
```

No libffi, no libpython, no SDL2, no GTK. The name-only check passes.
The glibc version check fails.

### B3 SKIP validity

Confirmed genuine. Passing a positional script path to the binary causes
the embedded romfs `main.py` to run (and call `sys.exit(0)`) before
execution reaches the script file. This is a runtime behaviour of the
unix port's `main.c` startup sequence, not a test-setup error. The skip
is correctly documented and the sys.argv shape for the `-c` path is
fully covered by B2.

### Summary of gate results

| Gate | Description | Result |
|---|---|---|
| 1 | rebuild-integration.sh exits 0 with overlay | Pass |
| 2 | Variant builds with VARIANT=picolet-cli | Pass |
| 3 | Artifact is executable | Pass |
| 4 | Frozen main.py runs, prints "ok", exits 0 | Pass |
| 5 | gc.add_heap callable (integer API) | Pass |
| 6 | ffi import succeeds | Pass |
| 7 | asyncio import succeeds | Pass |
| 8 | json available (C built-in) | Pass |
| 9 | os.path available (frozen manifest) | Pass |
| 10 | sys.argv shape for -c invocation | Pass |
| 11 | /rom/main.py fallback path works | Pass |
| 12 | Binary size ≤ 1 MiB (620,848 bytes) | Pass |
| 13 | ldd name-check only — NFR-8 container check | **FAIL** |
| 14 | Second run idempotent (no full rebuild) | Pass |

## Blockers

### BLK-01 — NFR-8: binary requires GLIBC_2.38, Ubuntu 22.04 provides GLIBC_2.35

**Severity**: gate-blocking. NFR-8 is an exit-gate requirement for PH01.

**Root cause**: The build host is Ubuntu 24.04 / GCC 13.3. GCC 13
produces `__isoc23_sscanf` (C23 scanf ABI change, glibc 2.38+) and a
`fmod` symbol at version GLIBC_2.38. Ubuntu 22.04 provides glibc 2.35
and does not have these versioned symbols.

**Neither developer nor SQE caught this**: the gate-13 `ldd`-name
filter passes because no *extra library* appears; it does not check the
minimum glibc *version* required.

**Required fix (developer)**: Build the Linux artifact against a
Ubuntu 22.04 sysroot or inside an `ubuntu:22.04` Docker image so the
compiler/linker target glibc 2.35 and do not emit 2.38-versioned
symbols. The `--user/-v/-w` dockcross shape from CLAUDE.md and
`build-runtime.sh`'s existing structure make this straightforward:
replace the native `make` invocation with:

```bash
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" --user "$(id -u):$(id -g)" \
    ubuntu:22.04 \
    bash -c "apt-get install -y gcc make python3 binutils libffi-dev ... && make -C ..."
```

or use a pre-built Ubuntu 22.04 build image. The resulting binary must
pass:

```bash
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)" --user "$(id -u):$(id -g)" \
    ubuntu:22.04 \
    packages/picolet-runtime/build/picolet-runtime-linux-x64-cli
# must print: ok
```

**Required fix (test suite)**: Add a glibc version check to the test
suite (e.g. `objdump -T | grep GLIBC` and assert maximum version ≤
2.35) and/or add the Ubuntu 22.04 container run as a mandatory subtest
rather than a tester-only step. The current A4 subtest passes on the
broken binary; it should not.

**Investigation note for the scrum-po**: The spec (`v1-spec.md`) states
`linux-x64` as `gcc, glibc 2.31+`. This describes the *target runtime
minimum*, meaning the artifact must run on glibc 2.31+, which
transitively means the binary's symbol version requirements must not
exceed what Ubuntu 22.04's glibc 2.35 provides (NFR-8). The two
requirements are consistent; the build host's glibc 2.39 is simply
higher than the target and the compiler defaults to the host's glibc
version unless told otherwise. This is a standard cross-compilation
concern, not a spec ambiguity.

## BLK-01 Resolution

**Status: RESOLVED** — commits `e674b44` and `2c955f7` on `dev`.

### Fix: Containerised Linux build (NFR-8)

Added `packages/picolet-runtime/scripts/dockerfiles/linux-x64-build/Dockerfile`
based on `ubuntu:22.04` (gcc 11.4.0 / glibc 2.35). Updated
`build-runtime.sh` so the three compiler steps — mpy-cross, deplibs,
and the unix port build — all run inside this container via `docker run`
with the repo bind-mounted at the same absolute host path. The romfs
assembly step (mpremote) remains on the host as it is pure Python.

The `/tmp` romfs staging workaround was replaced by passing `ROMFS_IMG`
as a relative path from the unix port working directory
(`../../../build/romfs_staging/picolet_romfs_*.romfs`). This path contains
no hyphens, satisfying the objcopy symbol rename constraint while also
being accessible inside the build container (unlike `/tmp`).

Image is built idempotently on first run; Docker layer caching skips
apt-get on subsequent runs. Build outputs persist in the bind-mounted
tree so incremental make works across container invocations.

### Fix: Mandatory ubuntu:22.04 runtime subtest (A5)

Added subtest A5 to `tests/phase-01/run.sh`. It runs the binary inside
`ubuntu:22.04` and asserts `print("ok")` returns `ok` and exit 0. This
is the authoritative NFR-8 gate; A4's ldd-name-only check is insufficient
as it does not detect versioned GLIBC symbol requirements. A5 skips
cleanly when Docker is unavailable rather than failing.

Group D's internal make invocations were also updated to use the
`picolet-linux-x64-build:22.04` container so the rebuilt binaries for the
romfs fallback test maintain the same glibc baseline.

### Verification results (post-fix)

- Binary size: 620,848 bytes (same as before; older toolchain does not
  change the stripped size for this configuration).
- Maximum GLIBC version required: 2.34 (pthread functions merged into
  libc in 2.34; Ubuntu 22.04 ships 2.35, fully satisfying this).
- No GLIBC_2.38 symbols (`__isoc23_sscanf`, `fmod` absent).
- Ubuntu 22.04 smoke test: `print("ok")` → `ok`, exit 0.
- Test suite: 22 passed, 0 failed, 1 skipped (B3 gap unchanged).

Gate 13 ldd output:
```
linux-vdso.so.1
libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
/lib64/ld-linux-x86-64.so.2
```

All gates now pass. NFR-8 is satisfied.
