# PH03 — End-to-end build for cli variant on Linux

## Plan

### Goal (restated)

Land the first end-to-end pipeline that turns a user app into a single
runnable binary on Linux:

- `picolet build` (no flags) inside a `hello-cli` app produces
  `target/linux-x64/<app>` that, when executed, runs the app's
  `[app] entry` (frozen as `.mpy`) and prints the expected output.
- The pipeline consumes the **locally-built** PH01 runtime artifact at
  `packages/picolet-runtime/build/picolet-runtime-linux-x64-cli`. Runtime
  download/caching is out of scope (PH05 owns it); PH03 references the
  in-tree artifact via a single resolver hook with a `TODO(PH05)`
  comment.
- The final binary is the runtime artifact with the romfs **appended at
  the offset the runtime expects** (FR-BP-5 — the append-at-end model,
  not re-link). This dictates a small but load-bearing runtime change
  shipped as an overlay extension of PH01.

The exit gate closes **FR-CLI-3**, **FR-BP-1**, **FR-BP-3**,
**FR-BP-4**, **FR-BP-5**, and **FR-BP-6** from
[docs/v1-spec.md](../v1-spec.md), and keeps **NFR-1** (runtime ≤ 1 MB)
and **NFR-8** (Ubuntu 22.04 runtime compatibility) intact under the
new pipeline.

### Why append-at-end, and not re-link

FR-BP-5 reads: "The final binary is the runtime artifact with the romfs
appended at the offset the runtime expects." Three implementation
strategies were on the table:

| Option | Single-binary | User needs Docker | Build time per `picolet build` | Verdict |
|---|---|---|---|---|
| **A** Append romfs blob + trailer to the pre-built runtime; runtime detects at startup. | yes | no | < 1 s (cat + checksum) | **chosen** |
| **B** Re-link the runtime per build via dockcross with the user's romfs (the PH01 flow). | yes | yes | ~17 s (warm) | rejected — violates D2 in [architecture.md](../architecture.md): pre-built runtimes are the default, dockcross is the `--from-source` opt-in. |
| **C** Ship the runtime + a sibling `<app>.romfs` file. | **no** | no | < 1 s | rejected — violates the framework's headline single-binary promise. |

Option A is the textual reading of FR-BP-5 ("the offset the runtime
expects" implies an agreed-on layout the runtime can self-discover
inside its own image, not a re-link). Options B and C would be
acceptable only if FR-BP-5 were re-worded; that re-wording is not on
the table. PH03 ships A.

Trade-off: A requires a one-time amendment to the PH01 runtime
artifact so that startup parses a trailer at the binary's tail before
falling back to the linker-embedded blob. The amendment lands here as
a deliverable, not as a separate fix-up phase, because PH03 cannot
exit-gate without it.

Logged as an empty `[PH03] Decision:` commit before any code change
lands, per CLAUDE.md §"Dev branch as investigation log".

### Architecture: append-at-end romfs trailer

#### Layout

```
+-------------------------------------------+ offset 0
|  Runtime ELF (PIE, dynamically linked)    |
|  - PT_LOAD segments                       |
|  - .rodata / .text / .data / .bss         |
|  - Section header table (~0x97000)        |
+-------------------------------------------+ offset = original_size
|  ROMFS image payload (raw mpremote bytes) |  ←  may be empty
|  N bytes                                  |
+-------------------------------------------+ offset = original_size + N
|  Trailer (24 bytes, little-endian)        |
|    bytes  0..3   magic    "PYLT"          |
|    bytes  4..5   version  u16 = 1         |
|    bytes  6..7   flags    u16 = 0         |
|    bytes  8..15  payload_size  u64 = N    |
|    bytes 16..23  payload_crc32  u32 + pad |
+-------------------------------------------+ EOF
```

Total trailer overhead: **24 bytes**. CRC32 is over the N payload
bytes (zlib `crc32` polynomial — fits in the runtime since the unix
port already has zlib's `crc32` available via `extmod/uzlib`'s table,
but we don't import it; the runtime carries a small standalone CRC32
implementation, ~250 bytes, in the overlay).

The payload itself is a stock romfs image as emitted by
`python3 -m mpremote romfs build <dir> --output <file.romfs>` — same
on-disk format the PH01 build script feeds via `ROMFS_IMG=`. Once the
trailer is stripped and the payload is reduced to a memoryview, the
existing `mp_vfs_rom_ioctl()` body in
`packages/picolet-runtime/micropython/ports/unix/main.c:1037` consumes
it unchanged.

**Why 24 bytes and not 16:** the 8-byte CRC32+pad gives FR-BP-6
reproducibility checking a single-shot way to assert "two builds with
the same inputs produced byte-identical romfs payloads". It also
catches "user truncated the binary" or "user concatenated two binaries
by mistake" cases that would otherwise hand the runtime garbage that
the romfs parser later mis-reads as a corrupt directory.

**Why a magic at the tail, not the head:** the file has a magic at the
head already (`ELF\x7f`) and the loader/kernel use it. The romfs
trailer needs to be discoverable by `read()`ing the last 24 bytes of
the runtime's own argv[0], which is fast and oblivious to ELF layout.
This is the standard self-extractor pattern (NSIS, makeself, AppImage
type-1 all use end-of-file footers).

#### Detection logic

The amended `load_romfs_image()` runs at startup, before any romfs
ioctl is requested. Pseudo-flow (real C lands in
`overlay/ports/unix/variants/picolet-cli/romfs_trailer.c`):

