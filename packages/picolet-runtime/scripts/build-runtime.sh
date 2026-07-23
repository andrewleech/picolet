#!/usr/bin/env bash
# build-runtime.sh — build a picolet runtime artifact.
#
# Usage:
#   ./packages/picolet-runtime/scripts/build-runtime.sh \
#       --target linux-x64 \
#       --variant cli \
#       [--test-romfs <fixture-name>] \
#       [--clean]
#
# Supported targets / variants:
#   --target linux-x64  --variant cli   (builds inside ubuntu:22.04 container)
#   --target windows-x64 --variant cli  (cross-compiles inside dockcross/windows-static-x64-posix)
#
# PH07/PH11 added --variant webview and --variant lvgl.
# picolet-tui Phase 2a adds --variant tui (linux-x64 + windows-x64).
# claude-net-mpy P2 adds --variant mcp (linux-x64 only; cli baseline + TLS).
#
# Outputs:
#   packages/picolet-runtime/build/picolet-runtime-{target}-{variant}[.exe]
#
# The build script is idempotent on a warm tree (make skips up-to-date
# objects; libffi is only rebuilt if its sources changed).  Build outputs
# land under the bind-mounted repo tree so they persist across container
# runs.
#
# Prerequisites (linux-x64/cli):
#   - docker (runs compilation inside ubuntu:22.04; image is built on first run)
#   - python3 + mpremote (pip install mpremote)  — host-side romfs assembly only
#   - strip (binutils)  — host-side final strip pass
#   - git (for submodule checks)
#
# Prerequisites (windows-x64/cli):
#   - docker with dockcross/windows-static-x64-posix image
#   - python3 + mpremote  — host-side romfs assembly only
#   - git

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/../.." && pwd)"
SUBMODULE="$PKG_ROOT/micropython"

export PICOLET_RUNTIME_ROOT="$PKG_ROOT"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

TARGET=""
VARIANT=""
CLEAN=0
FROM_SOURCE=0
TEST_ROMFS=""   # empty by default; pass --test-romfs <fixture> to embed a test romfs

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            TARGET="$2"; shift 2 ;;
        --variant)
            VARIANT="$2"; shift 2 ;;
        --clean)
            CLEAN=1; shift ;;
        --from-source)
            FROM_SOURCE=1; shift ;;
        --test-romfs)
            TEST_ROMFS="$2"; shift 2 ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "usage: $0 --target <target> --variant <variant> [--clean] [--from-source] [--test-romfs <fixture>]" >&2
            exit 1 ;;
    esac
done

if [[ -z "$TARGET" || -z "$VARIANT" ]]; then
    echo "error: --target and --variant are required" >&2
    exit 1
fi

# FR-BP-MAC-3: reject --from-source for macOS targets.
if [[ "$FROM_SOURCE" -eq 1 && "$TARGET" == macos-* ]]; then
    echo "error: --from-source for macos-* targets is not supported in v1.2." >&2
    echo "       Use 'picolet build --target $TARGET' to download the pre-built artifact," >&2
    echo "       or trigger a CI build manually." >&2
    exit 1
fi

# Validate combinations; unsupported ones exit early with a clear message so
# PH07/PH11 only have to add a branch, not redesign the contract.
case "${TARGET}/${VARIANT}" in
    linux-x64/cli)
        ;;
    windows-x64/cli)
        ;;
    linux-x64/mcp)
        # P2 (claude-net-mpy plugin): cli baseline plus TLS.  Built as
        # linux-x64/cli plus MICROPY_PY_SSL=1/MICROPY_SSL_MBEDTLS=1 (set
        # in the variant's own mpconfigvariant.mk, not passed here) and
        # hashlib.sha1 for Sec-WebSocket-Accept verification.  NFR-MCP-1
        # ceiling is 1 MiB, same as cli.  Windows is out of scope until P9.
        ;;
    linux-x64/webview)
        # PH07: WebKitGTK 4.1 webview variant.  Built as linux-x64/cli
        # plus the picolet_ui frozen manifest; libffi loads webkit2gtk-4.1
        # dynamically at runtime.  NFR-2 ceiling is 2 MiB, not 1 MiB.
        ;;
    linux-x64/lvgl)
        # PH11: SDL2-backed LVGL variant.  USER_C_MODULES points at
        # the lv_binding_micropython submodule under overlay/lib/.
        # NFR-3 ceiling is 2 MiB.
        ;;
    linux-x64/tui)
        # picolet-tui Phase 2a: Textual-inspired TUI framework.  Adds
        # the `tuiterm` C module (variants/tui/unix/tuiterm.c, ~250 LoC,
        # termios + SIGWINCH per docs/tui/research/04-terminal-handling.md
        # §4) and freezes the picolet_tui Python package.  NFR-TUI-1
        # ceiling is 2 MiB (matches webview, not cli).
        ;;
    windows-x64/tui)
        # picolet-tui Phase 2a (Windows leg).  Adds variants/tui/windows/
        # tuiterm.c (~300 LoC, conhost VT setup per research doc 04 §2).
        # No extra link deps beyond what MinGW pulls by default
        # (kernel32 is implicit).  NFR-TUI-1 ceiling is 2 MiB.
        ;;
    windows-x64/webview)
        # PH10: WebView2 (Edge Chromium) webview variant.  Built as
        # windows-x64/cli plus the picolet_webview2 C overlay + the
        # picolet_ui frozen manifest (which selects the win32 backend
        # via sys.platform at import time); WebView2Loader.dll is
        # dlopen'd at runtime from the app romfs.  NFR-2 ceiling is
        # 2 MiB.
        ;;
    windows-x64/lvgl)
        # PH12: SDL2 dynamic backend via upstream MinGW binary release.
        ;;
    macos-x64/cli)
        # PH24: native macOS x64 cli variant.  Builds on macos-13 CI runner.
        ;;
    macos-x64/webview)
        # PH25: WKWebView variant for macOS x64.  Builds on macos-13 CI runner.
        # mpconfigvariant.mk adds picolet_webview_mac.c and the Apple framework
        # linker flags when uname -s == Darwin.
        ;;
    macos-x64/lvgl)
        # PH27: SDL2/LVGL variant — not yet implemented.
        ;;
    macos-arm64/cli)
        # PH24: native macOS arm64 cli variant.  Builds on macos-14 CI runner.
        ;;
    macos-arm64/webview)
        # PH25: WKWebView variant for macOS arm64.  Builds on macos-14 CI runner.
        # mpconfigvariant.mk adds picolet_webview_mac.c and the Apple framework
        # linker flags when uname -s == Darwin.
        ;;
    macos-arm64/lvgl)
        # PH27: SDL2/LVGL variant — not yet implemented.
        ;;
    *)
        echo "error: unsupported target/variant combination: $TARGET/$VARIANT" >&2
        exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Derived names (common to both targets)
