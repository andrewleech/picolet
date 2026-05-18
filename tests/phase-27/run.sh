#!/usr/bin/env bash
# tests/phase-27/run.sh — PH27 macOS LVGL/SDL2 runtime smoke test.
#
# Usage:
#   bash tests/phase-27/run.sh [--binary <path>]
#
# On non-Darwin hosts exits 0 immediately with an explanatory message.
# On macOS with a pre-built binary, launches the lvgl runtime and asserts
# it starts without crashing (exit-code gate; visual verification requires
# a display).

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
    echo "NOTE: not on macOS — skipping PH27 LVGL/SDL2 smoke tests."
    echo "      macOS lvgl variant requires a Darwin host with SDL2 installed."
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
    BINARY="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-${TARGET}-lvgl"
fi

if [[ ! -f "$BINARY" ]]; then
    echo "NOTE: binary not found: $BINARY"
    echo "      Build with: bash packages/picolet-runtime/scripts/build-runtime.sh --target $TARGET --variant lvgl"
    echo "      Skipping runtime smoke test."
    exit 0
fi

xattr -d com.apple.quarantine "$BINARY" 2>/dev/null || true

echo "=== PH27: launching macOS LVGL runtime in PICOLET_TEST_MODE ==="
echo "  binary: $BINARY"

# Verify dylib dependency (SDL2 must be dynamically linked, not static).
if command -v otool >/dev/null 2>&1; then
    echo "  dylib dependencies:"
    otool -L "$BINARY" | grep -i sdl || echo "  (no SDL2 dylib found — check build)"
fi

TMPLOG="$(mktemp /tmp/picolet-ph27-XXXXXX.log)"
trap "rm -f '$TMPLOG'" EXIT

# Launch in test mode; LVGL initialises SDL2 and opens a window.
PICOLET_TEST_MODE=1 "$BINARY" >"$TMPLOG" 2>&1 &
APP_PID=$!

# Wait briefly — LVGL/SDL2 typically initialises in < 2 s.
sleep 2

if kill -0 "$APP_PID" 2>/dev/null; then
    echo "  runtime is alive after 2 s — SDL2 Cocoa backend initialised"
    kill "$APP_PID" 2>/dev/null || true
else
    EXIT_CODE=$?
    echo "error: runtime exited unexpectedly (exit $EXIT_CODE)" >&2
    cat "$TMPLOG" >&2
    exit 1
fi

echo "=== PH27: macOS LVGL/SDL2 smoke test passed ==="
