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
#   --target linux-x64 --variant cli   (native Linux build, no docker)
#
# PH04 will add --target windows-x64 (dockcross cross-compile).
# PH07/PH11 will add --variant webview and --variant lvgl.
#
# Outputs:
#   packages/picolet-runtime/build/picolet-runtime-{target}-{variant}
#
# The build script is idempotent on a warm tree (make skips up-to-date
# objects; libffi is only rebuilt if its sources changed).
#
# Prerequisites (linux-x64/cli):
#   - gcc, make, python3, strip (binutils)
#   - mpremote (pip install mpremote)  — used for romfs assembly
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

echo "=== build-runtime.sh: target=$TARGET variant=$VARIANT ==="

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

# ---------------------------------------------------------------------------
# Step 2 – Verify micropython-lib submodule is present
# ---------------------------------------------------------------------------

echo "[2/8] Verifying micropython-lib submodule"

MPL_DIR="$SUBMODULE/lib/micropython-lib"
ASYNCIO_DIR="$MPL_DIR/python-stdlib/asyncio"

if [[ ! -d "$ASYNCIO_DIR" ]]; then
    echo "error: $ASYNCIO_DIR not found." >&2
    echo "       The integration branch's lib/micropython-lib submodule is not initialised." >&2
    echo "       Run: git -C $SUBMODULE submodule update --init --recursive" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3 – Build mpy-cross
# ---------------------------------------------------------------------------

echo "[3/8] Building mpy-cross"
make -C "$SUBMODULE/mpy-cross" -j

# ---------------------------------------------------------------------------
# Step 4 – Fetch libffi submodule (triggered by MICROPY_STANDALONE=1)
# ---------------------------------------------------------------------------

echo "[4/8] Fetching port submodules (libffi)"
make -C "$UNIX_PORT" -j submodules \
    VARIANT="${VARIANT_NAME}" \
    MICROPY_STANDALONE=1

# ---------------------------------------------------------------------------
# Step 5 – Build the test romfs image
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
python3 -m mpremote romfs build --output "$ROMFS_IMG" "$ROMFS_FIXTURE"

echo "  romfs image: $ROMFS_IMG ($(wc -c < "$ROMFS_IMG") bytes)"

# ---------------------------------------------------------------------------
# Step 6 – Build the unix port variant
# ---------------------------------------------------------------------------

echo "[6/8] Building unix port variant=${VARIANT_NAME}"

make -C "$UNIX_PORT" \
    -j \
    VARIANT="${VARIANT_NAME}" \
    ROMFS_IMG="$(realpath "$ROMFS_IMG")" \
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