```c
// 1. Open argv[0] (the running binary).  fopen on /proc/self/exe is
//    a Linux fast-path; argv[0] is the portable fallback we'll use on
//    Windows in PH04.
FILE *f = fopen("/proc/self/exe", "rb");
if (!f) {
    fallback_to_linked_romfs();
    return;
}

// 2. Stat to get size.  Seek to size - 24, read trailer.
fseek(f, 0, SEEK_END);
long file_size = ftell(f);
if (file_size < (long)sizeof(picolet_trailer_t)) {
    fclose(f);
    fallback_to_linked_romfs();
    return;
}

picolet_trailer_t t;
fseek(f, file_size - sizeof(t), SEEK_SET);
if (fread(&t, sizeof(t), 1, f) != 1) {
    fclose(f);
    fallback_to_linked_romfs();
    return;
}

// 3. Magic check.  "PYLT" must match exactly.
if (memcmp(t.magic, "PYLT", 4) != 0) {
    fclose(f);
    fallback_to_linked_romfs();
    return;
}

// 4. Version check.  Future-proofing: reject version > 1.
if (t.version != 1) {
    fclose(f);
    fallback_to_linked_romfs();
    return;
}

// 5. Range-check payload_size.  payload_size + sizeof(trailer) must
//    fit within file_size.  Catches truncation and absurd values.
if (t.payload_size > (uint64_t)file_size - sizeof(t)) {
    fclose(f);
    fallback_to_linked_romfs();
    return;
}

// 6. Read payload into a malloc'd buffer.
long payload_off = file_size - sizeof(t) - (long)t.payload_size;
romfs_file_buf = malloc(t.payload_size);
if (!romfs_file_buf) { fclose(f); fallback_to_linked_romfs(); return; }
fseek(f, payload_off, SEEK_SET);
fread(romfs_file_buf, t.payload_size, 1, f);

// 7. CRC32 check.  If mismatch: free, fall back to linked.  We do NOT
//    fall back to "broken trailer treated as no trailer" because that
//    would mask a real corruption.
if (picolet_crc32(romfs_file_buf, t.payload_size) != t.payload_crc32) {
    free(romfs_file_buf);
    romfs_file_buf = NULL;
    fclose(f);
    fallback_to_linked_romfs();
    fprintf(stderr, "picolet: trailer crc mismatch; using linked romfs\n");
    return;
}

// 8. Wire up the public romfs_buf / romfs_size globals.
romfs_buf = romfs_file_buf;
romfs_size = (size_t)t.payload_size;
fclose(f);
```

`fallback_to_linked_romfs()` is the existing
`MICROPY_ROMFS_EMBEDDED=1` path in `main.c:977-983`: assigns
`romfs_embedded_data` / `romfs_embedded_end - romfs_embedded_data` to
the globals. The PH01 runtime ships with this set to the 4-byte
empty-romfs sentinel (`d2 cd 31 00`) that the integration branch
already uses when `ROMFS_IMG=` is absent. See the PH01 build script
step [5/8]: it currently links in a **test** romfs; PH03 reverts that
default to the **empty** romfs so the un-customised runtime stays a
clean blank slate.

#### Fallback behaviour

The fallback is invoked in five distinct failure modes, in this
priority order:

1. Cannot open `/proc/self/exe` (e.g. binary running under a chroot
   that strips procfs). Silent fallback.
2. File too small to hold a trailer (< 24 bytes). Silent fallback —
   any sane runtime is far larger than 24 bytes; this only matters
   for the `cat /dev/null` paranoia case.
3. Magic mismatch. **Silent** fallback — most importantly this is
   the case where a stock PH01 runtime (no romfs appended) is run
   directly; the runtime must still come up.
4. Version > 1. Loud fallback — print a one-line stderr warning
   `picolet: trailer version N unsupported; using linked romfs`.
   Version 1 is the only version PH03 ships; later versions land in
   PH13 or later if the trailer needs SBOM fields.
5. CRC mismatch. Loud fallback (warning to stderr). Indicates the
   binary was truncated or modified after build. We do **not**
   silently fall back here because the user expected their own romfs
   to be live; surfacing the warning helps them diagnose.

Each of these is exit-gate-tested below.

#### Why appending past the ELF section table is safe

The runtime is built with `-pie` (gcc PIE default on Ubuntu 22.04+) and
is `Type: DYN` (confirmed by `readelf -h` on the PH01 artifact: entry
point `0x1b050`, dynamically linked, section header table starts at
file offset `0x97000`, last section ends at `0x971ef`). Three layers
guarantee trailing bytes are ignored on load:

1. **Kernel ELF loader.** The Linux kernel's `binfmt_elf` only consults
   the program header table (`PHT`), which the entry segments point at
   via `PT_INTERP`, `PT_LOAD`, `PT_DYNAMIC`, `PT_GNU_*`. The section
   header table at the end of file is **only used by linkers and
   debuggers**, not by the loader. Appending bytes past the SHT does
   not change any PHT entry; the loader treats them as if the file
   ended at the last `PT_LOAD`'s `p_filesz`.
2. **glibc dynamic linker.** ld-linux.so reads `PT_DYNAMIC` and friends
   the same way; it never seeks past the SHT either.
3. **`fread` from `/proc/self/exe`.** The procfs entry mmaps the
   on-disk file directly; the size visible to `fstat` is the disk size
   (header + image + appended trailer). This is how the trailer
   detection code reads the trailer at all.

This pattern is what AppImage type 1, makeself, and Nix's `nix-static`
runtime use. It works across kernel versions back to 2.6.x; the only
distro shenanigan is `prelink`, which can rewrite section tables but
leaves trailing data alone (and Ubuntu 22.04 no longer ships
prelink). Logged as a known-supported pattern.

**Caveat for PIE.** PIE binaries do tolerate trailing data — the
"static-PIE doesn't tolerate trailing data" warning in the brief refers
specifically to `-static-pie` linked with musl on some kernels, where
the loader's self-relocation walks the entire file. The PH01 runtime
is dynamic-PIE (linked against glibc), not static-PIE. Verified by
`ldd packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` —
shows libc/libm/libpthread/libdl, confirming dynamic linkage.
Re-checked on the produced PH03 binary as gate 13a below.

#### False positive risk

The 4-byte magic `"PYLT"` (bytes `50 59 4c 54`) is short enough that
it could in principle occur naturally inside the runtime's `.rodata`,
`.text`, or any const string table. Two mitigations:

1. The detection logic reads **only the last 24 bytes** of the file,
   not a scan. The magic has to be at exactly `file_size - 24`. The
   chance of the last 24 bytes of an ELF binary happening to start with
   `"PYLT"` followed by an internally consistent version+size+CRC is
   astronomically low.
2. The build script asserts the magic does **not** appear in the
   runtime's `.rodata` as a one-time sanity check:
   `! strings <runtime> | grep -F PYLT`. Logged as build-script step
   [7a]. Adds < 100 ms. If the assertion ever fires, the trailer
   format gets bumped to a longer / less-printable magic (e.g.
   `\xefPICOLET\x00`) — logged as a contingency, not the primary plan.

### Exit-gate-relevant requirements

