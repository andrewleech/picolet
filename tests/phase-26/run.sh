#!/usr/bin/env bash
# tests/phase-26/run.sh — PH26 macOS webview variant smoke test.
#
# Usage:
#   bash tests/phase-26/run.sh [--binary <path>]
#
# On non-Darwin hosts exits 0 immediately with an explanatory message.
# On macOS with a pre-built binary, launches the runtime in PICOLET_TEST_MODE
# and asserts it announces a test port within 10 s.

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
    echo "NOTE: not on macOS — skipping PH26 runtime smoke tests."
    echo "      macOS webview variant requires a Darwin host with a display."
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

echo "=== PH26: launching macOS webview runtime in PICOLET_TEST_MODE ==="
echo "  binary: $BINARY"

TMPLOG="$(mktemp /tmp/picolet-ph26-XXXXXX.log)"
trap "rm -f '$TMPLOG'" EXIT

PICOLET_TEST_MODE=1 "$BINARY" >"$TMPLOG" 2>&1 &
APP_PID=$!

# Wait up to 10 s for the port announcement.
TEST_PORT=""
for i in $(seq 1 100); do
    if grep -q "picolet:test-port=" "$TMPLOG" 2>/dev/null; then
        TEST_PORT="$(grep -o 'picolet:test-port=[0-9]*' "$TMPLOG" | tail -1 | cut -d= -f2)"
        break
    fi
    sleep 0.1
done

kill "$APP_PID" 2>/dev/null || true

if [[ -z "$TEST_PORT" ]]; then
    echo "error: picolet:test-port= not announced within 10 s" >&2
    exit 1
fi

echo "  test port announced: $TEST_PORT"
echo "=== PH26: macOS webview smoke test passed ==="
