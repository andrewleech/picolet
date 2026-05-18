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
# PH07/PH11 will add --variant webview and --variant lvgl.
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
    windows-x64/webview)
        # PH10: WebView2 (Edge Chromium) webview variant.  Built as
        # windows-x64/cli plus the picolet_webview2 C overlay + the
        # picolet_ui frozen manifest (which selects the win32 backend
        # via sys.platform at import time); WebView2Loader.dll is
        # dlopen'd at runtime from the app romfs.  NFR-2 ceiling is
        # 2 MiB.
        ;;
    windows-x64/lvgl)
        # PH12: SDL2 static backend via MXE inside dockcross.
        ;;
    macos-x64/cli)
        # PH24: native macOS x64 cli variant.  Builds on macos-13 CI runner.
        ;;
    macos-x64/webview)
        # PH25/PH26: WKWebView variant — not yet implemented.
        ;;
    macos-x64/lvgl)
        # PH27: SDL2/LVGL variant — not yet implemented.
        ;;
    macos-arm64/cli)
        # PH24: native macOS arm64 cli variant.  Builds on macos-14 CI runner.
        ;;
    macos-arm64/webview)
        # PH25/PH26: WKWebView variant — not yet implemented.
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
    #   webview            → NFR-2, 2 MiB.
    #   lvgl (linux-x64)   → NFR-3, 2 MiB.
    #   lvgl (windows-x64) → NFR-3, 2 MiB.
    #     SDL2 is built from source with -ffunction-sections so --gc-sections
    #     strips unused SDL2 backends at link time.  The prior 4 MiB deviation
    #     (prebuilt archive, no per-function sections) is reverted.
    #   macos-{x64,arm64}  → NFR-MAC-1/2/3 (same ceilings as Linux/Windows).
    case "${TARGET:-linux-x64}/${VARIANT}" in
        linux-x64/cli)       CEILING=1048576;  NFR_ID="NFR-1" ;;
        linux-x64/webview)   CEILING=2097152;  NFR_ID="NFR-2" ;;
        linux-x64/lvgl)      CEILING=2097152;  NFR_ID="NFR-3" ;;
        windows-x64/cli)     CEILING=1048576;  NFR_ID="NFR-1" ;;
        windows-x64/webview) CEILING=2097152;  NFR_ID="NFR-2" ;;
        windows-x64/lvgl)    CEILING=2097152;  NFR_ID="NFR-3" ;;
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
        if [[ ! -d "$UNIX_PORT/variants/${VARIANT_NAME}" ]]; then
            echo "  overlay not applied; running rebuild-integration.sh"
            "$SCRIPT_DIR/rebuild-integration.sh"
        else
            echo "  integration branch warm; skipping rebuild"
        fi
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

    # PH11: lvgl variant pulls lv_binding_micropython (+ its nested
    # lvgl/lvgl and pycparser submodules) under overlay/lib/.  Init
    # them here so the USER_C_MODULES path is populated before make.
    if [[ "$VARIANT" == "lvgl" ]]; then
        local lvbm_dir="$PKG_ROOT/overlay/lib/lv_binding_micropython"
        echo "  ensuring lv_binding_micropython nested submodules"
        if [[ ! -d "$lvbm_dir/lvgl/src" ]] || [[ ! -d "$lvbm_dir/pycparser/pycparser" ]]; then
            git -C "$lvbm_dir" submodule update --init --recursive --quiet
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
        VARIANT="${VARIANT_NAME}" \
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
            VARIANT="${VARIANT_NAME}" \
            MICROPY_STANDALONE=1 \
            PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
            deplibs
    fi
    docker_linux "$UNIX_PORT" make \
        -j \
        VARIANT="${VARIANT_NAME}" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"

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

    finish_artifact "$artifact"

    # [PH13] Emit runtime SBOM sidecar (FR-SBOM-1, NFR-7).
    # Called on the host shell after the container exits — see Decision commit
    # (AD5 / Risk 1 mitigation).  The artifact is already in BUILD_DIR;
    # picolet-cli is importable from the host Python via PYTHONPATH.
    local sbom_out="${artifact}.cdx.json"
    echo "[SBOM] Emitting runtime SBOM: $sbom_out"
    PYTHONPATH="$REPO_ROOT/packages/picolet-cli" python3 -m picolet_cli.sbom_gen emit-runtime \
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
        if [[ ! -d "$UNIX_PORT/variants/${VARIANT_NAME}" ]]; then
            echo "  overlay not applied; running rebuild-integration.sh"
            "$SCRIPT_DIR/rebuild-integration.sh"
        else
            echo "  integration branch warm; skipping rebuild"
        fi
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

    echo "[3/8] Building mpy-cross (native macOS host binary)"
    make -C "$SUBMODULE/mpy-cross" -j

    echo "[4/8] Fetching port submodules (libffi)"
    make -C "$UNIX_PORT" -j submodules \
        VARIANT="${VARIANT_NAME}" \
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
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        make -C "$UNIX_PORT" \
            -j \
            VARIANT="${VARIANT_NAME}" \
            MICROPY_STANDALONE=1 \
            PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
            deplibs
    fi
    make -C "$UNIX_PORT" \
        -j \
        VARIANT="${VARIANT_NAME}" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"

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
    PYTHONPATH="$REPO_ROOT/packages/picolet-cli" python3 -m picolet_cli.sbom_gen emit-runtime \
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
    local MXE_SDL2_CFLAGS_HOST=""

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
        if [[ ! -d "$windows_port/variants/${VARIANT_NAME}" ]]; then
            echo "  Windows overlay not applied; running rebuild-integration.sh"
            "$SCRIPT_DIR/rebuild-integration.sh"
        else
            echo "  integration branch warm; skipping rebuild"
        fi
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

    # PH12: lvgl variant pulls lv_binding_micropython (+ its nested
    # lvgl/lvgl and pycparser submodules) under overlay/lib/.  Init
    # them here so the USER_C_MODULES path is populated before make.
    if [[ "$VARIANT" == "lvgl" ]]; then
        local lvbm_dir="$PKG_ROOT/overlay/lib/lv_binding_micropython"
        echo "  ensuring lv_binding_micropython nested submodules"
        if [[ ! -d "$lvbm_dir/lvgl/src" ]] || [[ ! -d "$lvbm_dir/pycparser/pycparser" ]]; then
            git -C "$lvbm_dir" submodule update --init --recursive --quiet
        fi
        if [[ ! -d "$lvbm_dir/lvgl/src" ]]; then
            echo "error: lvgl source tree not present after submodule update" >&2
            exit 1
        fi
    fi

    # [2b/8] For the lvgl variant, build SDL2 from source with per-function
    # sections so --gc-sections can strip unused SDL2 backends at link time.
    #
    # SDL2 2.26.2 is built inside the dockcross/windows-static-x64-posix
    # container using the MXE cmake wrapper, which auto-configures the
    # x86_64-w64-mingw32.static.posix toolchain.  Key flags:
    #
    #   -ffunction-sections -fdata-sections   each function/datum in its own
    #                                          ELF section; --gc-sections at
    #                                          link time then drops unreachable
    #                                          sections (unused SDL2 backends,
    #                                          DirectX audio, haptics, etc.).
    #   -Os                                    optimise for size.
    #   --disable-shared / -DSDL_SHARED=OFF   static library only.
    #
    # Source: SDL2-2.26.2.tar.gz downloaded once to build/sdl2-src/ and
    # cached by its SHA-256.  The from-source library lands in
    # build/sdl2-win64-ffs/ (ffs = function/function-sections).
    # A stamp file records the source SHA-256 so warm builds skip the
    # cmake+make step entirely.
    if [[ "$VARIANT" == "lvgl" ]]; then
        echo "[2b/8] Ensuring SDL2 static library (from-source, -ffunction-sections)"
        local SDL2_VERSION="2.26.2"
        local SDL2_TARBALL="SDL2-${SDL2_VERSION}.tar.gz"
        local SDL2_URL="https://github.com/libsdl-org/SDL/releases/download/release-${SDL2_VERSION}/${SDL2_TARBALL}"
        local SDL2_SRC_SHA256="95d39bc3de037fbdfa722623737340648de4f180a601b0afad27645d150b99e0"
        local SDL2_SRC_DIR="$BUILD_DIR/sdl2-src"
        local SDL2_SRC_TARBALL="$SDL2_SRC_DIR/$SDL2_TARBALL"
        local SDL2_CACHE="$BUILD_DIR/sdl2-win64-ffs"
        local SDL2_LIB="$SDL2_CACHE/lib/libSDL2.a"
        local SDL2_STAMP="$SDL2_CACHE/built-${SDL2_SRC_SHA256}.stamp"
        mkdir -p "$SDL2_SRC_DIR" "$SDL2_CACHE"

        if [[ -f "$SDL2_STAMP" ]] && [[ -f "$SDL2_LIB" ]]; then
            echo "  sdl2: MXE build cached; skipping"
        else
            # Ensure the source tarball is present.
            if [[ ! -f "$SDL2_SRC_TARBALL" ]]; then
                echo "  SDL2 source not present; downloading $SDL2_TARBALL"
                if ! curl -fsSL --retry 3 -o "$SDL2_SRC_TARBALL" "$SDL2_URL"; then
                    echo "error: SDL2 source download failed" >&2
                    exit 1
                fi
            fi
            # Verify SHA-256.
            local ACTUAL_SHA256
            ACTUAL_SHA256="$(sha256sum "$SDL2_SRC_TARBALL" | awk '{print $1}')"
            if [[ "$ACTUAL_SHA256" != "$SDL2_SRC_SHA256" ]]; then
                echo "error: SDL2 source SHA-256 mismatch" >&2
                echo "  expected: $SDL2_SRC_SHA256" >&2
                echo "  actual:   $ACTUAL_SHA256" >&2
                exit 1
            fi

            # Extract source into a sibling build dir (avoid polluting sdl2-src).
            local SDL2_BUILD_TMP="$BUILD_DIR/sdl2-build-tmp"
            rm -rf "$SDL2_BUILD_TMP"
            mkdir -p "$SDL2_BUILD_TMP"
            echo "  extracting SDL2 source..."
            tar -C "$SDL2_BUILD_TMP" -xzf "$SDL2_SRC_TARBALL"
            local SDL2_SRC_TREE="$SDL2_BUILD_TMP/SDL2-${SDL2_VERSION}"

            # Build SDL2 inside the dockcross container.  The MXE cmake wrapper
            # at /usr/src/mxe/usr/bin/x86_64-w64-mingw32.static.posix-cmake
            # injects the MinGW toolchain file automatically; we pass the extra
            # C flags and restrict to only the subsystems LVGL's SDL2 driver
            # needs (window, render, events, timer) to minimise the static lib.
            #
            # Disabled subsystems (not used by LVGL's SDL2 window driver):
            #   Audio, Joystick, Haptic, Sensor, HIDAPI, Power — completely off.
            #   DirectX, D3D render, OpenGL — SDL software renderer suffices.
            #   Locale, Misc — not needed for window + event loop.
            # ccache must be disabled inside the container (no writable cache
            # dir for the non-root user; SDL2's cmake enables it by default).
            echo "  building SDL2 from source (cmake + make inside dockcross)..."
            echo "  this takes ~5-10 min on first run; subsequent builds are cached"
            local SDL2_INSTALL_PREFIX="$SDL2_CACHE"
            docker run --rm \
                -v "$REPO_ROOT:$REPO_ROOT" \
                -w "$SDL2_SRC_TREE" \
                --user "$(id -u):$(id -g)" \
                -e HOME=/tmp \
                -e CCACHE_DISABLE=1 \
                "$DOCKCROSS_IMAGE" \
                bash -c "
                    set -euo pipefail
                    CMAKE=/usr/src/mxe/usr/bin/x86_64-w64-mingw32.static.posix-cmake
                    mkdir -p build_mxe && cd build_mxe
                    \$CMAKE .. \
                        -DCMAKE_BUILD_TYPE=MinSizeRel \
                        -DCMAKE_C_FLAGS='-ffunction-sections -fdata-sections -Os' \
                        -DSDL_SHARED=OFF \
                        -DSDL_STATIC=ON \
                        -DSDL_TEST=OFF \
                        -DSDL_CCACHE=OFF \
                        -DSDL_AUDIO=OFF \
                        -DSDL_JOYSTICK=OFF \
                        -DSDL_HAPTIC=OFF \
                        -DSDL_SENSOR=OFF \
                        -DSDL_HIDAPI=OFF \
                        -DSDL_POWER=OFF \
                        -DSDL_DIRECTX=OFF \
                        -DSDL_RENDER_D3D=OFF \
                        -DSDL_OPENGL=OFF \
                        -DSDL_OPENGLES=OFF \
                        -DSDL_LOCALE=OFF \
                        -DSDL_MISC=OFF \
                        -DCMAKE_INSTALL_PREFIX='${SDL2_INSTALL_PREFIX}' \
                        -DCMAKE_INSTALL_LIBDIR=lib \
                        -DCMAKE_INSTALL_INCLUDEDIR=include
                    make -j\$(nproc)
                    make install
                "
            rm -rf "$SDL2_BUILD_TMP"

            if [[ ! -f "$SDL2_LIB" ]]; then
                echo "error: SDL2 from-source build failed; $SDL2_LIB not found" >&2
                exit 1
            fi
            touch "$SDL2_STAMP"
            echo "  sdl2: from-source build complete; library at $SDL2_LIB"
        fi
        # Record the SDL2 install dir so Make overrides MXE_ROOT in the
        # variant .mk to point here (host path, bind-mounted into container).
        MXE_SDL2_CFLAGS_HOST="$SDL2_CACHE"
    fi

    echo "[3/8] Building mpy-cross (inside dockcross — produces Linux ELF host tool)"
    # dockcross includes a Linux GCC alongside MinGW; mpy-cross is a host
    # tool and is built as a Linux binary.  This matches the pydfu precedent.
    docker_windows "$SUBMODULE/mpy-cross" make -j

    echo "[4/8] Fetching port submodules (libffi)"
    # The Windows Makefile's deplibs target adds lib/libffi to GIT_SUBMODULES
    # when MICROPY_PY_FFI=1 (set in the variant .mk).  We run `submodules` on
    # the host (pure git op, no compiler needed).
    make -C "$windows_port" -j submodules VARIANT="${VARIANT_NAME}"

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

    # Build up optional Make overrides for lvgl (MXE_ROOT points to the
    # host-side SDL2 cache dir so the variant .mk resolves includes / libs).
    local EXTRA_MAKE_VARS=()
    if [[ "$VARIANT" == "lvgl" ]]; then
        EXTRA_MAKE_VARS+=("MXE_ROOT=${MXE_SDL2_CFLAGS_HOST}")
    fi

    echo "[6/8] Building libffi (deplibs) inside dockcross"
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        docker_windows "$windows_port" make \
            -j \
            VARIANT="${VARIANT_NAME}" \
            CROSS_COMPILE="$CROSS" \
            PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
            "${EXTRA_MAKE_VARS[@]}" \
            deplibs
    fi

    echo "[6b/8] Building windows port variant=${VARIANT_NAME} inside dockcross"
    docker_windows "$windows_port" make \
        -j \
        VARIANT="${VARIANT_NAME}" \
        CROSS_COMPILE="$CROSS" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
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

    finish_artifact "$artifact"

    # [PH13] Emit runtime SBOM sidecar (FR-SBOM-1, NFR-7).
    local sbom_out="${artifact}.cdx.json"
    echo "[SBOM] Emitting runtime SBOM: $sbom_out"
    PYTHONPATH="$REPO_ROOT/packages/picolet-cli" python3 -m picolet_cli.sbom_gen emit-runtime \
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
