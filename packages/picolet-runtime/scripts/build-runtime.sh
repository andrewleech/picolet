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
TEST_ROMFS=""   # empty by default; pass --test-romfs <fixture> to embed a test romfs

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            TARGET="$2"; shift 2 ;;
        --variant)
            VARIANT="$2"; shift 2 ;;
        --clean)
            CLEAN=1; shift ;;
        --test-romfs)
            TEST_ROMFS="$2"; shift 2 ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "usage: $0 --target <target> --variant <variant> [--clean] [--test-romfs <fixture>]" >&2
            exit 1 ;;
    esac
done

if [[ -z "$TARGET" || -z "$VARIANT" ]]; then
    echo "error: --target and --variant are required" >&2
    exit 1
fi

# Validate combinations; unsupported ones exit early with a clear message so
# PH07/PH11 only have to add a branch, not redesign the contract.
case "${TARGET}/${VARIANT}" in
    linux-x64/cli)
        ;;
    windows-x64/cli)
        ;;
    linux-x64/webview|linux-x64/lvgl)
        echo "error: --variant $VARIANT for linux-x64 not implemented; see PH07/PH11" >&2
        exit 1 ;;
    windows-x64/webview|windows-x64/lvgl)
        echo "error: --variant $VARIANT for windows-x64 not implemented; see PH10/PH12" >&2
        exit 1 ;;
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

    # Step [8/8] — NFR-1 size gate (≤ 1 MiB = 1048576 bytes).
    echo "[8/8] Checking binary size (NFR-1: ≤ 1 MiB)"
    SIZE=$(wc -c < "$artifact")
    CEILING=1048576
    if [[ "$SIZE" -gt "$CEILING" ]]; then
        echo "error: NFR-1 VIOLATED: $artifact_name is $SIZE bytes (ceiling $CEILING bytes / 1 MiB)" >&2
        echo "       Consider disabling MICROPY_ENABLE_COMPILER=0 in mpconfigvariant.mk." >&2
        exit 1
    fi
    PCT=$(( SIZE * 100 / CEILING ))
    echo "  size: $SIZE bytes (${PCT}% of NFR-1 ceiling of $CEILING bytes)"
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

    echo "[6/8] Building libffi (deplibs) inside dockcross"
    if [[ -f "$libffi_ffi_h" ]]; then
        echo "  deplibs: ffi.h cached; skipping deplibs"
    else
        docker_windows "$windows_port" make \
            -j \
            VARIANT="${VARIANT_NAME}" \
            CROSS_COMPILE="$CROSS" \
            PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
            deplibs
    fi

    echo "[6b/8] Building windows port variant=${VARIANT_NAME} inside dockcross"
    docker_windows "$windows_port" make \
        -j \
        VARIANT="${VARIANT_NAME}" \
        CROSS_COMPILE="$CROSS" \
        ROMFS_IMG="$ROMFS_IMG_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"

    echo "[7/8] Stripping and installing artifact"
    local built_binary="$variant_build/micropython.exe"
    if [[ ! -f "$built_binary" ]]; then
        echo "error: expected binary not found: $built_binary" >&2
        exit 1
    fi
    mkdir -p "$BUILD_DIR"
    cp "$built_binary" "$artifact"
    # Strip inside dockcross — the host strip is not MinGW-aware.
    docker_windows "$PKG_ROOT" "${CROSS}strip" --strip-unneeded "$artifact" \
        2>/dev/null || true
    echo "  artifact: $artifact"

    finish_artifact "$artifact"
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

echo "=== build-runtime.sh: target=$TARGET variant=$VARIANT ==="

if [[ "$TARGET" == "linux-x64" ]]; then
    build_linux_x64
elif [[ "$TARGET" == "windows-x64" ]]; then
    build_windows_x64
fi