# ---------------------------------------------------------------------------

VARIANT_NAME="picolet-${VARIANT}"          # e.g. picolet-cli
BUILD_DIR="$PKG_ROOT/build"
UNIX_PORT="$SUBMODULE/ports/unix"

# PICOLET_RUNTIME: absolute path to packages/picolet-runtime/ — passed to make
# as PICOLET_RUNTIME_ROOT so variant .mk files can reference out-of-tree paths.
PICOLET_RUNTIME="$(realpath "$PKG_ROOT")"

# Out-of-tree variant dirs (new layout: variants/<variant>/<port>/).
VARIANT_DIR_UNIX="$PICOLET_RUNTIME/variants/$VARIANT/unix"
VARIANT_DIR_WINDOWS="$PICOLET_RUNTIME/variants/$VARIANT/windows"

# ---------------------------------------------------------------------------
# linux-x64 build container setup
#
# Compilation runs inside picolet-linux-x64-build:22.04 (ubuntu:22.04 +
# build-essential + pkg-config + python3) to pin the resulting binary's
# minimum GLIBC requirement to 2.35.  Building on the host (Ubuntu 24.04
# / glibc 2.39 / gcc 13) silently emits GLIBC_2.38 versioned symbols
# (__isoc23_sscanf, fmod) that break on 22.04 at runtime.
#
# The image is built from the Dockerfile in scripts/dockerfiles/linux-x64-build/
# on first run and cached by Docker.  Subsequent runs skip the build step.
# Build outputs land under the bind-mounted repo tree and are reused across
# runs (make's normal incremental behaviour).
# ---------------------------------------------------------------------------

LINUX_BUILD_IMAGE="picolet-linux-x64-build:22.04"
LINUX_DOCKERFILE="$SCRIPT_DIR/dockerfiles/linux-x64-build/Dockerfile"

# Helper: run a command inside the linux build container with the full repo
# bind-mounted.  Usage: docker_linux <working-dir> <cmd...>
docker_linux() {
    local workdir="$1"; shift
    docker run --rm \
        -v "$REPO_ROOT:$REPO_ROOT" \
        -w "$workdir" \
        --user "$(id -u):$(id -g)" \
        "$LINUX_BUILD_IMAGE" \
        "$@"
}

# ---------------------------------------------------------------------------
# windows-x64 build container setup (dockcross MinGW cross-compile)
#
# dockcross/windows-static-x64-posix provides a statically linked MinGW-w64
# cross toolchain targeting x86_64 Windows.  The output is a PE-COFF .exe
# that runs under WSL interop and natively on Windows 10+.
# ---------------------------------------------------------------------------

DOCKCROSS_IMAGE="dockcross/windows-static-x64-posix:latest"
CROSS="x86_64-w64-mingw32.static.posix-"

# Helper: run a command inside the dockcross Windows container.
# Usage: docker_windows <working-dir> <cmd...>
docker_windows() {
    local workdir="$1"; shift
    docker run --rm \
        -v "$REPO_ROOT:$REPO_ROOT" \
        -w "$workdir" \
        --user "$(id -u):$(id -g)" \
        "$DOCKCROSS_IMAGE" \
        "$@"
}

# ---------------------------------------------------------------------------
# Shared step: build embedded romfs image (host — pure Python, no compiler)
#
# Default: empty romfs (4-byte d2 cd 31 00 sentinel) — the runtime ships
# as a clean blank slate; user romfs is appended at picolet-build time (PH03).
#
# With --test-romfs <fixture>: embed the named fixture from tests/phase-0*/
# for per-phase smoke tests.
# ---------------------------------------------------------------------------

build_romfs_image() {
    local staging="$1"
    local port_dir="$2"   # used to compute relative path for Make
    ROMFS_STAGING="$BUILD_DIR/romfs_staging"
    mkdir -p "$ROMFS_STAGING"

    if [[ -z "$TEST_ROMFS" ]]; then
        echo "[5/8] Building empty embedded romfs (default — blank-slate runtime)"
        local empty_dir="$ROMFS_STAGING/empty_romfs_src"
        mkdir -p "$empty_dir"
        # An empty source directory produces the 4-byte d2 cd 31 00 sentinel.
        python3 -m mpremote romfs --output "$ROMFS_STAGING/empty.romfs" build "$empty_dir"
        ROMFS_IMG_SAFE="$ROMFS_STAGING/picolet_romfs_empty.romfs"
        cp "$ROMFS_STAGING/empty.romfs" "$ROMFS_IMG_SAFE"
    else
        echo "[5/8] Building test romfs from tests/phase-*/${TEST_ROMFS}"
        # Search for the fixture in any phase test dir under PKG_ROOT/tests.
        local fixture=""
        for dir in "$PKG_ROOT/tests"/phase-*/; do
            if [[ -d "$dir/$TEST_ROMFS" ]]; then
                fixture="$dir/$TEST_ROMFS"
                break
            fi
        done
        if [[ -z "$fixture" ]]; then
            echo "error: romfs fixture '$TEST_ROMFS' not found under $PKG_ROOT/tests/phase-*/" >&2
            exit 1
        fi
        python3 -m mpremote romfs --output "$ROMFS_STAGING/${TEST_ROMFS}.romfs" build "$fixture"
        # Use a safe name (underscores only) for the embedded binary symbol.
        # objcopy converts ALL non-alphanumeric chars to _ in symbol names;
        # the Makefile's $(subst) does not, causing a symbol rename mismatch.
        ROMFS_IMG_SAFE="$ROMFS_STAGING/picolet_romfs_${TEST_ROMFS}.romfs"
        cp "$ROMFS_STAGING/${TEST_ROMFS}.romfs" "$ROMFS_IMG_SAFE"
    fi

    # Relative path from port_dir to romfs image (used by Make's ROMFS_IMG variable).
    ROMFS_IMG_REL="$(realpath --relative-to="$port_dir" "$ROMFS_IMG_SAFE")"
    echo "  romfs image: $ROMFS_IMG_SAFE ($(wc -c < "$ROMFS_IMG_SAFE") bytes, relative: $ROMFS_IMG_REL)"
}

