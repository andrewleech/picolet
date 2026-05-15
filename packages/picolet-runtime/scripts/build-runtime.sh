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
# Supported targets / variants in PH01:
#   --target linux-x64 --variant cli   (builds inside ubuntu:22.04 container)
#
# PH04 will add --target windows-x64 (dockcross cross-compile).
# PH07/PH11 will add --variant webview and --variant lvgl.
#
# Outputs:
#   packages/picolet-runtime/build/picolet-runtime-{target}-{variant}
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
TEST_ROMFS="test_romfs"   # default fixture for gate-4 embedded romfs

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
# PH04/PH07/PH11 only have to add a branch, not redesign the contract.
case "${TARGET}/${VARIANT}" in
    linux-x64/cli)
        ;;
    windows-x64/*)
        echo "error: --target windows-x64 not implemented in PH01; see PH04" >&2
        exit 1 ;;
    linux-x64/webview|linux-x64/lvgl)
        echo "error: --variant $VARIANT for linux-x64 not implemented in PH01; see PH07/PH11" >&2
        exit 1 ;;
    *)
        echo "error: unsupported target/variant combination: $TARGET/$VARIANT" >&2
        exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Derived names
# ---------------------------------------------------------------------------

VARIANT_NAME="picolet-${VARIANT}"          # e.g. picolet-cli
ARTIFACT_NAME="picolet-runtime-${TARGET}-${VARIANT}"   # e.g. picolet-runtime-linux-x64-cli
BUILD_DIR="$PKG_ROOT/build"
ARTIFACT="$BUILD_DIR/$ARTIFACT_NAME"
UNIX_PORT="$SUBMODULE/ports/unix"
VARIANT_BUILD="$UNIX_PORT/build-${VARIANT_NAME}"

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

# Helper: run a command inside the build container with the full repo bind-mounted.
# Usage: docker_linux <working-dir-relative-to-REPO_ROOT> <cmd...>
# The working dir is passed as an absolute path so Make's relative references work.
docker_linux() {
    local workdir="$1"; shift
    docker run --rm \
        -v "$REPO_ROOT:$REPO_ROOT" \
        -w "$workdir" \
        --user "$(id -u):$(id -g)" \
        "$LINUX_BUILD_IMAGE" \
        "$@"
}

echo "=== build-runtime.sh: target=$TARGET variant=$VARIANT ==="

# ---------------------------------------------------------------------------
# Step 0 – Ensure the linux build image is present (idempotent docker build)
# ---------------------------------------------------------------------------

echo "[0/8] Ensuring linux build image: $LINUX_BUILD_IMAGE"

if ! docker image inspect "$LINUX_BUILD_IMAGE" >/dev/null 2>&1; then
    echo "  image not found; building from $LINUX_DOCKERFILE"
    docker build -t "$LINUX_BUILD_IMAGE" "$(dirname "$LINUX_DOCKERFILE")"
else
    echo "  image present; skipping build"
fi

# ---------------------------------------------------------------------------
# Step 1 – Ensure submodule is on the integration branch
# ---------------------------------------------------------------------------

echo "[1/8] Checking integration branch"

if [[ "$CLEAN" -eq 1 ]]; then
    echo "  --clean: re-running rebuild-integration.sh"
    "$SCRIPT_DIR/rebuild-integration.sh"
elif ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
    echo "  integration branch not found; running rebuild-integration.sh"
    "$SCRIPT_DIR/rebuild-integration.sh"
else
    # Fast path: integration branch exists; check whether the overlay has
    # been applied (look for the variant directory in the submodule tree).
    if [[ ! -d "$UNIX_PORT/variants/${VARIANT_NAME}" ]]; then
        echo "  overlay not applied; running rebuild-integration.sh"
        "$SCRIPT_DIR/rebuild-integration.sh"
    else
        echo "  integration branch warm; skipping rebuild"
    fi
fi

# Ensure submodule working tree is on the integration tip.
git -C "$SUBMODULE" checkout integration --quiet

# Always ensure nested submodules (micropython-lib, libffi, etc.) are
# initialised after a branch switch — the checkout above may re-point them.
echo "  updating nested submodules"
git -C "$SUBMODULE" submodule update --init --recursive --quiet

# ---------------------------------------------------------------------------
# Step 2 – Verify micropython-lib submodule is present
# ---------------------------------------------------------------------------

echo "[2/8] Verifying submodule presence"

# asyncio is built into extmod in this MicroPython version (not micropython-lib).
# Verify the extmod/asyncio directory is present.
ASYNCIO_EXTMOD="$SUBMODULE/extmod/asyncio"
if [[ ! -d "$ASYNCIO_EXTMOD" ]]; then
    echo "error: $ASYNCIO_EXTMOD not found." >&2
    echo "       The submodule checkout looks incomplete." >&2
    exit 1
fi

# Verify os-path is in micropython-lib (python-stdlib is an external submodule).
MPL_DIR="$SUBMODULE/lib/micropython-lib"
OS_PATH_DIR="$MPL_DIR/python-stdlib/os-path"
if [[ ! -d "$OS_PATH_DIR" ]]; then
    echo "error: $OS_PATH_DIR not found." >&2
    echo "       The integration branch's lib/micropython-lib submodule is not initialised." >&2
    echo "       Run: git -C $SUBMODULE submodule update --init --recursive" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3 – Build mpy-cross inside the container
#
# mpy-cross must be compiled by the same toolchain that builds the port so
# that the host-run mpy-cross binary's bytecode format matches the runtime.
# Running it in the container also ensures any compiler-generated symbols
# remain in the glibc 2.35 baseline.
# ---------------------------------------------------------------------------

echo "[3/8] Building mpy-cross (inside $LINUX_BUILD_IMAGE)"
docker_linux "$SUBMODULE/mpy-cross" make -j

# ---------------------------------------------------------------------------
# Step 4 – Fetch libffi submodule (triggered by MICROPY_STANDALONE=1)
#
# The submodules target only fetches / initialises the libffi git submodule;
# it doesn't compile anything.  Running on the host avoids a docker exec for
# a pure git operation.
# ---------------------------------------------------------------------------

echo "[4/8] Fetching port submodules (libffi)"
make -C "$UNIX_PORT" -j submodules \
    VARIANT="${VARIANT_NAME}" \
    MICROPY_STANDALONE=1

# ---------------------------------------------------------------------------
# Step 5 – Build the test romfs image (host — pure Python, no compiler)
# ---------------------------------------------------------------------------

echo "[5/8] Building test romfs from tests/phase-01/${TEST_ROMFS}"

ROMFS_FIXTURE="$PKG_ROOT/tests/phase-01/${TEST_ROMFS}"
if [[ ! -d "$ROMFS_FIXTURE" ]]; then
    echo "error: romfs fixture directory not found: $ROMFS_FIXTURE" >&2
    exit 1
fi

# mpremote romfs build writes <dir>.romfs in the parent of the source dir.
# We pass the directory and let mpremote name it; then move to build tree.
ROMFS_STAGING="$BUILD_DIR/romfs_staging"
mkdir -p "$ROMFS_STAGING"

ROMFS_IMG="$ROMFS_STAGING/${TEST_ROMFS}.romfs"
# Note: mpremote romfs requires --output before the 'build' subcommand.
python3 -m mpremote romfs --output "$ROMFS_IMG" build "$ROMFS_FIXTURE"

# The unix port Makefile's romfs_data.o objcopy rule derives the symbol name
# by substituting / and . with _, but objcopy itself converts ALL
# non-alphanumeric characters (including -) to _ in embedded binary symbols.
# If the ROMFS_IMG path contains hyphens (e.g. from 'picolet-runtime'), the
# Makefile's $(subst) would keep the hyphen while objcopy converts it, so
# the --redefine-sym old name does not match and the rename silently fails.
#
# Fix: pass ROMFS_IMG as a path relative to the unix port working directory.
# The relative path traverses only directories with no hyphens (mpy-cross
# has a hyphen but is not on this path: ../micropython/ports/unix → ../../../build/).
# The staging filename also uses underscores.  This avoids the /tmp workaround
# which is inaccessible inside the Docker build container.
ROMFS_IMG_SAFE="$ROMFS_STAGING/picolet_romfs_${TEST_ROMFS}.romfs"
cp "$ROMFS_IMG" "$ROMFS_IMG_SAFE"
# Relative path from $UNIX_PORT (micropython/ports/unix) to $ROMFS_STAGING.
# The path ../../../build/romfs_staging contains no hyphens.
ROMFS_IMG_REL="$(realpath --relative-to="$UNIX_PORT" "$ROMFS_IMG_SAFE")"

echo "  romfs image: $ROMFS_IMG_SAFE ($(wc -c < "$ROMFS_IMG_SAFE") bytes, relative: $ROMFS_IMG_REL)"

# ---------------------------------------------------------------------------
# Step 6 – Build the unix port variant inside the container
#
# Two make invocations mirror the host-native pattern but run inside the
# container.  deplibs first (libffi configure + compile), then the main
# port build.  The PICOLET_RUNTIME_ROOT absolute path is the same inside the
# container because we bind-mount at the same host path.
# ---------------------------------------------------------------------------

echo "[6/8] Building unix port variant=${VARIANT_NAME} (inside $LINUX_BUILD_IMAGE)"

# deplibs first: libffi configure + compile.
# The unix port Makefile evaluates LIBFFI_CFLAGS at parse time via a shell
# $(ls ...) of the build output dir.  That directory doesn't exist until
# deplibs runs, so deplibs must complete before the main compile invocation.
docker_linux "$UNIX_PORT" make \
    -j \
    VARIANT="${VARIANT_NAME}" \
    MICROPY_STANDALONE=1 \
    PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
    deplibs

docker_linux "$UNIX_PORT" make \
    -j \
    VARIANT="${VARIANT_NAME}" \
    ROMFS_IMG="$ROMFS_IMG_REL" \
    PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")"

# ---------------------------------------------------------------------------
# Step 7 – Strip and install artifact
# ---------------------------------------------------------------------------

echo "[7/8] Stripping and installing artifact"

BUILT_BINARY="$VARIANT_BUILD/micropython"

if [[ ! -f "$BUILT_BINARY" ]]; then
    echo "error: expected binary not found: $BUILT_BINARY" >&2
    exit 1
fi

mkdir -p "$BUILD_DIR"
cp "$BUILT_BINARY" "$ARTIFACT"
strip --strip-unneeded "$ARTIFACT"

echo "  artifact: $ARTIFACT"

# ---------------------------------------------------------------------------
# Step 8 – NFR-1 size gate (≤ 1 MiB = 1048576 bytes)
# ---------------------------------------------------------------------------

echo "[8/8] Checking binary size (NFR-1: ≤ 1 MiB)"

SIZE=$(wc -c < "$ARTIFACT")
CEILING=1048576

if [[ "$SIZE" -gt "$CEILING" ]]; then
    echo "error: NFR-1 VIOLATED: $ARTIFACT is $SIZE bytes (ceiling is $CEILING bytes / 1 MiB)" >&2
    echo "       Consider enabling MICROPY_ENABLE_COMPILER=0 in mpconfigvariant.mk." >&2
    exit 1
fi

PCT=$(( SIZE * 100 / CEILING ))
echo "  size: $SIZE bytes (${PCT}% of NFR-1 ceiling of $CEILING bytes)"
echo
echo "=== Build complete: $ARTIFACT ==="
