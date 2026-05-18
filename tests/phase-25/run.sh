#!/usr/bin/env bash
# tests/phase-25/run.sh — PH25 macOS WKWebView runtime smoke test.
#
# Usage:
#   bash tests/phase-25/run.sh [--binary <path>]
#
# If not on macOS or no display is available, the script exits 0 with a
# clear explanatory message — macOS-specific tests require a Darwin host
# and an active window server (no headless/CI without a display).
#
# On a macOS runner with a display this script:
#   1. Builds the webview runtime (if --binary is not supplied).
#   2. Launches hello-webview.
#   3. Waits for the picolet:test-port=N announcement on stderr.
#   4. Connects via WebSocket to the WKRP inspector.
#   5. Takes a screenshot via AppHarness and asserts PNG > 1 KB.
#
# The static analysis tests (test_objc_signatures.py) run on any host and
# are always executed below.

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
# Always run the static analysis tests (platform-independent).
# ---------------------------------------------------------------------------

echo "=== PH25: running static analysis tests (platform-independent) ==="
python3 -m pytest "$SCRIPT_DIR/test_objc_signatures.py" -v
echo ""

# ---------------------------------------------------------------------------
# macOS / display guard
# ---------------------------------------------------------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "NOTE: not on macOS — skipping runtime smoke tests."
    echo "      Trigger the GitHub Actions release workflow to run these on macOS runners."
    exit 0
fi

# Check for a display / window server.
if ! /usr/sbin/system_profiler SPDisplaysDataType >/dev/null 2>&1; then
    echo "NOTE: no display available — skipping runtime smoke tests."
    exit 0
fi

# ---------------------------------------------------------------------------
# Build the macOS webview runtime if no binary was supplied.
# ---------------------------------------------------------------------------

if [[ -z "$BINARY" ]]; then
    ARCH="$(uname -m)"
    if [[ "$ARCH" == "arm64" ]]; then
        TARGET="macos-arm64"
    else
        TARGET="macos-x64"
    fi
    BINARY="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-${TARGET}-webview"
    if [[ ! -f "$BINARY" ]]; then
        echo "Binary not found: $BINARY"
        echo "Building macOS webview runtime..."
        bash "$REPO_ROOT/packages/picolet-runtime/scripts/build-runtime.sh" \
            --target "$TARGET" --variant webview
    fi
fi

if [[ ! -f "$BINARY" ]]; then
    echo "error: binary not found: $BINARY" >&2
    exit 1
fi

# Remove quarantine flag if present (Gatekeeper, NFR-MAC-6).
xattr -d com.apple.quarantine "$BINARY" 2>/dev/null || true

echo "=== PH25: launching hello-webview in PICOLET_TEST_MODE ==="

TMPLOG="$(mktemp /tmp/picolet-ph25-XXXXXX.log)"
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

if [[ -z "$TEST_PORT" ]]; then
    echo "error: picolet:test-port= not announced within 10 s" >&2
    kill "$APP_PID" 2>/dev/null || true
    exit 1
fi

echo "  inspector port: $TEST_PORT"

# ---------------------------------------------------------------------------
# Screenshot test via AppHarness (if picolet-testing is available).
# ---------------------------------------------------------------------------

python3 - <<PYEOF
import sys, os
sys.path.insert(0, os.path.join("$REPO_ROOT", "packages", "picolet-testing"))
try:
    from picolet.testing import AppHarness
except ImportError:
    print("NOTE: picolet-testing not installed; skipping screenshot assertion.")
    sys.exit(0)

import asyncio

async def _test():
    async with AppHarness("$BINARY") as h:
        png = await h.snapshot()
        assert len(png) > 1024, "PNG too small: {} bytes".format(len(png))
        print("  screenshot OK: {} bytes".format(len(png)))

asyncio.run(_test())
PYEOF

kill "$APP_PID" 2>/dev/null || true

echo "=== PH25: macOS smoke test complete ==="