# ---------------------------------------------------------------------------
# Shared step: assert stock runtime tail + write version sidecar
# ---------------------------------------------------------------------------

finish_artifact() {
    local artifact="$1"
    local artifact_name="$(basename "$artifact")"

    # Step [7a] — Assert the stock runtime's last 4 bytes are not "PYLT".
    # romfs_trailer.c has "PYLT" as a .rodata string (for the memcmp), so
    # `strings | grep PYLT` always fires.  Check the actual tail bytes.
    echo "  [7a] Asserting stock runtime tail does not match trailer magic"
    LAST4="$(tail -c 4 "$artifact" | od -An -tx1 | tr -d ' \n')"
    if [[ "$LAST4" == "50594c54" ]]; then
        echo "error: [7a] last 4 bytes of $artifact_name are 'PYLT' (50 59 4c 54)" >&2
        echo "       The stock runtime would false-positive the trailer detector." >&2
        echo "       Bump the magic to a longer/less-printable value." >&2
        exit 1
    fi
    echo "  [7a] OK: stock runtime tail ($LAST4) does not match trailer magic"

    # Step [7b] — Write a .version sidecar (mpy-cross bytecode format token).
    VERSION_FILE="${artifact}.version"
    MPY_CROSS_SHA="$(git -C "$SUBMODULE" rev-parse --short HEAD)"
    MPY_CROSS_BIN="$SUBMODULE/mpy-cross/build/mpy-cross"
    if [[ -f "$MPY_CROSS_BIN" ]]; then
        MPY_VER_LINE="$("$MPY_CROSS_BIN" --version 2>&1 || true)"
        MPY_VER="$(echo "$MPY_VER_LINE" | grep -oE 'mpy v[0-9]+\.[0-9]+' || echo "$MPY_CROSS_SHA")"
    else
        MPY_VER="$MPY_CROSS_SHA"
    fi
    echo "$MPY_VER" > "$VERSION_FILE"
    echo "  [7b] version sidecar: $VERSION_FILE ($MPY_VER)"

    # Step [8/8] — size gate.  Variant-specific NFR ceiling.
    #   cli                → NFR-1, 1 MiB.
    #   mcp                → NFR-MCP-1, 1 MiB (cli baseline + TLS; SSL/mbedtls
    #     and hashlib.sha1 fit within the same ceiling as cli, per the P2
    #     mcp-variant ticket's measured headroom).
    #   webview            → NFR-2, 2 MiB.
    #   lvgl (linux-x64)   → NFR-3, 2 MiB.
    #   lvgl (windows-x64) → NFR-3, 3 MiB.
    #   tui                → NFR-TUI-1, 2 MiB.  Sub-budgets on the frozen
    #     picolet_tui .mpy (NFR-TUI-19: core ≤ 120 KiB, _rich ≤ 60 KiB,
    #     _shims ≤ 20 KiB) are gated separately at Phase 3+ via
    #     `picolet inspect-romfs` once that surface exists.
    #     The windows-x64/lvgl ceiling is relaxed to 3 MiB because SDL2 is now
    #     sourced from the official upstream MinGW binary release (SDL2.dll,
    #     dynamic linkage) rather than a custom from-source build with
    #     -ffunction-sections + --gc-sections.  The aggressive size-reduction
    #     approach (custom SDL2 build targeting < 2 MiB) is deferred to the
    #     roadmap.  3 MiB provides ~1 MiB headroom over the observed build size.
    #   macos-{x64,arm64}  → NFR-MAC-1/2/3 (same ceilings as Linux/Windows).
    case "${TARGET:-linux-x64}/${VARIANT}" in
        linux-x64/cli)       CEILING=1048576;  NFR_ID="NFR-1" ;;
        linux-x64/mcp)       CEILING=1048576;  NFR_ID="NFR-MCP-1" ;;
        linux-x64/webview)   CEILING=2097152;  NFR_ID="NFR-2" ;;
        linux-x64/lvgl)      CEILING=2097152;  NFR_ID="NFR-3" ;;
        linux-x64/tui)       CEILING=2097152;  NFR_ID="NFR-TUI-1" ;;
        windows-x64/cli)     CEILING=1048576;  NFR_ID="NFR-1" ;;
        windows-x64/webview) CEILING=2097152;  NFR_ID="NFR-2" ;;
        windows-x64/lvgl)    CEILING=3145728;  NFR_ID="NFR-3" ;;
        windows-x64/tui)     CEILING=2097152;  NFR_ID="NFR-TUI-1" ;;
        macos-x64/cli)       CEILING=1048576;  NFR_ID="NFR-MAC-1" ;;
        macos-x64/webview)   CEILING=2097152;  NFR_ID="NFR-MAC-2" ;;
        macos-x64/lvgl)      CEILING=2097152;  NFR_ID="NFR-MAC-3" ;;
        macos-arm64/cli)     CEILING=1048576;  NFR_ID="NFR-MAC-1" ;;
        macos-arm64/webview) CEILING=2097152;  NFR_ID="NFR-MAC-2" ;;
        macos-arm64/lvgl)    CEILING=2097152;  NFR_ID="NFR-MAC-3" ;;
        *)                   CEILING=1048576;  NFR_ID="NFR-1" ;;
    esac
    SIZE=$(wc -c < "$artifact")
    echo "[8/8] Checking binary size ($NFR_ID: ≤ $CEILING bytes)"
    if [[ "$SIZE" -gt "$CEILING" ]]; then
        echo "error: $NFR_ID VIOLATED: $artifact_name is $SIZE bytes (ceiling $CEILING bytes)" >&2
        exit 1
    fi
    PCT=$(( SIZE * 100 / CEILING ))
    echo "  size: $SIZE bytes (${PCT}% of $NFR_ID ceiling of $CEILING bytes)"
    echo
    echo "=== Build complete: $artifact ==="
}

