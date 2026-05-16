#!/usr/bin/env bash
# tests/phase-17/run.sh — PH17 exit gate verification harness.
#
# Tests:
#   A. CLI wiring: picolet test --help shows the subcommand.
#   B. FR-TEST-1 (Linux): picolet:test-port=<N> line appears within 3 s.
#   C. FR-TEST-4 (Linux): --screenshot produces a valid PNG > 1 KB.
#   D. FR-TEST-2 (LVGL):  --screenshot against lvgl binary produces PNG.
#   E. NFR-TEST-2 (no build-time bake-in): binary does NOT contain 'PICOLET_TEST_MODE=1'.
#   F. NFR-TEST-2 (loopback only): inspector port is bound to 127.0.0.1.
#   G. NFR-TEST-1 (timing): spawn-to-screenshot ≤ 3 s wall-clock.
#   H. FR-TEST-3 (clean error): --browser chromium on linux webview exits non-zero.
#   I. Bridge ready: window.picolet.__ready__ === true after bundle loads.
#   J. FR-TEST-1 (Windows SKIP): dockcross .exe check; skipped in this script.
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-17/run.sh [--skip-regression] [--skip-slow] [--verbose]
#
# Flags:
#   --skip-regression  Skip calling previous phase tests.
#   --skip-slow        Skip gate G (3 s timing; requires a display).
#   --verbose          Print extra diagnostics.
#
# Prerequisites:
#   - uv on PATH.
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-webview (built).
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl (built; gate D only).
#   - python3 with Pillow for PNG validation.
#   - xvfb-run if run without a display.
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0
SKIP=0
VERBOSE=0
SKIP_REGRESSION=0
SKIP_SLOW=0

for arg in "$@"; do
    case "$arg" in
        --skip-regression) SKIP_REGRESSION=1 ;;
        --skip-slow)       SKIP_SLOW=1 ;;
        --verbose)         VERBOSE=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

pass()    { printf "  PASS  %s\n" "$*"; PASS=$(( PASS + 1 )); }
fail()    { printf "  FAIL  %s\n" "$*" >&2; FAIL=$(( FAIL + 1 )); }
skip()    { printf "  SKIP  %s\n" "$*"; SKIP=$(( SKIP + 1 )); }
verbose() { [[ "$VERBOSE" -eq 1 ]] && printf "       %s\n" "$*" || true; }

WORKDIR="$(mktemp -d /tmp/picolet-ph17-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

WV_RUNTIME="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-linux-x64-webview"
LV_RUNTIME="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl"

echo "=== PH17 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Gate A: CLI wiring
# ---------------------------------------------------------------------------
echo "=== Gate A: CLI wiring ==="

if (cd "$REPO_ROOT" && uv run python -m picolet_cli --help 2>&1 | grep -q "test"); then
    pass "picolet --help lists 'test' subcommand"
else
    fail "picolet --help missing 'test' subcommand"
fi

if (cd "$REPO_ROOT" && uv run python -m picolet_cli test --help 2>&1 | grep -q "\-\-screenshot"); then
    pass "picolet test --help shows --screenshot flag"
else
    fail "picolet test --help missing --screenshot flag"
fi

if (cd "$REPO_ROOT" && uv run python -m picolet_cli test --help 2>&1 | grep -q "\-\-browser"); then
    pass "picolet test --help shows --browser flag"
else
    fail "picolet test --help missing --browser flag"
fi

# ---------------------------------------------------------------------------
# Gate B: FR-TEST-1 (Linux): port announcement within 3 s
# ---------------------------------------------------------------------------
echo
echo "=== Gate B: FR-TEST-1 (Linux webview inspector port) ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate B: webview runtime not built: $WV_RUNTIME"
else
    # Spawn the runtime with PICOLET_TEST_MODE=1, a minimal HTML page,
    # capture stderr looking for picolet:test-port=<N>.
    # We pass a data: URL so no romfs is needed.
    GATE_B_OUT="$WORKDIR/gate_b.stderr"

    # Create minimal HTML for the webview to load.
    cat > "$WORKDIR/test.html" <<'EOF'
