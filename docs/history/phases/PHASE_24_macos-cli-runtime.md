# PHASE 24 — macOS cli runtime variants

## Goal

Produce `picolet-runtime-macos-x64-cli` and `picolet-runtime-macos-arm64-cli`
by running the existing unix port overlay natively on the respective GitHub
Actions runners (`macos-13` for x64, `macos-14` for arm64). Validate that
the romfs trailer mechanism works on Mach-O, the size gate passes, and
`build-runtime.sh` has a clean native macOS path.

This phase is the v1.2 foundation. Every subsequent phase depends on it.

## Prerequisites

- PH00–PH23 complete and green on `dev`.
- GitHub repository has `macos-13` and `macos-14` runners available
  (standard GitHub-hosted runners — no self-hosted hardware needed).

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-RT-MAC-1 (cli only) | Both macos-x64-cli and macos-arm64-cli artifacts |
| FR-RT-MAC-2 | Single Mach-O executable, no dylib sidecars |
| FR-RT-MAC-3 | romfs trailer mechanism works on Mach-O |
| FR-RT-MAC-4 | `gc.add_heap()` and `ffi` available |
| FR-RT-MAC-5 | Existing unix overlay serves macOS without Darwin fork |
| FR-BP-MAC-3 | `--from-source` for macos-* emits a clear error |
| FR-BP-MAC-4 | `build-runtime.sh` has a native macOS build path |
| FR-BP-MAC-5 | macOS path guarded by `uname -s == Darwin` check |
| FR-BP-MAC-6 | SBOM sidecar emitted for macOS builds |
| NFR-MAC-1 | cli artifact ≤ 1 MiB |
| NFR-MAC-4 | cli variant requires no runtime packages |
| NFR-MAC-6 | `docs/macos-unsigned.md` created |
| NFR-MAC-8 | No GPL/AGPL static link |

## Dependencies

- PH01 (linux-x64-cli) and PH04 (windows-x64-cli) as precedent for the
  build shape.
- The existing `overlay/ports/unix/variants/picolet-cli/` files (see
  `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/`).

## Key research findings

### MicroPython unix port on Darwin

The unix port Makefile already has a Darwin clause at line 109:
```make
ifeq ($(UNAME_S),Darwin)
CC = clang  # (or clang -m32 for 32-bit)
LDFLAGS_ARCH = -Wl,-map,$@.map -Wl,-dead_strip
```