# ---------------------------------------------------------------------------
# linux-x64/cli build
# ---------------------------------------------------------------------------

build_linux_x64() {
    local artifact_name="picolet-runtime-linux-x64-${VARIANT}"
    local artifact="$BUILD_DIR/$artifact_name"
    local variant_build="$UNIX_PORT/build-${VARIANT_NAME}"
    local libffi_ffi_h="$variant_build/lib/libffi/include/ffi.h"
    local libffi_src="$SUBMODULE/lib/libffi"

    echo "[0/8] Ensuring linux build image: $LINUX_BUILD_IMAGE"
    if ! docker image inspect "$LINUX_BUILD_IMAGE" >/dev/null 2>&1; then
        echo "  image not found; building from $LINUX_DOCKERFILE"
        docker build -t "$LINUX_BUILD_IMAGE" "$(dirname "$LINUX_DOCKERFILE")"
    else
        echo "  image present; skipping build"
    fi

    echo "[1/8] Checking integration branch"
    if [[ "$CLEAN" -eq 1 ]]; then
        echo "  --clean: re-running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    elif ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
        echo "  integration branch not found; running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    else
        echo "  integration branch warm; skipping rebuild"
    fi

    git -C "$SUBMODULE" checkout integration --quiet
    echo "  updating nested submodules"
    git -C "$SUBMODULE" submodule update --init --recursive --quiet

    echo "[2/8] Verifying submodule presence"
    if [[ ! -d "$SUBMODULE/extmod/asyncio" ]]; then
        echo "error: $SUBMODULE/extmod/asyncio not found." >&2
        exit 1
    fi
    if [[ ! -d "$SUBMODULE/lib/micropython-lib/python-stdlib/os-path" ]]; then
        echo "error: micropython-lib/python-stdlib/os-path not found." >&2
        echo "       Run: git -C $SUBMODULE submodule update --init --recursive" >&2
        exit 1
    fi

    # lvgl variant: init lv_binding_micropython (now at lib/, not overlay/lib/).
    if [[ "$VARIANT" == "lvgl" ]]; then
        local lvbm_dir="$PKG_ROOT/lib/lv_binding_micropython"
        echo "  ensuring lv_binding_micropython nested submodules"
        if [[ ! -d "$lvbm_dir/lvgl/src" ]] || [[ ! -d "$lvbm_dir/pycparser/pycparser" ]]; then
            git -C "$REPO_ROOT" submodule update --init --recursive \
                packages/picolet-runtime/lib/lv_binding_micropython --quiet
        fi
        if [[ ! -d "$lvbm_dir/lvgl/src" ]]; then
            echo "error: lvgl source tree not present after submodule update" >&2
            exit 1
        fi
    fi

    echo "[3/8] Building mpy-cross (inside $LINUX_BUILD_IMAGE)"
    docker_linux "$SUBMODULE/mpy-cross" make -j

    echo "[4/8] Fetching port submodules (libffi)"
    make -C "$UNIX_PORT" -j submodules \
        VARIANT_DIR="$VARIANT_DIR_UNIX" \
        BUILD="build-${VARIANT_NAME}" \
        MICROPY_STANDALONE=1

    # Warm-cache mitigation: when ffi.h and libffi.a already exist from a prior
    # build, touch all build-dir timestamps so make does not try to re-run
    # configure/autogen.  Needed after rebuild-integration.sh reinitialises
    # the libffi submodule (removing generated files like Makefile.in, missing).
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  libffi: warm cache; touching build timestamps to skip re-configure"
        if [[ ! -f "$libffi_src/configure" ]]; then
            touch "$libffi_src/configure"
            chmod +x "$libffi_src/configure"
        fi
        find "$variant_build/lib/libffi" -type f -exec touch {} \;
    elif [[ ! -f "$libffi_src/configure" ]]; then
        # Cold cache and no pre-generated configure.  The ubuntu:22.04 build
        # container ships libtool 2.4.6 which lacks the LT_SYS_SYMBOL_USCORE
        # macro required by libffi's autogen.sh, so autogen *inside* the
        # container fails.  Workaround: run autogen on the *host* if it has
        # a newer libtool (Ubuntu 24.04 ships 2.4.7+, Homebrew 2.5.4).
        # The generated `configure` is portable; the actual compile then
        # proceeds inside the container as normal.
        echo "  libffi: cold cache and no configure — running autogen on host"
        if command -v libtoolize >/dev/null 2>&1; then
            (cd "$libffi_src" && ./autogen.sh) >/dev/null 2>&1 || {
                echo "  libffi: host autogen.sh failed; need libtool 2.4.7+ on host or pre-shipped configure" >&2
                exit 1
            }
        else
            echo "  libffi: no host libtoolize; cannot bootstrap libffi configure" >&2
            exit 1
        fi
    fi

    build_romfs_image "$BUILD_DIR" "$UNIX_PORT"

    echo "[6/8] Building unix port variant=${VARIANT_NAME} (inside $LINUX_BUILD_IMAGE)"
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        docker_linux "$UNIX_PORT" make \
            -j \
            VARIANT_DIR="$VARIANT_DIR_UNIX" \
            BUILD="build-${VARIANT_NAME}" \
            MICROPY_STANDALONE=1 \
            PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME" \
            deplibs
    fi
    docker_linux "$UNIX_PORT" make \
        -j \
        VARIANT_DIR="$VARIANT_DIR_UNIX" \
        BUILD="build-${VARIANT_NAME}" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME"

    echo "[7/8] Stripping and installing artifact"
    local built_binary="$variant_build/micropython"
    if [[ ! -f "$built_binary" ]]; then
        echo "error: expected binary not found: $built_binary" >&2
        exit 1
    fi
    mkdir -p "$BUILD_DIR"
    cp "$built_binary" "$artifact"
    strip --strip-unneeded "$artifact"
    echo "  artifact: $artifact"

    # [7c] Assert the mcp-linux binary has NO dynamic mbedtls/libffi dependency
    # beyond the ubiquitous system libraries every Linux install ships.
    # mbedtls ships both a static archive and a shared library form; if the
    # link step ever silently prefers the shared form, the single-binary
    # guarantee breaks while still passing the NFR-MCP-1 size gate (a dynamic
    # mbedtls would even shrink the artifact) — so this must be an explicit
    # check, not an inference from binary size.
    if [[ "$VARIANT" == "mcp" ]]; then
        local ALLOWED_NEEDED='^(libc\.so\.6|libm\.so\.6|libpthread\.so\.0|libdl\.so\.2|librt\.so\.1|ld-linux-x86-64\.so\.2|linux-vdso\.so\.1)$'
        local NEEDED
        NEEDED="$(objdump -p "$artifact" | awk '/NEEDED/ {print $2}')"
        if [[ -z "$NEEDED" ]]; then
            echo "error: [7c] objdump produced no NEEDED entries for $artifact_name (unexpected)" >&2
            exit 1
        fi
        local BAD
        BAD="$(echo "$NEEDED" | grep -vE "$ALLOWED_NEEDED" || true)"
        if [[ -n "$BAD" ]]; then
            echo "error: [7c] $artifact_name links non-system shared libraries:" >&2
            echo "$BAD" >&2
            echo "       mbedtls/libffi must link statically (single-binary rule)." >&2
            exit 1
        fi
        echo "  [7c] single-binary check OK: only system libs in NEEDED ($(echo "$NEEDED" | tr '\n' ' '))"
    fi

    finish_artifact "$artifact"

    # [PH13] Emit runtime SBOM sidecar (FR-SBOM-1, NFR-7).
    # Called on the host shell after the container exits — see Decision commit
    # (AD5 / Risk 1 mitigation).  The artifact is already in BUILD_DIR;
    # picolet-cli is importable from the host Python via PYTHONPATH.
    local sbom_out="${artifact}.cdx.json"
    echo "[SBOM] Emitting runtime SBOM: $sbom_out"
    PYTHONPATH="$REPO_ROOT/packages/picolet" python3 -m picolet.cli.sbom_gen emit-runtime \
        --output "$sbom_out" \
        --target "$TARGET" \
        --variant "$VARIANT" \
        --repo-root "$REPO_ROOT" \
        --artifact "$artifact"
}