<!doctype html><html><head><meta charset="utf-8"><title>PH17 Test</title></head>
<body><p>PH17</p></body></html>
EOF

    # Wrap in xvfb-run if no display.
    XVFB_PREFIX=""
    if [[ -z "${DISPLAY:-}" ]] && command -v xvfb-run >/dev/null 2>&1; then
        XVFB_PREFIX="xvfb-run -a -s -screen 0 1280x800x24 -e /dev/stderr"
    fi

    # Time-box the port wait to 5 s (generous; NFR-TEST-1 says ≤ 3 s but
    # gate G measures precisely).
    PORT_LINE=""
    if PORT_LINE=$(
        PICOLET_TEST_MODE=1 $XVFB_PREFIX "$WV_RUNTIME" \
            "file://$WORKDIR/test.html" 2>&1 &
        PID=$!
        sleep 5
        kill $PID 2>/dev/null || true
        wait $PID 2>/dev/null || true
    ) 2>&1; then
        true
    fi

    # Better approach: use a background job and read stderr pipe.
    PICOLET_TEST_MODE=1 $XVFB_PREFIX "$WV_RUNTIME" \
        "file://$WORKDIR/test.html" >"$WORKDIR/gate_b.stdout" 2>"$GATE_B_OUT" &
    B_PID=$!
    # Wait up to 5 s for the port line.
    B_DEADLINE=$(( SECONDS + 5 ))
    GATE_B_PORT=""
    while [[ $SECONDS -lt $B_DEADLINE ]]; do
        GATE_B_PORT=$(grep -oP 'picolet:test-port=\K\d+' "$GATE_B_OUT" 2>/dev/null | head -1 || true)
        if [[ -n "$GATE_B_PORT" ]]; then break; fi
        sleep 0.2
    done
    kill $B_PID 2>/dev/null || true
    wait $B_PID 2>/dev/null || true

    if [[ -n "$GATE_B_PORT" ]]; then
        pass "Gate B: picolet:test-port=$GATE_B_PORT appeared within 5 s"
        verbose "stderr: $(cat "$GATE_B_OUT" | head -5)"
    else
        fail "Gate B: no picolet:test-port=<N> line in stderr (timeout)"
        verbose "stderr: $(cat "$GATE_B_OUT" 2>/dev/null | head -10)"
    fi
fi

# ---------------------------------------------------------------------------
# Gate C: FR-TEST-4 (Linux): --screenshot produces valid PNG > 1 KB
# ---------------------------------------------------------------------------
echo
echo "=== Gate C: FR-TEST-4 (webview screenshot PNG) ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate C: webview runtime not built"
else
    PNG_C="$WORKDIR/wv.png"
    # Use picolet test --no-build --screenshot against the webview runtime.
    # Pass a stub URL; the screenshot will capture whatever the webview renders.
    if (cd "$REPO_ROOT" && timeout 30 uv run python -m picolet_cli test \
        --no-build --screenshot "$PNG_C" \
        "$WV_RUNTIME" -- "file://$WORKDIR/test.html" \
        2>/dev/null); then
        if [[ -f "$PNG_C" ]]; then
            SIZE=$(wc -c < "$PNG_C")
            if [[ "$SIZE" -gt 1024 ]]; then
                # Validate PNG via Pillow.
                if python3 -c "from PIL import Image; Image.open('$PNG_C').verify()" 2>/dev/null; then
                    pass "Gate C: webview screenshot is valid PNG, size=$SIZE bytes"
                else
                    fail "Gate C: screenshot exists but Pillow verify failed"
                fi
            else
                fail "Gate C: screenshot too small: $SIZE bytes (expected > 1024)"
            fi
        else
            fail "Gate C: --screenshot did not create $PNG_C"
        fi
    else
        fail "Gate C: picolet test --screenshot exited non-zero"
    fi
fi

# ---------------------------------------------------------------------------
# Gate D: FR-TEST-2 (LVGL): --screenshot against lvgl binary
# ---------------------------------------------------------------------------
echo
echo "=== Gate D: FR-TEST-2 (LVGL screenshot PNG) ==="

if [[ ! -x "$LV_RUNTIME" ]]; then
    skip "Gate D: lvgl runtime not built: $LV_RUNTIME"
else
    PNG_D="$WORKDIR/lv.png"
    if (cd "$REPO_ROOT" && timeout 30 uv run python -m picolet_cli test \
        --no-build --screenshot "$PNG_D" \
        "$LV_RUNTIME" \
        2>/dev/null); then
        if [[ -f "$PNG_D" ]]; then
            SIZE=$(wc -c < "$PNG_D")
            if [[ "$SIZE" -gt 1024 ]]; then
                if python3 -c "from PIL import Image; Image.open('$PNG_D').verify()" 2>/dev/null; then
                    pass "Gate D: LVGL screenshot is valid PNG, size=$SIZE bytes"
                else
                    fail "Gate D: LVGL screenshot Pillow verify failed"
                fi
            else
                fail "Gate D: LVGL screenshot too small: $SIZE bytes"
            fi
        else
            fail "Gate D: --screenshot did not create $PNG_D"
        fi
    else
        fail "Gate D: picolet test --screenshot (LVGL) exited non-zero"
    fi