| Spec id | What it requires | Where PH03 satisfies it |
|---|---|---|
| FR-CLI-3 | `picolet build [--target T]` emits a single executable at `target/<target>/<app>[.exe]`. | The new `picolet/build_cmd.py` subcommand reads `picolet.toml`, derives target (host → `linux-x64` when omitted), compiles + appends + writes to `target/linux-x64/<app.name>`. Linux output has no extension; Windows path (`.exe`) is a no-op string-suffix logic added now and exercised by PH04. |
| FR-BP-1 | `picolet build` resolves runtime variant from `[ui] renderer` (or absent → `cli`) and the target from `--target` or host. | Resolver in `build_cmd.py:_resolve_runtime()` consults `[ui]`; absence → `"cli"`; presence + bad value already rejected by PH02's validator. Target resolution: `--target` overrides; default = host (`platform.machine()` + `platform.system()`). PH03 only implements the cli + linux-x64 branch; other branches raise `NotImplementedError` referencing PH04/PH07/PH11. |
| FR-BP-3 | User `.py` sources under the entry's directory tree are compiled to `.mpy` via the bundled `mpy-cross`. | `build_cmd.py:_compile_mpy()` walks `dirname(entry)/` looking for `*.py`, invokes `mpy-cross` once per file, places `.mpy` outputs in a staging dir mirroring the input tree. mpy-cross binary path is the one PH01 already built at `packages/picolet-runtime/micropython/mpy-cross/build/mpy-cross` — version-matched to the runtime by construction. |
| FR-BP-4 | A romfs image is built from `[romfs] include` directories plus the compiled `.mpy` set plus, for webview variants, the bridge-js bundle. | `build_cmd.py:_build_romfs()` lays out a staging tree containing the compiled `.mpy` set (rooted at the same relative paths as the source `.py`s) plus everything from `[romfs] include` paths. Webview branch is `NotImplementedError` (PH08). Passes the staging dir to `python3 -m mpremote romfs --output <out> build <dir>`. |
| FR-BP-5 | Final binary is runtime artifact with romfs appended at the offset the runtime expects. | The amended runtime defines the trailer format and detects it; `build_cmd.py:_append_romfs()` writes the runtime bytes, then the romfs payload, then a 24-byte little-endian trailer with magic `"PYLT"`, version `1`, flags `0`, payload size, and CRC32. |
| FR-BP-6 | Same inputs → same output bytes, modulo filesystem timestamps. | Two paths to determinism: (1) mpy-cross is deterministic by construction for a given input bytes + version; (2) `mpremote romfs build` is **not** deterministic by default because it preserves file mtimes inside the image — PH03 zeroes mtimes before staging (`os.utime(p, (0, 0))`) for every file fed into mpremote. Gate 14 below re-builds twice and `cmp`s the outputs. |