This means:
1. `UNAME_S` is already detected and `clang` is already selected on Darwin.
2. `LDFLAGS_ARCH` uses `-Wl,-dead_strip` (Darwin's equivalent of `--gc-sections`).
3. The unix port has `#if defined(__APPLE__)` guards throughout `main.c`,
   `mpthreadport.c`, `mpconfigport.h` — it has explicit macOS support.
4. libffi (used for FFI support) also has Darwin x86_64 and arm64 support
   confirmed in `lib/libffi/generate-darwin-source-and-headers.py`.

The port builds natively on Darwin without modification. The key concern
is whether `MICROPY_STANDALONE=1` (static libffi) works correctly on
Darwin — it should, since libffi's `configure` detects Darwin and uses
`.s` assembly files from `src/aarch64/` or `src/x86_64/` as appropriate.

### libffi on Darwin: autogen caveat

On the Linux path, `build-runtime.sh` runs `autogen.sh` on the host to
generate `configure` when the cold cache has no pre-generated configure.
On macOS, `autogen.sh` requires `automake` and `libtool`. The macOS
runners have Homebrew; `brew install automake libtool` must be called in
the CI setup step before the first cold-cache build.

Subsequent warm-cache runs skip autogen (the existing warm-cache
mitigation in `build-runtime.sh` already handles this correctly).

### `strip` on Darwin

macOS `strip` uses `-x` instead of `--strip-unneeded`. The current
`build-runtime.sh` calls `strip --strip-unneeded "$artifact"` which
would fail on macOS. The macOS path must use:
```bash
strip -x "$artifact"  # strip debug symbols, keep global symbols on Darwin
```

### SBOM on macOS

`picolet_cli.sbom_gen emit-runtime` is a host Python script. It runs on
the macOS runner using the Python from Homebrew or the runner's
pre-installed Python 3.x. No changes to the SBOM generator are needed —
it is purely a host-side CLI tool.

### Docker not used on macOS

The Linux build path wraps compilation inside `picolet-linux-x64-build:22.04`
to pin the glibc version. On macOS, the SDK target is controlled by the
`-mmacosx-version-min=` flag (or `MACOSX_DEPLOYMENT_TARGET` env var),
not by the container. The target is `10.15` (Catalina) as a reasonable
minimum: it covers all Intel hardware Apple supports and all Apple Silicon
(which launches at 11.0). Setting `MACOSX_DEPLOYMENT_TARGET=10.15` in
the macOS build env ensures the binary does not accidentally use newer
APIs.

## Files to create

### `packages/picolet-runtime/scripts/build-runtime.sh` (modify)

Add the following in the `case "${TARGET}/${VARIANT}"` block:
```bash
macos-x64/cli)   ;;
macos-x64/webview)   ;;   # PH25/PH26
macos-x64/lvgl)  ;;       # PH27
macos-arm64/cli)  ;;
macos-arm64/webview)  ;;  # PH25/PH26
macos-arm64/lvgl) ;;      # PH27
```

Add `build_macos` function that runs `clang` natively:
- Checks `[[ "$(uname -s)" == "Darwin" ]]` and exits with an error if
  run on a non-Darwin host (FR-BP-MAC-5).
- Rejects `--from-source` for macOS targets with the specified error
  message (FR-BP-MAC-3).
- Sets `MACOSX_DEPLOYMENT_TARGET=10.15` (Intel) or `11.0` (arm64) in
  the environment.
- Checks that `automake` and `libtool` are on PATH for the cold-cache
  libffi autogen path; installs via `brew` if not present.
- Builds mpy-cross natively (no Docker).
- Runs `make submodules` on the host.
- Runs `make` for the unix port variant.
- Calls `strip -x` (not `--strip-unneeded`).
- Calls `finish_artifact`.
- Calls SBOM emission (same as Linux path).

Add `--from-source` rejection early in argument parsing when target is
`macos-*`:
```bash
if [[ "$FROM_SOURCE" -eq 1 && "$TARGET" == macos-* ]]; then
    echo "error: --from-source for macos-* targets is not supported in v1.2." >&2
    echo "       Use 'picolet build --target $TARGET' to download the pre-built artifact." >&2
    exit 1
fi
```

Also update the main dispatch at the bottom:
```bash
elif [[ "$TARGET" == macos-* ]]; then
    build_macos
```

### `docs/macos-unsigned.md` (create)

Explains Gatekeeper quarantine, the two workarounds (`xattr -d
com.apple.quarantine <binary>`, or right-click → Open in Finder), and
the v1.3 roadmap for signing/notarisation. NFR-MAC-6.

## Files to modify

### `.github/workflows/release.yml`

Add a stub macOS job just for the `cli` variant in this phase (full
matrix expansion is PH29). The purpose is to prove the build pipeline
shape. This can be a separate job or a matrix row:

```yaml
build-macos-stub:
  name: "build macos cli (PH24 stub)"
  runs-on: ${{ matrix.os }}
  strategy:
    matrix:
      include:
        - os: macos-13
          target: macos-x64
        - os: macos-14
          target: macos-arm64
  steps:
    - uses: actions/checkout@v4
      with:
        submodules: recursive
        fetch-depth: 0
    - name: Install automake and libtool (for libffi autogen)
      run: brew install automake libtool
    - name: Assert PICOLET_TEST_MODE is not set
      run: |
        if env -0 | tr '\0' '\n' | grep -q '^PICOLET_TEST_MODE='; then
          echo "ERROR: PICOLET_TEST_MODE is set" >&2; exit 1
        fi
    - name: Build macos cli runtime
      run: |
        bash packages/picolet-runtime/scripts/build-runtime.sh \
          --target "${{ matrix.target }}" \
          --variant cli
    - name: Compute SHA256 sidecar
      run: |
        cd packages/picolet-runtime/build
        ARTIFACT="picolet-runtime-${{ matrix.target }}-cli"
        sha256sum "$ARTIFACT" > "${ARTIFACT}.sha256"
    - name: Upload build artifacts
      uses: actions/upload-artifact@v4
      with:
        name: runtime-${{ matrix.target }}-cli
        path: packages/picolet-runtime/build/picolet-runtime-${{ matrix.target }}-cli*
        if-no-files-found: error
        retention-days: 1
```

## Integration points

### `finish_artifact` function in `build-runtime.sh`

The existing `finish_artifact` function's size gate uses a
`case "${TARGET:-linux-x64}/${VARIANT}"` switch. Add:
```bash
macos-x64/cli)    CEILING=1048576;  NFR_ID="NFR-MAC-1" ;;
macos-x64/webview) CEILING=2097152; NFR_ID="NFR-MAC-2" ;;
macos-x64/lvgl)   CEILING=2097152;  NFR_ID="NFR-MAC-3" ;;
macos-arm64/cli)   CEILING=1048576;  NFR_ID="NFR-MAC-1" ;;
macos-arm64/webview) CEILING=2097152; NFR_ID="NFR-MAC-2" ;;
macos-arm64/lvgl)  CEILING=2097152;  NFR_ID="NFR-MAC-3" ;;
```

The `strip --strip-unneeded` call in `finish_artifact` must be
conditional on the platform:
```bash
if [[ "$(uname -s)" == "Darwin" ]]; then
    strip -x "$artifact"
else
    strip --strip-unneeded "$artifact"
fi
```

### `sbom/runtime.toml`

Add macOS-specific variants to the target filter of existing entries
where applicable (libffi static, micropython-lib components). New
components specific to macOS (WebKit.framework, AppKit.framework,
SDL2 dylib) will be added in PH25, PH26, and PH27 respectively.

## Implementation guidance

### Makefile invocation on macOS

The existing unix port Makefile uses `$(UNAME_S)` and detects Darwin
automatically. The `build_macos` function should call Make the same
way as `build_linux_x64` but without the Docker wrapper:
```bash
# No docker wrapper — run natively
cd "$UNIX_PORT" && make -j \
    VARIANT="${VARIANT_NAME}" \
    MICROPY_STANDALONE=1 \
    ROMFS_IMG="$ROMFS_IMG_REL" \
    PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"
```

For arm64, the runner's native architecture is arm64 — no
`-arch arm64` flag needed. For x64 on `macos-13`, `uname -m` returns
`x86_64` — also no explicit flag needed.

### mpy-cross on macOS

`make -C "$SUBMODULE/mpy-cross"` builds a macOS host binary. On the
`macos-13` runner this produces an x86_64 ELF-equivalent Mach-O; on
`macos-14` it produces an arm64 Mach-O. Both are host tools only
(frozen `.mpy` compilation) — the resulting bytecode is
architecture-independent.

### libffi cold cache on macOS

If `configure` is absent (cold cache), `autogen.sh` must run. The
existing code path in `build-runtime.sh` already handles this with
`libtoolize`. On macOS with Homebrew:
- `libtoolize` is provided by `libtool` brew package.
- `aclocal`/`automake` are provided by `automake`.
- The CI setup step `brew install automake libtool` covers both.

The warm-cache `touch` mitigation is identical to the Linux path.

## Testing strategy

1. Trigger a CI workflow run (manual dispatch on `dev`) with the stub
   macOS job in `release.yml`.
2. Verify both `picolet-runtime-macos-x64-cli` and
   `picolet-runtime-macos-arm64-cli` appear as job artifacts.
3. Download the `macos-arm64-cli` artifact on a macOS arm64 machine
   (or the `macos-13` runner) and run a minimal sanity check:
   ```bash
   xattr -d com.apple.quarantine picolet-runtime-macos-arm64-cli
   ./picolet-runtime-macos-arm64-cli --help
   ```
   (Expected: help text from the embedded romfs or runtime help.)
4. Check binary size: `wc -c picolet-runtime-macos-arm64-cli` must be
   ≤ 1048576 bytes.
5. Verify SBOM sidecar `picolet-runtime-macos-arm64-cli.cdx.json` is
   valid JSON and contains a `components` array.

## Success criteria

- [ ] `build-runtime.sh` for `--target macos-x64 --variant cli` runs
      to completion on a Darwin host (CI `macos-13` runner).
- [ ] `build-runtime.sh` for `--target macos-arm64 --variant cli` runs
      to completion on a Darwin arm64 host (CI `macos-14` runner).
- [ ] Both artifacts are ≤ 1 MiB.
- [ ] Both artifacts produce valid PNG bytes when a frozen `main.py`
      that calls `print("ok")` is embedded.
- [ ] `build-runtime.sh --from-source --target macos-x64` exits with
      a clear error message and non-zero exit code.
- [ ] `docs/macos-unsigned.md` exists and explains the Gatekeeper
      workaround.
- [ ] The CI stub job uploads both artifacts and their SHA256 sidecars.

## Risks

1. **libffi autogen on macOS**: The `autogen.sh` dependency chain on
   macOS may differ from Linux. Mitigation: pre-install `automake
   libtool` via brew in CI. If autogen still fails, ship a pre-generated
   `configure` in the overlay.

2. **mpy-cross compilation fails on arm64**: Some autoconf-generated
   files use `config.sub` which may not recognise `arm64-apple-darwin`
   vs `aarch64-apple-darwin`. Mitigation: MicroPython's submodule has
   an updated `config.sub` that knows both.

3. **Size gate**: Darwin's `clang` with `-Wl,-dead_strip` may produce
   slightly different sizes than Linux gcc with `--gc-sections`. The
   cli variant on Linux is well under 1 MiB; Darwin should be similar
   but may need investigation if it exceeds the gate.

## Model tier recommendation

planner `opus`, developer `sonnet`, sqe `sonnet`, tester `sonnet`.
CI verification work is mechanical once the script changes are in place.