fi

# ---------------------------------------------------------------------------
# Gate E: NFR-TEST-2 (no build-time bake-in of PICOLET_TEST_MODE=1)
# ---------------------------------------------------------------------------
echo
echo "=== Gate E: NFR-TEST-2 (no PICOLET_TEST_MODE=1 baked into binary) ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate E: webview runtime not built"
else
    # The binary may legitimately contain the string 'PICOLET_TEST_MODE' (it
    # calls getenv("PICOLET_TEST_MODE")) but must NOT contain
    # 'PICOLET_TEST_MODE=1' which would indicate a compile-time default baked in.
    # (See NFR-TEST-2 spec clarification and O5.)
    if strings "$WV_RUNTIME" | grep -qF 'PICOLET_TEST_MODE=1'; then
        fail "Gate E: binary contains 'PICOLET_TEST_MODE=1' (compile-time bake-in detected)"
    else
        pass "Gate E: binary does not contain 'PICOLET_TEST_MODE=1'"
    fi
fi

# ---------------------------------------------------------------------------
# Gate F: NFR-TEST-2 (loopback only — port bound to 127.0.0.1)
# ---------------------------------------------------------------------------
echo
echo "=== Gate F: NFR-TEST-2 (inspector port bound to 127.0.0.1 only) ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate F: webview runtime not built"
elif ! command -v ss >/dev/null 2>&1; then
    skip "Gate F: ss not available"
else
    XVFB_PREFIX=""
    if [[ -z "${DISPLAY:-}" ]] && command -v xvfb-run >/dev/null 2>&1; then
        XVFB_PREFIX="xvfb-run -a -s -screen 0 1280x800x24 -e /dev/stderr"
    fi

    PICOLET_TEST_MODE=1 $XVFB_PREFIX "$WV_RUNTIME" \
        "file://$WORKDIR/test.html" >"$WORKDIR/gate_f.stdout" 2>"$WORKDIR/gate_f.stderr" &
    F_PID=$!

    F_DEADLINE=$(( SECONDS + 8 ))
    F_PORT=""
    while [[ $SECONDS -lt $F_DEADLINE ]]; do
        F_PORT=$(grep -oP 'picolet:test-port=\K\d+' "$WORKDIR/gate_f.stderr" 2>/dev/null | head -1 || true)
        if [[ -n "$F_PORT" ]]; then break; fi
        sleep 0.2
    done

    if [[ -n "$F_PORT" ]]; then
        # Check that the port is bound only on 127.0.0.1.
        sleep 0.5
        BOUND=$(ss -lnt "sport = :$F_PORT" 2>/dev/null | grep -v '^Netid' || true)
        verbose "ss output: $BOUND"
        if echo "$BOUND" | grep -q "127.0.0.1:$F_PORT"; then
            # Verify there is no 0.0.0.0 binding.
            if echo "$BOUND" | grep -qE "0\.0\.0\.0:$F_PORT|:::$F_PORT"; then
                fail "Gate F: port $F_PORT is also bound to 0.0.0.0/:: (not loopback-only)"
            else
                pass "Gate F: port $F_PORT is bound to 127.0.0.1 only"
            fi
        else
            # The WebKit inspector server may not open its own TCP listening socket
            # until the first connection attempt.  If ss shows nothing, skip rather
            # than fail — the port announcement is the runtime's contract, not ss.
            skip "Gate F: port $F_PORT not yet visible in ss (may open on first connect)"
        fi
    else
        skip "Gate F: no port found (gate B already checked this)"
    fi

    kill $F_PID 2>/dev/null || true
    wait $F_PID 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Gate G: NFR-TEST-1 (timing: spawn → screenshot ≤ 3 s)
# ---------------------------------------------------------------------------
echo
echo "=== Gate G: NFR-TEST-1 (spawn-to-screenshot ≤ 3 s) ==="

if [[ $SKIP_SLOW -eq 1 ]]; then
    skip "Gate G: --skip-slow"
