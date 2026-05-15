#!/usr/bin/env bash
# tests/phase-08/run.sh — PH08 exit gate verification harness.
#
# Covers: FR-WV-{4,5}, FR-BP-4, FR-IPC-{2,3}, NFR-2.
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-08/run.sh [--skip-integration] [--skip-regression]
#                               [--skip-rebuild] [--verbose]
#
# Prerequisites:
#   - Node >= 18 on PATH
#   - packages/picolet-bridge-js/dist/picolet-bridge.js built
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-webview
#   - xvfb-run on PATH (for integration gates)
#   - uv on PATH (for picolet build invocations)
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRIDGE_JS="$REPO_ROOT/packages/picolet-bridge-js/dist/picolet-bridge.js"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
WEBVIEW_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-webview"

SKIP_INTEGRATION=0
SKIP_REGRESSION=0
SKIP_REBUILD=0
VERBOSE=0

for arg in "$@"; do
    case "$arg" in
        --skip-integration) SKIP_INTEGRATION=1 ;;
        --skip-regression)  SKIP_REGRESSION=1 ;;
        --skip-rebuild)     SKIP_REBUILD=1 ;;
        --verbose)          VERBOSE=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

PASS=0
FAIL=0
SKIP=0
FAILED_GATES=()

pass()  { printf "  PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
fail()  {
    printf "  FAIL  %s\n" "$1"
    if [[ -n "${2:-}" ]]; then printf "        %s\n" "$2"; fi
    FAIL=$((FAIL + 1))
    FAILED_GATES+=("$1")
}
skip()  {
    printf "  SKIP  %s" "$1"
    if [[ -n "${2:-}" ]]; then printf "  (%s)" "$2"; fi
    printf "\n"
    SKIP=$((SKIP + 1))
}

WORKDIR="/tmp/picolet-ph08-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH08 exit gate verification ==="
echo "    repo:   $REPO_ROOT"
echo "    bundle: $BRIDGE_JS"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: esbuild bundle (gates 1–2)
# ---------------------------------------------------------------------------

echo "--- Group A: bundle build ---"

NAME="A1 bundle-exists (gate 1)"
if [[ -f "$BRIDGE_JS" && -s "$BRIDGE_JS" ]]; then
    BSIZE=$(wc -c < "$BRIDGE_JS")
    pass "$NAME"
    echo "       dist/picolet-bridge.js: $BSIZE bytes"
else
    fail "$NAME" "missing or empty: $BRIDGE_JS — run: cd packages/picolet-bridge-js && node build.mjs"
fi

NAME="A2 bundle-valid-js (gate 2)"
if ! command -v node >/dev/null 2>&1; then
    skip "$NAME" "node not on PATH"
elif [[ ! -f "$BRIDGE_JS" ]]; then
    skip "$NAME" "bundle missing"
else
    # Run the bundle under Node with a minimal window mock.
    NODE_OUT="$(node -e "
global.window = { webkit: { messageHandlers: { picolet: { postMessage: function(){} } } } };
require('$BRIDGE_JS');
console.log('ok');
" 2>&1)"
    if [[ "$NODE_OUT" == "ok" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "bundle failed to parse/execute: $NODE_OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: JS unit tests (gates 3–8, 14)
# ---------------------------------------------------------------------------

echo "--- Group B: JS unit tests ---"

JS_TESTS=(
    "B1:gate3:test_api_surface.js"
    "B2:gate4+14:test_invoke_roundtrip.js"
    "B3:gate5:test_invoke_error.js"
    "B4:gate6:test_event_dispatch.js"
    "B5:gate7:test_unsubscribe.js"
    "B6:gate8:test_emit.js"
    "B7:sqe-concurrent-invokes:test_concurrent_invokes.js"
    "B8:sqe-multi-subscriber:test_multi_subscriber.js"
    "B9:sqe-error-empty-message:test_error_empty_message.js"
    "B10:sqe-args-edge-cases:test_args_edge_cases.js"
    "B11:sqe-bundle-size:test_bundle_size.js"
    "B12:sqe-malformed-inbound:test_malformed_inbound.js"
)

if ! command -v node >/dev/null 2>&1; then
    for entry in "${JS_TESTS[@]}"; do
        label="${entry%%:*}"
        rest="${entry#*:}"
        gatelabel="${rest%%:*}"
        skip "$label $gatelabel" "node not on PATH"
    done
else
    for entry in "${JS_TESTS[@]}"; do
        label="${entry%%:*}"
        rest="${entry#*:}"
        gatelabel="${rest%%:*}"
        testfile="${rest##*:}"
        NAME="$label $gatelabel ($testfile)"
        TPATH="$SCRIPT_DIR/$testfile"
        if [[ ! -f "$TPATH" ]]; then
            fail "$NAME" "test file not found: $TPATH"
            continue
        fi
        OUT="$(node "$TPATH" 2>&1)"
        RC=$?
        if [[ "$RC" -eq 0 && "$OUT" == *"PASS"* ]]; then
            pass "$NAME"
        else
            fail "$NAME" "$OUT"
        fi
    done
fi

echo

# ---------------------------------------------------------------------------
# Group C: picolet build includes bridge bundle (gate 9)
# ---------------------------------------------------------------------------

echo "--- Group C: romfs includes bridge bundle (gate 9) ---"

FIXTURE_MIN="$REPO_ROOT/tests/phase-07/fixtures/hello-webview-min"

if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "C1 bridge-in-romfs" "--skip-integration"
elif ! command -v uv >/dev/null 2>&1; then
    skip "C1 bridge-in-romfs" "uv not on PATH"
elif [[ ! -f "$WEBVIEW_RUNTIME" ]]; then
    skip "C1 bridge-in-romfs" "webview runtime not found"
elif [[ ! -f "$BRIDGE_JS" ]]; then
    skip "C1 bridge-in-romfs" "bridge bundle not built"
else
    NAME="C1 bridge-in-romfs (gate 9)"
    STAGING="$WORKDIR/c1-staging"
    mkdir -p "$STAGING"
    (
        cd "$FIXTURE_MIN" && \
        uv run python -m picolet build \
            --target linux-x64 \
            --runtime "$WEBVIEW_RUNTIME" \
            --keep-staging \
            > "$WORKDIR/c1.log" 2>&1
    ) || true
    BUILT="$FIXTURE_MIN/target/linux-x64/hello-webview-min"
    if [[ ! -f "$BUILT" ]]; then
        fail "$NAME" "picolet build failed; see $WORKDIR/c1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/c1.log"; fi
    else
        # Inspect the romfs image for picolet/picolet-bridge.js.
        # mpremote romfs can list the image.
        MPREMOTE_OUT="$(uv run python -c "
import sys, subprocess
result = subprocess.run(
    [sys.executable, '-m', 'mpremote', 'romfs', 'ls', '$STAGING/hello-webview-min.romfs'],
    capture_output=True, text=True
) if False else None
# Alternative: check via the built binary itself.
import subprocess, sys
r = subprocess.run(
    ['$BUILT', '-c', 'import os; print(os.listdir(\"/rom/picolet\"))'],
    capture_output=True, text=True
)
print(r.stdout.strip())
print(r.stderr.strip(), file=sys.stderr)
" 2>"$WORKDIR/c1-mpout.err" || true)"
        # Try via the built binary.
        BRIDGE_CHECK="$("$BUILT" -c '
import os
try:
    files = os.listdir("/rom/picolet")
    print("picolet-bridge.js" in files)
except OSError as e:
    print("OSError:", e)
' 2>&1 || true)"
        if [[ "$BRIDGE_CHECK" == *"True"* ]]; then
            pass "$NAME"
            echo "       picolet/picolet-bridge.js confirmed in romfs"
        else
            fail "$NAME" "picolet-bridge.js not found in /rom/picolet; got: $BRIDGE_CHECK"
        fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: integration tests under xvfb (gates 10–13)
# ---------------------------------------------------------------------------

echo "--- Group D: integration tests (xvfb) ---"

_run_fixture() {
    local gate="$1"
    local label="$2"
    local fixture_dir="$3"
    local token="$4"
    local timeout_s="${5:-15}"

    if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
        skip "$label" "--skip-integration"
        return
    fi
    if ! command -v xvfb-run >/dev/null 2>&1; then
        skip "$label" "xvfb-run not on PATH"
        return
    fi
    if ! command -v uv >/dev/null 2>&1; then
        skip "$label" "uv not on PATH"
        return
    fi
    if [[ ! -f "$WEBVIEW_RUNTIME" ]]; then
        skip "$label" "webview runtime not found"
        return
    fi

    local build_log="$WORKDIR/${gate}-build.log"
    local run_log="$WORKDIR/${gate}-run.log"
    local built="$fixture_dir/target/linux-x64/$(basename "$fixture_dir")"

    # Build the fixture.
    (
        cd "$fixture_dir" && \
        uv run python -m picolet build \
            --target linux-x64 \
            --runtime "$WEBVIEW_RUNTIME" \
            > "$build_log" 2>&1
    ) || true

    if [[ ! -f "$built" ]]; then
        fail "$label" "picolet build failed; see $build_log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$build_log"; fi
        return
    fi

    xvfb-run -a -s '-screen 0 800x600x24' \
        timeout "$timeout_s" "$built" \
        > "$run_log" 2>&1 || true

    if grep -q "$token" "$run_log"; then
        pass "$label"
    else
        fail "$label" "token '$token' not found in output"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$run_log"; fi
    fi
}

_run_fixture "d1" "D1 bridge-inject-order (gate 10 / FR-WV-4)" \
    "$SCRIPT_DIR/fixtures/bridge-inject-order" \
    "PICOLET_WV_BRIDGE_INJECT_OK" 12

_run_fixture "d2a" "D2a invoke-roundtrip (gate 11 / FR-WV-5)" \
    "$SCRIPT_DIR/fixtures/invoke-roundtrip" \
    "PICOLET_WV_INVOKE_OK" 15

_run_fixture "d2b" "D2b error-propagation (gate 13 / FR-IPC-2)" \
    "$SCRIPT_DIR/fixtures/invoke-roundtrip" \
    "PICOLET_WV_ERROR_OK" 15

_run_fixture "d3" "D3 event-push (gate 12 / FR-IPC-3)" \
    "$SCRIPT_DIR/fixtures/event-push" \
    "PICOLET_WV_EVENT_OK" 15

_run_fixture "d4" "D4 js-emit-fire-and-forget (sqe / FR-WV-5 emit)" \
    "$SCRIPT_DIR/fixtures/js-emit-to-python" \
    "PICOLET_WV_EMIT_OK" 12

echo

# ---------------------------------------------------------------------------
# Group E: NFR-2 size check (gate 16)
# ---------------------------------------------------------------------------

echo "--- Group E: NFR-2 size (gate 16) ---"

NAME="E1 nfr-2-webview-le-2mib (gate 16)"
NFR_CEILING=2097152
if [[ ! -f "$WEBVIEW_RUNTIME" ]]; then
    skip "$NAME" "webview runtime not found"
else
    RT_SIZE=$(wc -c < "$WEBVIEW_RUNTIME")
    if [[ "$RT_SIZE" -le "$NFR_CEILING" ]]; then
        pass "$NAME"
        PCT=$(( RT_SIZE * 100 / NFR_CEILING ))
        echo "       webview runtime: $RT_SIZE bytes (${PCT}% of 2 MiB)"
    else
        fail "$NAME" "runtime size $RT_SIZE bytes exceeds NFR-2 ceiling $NFR_CEILING"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group F: PH07 regression (gate 15)
# ---------------------------------------------------------------------------

echo "--- Group F: PH07 regression (gate 15) ---"

NAME="F1 ph07-gates-still-pass (gate 15)"
if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-regression"
elif [[ ! -f "$REPO_ROOT/tests/phase-07/run.sh" ]]; then
    skip "$NAME" "PH07 run.sh not found"
else
    PH07_OUT="$WORKDIR/ph07.log"
    bash "$REPO_ROOT/tests/phase-07/run.sh" \
        --skip-rebuild \
        > "$PH07_OUT" 2>&1 || true
    if grep -q "All mandatory gates PASS" "$PH07_OUT"; then
        pass "$NAME"
    else
        fail "$NAME" "PH07 regression detected"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$PH07_OUT"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH08 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