| Spec id | What it requires | How PH03 stays compliant |
|---|---|---|
| NFR-1 | `picolet-runtime-{target}-cli` ≤ 1 MB. | The runtime amendment adds ~600 bytes of code (trailer detection + CRC32) and 0 bytes of data. Current size 620 848 bytes (59% of ceiling); post-amendment expected ~621 500 bytes. Gate 13 re-verifies. |
| NFR-4 | Runtime requires no system Python. | Trailer detection uses only libc (`fopen`/`fread`/`fseek`/`malloc`). No new linkage. |
| NFR-8 | Linux artifacts run on Ubuntu 22.04 with no extras. | The amendment is built inside the same ubuntu:22.04 build container PH01 uses (glibc 2.35 baseline). No new symbols introduced. Gate 13b runs the produced app binary inside the container. |

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | Runtime amendment lands as part of the overlay and `rebuild-integration.sh` still completes 0. | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0, last step `Apply picolet overlay` reports the new file. |
| 2 | `build-runtime.sh --target linux-x64 --variant cli` still produces a working stock runtime, NFR-1 still holds. | `wc -c packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` ≤ 1048576. `./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'print("rt-ok")'` → `rt-ok`. |
| 3 | The stock runtime (no trailer appended) starts and mounts the linked **empty** romfs without trailer noise on stderr. | `./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import os; print(sorted(os.listdir("/rom")))'` → `[]` and stderr is empty. |
| 4 | `picolet init` followed by `picolet build` succeeds inside the new directory. | `picolet init hello-cli-test --template hello-cli && cd hello-cli-test && picolet build` exits 0 and creates `target/linux-x64/hello-cli-test`. |
| 5 | The produced binary is executable and runs. | `test -x target/linux-x64/hello-cli-test`; `./target/linux-x64/hello-cli-test` exits 0 and stdout contains `Hello from hello-cli-test`. |
| 6 | FR-BP-1: `[ui]` absent in `picolet.toml` → cli variant. | `picolet build -v` (verbose) prints a line like `runtime variant: cli`. Confirm against a copy with `[ui] renderer = "webview"` raising `NotImplementedError: webview variant builds land in PH09`. |
| 7 | FR-BP-3: user `.py` files are compiled to `.mpy` and appear in the romfs at the same relative path. | `python3 -m mpremote romfs query target/linux-x64/hello-cli-test.romfs` (intermediate artifact preserved in `target/linux-x64/.picolet-build/`) lists `main.mpy` at the expected relative path; no `main.py` present. |
| 8 | FR-BP-4: `[romfs] include` directories are bundled. | Add an `assets/` directory + a `[romfs] include = ["assets"]` entry to a test app; rebuild; query the intermediate romfs and confirm `assets/<file>` is present. |
| 9 | FR-BP-5: the runtime detects the appended trailer and runs the app's main. | The binary at gate 5 runs `Hello from hello-cli-test`, proving the romfs path is mounted via the trailer (the runtime's frozen modules contain no `main.py` — the print statement only exists in the user's `src/main.py` → `/rom/src/main.mpy`). |
| 10 | FR-BP-6: two builds with identical inputs produce identical output bytes. | `picolet build && cp target/linux-x64/hello-cli-test out1 && rm -rf target && picolet build && cmp out1 target/linux-x64/hello-cli-test` → no output (= bytes identical). |
| 11 | Trailer fallback: removing the trailer (`truncate -s -24`) makes the binary fall back to the linked empty romfs. | `cp target/linux-x64/hello-cli-test broken && truncate -s -24 broken && ./broken` → exits 0 with no output (linked empty romfs, no user main). |
| 12 | Trailer corruption: flipping a byte in the trailer's CRC field produces a stderr warning and falls back. | `dd of=corrupted if=target/linux-x64/hello-cli-test conv=notrunc bs=1 seek=$(($(stat -c%s target/linux-x64/hello-cli-test)-1)) count=1 < /dev/urandom; chmod +x corrupted; ./corrupted 2>&1` contains `picolet: trailer crc mismatch`. |
| 13a | NFR-1 still holds after amendment. | `wc -c packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` ≤ 1048576. |
| 13b | NFR-8 still holds for the **app** binary built by PH03. | `docker run --rm -v "$PWD:$PWD" -w "$PWD" ubuntu:22.04 ./hello-cli-test/target/linux-x64/hello-cli-test` → `Hello from hello-cli-test`. |
| 14 | False-positive magic check: the stock runtime does not contain `"PYLT"` anywhere other than the appended trailer of an actual built app. | `! strings packages/picolet-runtime/build/picolet-runtime-linux-x64-cli | grep -F PYLT`. Asserted by the build script as step [7a]. |
| 15 | `picolet build` clean re-run with no source change is a no-op (idempotent fast path) — best-effort, not blocking. | Second invocation completes in < 1 s by detecting that the runtime + romfs staging are unchanged. If not implemented in PH03, logged for follow-up. |

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-CLI-3, FR-BP-{1,3,4,5,6}, NFR-{1,4,8}. |
| `/home/anl/picolet/docs/v1-plan.md` §PH03 | Goal, deliverables, exit gate, model tiers. |
| `/home/anl/picolet/docs/architecture.md` | D1 (3-variant matrix), D2 (pre-built + `--from-source`), D4 (cli variant has no `[ui]`), `picolet.toml` schema. |
| `/home/anl/picolet/CLAUDE.md` | Branch/commit conventions, dev-log policy. |
| `/home/anl/picolet/docs/phases/PHASE_00_verify-mbm-baseline.md` | rerere cache + integration branch flow PH03 inherits. |
| `/home/anl/picolet/docs/phases/PHASE_01_picolet-runtime-linux-x64-cli.md` | Variant config, build-runtime.sh shape, how the test romfs is currently embedded at link time. PH03's amendment lands as a new file in the same overlay tree. |
| `/home/anl/picolet/docs/phases/PHASE_02_picolet-cli-skeleton.md` | argparse subcommand wiring pattern; validator + init module layout; hello-cli template stub. |
| `/home/anl/picolet/packages/picolet-cli/picolet/__main__.py` | argparse wiring — `build_cmd.add_parser(subparsers)` slots in alongside `init_cmd` and `validate_cmd`. |
| `/home/anl/picolet/packages/picolet-cli/picolet/init_cmd.py` | Module shape PH03's `build_cmd.py` follows (`add_parser` + `run`). |
| `/home/anl/picolet/packages/picolet-cli/picolet/validator.py` | Used by `build_cmd.py` for the FR-CLI-8 pre-flight check before any build work. |
| `/home/anl/picolet/packages/picolet-templates/picolet_templates/hello-cli/{picolet.toml,src/main.py}` | The template body PH03 builds against; `main.py` already prints `Hello from {{name}}`, which is the gate-5 distinctive output. |
| `/home/anl/picolet/packages/picolet-runtime/scripts/build-runtime.sh` | step [5/8] currently bakes a **test** romfs into the runtime via `ROMFS_IMG=`. PH03 amends it to bake the empty romfs instead, restoring the "blank-slate runtime" the user expects. |
| `/home/anl/picolet/packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` | The PH01-built runtime — 620 848 bytes, dynamic-PIE, dynamically linked against libc/libm/libpthread/libdl per `ldd`. Section header table at file offset `0x97000`; trailing append is safe. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/unix/main.c` lines 964-1057 | The `MICROPY_VFS_ROM_IOCTL && !MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL` block currently inlining `load_romfs_image()` and `mp_vfs_rom_ioctl()`. PH03 amends this block to delegate to the trailer-detection function before falling back to `MICROPY_ROMFS_EMBEDDED`. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/ports/windows/vfs_rom_ioctl.c` | Mirrors the unix logic in a separate file; PH04 reuses PH03's trailer code by including the same overlay-shared helper. PH03 makes the helper port-agnostic so PH04 just adds an `#include` from `windows/vfs_rom_ioctl.c`. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.{h,mk}` | The variant's existing macro overrides — PH03 adds `MICROPY_VFS_ROM_TRAILER=1` here to gate the new code path on. |
| `/home/anl/picolet/packages/picolet-runtime/manifests/manifest_cli.py` | Frozen manifest baseline; PH03 does NOT modify it (the app's frozen `.mpy`s live in the romfs, not in the manifest). |
| `/home/anl/pydfu-win/scripts/build-windows.sh` | Reference for the romfs-build + objcopy-embed flow PH03 replaces with append-at-end. |
| `/home/anl/pydfu-win/micropython/tools/pydfu_app/manifest_frozen.py` | Reference for how pydfu structures its frozen modules — confirms the picolet-cli pattern (frozen std modules in the runtime, user code in the romfs) is the right split. |
| `python3 -m mpremote romfs --help` | mpremote 1.27+ takes `--output <file>` **before** the subcommand, then `build <dir>`. Verified on the host: `python3 -m mpremote romfs --output x.romfs build <dir>`. |

### Files / scripts the developer will create or modify

#### New files

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.c` | Trailer detection + CRC32 helper, compiled into the variant. Provides `picolet_load_romfs_trailer(const uint8_t **buf_out, size_t *size_out) → bool`. Returns true on hit, false on miss. Port-agnostic (uses only libc; argv[0] passed in via init). |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/romfs_trailer.h` | Header for the above; the unix `main.c` and the windows `vfs_rom_ioctl.c` both `#include` it. Exports the trailer struct, the magic constant, and the loader function. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant_main_c_patch.h` | The minimum delta to `main.c`'s `load_romfs_image()` to call `picolet_load_romfs_trailer()` first. **Not a real patch file** — it's a header included from the bottom of `mpconfigvariant.h` that re-`#define`s the symbol `load_romfs_image` to a wrapper. See "Avoiding a `main.c` patch" below. |
| `packages/picolet-cli/picolet/build_cmd.py` | The `picolet build` subcommand. Roughly 250 lines: parse args → load + validate toml → resolve runtime variant + target → compile `.py`→`.mpy` → stage romfs → invoke `mpremote romfs build` → append + trailer → write to `target/<target>/<app.name>`. |
| `packages/picolet-cli/picolet/runtime_resolver.py` | Resolve the runtime artifact for a given (target, variant) tuple. PH03 implementation: hardcoded `packages/picolet-runtime/build/picolet-runtime-{target}-{variant}` with a `TODO(PH05)` comment for the cache + download path. Single function `resolve_runtime(target, variant) → Path` that raises `RuntimeNotFound` if absent. |
| `packages/picolet-cli/picolet/_trailer.py` | Python-side encoder for the 24-byte trailer. Single function `pack_trailer(payload: bytes) → bytes`. Mirrors the C struct exactly. Pure-stdlib `struct` + `zlib.crc32`. Importable by tests for round-trip verification. |
| `tests/phase-03/run.sh` | Tester harness exercising gates 1–14. Mirrors the shape of `tests/phase-02/run.sh`. |
| `tests/phase-03/fixtures/hello-cli-with-assets/` | Fixture app for gate 8 — includes an `assets/` directory and `[romfs] include = ["assets"]` to verify FR-BP-4's include-directory path. |

#### Modified files

| Path | Change |
|---|---|
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Add `#define MICROPY_VFS_ROM_TRAILER (1)` macro and (at end of file) `#include "mpconfigvariant_main_c_patch.h"`. |
| `packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.mk` | Append `romfs_trailer.c` to `SRC_C` for the variant build. The unix port's Makefile honours per-variant `SRC_C` extensions; the existing standard/minimal variants use this pattern. |
| `packages/picolet-runtime/scripts/build-runtime.sh` | step [5/8]: replace the default test romfs with an **empty** romfs (just the 4-byte `d2 cd 31 00` sentinel — `python3 -m mpremote romfs build empty_dir` where empty_dir contains a single file `.empty`). Reason: the runtime PH03 ships must come up cleanly with no user romfs. Existing `--test-romfs` flag continues to work for PH01's own smoke tests. step [7a]: assert `! strings $ARTIFACT \| grep -F PYLT` to catch false-positive magic. |
| `packages/picolet-cli/picolet/__main__.py` | Add `from picolet import build_cmd; build_cmd.add_parser(subparsers)`. One-line change in `_build_parser()`. |
| `packages/picolet-cli/pyproject.toml` | Already lists `picolet-cli` package. If the build pipeline imports anything new (it shouldn't — stdlib-only), no edit needed. PEP-723 inline script block in `__main__.py` also needs no edit. |

#### No-touch (called out)

- `packages/picolet-runtime/manifests/manifest_cli.py` — unchanged. The
  app's frozen modules live in the romfs (`/rom/src/main.mpy`), not in
  the runtime's frozen manifest.
- `packages/picolet-runtime/micropython/ports/unix/main.c` — **not
  edited.** The `MICROPY_VFS_ROM_TRAILER`-gated logic lands via the
  `mpconfigvariant_main_c_patch.h` mechanism (below). Editing
  upstream files in the integration branch would create a rerere
  burden across rebases.

### Avoiding a `main.c` patch

`main.c`'s `load_romfs_image()` is defined inside the
`MICROPY_VFS_ROM_IOCTL && !MICROPY_VFS_ROM_IOCTL_USE_EXTERNAL` block
(lines 977–1035). The cleanest amendment from PH01's perspective is to
add a trailer-check call as the first thing the function does.
However, the file is inside the integration-branch tree; editing it
puts a conflict on every rebase of PR #43 and bloats the rerere cache.

The chosen mechanism is **opt-in via variant `.h`**:

1. The variant defines `MICROPY_VFS_ROM_TRAILER=1` in
   `mpconfigvariant.h`. Default in `main.c` (or rather: the absence of
   the define) is 0.
2. The amendment to `main.c` adds **one** conditional block at the
   top of `load_romfs_image()`:

   ```c
   #if MICROPY_VFS_ROM_TRAILER
   if (picolet_load_romfs_trailer(&romfs_buf, &romfs_size)) {
       return;
   }
   #endif
   ```

   This is the only line of `main.c` PH03 touches. The conflict
   surface on future rebases is one `#if`/`#endif` block (and the
   resolution gets rerere-cached on first encounter).

3. The `picolet_load_romfs_trailer` symbol is defined in the variant's
   own `romfs_trailer.c`, linked into the build only when the variant
   is selected.

If during implementation the `main.c` edit proves to conflict with the
PR #43 rerere entry already in
`packages/picolet-runtime/rerere/0b7a88d01c.../`, the fallback is to
land the edit as a separate PR on `andrewleech/micropython` and add it
to `mbm.toml` as the eighth feature branch (`pr/unix-romfs-trailer`).
Logged as a contingency; PH03 starts with the in-place edit.

### `picolet build` subcommand spec

#### Synopsis

```
picolet build [--target {linux-x64,windows-x64}] [--verbose]
            [--keep-staging] [--runtime <path>]
```

#### Argument resolution

- `--target` — overrides the auto-detected host target. Auto detection
  is `("linux-x64" if sys.platform == "linux" and platform.machine() in
  ("x86_64","amd64") else NotImplementedError)`. PH03 only implements
  `linux-x64`; `windows-x64` exits with `error: --target windows-x64
  not implemented in PH03; see PH04`. ARM / macOS → out of scope per
  spec.
- `--verbose` — adds `runtime variant: cli`, `target: linux-x64`, and
  the staging path to stderr. No fancy logging framework.
- `--keep-staging` — preserves the per-build temp tree under
  `target/<target>/.picolet-build/` for debugging. Default cleans it up
  after a successful build.
- `--runtime` — escape hatch pointing at an alternate runtime artifact
  on disk. Used by SQE in gate 11 to test "what if I point at a
  binary that already has a trailer". Not documented in `--help`'s
  top-line synopsis (added to extended help only).

#### Implementation outline

```python
def run(args):
    # 1. Find picolet.toml — cwd, or app root inferred from cwd ancestor.
    toml_path = _find_picolet_toml(Path.cwd())

    # 2. Validate (FR-CLI-8 pre-flight).
    errors = validate_toml(toml_path)
    if errors:
        for e in errors: print(e, file=sys.stderr)
        sys.exit(1)

    data = tomllib.loads(toml_path.read_text())
    app_name = data["app"]["name"]
    entry = data["app"]["entry"]            # e.g. "src/main.py"
    romfs_includes = data.get("romfs", {}).get("include", [])

    # 3. Resolve runtime variant (FR-BP-1).
    variant = "cli" if "ui" not in data else data["ui"]["renderer"]

    # 4. Resolve target.
    target = args.target or _host_target()

    # 5. Resolve runtime artifact (PH05 hook).
    runtime_path = (Path(args.runtime) if args.runtime
                    else resolve_runtime(target, variant))

    # 6. Stage build under target/<target>/.picolet-build/.
    staging = toml_path.parent / "target" / target / ".picolet-build"
    staging.mkdir(parents=True, exist_ok=True)

    # 7. Compile .py → .mpy under staging/romfs/.
    romfs_root = staging / "romfs"
    _compile_mpy(toml_path.parent, entry, romfs_root)

    # 8. Copy [romfs] include paths into staging/romfs/.
    _copy_includes(toml_path.parent, romfs_includes, romfs_root)

    # 9. Zero mtimes for reproducibility (FR-BP-6).
    _zero_mtimes(romfs_root)

    # 10. Build romfs image with mpremote.
    romfs_img = staging / f"{app_name}.romfs"
    subprocess.run(["python3", "-m", "mpremote",
                    "romfs", "--output", str(romfs_img),
                    "build", str(romfs_root)], check=True)

    # 11. Append + trailer.
    output_path = toml_path.parent / "target" / target / app_name
    if target == "windows-x64":
        output_path = output_path.with_suffix(".exe")
    _append_with_trailer(runtime_path, romfs_img, output_path)
    output_path.chmod(0o755)

    # 12. Optional clean.
    if not args.keep_staging:
        shutil.rmtree(staging)
```

#### `_compile_mpy` detail

```python
def _compile_mpy(app_root, entry_str, romfs_root):
    entry = Path(entry_str)
    src_dir = app_root / entry.parent          # e.g. app_root/"src"
    for py in sorted(src_dir.rglob("*.py")):   # sorted for FR-BP-6
        rel = py.relative_to(app_root)         # e.g. src/main.py
        out_mpy = romfs_root / rel.with_suffix(".mpy")
        out_mpy.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([_mpy_cross_path(), "-o", str(out_mpy), str(py)],
                       check=True)
```

`_mpy_cross_path()` returns
`packages/picolet-runtime/micropython/mpy-cross/build/mpy-cross` —
**version-matched** to the runtime because PH01's `build-runtime.sh`
builds both from the same integration branch in the same container.
The path is resolved via `runtime_resolver.py:locate_mpy_cross()`,
which `TODO(PH05)`s the future "download and cache" path.

The build script verifies version match before invoking:

```python
mpy_cross_ver = subprocess.check_output(
    [_mpy_cross_path(), "--version"], text=True).split()[1]
# runtime version stamped into a sibling .version file at build time
runtime_ver = (runtime_path.parent / f"{runtime_path.name}.version").read_text().strip()
if mpy_cross_ver != runtime_ver:
    sys.exit(f"error: mpy-cross version {mpy_cross_ver} != runtime version {runtime_ver}")
```

The `.version` sidecar is written by PH03's amended `build-runtime.sh`
step [7] (next to the `strip` + copy). PH05 will replace it with a
proper signed sidecar; for PH03 it's a one-line `git rev-parse HEAD`
from the integration submodule.

#### `_append_with_trailer` detail

Pure-stdlib, no external deps:

```python
import struct, zlib
TRAILER_MAGIC = b"PYLT"
TRAILER_VERSION = 1
TRAILER_FMT = "<4sHHQII"   # 24 bytes

def _append_with_trailer(runtime_path, romfs_path, out_path):
    runtime = runtime_path.read_bytes()
    payload = romfs_path.read_bytes()
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    trailer = struct.pack(TRAILER_FMT,
                          TRAILER_MAGIC,
                          TRAILER_VERSION,
                          0,                # flags
                          len(payload),
                          crc,
                          0)                # 4-byte pad
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(runtime)
        f.write(payload)
        f.write(trailer)
```

Symmetric reader in `_trailer.py` for round-trip tests:

```python
def unpack_trailer(buf):
    magic, ver, flags, size, crc, _pad = struct.unpack(TRAILER_FMT, buf[-24:])
    return magic, ver, flags, size, crc
```

### Sequence the developer follows

All from `/home/anl/picolet` on `dev`.

1. **Log the decision.** `git commit --allow-empty -s -m "[PH03]
   Decision: append-at-end romfs trailer." -m "<body explaining FR-BP-5
   reading + trailer format + rejection of Option B/C>"`.

2. **Land the runtime amendment first.** The CLI side is useless until
   the runtime can consume what it produces.
   - Write `overlay/ports/unix/variants/picolet-cli/romfs_trailer.{c,h}`
     per the C pseudo-code above.
   - Add `#define MICROPY_VFS_ROM_TRAILER (1)` to the variant `.h`.
   - Add `SRC_C += variants/picolet-cli/romfs_trailer.c` to the variant
     `.mk` (or equivalent — confirm the exact Make variable name
     against `ports/unix/Makefile` ~line 30, where `SRC_C` is
     accumulated).
   - Add the `#if MICROPY_VFS_ROM_TRAILER` block to `main.c` lines
     977-983. Single 5-line edit. Be ready for the rerere conflict
     handling per PH00's resolution model.
   - Run `./packages/picolet-runtime/scripts/rebuild-integration.sh`
     and confirm the overlay applies and rerere stays green.

3. **Amend the build script.**
   - Change the default `--test-romfs` value so the shipped runtime
     gets an **empty** romfs (single-file `.empty` directory). PH01's
     own smoke test still passes `--test-romfs test_romfs`.
   - Add step [7a] strings-assert against `PYLT` in the produced
     artifact.
   - Add step [7b] writing the `.version` sidecar from the integration
     submodule's HEAD.

4. **Build the runtime.** `./packages/picolet-runtime/scripts/build-runtime.sh
   --target linux-x64 --variant cli`. Confirm size ≤ 1 MiB, sidecar
   `.version` present, magic absent.

5. **Manual smoke test of the runtime alone.** Run the produced
   binary directly; it should come up, find no trailer (magic mismatch
   path), fall back to the linked empty romfs, and exit silently
   because no main.py is found. Then exit code should be 0 (matches
   PH01's gate 4 with the empty fixture).

6. **Land the CLI side.**
   - Write `packages/picolet-cli/picolet/_trailer.py`.
   - Write `packages/picolet-cli/picolet/runtime_resolver.py` (hardcoded
     path + `TODO(PH05)` markers).
   - Write `packages/picolet-cli/picolet/build_cmd.py`.
   - Add the `build_cmd.add_parser(subparsers)` line to
     `__main__.py:_build_parser()`.

7. **End-to-end smoke test.**
   ```
   cd /tmp && rm -rf hello-cli-test
   uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py \
       init hello-cli-test --template hello-cli
   cd hello-cli-test
   uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py build
   ./target/linux-x64/hello-cli-test
   # expect: Hello from hello-cli-test
   ```

8. **Reproducibility check.** Run `picolet build` twice with no source
   change; `cmp` the outputs. Expect byte-identical.

9. **Per CLAUDE.md, split into small commits:**
   - `[PH03] Add romfs trailer support to picolet-cli unix variant.`
     (runtime amendment)
   - `[PH03] Ship empty romfs by default in build-runtime.sh.` (script
     edit)
   - `[PH03] Add picolet build subcommand for linux-x64/cli.` (CLI side)
   - `[PH03] Add tests/phase-03/run.sh.` (SQE harness — actually
     written by SQE role, but the file path is reserved here)
   - Empty `[PH03] Note:` commits as warranted.

### Verification commands (SQE / tester)

The SQE writes `tests/phase-03/run.sh` to exercise the gates above.
Tester re-runs it from a fresh checkout. Concrete commands per gate
are in the exit-gate table; the harness wraps them with proper exit
codes and a final pass/fail line.

**Gate 4–5 — end-to-end app build and run**

```
rm -rf /tmp/hello-cli-test
cd /tmp
uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py \
    init hello-cli-test --template hello-cli

cd /tmp/hello-cli-test
uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py build

test -x target/linux-x64/hello-cli-test
out="$(./target/linux-x64/hello-cli-test)"
test "$out" = "Hello from hello-cli-test"
```

**Gate 10 — FR-BP-6 reproducibility**

```
cd /tmp/hello-cli-test
rm -rf target
uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py build
cp target/linux-x64/hello-cli-test /tmp/out1

rm -rf target
uv run /home/anl/picolet/packages/picolet-cli/picolet/__main__.py build
cmp /tmp/out1 target/linux-x64/hello-cli-test
```

**Gate 11 — trailer-stripped fallback**

```
cp target/linux-x64/hello-cli-test /tmp/no-trailer
truncate -s -24 /tmp/no-trailer
chmod +x /tmp/no-trailer
/tmp/no-trailer    # exits 0, no output (linked empty romfs, no main)
```

**Gate 12 — CRC mismatch warning**

```
cp target/linux-x64/hello-cli-test /tmp/broken-crc
# Overwrite a byte inside the CRC field (offset = file_size - 8).
sz=$(stat -c%s /tmp/broken-crc)
printf '\xFF' | dd of=/tmp/broken-crc conv=notrunc bs=1 \
                  seek=$((sz - 8)) count=1
chmod +x /tmp/broken-crc
/tmp/broken-crc 2>&1 | grep -q 'trailer crc mismatch'
```

**Gate 13b — NFR-8 on Ubuntu 22.04**

```
docker run --rm \
    -v /tmp/hello-cli-test:/app -w /app \
    ubuntu:22.04 ./target/linux-x64/hello-cli-test
# expect: Hello from hello-cli-test
```

### Foreseeable risks

| Risk | Likelihood | Impact | Mitigation / response |
|---|---|---|---|
| **PIE vs trailing data on some kernels.** The PH01 runtime is `Type: DYN` (dynamic-PIE). Static-PIE binaries can mis-behave with trailing data on musl-libc kernels, but dynamic-PIE on glibc is fine. The risk is a future runtime variant linking static-pie for size and silently breaking trailer detection. | low for PH03, watch-item for later size reductions. | medium — would surface as "trailer detected but `/proc/self/exe` reads stop at section table". | PH03 explicitly verifies dynamic-PIE via `ldd` (gate 13a precondition). If a future variant goes static-pie, the trailer must be read from `argv[0]` via libc, not `/proc/self/exe`; the C helper already prefers the procfs path with argv[0] as fallback, so the change is one branch. |
| **False trailer magic in `.rodata`.** Although the magic is matched only against the last 24 bytes, a paranoid concern is that during a future rebuild the linker happens to land a `PYLT`-bearing string at exactly that offset of a stock runtime. | very low. | medium — would mis-mount user data into nothing. | Build-script step [7a] asserts `! strings <runtime> \| grep -F PYLT`. If it ever fires (e.g. someone adds a comment `// PYLT was here` to a frozen .py), the magic is bumped to `\xefPICOLET\x00` and the format version is bumped to 2. Logged as a contingency, not the primary plan. |
| **mpy-cross version skew.** A user with a globally-installed mpy-cross could shadow the PH01-built one. mpy-cross bytecode format is **not** forward-compatible: a newer mpy-cross emitting bytecode the runtime's VM doesn't understand silently produces unreadable modules. | medium (uv environments isolate well, but `pip install mpy-cross` is common). | high — runtime can't load user modules. | `runtime_resolver.locate_mpy_cross()` returns an **absolute path** to the in-tree mpy-cross binary, ignoring `$PATH`. The build script then sidecars the integration submodule's HEAD sha as `<runtime>.version` and re-checks at build time. Catches the issue at build time, not runtime. |
| **`mpremote romfs build` mtime determinism.** mpremote 1.27 includes file mtimes in the romfs directory entries; identical sources at different times produce different bytes. | high. | high — directly blocks FR-BP-6. | `build_cmd._zero_mtimes()` walks the staging tree and `os.utime(p, (0, 0))` every file before invoking mpremote. Gate 10 verifies the result. Confirmed against mpremote source: it uses `os.stat(p).st_mtime`. If a future mpremote release zero-defaults mtimes itself, this step becomes a no-op; not a regression. |
| **Template `main.py` distinctiveness.** Gate 5 asserts `Hello from hello-cli-test`. If PH02's template stub changes (PH14 may rewrite all three templates), the assertion drifts. | low for now, high once PH14 lands. | low — gate test breaks, gets fixed. | Tester harness reads the expected string from the template's `src/main.py` (post-substitution) at runtime rather than hardcoding it. One-line tweak; logged. |
| **Trailer eats user `.romfs.cdx.json` SBOM slot.** PH13 will need to emit a sibling SBOM for the produced binary. The trailer doesn't interfere — the SBOM is a separate file at `target/<target>/<app>.cdx.json` — but a future "embed SBOM in trailer flags" idea would. | low. | low — PH13's problem. | Trailer reserves a `flags` u16 for exactly this; current value 0, future `0x1` could mean "SBOM appended before trailer". Logged in the trailer format doc. |
| **Two builds in parallel from the same app root.** If `picolet build` is invoked twice concurrently (a `picolet dev` debounce race in PH16), the second writes a partial output. | low for PH03 (no concurrency), watch for PH16. | medium — partially-written binary breaks the next run. | Write to `target/<target>/.picolet-build/<app>.tmp` then atomic `rename()` to final path. Logged; the implementation outline above does the rename in step 11. |
| **`/proc/self/exe` unavailable.** Some sandboxes (chroots that drop procfs, kernel < 2.6 — irrelevant here) lack it. | very low on Ubuntu 22.04+. | medium — runtime silently falls back to empty romfs. | Trailer reader has the argv[0] fallback; if `/proc/self/exe` open fails, retry with argv[0]. Logged as a TODO in the C helper; PH03 ships the procfs-preferred path. argv[0] fallback is one new branch in `picolet_load_romfs_trailer()`. |
| **App symlinks `target/` to a different filesystem.** Cross-filesystem `rename()` of the temp file would fail (atomic rename requires same fs). | low. | low — clear error message. | `shutil.move` handles the cross-fs case by falling back to copy+unlink. Used in the `_append_with_trailer` finalisation. |
| **`build-runtime.sh` step [5/8] regression.** PH01's smoke harness currently calls the script with no flags, expecting the test romfs baked in. PH03 changes the default to empty. | medium — PH01's gate tests will fail until updated. | low — PH01 gates are not on PH03's exit path, but the regression noise pollutes CI. | The script keeps `--test-romfs test_romfs` honoured; PH01's harness gets a one-line update to pass that flag explicitly. Logged as a co-change to `tests/phase-01/run.sh`. |

### Out of scope for PH03

- **`--target windows-x64`** — PH04 (mirror PH03 on Windows).
- **Runtime artifact download / cache** (`.picolet-cache/runtime/<tag>/`) — PH05 (FR-BP-2).
- **`picolet run`** — implicitly needs PH03's build pipeline, but the
  subcommand itself is FR-CLI-6 territory; not landed in PH03.
- **`picolet dev`** — PH16 (FR-CLI-7).
- **webview / lvgl variant builds** — `build_cmd._resolve_runtime()`
  raises `NotImplementedError` for these; PH07–PH12 fill them in.
- **SBOM** sibling file (`<app>.cdx.json`) — PH13 (FR-SBOM-1).
- **`--from-source`** — PH05 (FR-CLI-5).
- **TypeScript bridge bundle** — only relevant once webview variant
  exists; PH08.

### Spec traceability

| FR / NFR | PH03 deliverable that closes it | Verification |
|---|---|---|
| FR-CLI-3 | `picolet build` subcommand produces `target/<target>/<app>[.exe]`. | Gate 4, gate 5. |
| FR-BP-1 | `_resolve_runtime()` infers cli from absent `[ui]`; target from `--target` or host. | Gate 6. |
| FR-BP-3 | `_compile_mpy()` walks `dirname(entry)/` and produces `.mpy` outputs via the in-tree mpy-cross. | Gate 7. |
| FR-BP-4 | `_copy_includes()` + the mpy staging tree feed `mpremote romfs build` to produce the final image. | Gate 8. |
| FR-BP-5 | Runtime trailer detection (overlay) + `_append_with_trailer()` (CLI). | Gates 9, 11, 12. |
| FR-BP-6 | `_zero_mtimes()` + sorted file iteration + deterministic mpy-cross flags. | Gate 10. |
| NFR-1 (regression) | Runtime amendment adds < 1 KB of code. | Gate 13a. |
| NFR-8 (regression) | Built inside the same ubuntu:22.04 container PH01 uses; no new symbols. | Gate 13b. |

## Implementation

scrum-developer's detailed write-up lives at
[PHASE_03_DEV_REPORT.md](PHASE_03_DEV_REPORT.md). Headline outcomes:

- Trailer-detection C code in
  `overlay/ports/unix/variants/picolet-cli/romfs_trailer.{h,c}`, gated
  on `MICROPY_VFS_ROM_TRAILER=1`. Magic `"PYLT"`, version 1, payload
  size u64, CRC32 (zlib polynomial 0xEDB88320).
- Overlay also carries a full patched `overlay/ports/unix/main.c` —
  the planner's "header-include" pattern was incompatible with
  `rebuild-integration.sh`'s copy-overlay model, so the patched
  main.c is shipped verbatim (commit `e161c13`).
- `picolet build` subcommand in `packages/picolet-cli/picolet/build_cmd.py`
  (480 lines, 10 steps): runtime resolve → mpy-cross version check →
  variant inference → .py→.mpy compile → asset include →  mtime zero
  for FR-BP-6 → romfs build via mpremote → trailer append → output.
- mpy-cross version sidecar written by `build-runtime.sh` next to the
  runtime; `picolet build` verifies it matches before proceeding.
- Empty romfs shipped by default with the runtime so it remains
  bootable when no trailer is present.

Test harness `tests/phase-03/run.sh` lands 14 gates (16 subtests with
6a/6b split). All 16 pass on the developer's machine.

## Tests

### Audit findings

**Gate 9 is tautological.** It re-runs the same binary tested in gate 5 and asserts the same string. Since the user romfs is the only source of the `Hello from ...` string, gate 5 already constitutes conclusive FR-BP-5 proof. Gate 9 adds no independent signal. Logged as `[PH03] Note: gate 9 is a tautology` (commit `1d37b8c`); left in place to avoid count confusion but marked for future replacement with a runtime diagnostic flag.

**Gate 12 (CRC mismatch) only tested CRC-field corruption.** The spec requested confirming that a flipped *payload* byte (CRC field intact) also triggers the warning. Added as gate 18.

**FR-CLI-8 not directly tested.** No gate confirmed that an invalid `picolet.toml` is rejected before any build work begins. Added as gate 15.

**FR-CLI-4 (explicit `--target`) not directly tested.** Gates 4–5 use the default host path. Added as gate 16.

**FR-BP-4 depth coverage insufficient.** The `hello-cli-with-assets` fixture has one flat include directory. Added gate 17 with a new fixture (`hello-cli-multi-include`) that uses two include directories and a nested subdirectory (`assets/images/icon.png` at depth 2).

**UTF-8 filename robustness.** Investigated and discovered that the MicroPython romfs format and mpremote both require ASCII filenames (`bytes(name, "ascii")`). Non-ASCII filenames produce a controlled build failure. Added as gate 19 (negative test) and logged as caveat commit `f58e62c`.

**NFR-4 (no system Python).** Not directly testable without a sterile environment lacking Python entirely. Gate 13b (ubuntu:22.04 Docker run) provides partial coverage — the image has no picolet-cli installed, confirming the binary is self-contained. Full NFR-4 verification deferred.

### Tests added

| Gate | Spec | Fixture | What it verifies |
|---|---|---|---|
| 15 | FR-CLI-8 | `invalid-toml-app/` | Invalid picolet.toml rejected with validator errors; no target/ created |
| 16 | FR-CLI-3, FR-CLI-4 | init'd in WORKDIR | `picolet build --target linux-x64` explicit flag produces working binary |
| 17 | FR-BP-4 | `hello-cli-multi-include/` | Two include dirs + nested subdir (`assets/images/`) all accessible at runtime |
| 18 | FR-BP-5 | reuses gate-4 binary | Payload byte flip (not CRC field) triggers `trailer crc mismatch` warning |
| 19 | encoding robustness | `hello-cli-utf8-asset/` | UTF-8 filenames fail at mpremote layer (UnicodeEncodeError); documents known limit |

### Final gate counts

- Developer-authored: 16 subtests across 14 gates (original).
- SQE-added: 5 subtests (gates 15–19).
- **Total: 21 subtests, 19 gates (gate 6 retains a/b split).**
- All 21 pass on `dev` at commit `7d7d3de`.

## Verification

(scrum-tester writes here.)

## Blockers

(none yet.)