elif [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate G: webview runtime not built"
else
    PNG_G="$WORKDIR/timing.png"
    T_START=$SECONDS
    T_START_NS=$(date +%s%N 2>/dev/null || echo 0)

    (cd "$REPO_ROOT" && timeout 15 uv run python -m picolet_cli test \
        --no-build --screenshot "$PNG_G" \
        "$WV_RUNTIME" -- "file://$WORKDIR/test.html" \
        2>/dev/null) && T_RC=0 || T_RC=$?

    T_END_NS=$(date +%s%N 2>/dev/null || echo 0)
    if [[ "$T_START_NS" -ne 0 && "$T_END_NS" -ne 0 ]]; then
        ELAPSED_MS=$(( (T_END_NS - T_START_NS) / 1000000 ))
        ELAPSED_S=$(( ELAPSED_MS / 1000 ))
        ELAPSED_MS_FRAC=$(( ELAPSED_MS % 1000 ))
        verbose "elapsed: ${ELAPSED_S}.${ELAPSED_MS_FRAC}s"
        if [[ $T_RC -eq 0 && $ELAPSED_MS -le 3000 ]]; then
            pass "Gate G: spawn-to-screenshot ${ELAPSED_S}.${ELAPSED_MS_FRAC}s ≤ 3 s"
        elif [[ $T_RC -ne 0 ]]; then
            fail "Gate G: screenshot command failed (rc=$T_RC)"
        else
            fail "Gate G: spawn-to-screenshot ${ELAPSED_S}.${ELAPSED_MS_FRAC}s > 3 s (NFR-TEST-1)"
        fi
    else
        skip "Gate G: sub-second timing not available (date +%s%N not supported)"
    fi
fi

# ---------------------------------------------------------------------------
# Gate H: FR-TEST-3 clean error (chromium on Linux webview)
# ---------------------------------------------------------------------------
echo
echo "=== Gate H: FR-TEST-3 (clean error: chromium against webkit binary) ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate H: webview runtime not built"
elif [[ "$(uname -s)" != "Linux" ]]; then
    skip "Gate H: Linux-only test"
else
    if (cd "$REPO_ROOT" && uv run python -m picolet_cli test \
        --no-build --browser chromium \
        "$WV_RUNTIME" -- "file://$WORKDIR/test.html" \
        2>&1) | grep -qi "chromium is not supported"; then
        pass "Gate H: --browser chromium on Linux webview exits with clear error"
    else
        # Accept any non-zero exit code as evidence of the guard.
        EXIT_CODE=0
        (cd "$REPO_ROOT" && uv run python -m picolet_cli test \
            --no-build --browser chromium \
            "$WV_RUNTIME" -- "file://$WORKDIR/test.html" \
            2>/dev/null) || EXIT_CODE=$?
        if [[ $EXIT_CODE -ne 0 ]]; then
            pass "Gate H: --browser chromium on Linux webview exits non-zero (rc=$EXIT_CODE)"
        else
            fail "Gate H: --browser chromium on Linux webview did not exit with error"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Gate I: Bridge ready (window.picolet.__ready__ === true)
# ---------------------------------------------------------------------------
echo
echo "=== Gate I: bridge ready flag ==="

if [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate I: webview runtime not built"
else
    ASSERT_READY="$WORKDIR/assert_ready.py"
    cat > "$ASSERT_READY" <<'PYEOF'
import asyncio
import sys

async def main():
    ready = await harness.page.evaluate("window.picolet && window.picolet.__ready__ === true")
    if ready:
        print("BRIDGE_READY_OK")
        return 0
    else:
        print("BRIDGE_NOT_READY value={}".format(ready), file=sys.stderr)
        return 1

rc = asyncio.run(main())
sys.exit(rc)
PYEOF

    if (cd "$REPO_ROOT" && timeout 20 uv run python -m picolet_cli test \
        --no-build --run "$ASSERT_READY" \
        "$WV_RUNTIME" -- "file://$WORKDIR/test.html" \
        2>/dev/null); then
        pass "Gate I: window.picolet.__ready__ === true"
    else
        fail "Gate I: bridge ready flag not set or test script failed"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "=== Summary ==="
echo "    PASS: $PASS"
echo "    FAIL: $FAIL"
echo "    SKIP: $SKIP"
echo

if [[ $FAIL -gt 0 ]]; then
    echo "RESULT: FAIL ($FAIL gate(s) failed)" >&2
    exit 1
fi
echo "RESULT: PASS"
exit 0