# ---------------------------------------------------------------------------
# macos-x64 / macos-arm64 build (native on CI runner — no Docker)
#
# FR-BP-MAC-4: runs natively on Darwin using the system clang.
# FR-BP-MAC-5: guarded by uname -s == Darwin; exits with an actionable
#              error if invoked on a non-Darwin host (e.g. Linux CI).
# NFR-MAC-9:   MACOSX_DEPLOYMENT_TARGET=11.0 (Big Sur) for both arches.
# ---------------------------------------------------------------------------

build_macos() {
    # FR-BP-MAC-5: guard — must run on Darwin.
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "error: macos-* targets must be built on a Darwin host." >&2
        echo "       This script was invoked on $(uname -s)." >&2
        echo "       Trigger the GitHub Actions release workflow to build macOS artifacts." >&2
        exit 1
    fi

    local artifact_name="picolet-runtime-${TARGET}-${VARIANT}"
    local artifact="$BUILD_DIR/$artifact_name"
    local variant_build="$UNIX_PORT/build-${VARIANT_NAME}"
    local libffi_ffi_h="$variant_build/lib/libffi/include/ffi.h"
    local libffi_src="$SUBMODULE/lib/libffi"

    # NFR-MAC-9: deployment target 11.0 for both x64 and arm64.
    export MACOSX_DEPLOYMENT_TARGET=11.0

    echo "[1/8] Checking integration branch"
    if [[ "$CLEAN" -eq 1 ]]; then
        echo "  --clean: re-running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    elif ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
        echo "  integration branch not found; running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    else
        echo "  integration branch warm; skipping rebuild"
    fi

    git -C "$SUBMODULE" checkout integration --quiet
    echo "  updating nested submodules"
    git -C "$SUBMODULE" submodule update --init --recursive --quiet

    echo "[2/8] Verifying submodule presence"
    if [[ ! -d "$SUBMODULE/extmod/asyncio" ]]; then
        echo "error: $SUBMODULE/extmod/asyncio not found." >&2
        exit 1
    fi
    if [[ ! -d "$SUBMODULE/lib/micropython-lib/python-stdlib/os-path" ]]; then
        echo "error: micropython-lib/python-stdlib/os-path not found." >&2
        echo "       Run: git -C $SUBMODULE submodule update --init --recursive" >&2
        exit 1
    fi

    # lvgl variant — init lv_binding_micropython (now at lib/) and locate brew SDL2.
    local SDL2_INCLUDE_DIR="" SDL2_LIB_DIR=""
    if [[ "$VARIANT" == "lvgl" ]]; then
        local lvbm_dir="$PKG_ROOT/lib/lv_binding_micropython"
        echo "  ensuring lv_binding_micropython nested submodules"
        if [[ ! -d "$lvbm_dir/lvgl/src" ]] || [[ ! -d "$lvbm_dir/pycparser/pycparser" ]]; then
            git -C "$REPO_ROOT" submodule update --init --recursive \
                packages/picolet-runtime/lib/lv_binding_micropython --quiet
        fi
        if [[ ! -d "$lvbm_dir/lvgl/src" ]]; then
            echo "error: lvgl source tree not present after submodule update" >&2
            exit 1
        fi

        # Locate SDL2 via brew.  brew --prefix sdl2 returns the formula prefix:
        #   Intel:  /usr/local/opt/sdl2
        #   ARM64:  /opt/homebrew/opt/sdl2
        echo "  locating SDL2 via Homebrew"
        if ! command -v brew >/dev/null 2>&1; then
            echo "error: brew not found; install Homebrew and run: brew install sdl2" >&2
            exit 1
        fi
        SDL2_PREFIX="$(brew --prefix sdl2 2>/dev/null)" || {
            echo "error: sdl2 not installed; run: brew install sdl2" >&2
            exit 1
        }
        SDL2_INCLUDE_DIR="${SDL2_PREFIX}/include"
        SDL2_LIB_DIR="${SDL2_PREFIX}/lib"
        echo "  SDL2 prefix: $SDL2_PREFIX"
    fi

    echo "[3/8] Building mpy-cross (native macOS host binary)"
    make -C "$SUBMODULE/mpy-cross" -j

    echo "[4/8] Fetching port submodules (libffi)"
    make -C "$UNIX_PORT" -j submodules \
        VARIANT_DIR="$VARIANT_DIR_UNIX" \
        BUILD="build-${VARIANT_NAME}" \
        MICROPY_STANDALONE=1

    # Warm-cache mitigation: same logic as the Linux path.
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  libffi: warm cache; touching build timestamps to skip re-configure"
        if [[ ! -f "$libffi_src/configure" ]]; then
            touch "$libffi_src/configure"
            chmod +x "$libffi_src/configure"
        fi
        find "$variant_build/lib/libffi" -type f -exec touch {} \;
    elif [[ ! -f "$libffi_src/configure" ]]; then
        # Cold cache on macOS.  autogen.sh needs automake + libtool (from
        # Homebrew: brew install automake libtool).  The CI setup step
        # installs these before invoking this script.
        echo "  libffi: cold cache and no configure — running autogen on host"
        if command -v libtoolize >/dev/null 2>&1; then
            (cd "$libffi_src" && ./autogen.sh) >/dev/null 2>&1 || {
                echo "  libffi: autogen.sh failed; ensure automake + libtool are installed" >&2
                echo "  Hint: brew install automake libtool" >&2
                exit 1
            }
        else
            echo "  libffi: no libtoolize; install via: brew install automake libtool" >&2
            exit 1
        fi
    fi

    build_romfs_image "$BUILD_DIR" "$UNIX_PORT"

    echo "[6/8] Building unix port variant=${VARIANT_NAME} (native macOS)"

    # Build extra Make variables for lvgl variant (SDL2 paths).
    local EXTRA_MAKE_VARS=()
    if [[ "$VARIANT" == "lvgl" && -n "$SDL2_INCLUDE_DIR" ]]; then
        EXTRA_MAKE_VARS+=(
            "SDL2_INCLUDE_DIR=${SDL2_INCLUDE_DIR}"
            "SDL2_LIB_DIR=${SDL2_LIB_DIR}"
        )
    fi

    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        make -C "$UNIX_PORT" \
            -j \
            VARIANT_DIR="$VARIANT_DIR_UNIX" \
            BUILD="build-${VARIANT_NAME}" \
            MICROPY_STANDALONE=1 \
            PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME" \
            "${EXTRA_MAKE_VARS[@]}" \
            deplibs
    fi
    make -C "$UNIX_PORT" \
        -j \
        VARIANT_DIR="$VARIANT_DIR_UNIX" \
        BUILD="build-${VARIANT_NAME}" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME" \
        "${EXTRA_MAKE_VARS[@]}"

    echo "[7/8] Stripping and installing artifact"
    local built_binary="$variant_build/micropython"
    if [[ ! -f "$built_binary" ]]; then
        echo "error: expected binary not found: $built_binary" >&2
        exit 1
    fi
    mkdir -p "$BUILD_DIR"
    cp "$built_binary" "$artifact"
    # Darwin strip uses -x (strip debug symbols, keep global symbols).
    # --strip-unneeded is a GNU binutils flag not present on macOS.
    strip -x "$artifact"
    echo "  artifact: $artifact"

    finish_artifact "$artifact"

    # [PH13] Emit runtime SBOM sidecar (FR-BP-MAC-6).
    local sbom_out="${artifact}.cdx.json"
    echo "[SBOM] Emitting runtime SBOM: $sbom_out"
    PYTHONPATH="$REPO_ROOT/packages/picolet" python3 -m picolet.cli.sbom_gen emit-runtime \
        --output "$sbom_out" \
        --target "$TARGET" \
        --variant "$VARIANT" \
        --repo-root "$REPO_ROOT" \
        --artifact "$artifact"
}

