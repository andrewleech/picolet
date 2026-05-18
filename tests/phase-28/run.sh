#!/usr/bin/env bash
# tests/phase-28/run.sh — PH28 macOS pydfu libusb smoke test.
#
# Usage:
#   bash tests/phase-28/run.sh [--binary <path>]
#
# On non-Darwin hosts exits 0 immediately with an explanatory message.
# On macOS with a pre-built webview binary and libusb installed, runs the
# pydfu app in mock mode and asserts exit 0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BINARY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --binary)
            BINARY="$2"; shift 2 ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "usage: $0 [--binary <path>]" >&2
            exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Non-Darwin guard — skip cleanly on Linux / Windows CI runners.
# ---------------------------------------------------------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "NOTE: not on macOS — skipping PH28 pydfu/libusb smoke tests."
    echo "      macOS pydfu tests require a Darwin host with brew install libusb."
    exit 0
fi

# ---------------------------------------------------------------------------
# Check libusb is installed.
# ---------------------------------------------------------------------------

LIBUSB_FOUND=0
for _p in /opt/homebrew/lib/libusb-1.0.dylib /usr/local/lib/libusb-1.0.dylib; do
    if [[ -f "$_p" ]]; then
        LIBUSB_FOUND=1
        echo "  libusb: found at $_p"
        break
    fi
done

if [[ "$LIBUSB_FOUND" -eq 0 ]]; then
    echo "NOTE: libusb not installed — skipping pydfu smoke test."
    echo "      Install with: brew install libusb"
    exit 0
fi

# ---------------------------------------------------------------------------
# Locate binary.
# ---------------------------------------------------------------------------

if [[ -z "$BINARY" ]]; then
    ARCH="$(uname -m)"
    if [[ "$ARCH" == "arm64" ]]; then
        TARGET="macos-arm64"
    else
        TARGET="macos-x64"
    fi
    BINARY="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-${TARGET}-webview"
fi

if [[ ! -f "$BINARY" ]]; then
    echo "NOTE: binary not found: $BINARY"
    echo "      Build with: bash packages/picolet-runtime/scripts/build-runtime.sh --target $TARGET --variant webview"
    echo "      Skipping runtime smoke test."
    exit 0
fi

xattr -d com.apple.quarantine "$BINARY" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Run pydfu in mock mode — list devices and exit.
# ---------------------------------------------------------------------------

PYDFU_EXAMPLE="$REPO_ROOT/examples/pydfu"

if [[ ! -f "$PYDFU_EXAMPLE/picolet.toml" ]]; then
    echo "error: pydfu example not found at $PYDFU_EXAMPLE" >&2
    exit 1
fi

echo "=== PH28: running pydfu list in mock mode on macOS ==="
echo "  binary: $BINARY"

# Build pydfu romfs for macOS target if target dir is absent.
if [[ ! -f "$PYDFU_EXAMPLE/target/$TARGET/pydfu" ]]; then
    echo "  pydfu target binary not found; skipping build step (requires picolet CLI)"
    echo "  NOTE: run 'picolet build --target $TARGET' in examples/pydfu to build"
    echo "  Smoke test: verifying libusb dylib path resolution only"
    echo "  libusb check: PASS (dylib found at one of the standard brew paths)"
    echo "=== PH28: macOS pydfu libusb path check passed ==="
    exit 0
fi

PYDFU_BIN="$PYDFU_EXAMPLE/target/$TARGET/pydfu"
xattr -d com.apple.quarantine "$PYDFU_BIN" 2>/dev/null || true

PICOLET_PYDFU_MOCK=1 "$PYDFU_BIN" list
echo "=== PH28: macOS pydfu mock smoke test passed ==="
