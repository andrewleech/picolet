# rerere cache

This directory holds [git rerere](https://git-scm.com/docs/git-rerere)
("reuse recorded resolution") entries for known cross-PR merge conflicts
encountered when `mbm` composes the integration branch from
`mbm.toml`.

Each subdirectory is named by the hash of the conflict preimage and
contains:

- `preimage` — the conflicted file as git produced it (with `<<<<<<<` /
  `=======` / `>>>>>>>` markers).
- `postimage` — the resolved file.

When `scripts/rebuild-integration.sh` runs, it copies this directory's
contents into the submodule's `.git/rr-cache/` and enables
`rerere.enabled` + `rerere.autoUpdate` in the submodule. `git merge`
then auto-resolves any conflict whose preimage matches a stored entry,
and `mbm` proceeds without manual intervention.

These hashes identify conflict preimages, not runtime artifacts or release
checksums. Rerere may store numbered `preimage.N` / `postimage.N` variants;
only a matching preimage with a recorded postimage can be reused. Local
integration changes, including the Windows socket and stdio work, are not
published binaries and do not have release hashes here.

## Why this exists

Some of the feature PRs in `mbm.toml` touch overlapping regions of
upstream files without being authored as a linear stack (each was
written against upstream master, independently of the others). When
composed sequentially via `git merge`, they conflict in real but
mechanically-resolvable ways. The resolutions stored here are the
canonical compositions verified against the pydfu-win precedent.

## Adding a new entry

When `rebuild-integration.sh` surfaces a new conflict:

1. Resolve manually in the submodule working tree.
2. `git add` and complete the merge — rerere records the resolution
   automatically.
3. Copy the new `<hash>/` directory from
   `.git/modules/packages/picolet-runtime/micropython/rr-cache/` into
   this directory.
4. Commit the new entry on `dev` with `[PHnn] Note: Record rerere
   resolution for <file>` and a body describing what the two sides
   conflicted on and what the canonical resolution does.

Entries are conflict-region-stable: rerere keys on the preimage
content, so if the upstream PRs evolve in ways that change the
conflict region, the recorded entry stops matching and the conflict
must be re-resolved + re-recorded. The script will surface that
condition by failing again at `mbm rebase`.

## Catalogue

| Hash | Origin conflict | Resolution |
|---|---|---|
| `1a64eaaf50add787522082efe25c83d322403270` | `ports/windows/Makefile` between `pr/ports-windows-ffi` (#42) and `pr/unix-windows-romfs` (#43). Both append rule blocks after `include $(TOP)/py/mkrules.mk` and modify the `.PHONY` line. | Keep both rule blocks (romfs `objcopy` rule first, then libffi configure block); combine `.PHONY` to `test test_full deplibs libffi romfs`. Matches the pydfu-win integration tree. |
| `0b7a88d01cba8ab06f609af61792f6601425dca5` | `ports/windows/mpconfigport.h` between `pr/unix-windows-romfs` (#43) and `pr/ports-windows-variant-overrides` (#44). #43 adds three `MICROPY_VFS_ROM*` guarded defines; #44 wraps the adjacent `MICROPY_PY_FUNCTION_ATTRS` define in an `#ifndef`. The two `#ifndef` blocks abut each other in the file. | Keep #43's three `MICROPY_VFS_ROM*` `#ifndef` blocks, then proceed with #44's `MICROPY_PY_FUNCTION_ATTRS` `#ifndef`. Matches the pydfu-win integration tree. |
| `21de21a1ace4fa2ba95762eb51146d488b467818` | `ports/unix/main.c` between this fork's base (`#if MICROPY_ENABLE_COMPILER` guard around the emit_opt/native-emitter setup) and `unix-sleep-process-pending` (#18810, adds `mp_unix_init_sched_signal()` under `#if MICROPY_ENABLE_SCHEDULER && !defined(_WIN32)` at the same point, right after `mp_init()`). Both additive, non-overlapping in intent. | Run the scheduler-signal init first, then picolet's `#if MICROPY_ENABLE_COMPILER` guard unchanged around the emitter-options block. |
| `fbe548e8f69a21296b4451cec02c8a10a55840f7` | `ports/unix/main.c` between the compiler guard and wake-event initialization from the event-source stack. | Initialize the wake event after `mp_init()`, then retain the compiler guard around emitter setup. |
| `c0c46dc2c04d35138d944df2e836c88c1a711ff6` | `ports/windows/mpconfigport.h` between the variant-overrides/windows base and `winsock` (#12810). Base closes the `MICROPY_PY_SYS_PATH_ARGV_DEFAULTS` `#ifndef`; winsock adds `MICROPY_PY_SOCKET` at the same spot. | Keep the `#endif`, then add `MICROPY_PY_SOCKET (1)` inside an `#ifndef` so variants can override it. |
| `d8611b6fe00bce2e6773ccd4bdd77a273f63e495` | `ports/windows/mpconfigport.h` between the base (`MICROPY_PY_SOCKET` inside `#ifndef`, from the `winsock` resolution above) and `windows-select-sockets`, which adds `MICROPY_PY_SELECT_WSAPOLL` straight after it. | Keep the `#endif` closing the `MICROPY_PY_SOCKET` guard, then add `MICROPY_PY_SELECT_WSAPOLL (1)` inside its own `#ifndef`. |