# ---------------------------------------------------------------------------
# windows-x64/cli build (dockcross MinGW cross-compile)
# ---------------------------------------------------------------------------

build_windows_x64() {
    local artifact_name="picolet-runtime-windows-x64-${VARIANT}.exe"
    local artifact="$BUILD_DIR/$artifact_name"
    local windows_port="$SUBMODULE/ports/windows"
    local variant_build="$windows_port/build-${VARIANT_NAME}"
    local libffi_ffi_h="$variant_build/lib/libffi/include/ffi.h"
    local libffi_src="$SUBMODULE/lib/libffi"
    # SDL2 host-side cache dir (set in [2b/8] for lvgl; empty for other variants).
    local SDL2_UPSTREAM_DIR=""

    echo "[0/8] Ensuring dockcross image: $DOCKCROSS_IMAGE"
    if ! docker image inspect "$DOCKCROSS_IMAGE" >/dev/null 2>&1; then
        echo "  image not found; pulling $DOCKCROSS_IMAGE (~1.5 GB)"
        docker pull "$DOCKCROSS_IMAGE"
    else
        echo "  image present; skipping pull"
    fi

    echo "[1/8] Checking integration branch"
    if [[ "$CLEAN" -eq 1 ]]; then
        echo "  --clean: re-running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    elif ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
        echo "  integration branch not found; running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    else
        echo "  integration branch warm; skipping rebuild"
    fi

    git -C "$SUBMODULE" checkout integration --quiet
    echo "  updating nested submodules"
    git -C "$SUBMODULE" submodule update --init --recursive --quiet

    echo "[2/8] Verifying submodule presence"
    if [[ ! -d "$SUBMODULE/extmod/asyncio" ]]; then
        echo "error: $SUBMODULE/extmod/asyncio not found." >&2
        exit 1
    fi
    if [[ ! -d "$SUBMODULE/lib/micropython-lib/python-stdlib/os-path" ]]; then
        echo "error: micropython-lib/python-stdlib/os-path not found." >&2
        echo "       Run: git -C $SUBMODULE submodule update --init --recursive" >&2
        exit 1
    fi

    # lvgl variant: init lv_binding_micropython (now at lib/, not overlay/lib/).
    if [[ "$VARIANT" == "lvgl" ]]; then
        local lvbm_dir="$PKG_ROOT/lib/lv_binding_micropython"
        echo "  ensuring lv_binding_micropython nested submodules"
        if [[ ! -d "$lvbm_dir/lvgl/src" ]] || [[ ! -d "$lvbm_dir/pycparser/pycparser" ]]; then
            git -C "$REPO_ROOT" submodule update --init --recursive \
                packages/picolet-runtime/lib/lv_binding_micropython --quiet
        fi
        if [[ ! -d "$lvbm_dir/lvgl/src" ]]; then
            echo "error: lvgl source tree not present after submodule update" >&2
            exit 1
        fi
    fi

    # [2b/8] For the lvgl variant, fetch the official SDL2 upstream MinGW
    # binary release.  This provides pre-built headers and SDL2.dll (+ import
    # lib) for x86_64-w64-mingw32, with no custom compile step required.
    #
    # The tarball is downloaded once to build/cache/ and verified by SHA-256;
    # subsequent builds reuse the extracted tree (stamp file guards the
    # extraction).  SDL2.dll is copied alongside the final artifact so end
    # users do not need a separate SDL2 install.
    #
    # Release URL: https://github.com/libsdl-org/SDL/releases/tag/release-2.30.10
    # Tarball:     SDL2-devel-2.30.10-mingw.tar.gz
    #
    # Roadmap: a future pass will switch back to a custom from-source SDL2
    # build with -ffunction-sections + --gc-sections to reclaim the size
    # budget and get back under the 2 MiB NFR-3 target.  See docs/architecture.md.
    if [[ "$VARIANT" == "lvgl" ]]; then
        echo "[2b/8] Ensuring SDL2 upstream MinGW binary release"
        local SDL2_VERSION="2.30.10"
        local SDL2_TARBALL="SDL2-devel-${SDL2_VERSION}-mingw.tar.gz"
        local SDL2_URL="https://github.com/libsdl-org/SDL/releases/download/release-${SDL2_VERSION}/${SDL2_TARBALL}"
        local SDL2_SHA256="a7763f9439ea25685b053e9257dac1eac012e5cd0824f1a801b27b1d92ebe321"
        local SDL2_CACHE_DIR="$BUILD_DIR/cache"
        local SDL2_TARBALL_PATH="$SDL2_CACHE_DIR/$SDL2_TARBALL"
        local SDL2_EXTRACT_DIR="$BUILD_DIR/sdl2-mingw-${SDL2_VERSION}"
        local SDL2_MINGW_ROOT="$SDL2_EXTRACT_DIR/SDL2-${SDL2_VERSION}/x86_64-w64-mingw32"
        local SDL2_STAMP="$SDL2_EXTRACT_DIR/extracted-${SDL2_VERSION}.stamp"
        mkdir -p "$SDL2_CACHE_DIR"

        if [[ -f "$SDL2_STAMP" ]] && [[ -d "$SDL2_MINGW_ROOT" ]]; then
            echo "  sdl2: upstream binary cached; skipping download"
        else
            if [[ ! -f "$SDL2_TARBALL_PATH" ]]; then
                echo "  downloading SDL2 upstream MinGW release: $SDL2_TARBALL"
                if ! curl -fsSL --retry 3 -o "$SDL2_TARBALL_PATH" "$SDL2_URL"; then
                    echo "error: SDL2 download failed from $SDL2_URL" >&2
                    echo "       If offline, manually place the tarball at:" >&2
                    echo "       $SDL2_TARBALL_PATH" >&2
                    exit 1
                fi
            fi
            # Verify SHA-256 of the cached tarball.
            local ACTUAL_SHA256
            ACTUAL_SHA256="$(sha256sum "$SDL2_TARBALL_PATH" | awk '{print $1}')"
            if [[ "$ACTUAL_SHA256" != "$SDL2_SHA256" ]]; then
                echo "error: SDL2 tarball SHA-256 mismatch" >&2
                echo "  expected: $SDL2_SHA256" >&2
                echo "  actual:   $ACTUAL_SHA256" >&2
                exit 1
            fi
            echo "  extracting SDL2 upstream MinGW release..."
            mkdir -p "$SDL2_EXTRACT_DIR"
            tar -C "$SDL2_EXTRACT_DIR" -xzf "$SDL2_TARBALL_PATH"
            if [[ ! -d "$SDL2_MINGW_ROOT" ]]; then
                echo "error: expected $SDL2_MINGW_ROOT not found after extract" >&2
                exit 1
            fi
            touch "$SDL2_STAMP"
            echo "  sdl2: upstream binary extracted to $SDL2_MINGW_ROOT"
        fi
        SDL2_UPSTREAM_DIR="$SDL2_MINGW_ROOT"
    fi

    echo "[3/8] Building mpy-cross (inside dockcross — produces Linux ELF host tool)"
    # dockcross includes a Linux GCC alongside MinGW; mpy-cross is a host
    # tool and is built as a Linux binary.  This matches the pydfu precedent.
    docker_windows "$SUBMODULE/mpy-cross" make -j

    echo "[4/8] Fetching port submodules (libffi)"
    # Build up optional Make overrides for lvgl.  SDL2_UPSTREAM_DIR is the
    # x86_64-w64-mingw32 subtree from the official MinGW release tarball;
    # the variant .mk uses SDL2_INCLUDE_DIR and SDL2_LIB_DIR to locate
    # headers and the import library (libSDL2.dll.a).  Built early because
    # every make invocation below parses the variant .mk (which $(error)s
    # if these vars are unset, including for non-build targets like
    # `submodules`).
    local EXTRA_MAKE_VARS=()
    if [[ "$VARIANT" == "lvgl" ]]; then
        EXTRA_MAKE_VARS+=(
            "SDL2_INCLUDE_DIR=${SDL2_UPSTREAM_DIR}/include"
            "SDL2_LIB_DIR=${SDL2_UPSTREAM_DIR}/lib"
        )
    fi

    # The Windows Makefile's deplibs target adds lib/libffi to GIT_SUBMODULES
    # when MICROPY_PY_FFI=1 (set in the variant .mk).  We run `submodules` on
    # the host (pure git op, no compiler needed).
    make -C "$windows_port" -j submodules \
        VARIANT_DIR="$VARIANT_DIR_WINDOWS" \
        BUILD="build-${VARIANT_NAME}" \
        "${EXTRA_MAKE_VARS[@]}"

    # Warm-cache mitigation for libffi: when ffi.h and libffi.a already exist
    # from a prior successful deplibs build, touch all relevant timestamps so
    # make does not try to re-run configure/autogen.  This covers two cases:
    #   1. configure was removed by a submodule reinit (touch it back).
    #   2. configure exists but the build dir's Makefile references source files
    #      (Makefile.in, missing, install-sh) that were also removed by reinit;
    #      touching ffi.h and libffi.a makes them appear newer than any source.
    local libffi_a="$variant_build/lib/libffi/out/lib/libffi.a"
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  libffi: warm cache; touching build timestamps to skip re-configure"
        # Ensure configure exists (may have been removed by submodule reinit).
        if [[ ! -f "$libffi_src/configure" ]]; then
            touch "$libffi_src/configure"
            chmod +x "$libffi_src/configure"
        fi
        # Touch ALL files in the libffi build directory so they appear newer
        # than any source file.  The build-dir's Makefile (generated by a
        # prior configure run) has rules that try to regenerate Makefile.in
        # and other autotools artifacts from the source tree; those source
        # files don't exist after a submodule reinit.  Making every build
        # artifact newer than every source file is the only reliable way to
        # suppress the autotools regeneration chain without modifying the
        # generated Makefiles themselves.
        find "$variant_build/lib/libffi" -type f -exec touch {} \;
        # Also touch the output include dir so the port's libffi rule
        # ($(BUILD)/lib/libffi/out/include/ffi.h: $(TOP)/lib/libffi/configure)
        # sees ffi.h as newer than configure.
        [[ -f "$libffi_a" ]] && touch "$libffi_a"
    fi

    build_romfs_image "$BUILD_DIR" "$windows_port"
    # EXTRA_MAKE_VARS is set above (before [4/8] make submodules).

    echo "[6/8] Building libffi (deplibs) inside dockcross"
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        docker_windows "$windows_port" make \
            -j \
            VARIANT_DIR="$VARIANT_DIR_WINDOWS" \
            BUILD="build-${VARIANT_NAME}" \
            CROSS_COMPILE="$CROSS" \
            PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME" \
            "${EXTRA_MAKE_VARS[@]}" \
            deplibs
    fi

    echo "[6b/8] Building windows port variant=${VARIANT_NAME} inside dockcross"
    docker_windows "$windows_port" make \
        -j \
        VARIANT_DIR="$VARIANT_DIR_WINDOWS" \
        BUILD="build-${VARIANT_NAME}" \
        CROSS_COMPILE="$CROSS" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$PICOLET_RUNTIME" \
        "${EXTRA_MAKE_VARS[@]}"

    echo "[7/8] Stripping and installing artifact"
    local built_binary="$variant_build/micropython.exe"
    if [[ ! -f "$built_binary" ]]; then
        echo "error: expected binary not found: $built_binary" >&2
        exit 1
    fi
    mkdir -p "$BUILD_DIR"
    cp "$built_binary" "$artifact"
    # Strip inside dockcross — the host strip is not MinGW-aware.
    # Propagate both stderr and exit code; silent failures would produce an
    # unstripped binary that only the NFR-1/2 size gate would catch.
    docker_windows "$PKG_ROOT" "${CROSS}strip" --strip-unneeded "$artifact"
    echo "  artifact: $artifact"

    # [7c] Assert the lvgl-windows binary has NO runtime SDL2.dll dependency.
    # Static linkage against libSDL2.a is non-negotiable for this project;
    # the "one binary, no runtime deps" property is a primary user-visible
    # guarantee.  If SDL2.dll appears in the PE import directory the build
    # has silently regressed to the upstream import-lib path — fail loudly.
    if [[ "$VARIANT" == "lvgl" && "$TARGET" == "windows-x64" ]]; then
        local IMPORTS
        IMPORTS="$(docker_windows "$PKG_ROOT" "${CROSS}objdump" -p "$artifact" \
                   | grep -i 'DLL Name:' || true)"
        if echo "$IMPORTS" | grep -qi 'SDL2\.dll'; then
            echo "error: lvgl-windows binary imports SDL2.dll (should be statically linked)" >&2
            echo "$IMPORTS" >&2
            exit 1
        fi
        echo "  [7c] SDL2 statically linked (no SDL2.dll import in PE table)"
    fi

    finish_artifact "$artifact"

    # [PH13] Emit runtime SBOM sidecar (FR-SBOM-1, NFR-7).
    local sbom_out="${artifact}.cdx.json"
    echo "[SBOM] Emitting runtime SBOM: $sbom_out"
    PYTHONPATH="$REPO_ROOT/packages/picolet" python3 -m picolet.cli.sbom_gen emit-runtime \
        --output "$sbom_out" \
        --target "$TARGET" \
        --variant "$VARIANT" \
        --repo-root "$REPO_ROOT" \
        --artifact "$artifact"
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

echo "=== build-runtime.sh: target=$TARGET variant=$VARIANT ==="

if [[ "$TARGET" == "linux-x64" ]]; then
    build_linux_x64
elif [[ "$TARGET" == "windows-x64" ]]; then
    build_windows_x64
elif [[ "$TARGET" == macos-* ]]; then
    build_macos
fi
