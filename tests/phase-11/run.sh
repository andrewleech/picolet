#!/usr/bin/env bash
# tests/phase-11/run.sh — PH11 exit gate verification harness.
#
# Covers: FR-LV-{1,2,3,4}, FR-RT-2 (Linux lvgl half), NFR-3, NFR-5,
#         NFR-8 (per-variant carve-out).
# Operational: PH00-PH10 non-regression spot-checks.
#
# Usage:
#   cd /home/anl/picolet
#   ./tests/phase-11/run.sh [--skip-fixture] [--skip-rebuild]
#                            [--skip-non-regression]
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-lvgl
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-cli (for
#     non-regression check)
#   - xvfb-run on PATH (for gate-5 sanity test)
#   - libsdl2-2.0-0 installed at the runtime host
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
LVGL_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-lvgl"
CLI_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-cli"
WV_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-webview"
FIXTURE="$SCRIPT_DIR/fixtures/hello-lvgl-min-e2e"

SKIP_FIXTURE=0
SKIP_REBUILD=0
SKIP_NON_REGRESSION=0
for arg in "$@"; do
    case "$arg" in
        --skip-fixture) SKIP_FIXTURE=1 ;;
        --skip-rebuild) SKIP_REBUILD=1 ;;
        --skip-non-regression) SKIP_NON_REGRESSION=1 ;;
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

pass() { printf "  PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
fail() {
    printf "  FAIL  %s\n" "$1"
    if [[ -n "${2:-}" ]]; then printf "        %s\n" "$2"; fi
    FAIL=$((FAIL + 1))
    FAILED_GATES+=("$1")
}
skip() {
    printf "  SKIP  %s" "$1"
    if [[ -n "${2:-}" ]]; then printf "  (%s)" "$2"; fi
    printf "\n"
    SKIP=$((SKIP + 1))
}

WORKDIR="/tmp/picolet-ph11-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH11 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $LVGL_RUNTIME"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: Runtime binary properties (gates 2, 3, 4, 16, 17, 20)
# ---------------------------------------------------------------------------

echo "--- Group A: lvgl runtime properties ---"

NAME="A1 runtime-binary-exists (gate 2)"
if [[ -f "$LVGL_RUNTIME" ]]; then
    pass "$NAME"
else
    fail "$NAME" "missing: $LVGL_RUNTIME — run build-runtime.sh --target linux-x64 --variant lvgl"
    echo "Aborting."
    exit 1
fi

NAME="A2 nfr-3-size-le-2mib (gate 4)"
RT_SIZE=$(wc -c < "$LVGL_RUNTIME")
NFR_CEILING=2097152
if [[ "$RT_SIZE" -le "$NFR_CEILING" ]]; then
    pass "$NAME"
    PCT=$(( RT_SIZE * 100 / NFR_CEILING ))
    echo "       lvgl runtime: $RT_SIZE bytes (${PCT}% of NFR-3 ceiling)"
else
    fail "$NAME" "size $RT_SIZE > $NFR_CEILING (NFR-3 violated)"
fi

NAME="A3 import-lvgl-ok (gate 3, FR-LV-3)"
actual="$(env -u DISPLAY "$LVGL_RUNTIME" -c 'import lvgl as lv; print("ok")' 2>&1)"
if [[ "$actual" == "ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'ok'; got: $(printf '%q' "$actual")"
fi

NAME="A4 import-picolet-ok"
actual="$(env -u DISPLAY "$LVGL_RUNTIME" -c 'import picolet; print("picolet-ok")' 2>&1)"
if [[ "$actual" == "picolet-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'picolet-ok'; got: $(printf '%q' "$actual")"
fi

NAME="A5 import-picolet_ui-ok"
actual="$(env -u DISPLAY "$LVGL_RUNTIME" -c 'import picolet_ui; print(picolet_ui.LVGL_TICK_MS)' 2>&1)"
if [[ "$actual" == "5" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected '5'; got: $(printf '%q' "$actual")"
fi

NAME="A6 nfr-5-ldd-no-gpl-static (gate 20)"
LDD_OUT="$(ldd "$LVGL_RUNTIME" 2>&1)"
if echo "$LDD_OUT" | grep -qE "(libgpl|libreadline)"; then
    fail "$NAME" "GPL link found: $LDD_OUT"
else
    pass "$NAME"
    # Show SDL2 + libc family.
    echo "       NEEDED: $(objdump -p "$LVGL_RUNTIME" | grep NEEDED | tr '\n' ' ')"
fi

NAME="A7 sdl2-dynamic-linkage (FR-LV-1)"
if echo "$LDD_OUT" | grep -q "libSDL2-2.0.so.0"; then
    pass "$NAME"
else
    fail "$NAME" "libSDL2-2.0.so.0 not in ldd output (gate 20)"
fi

NAME="A8 submodule-pin-recorded (gate 17)"
SUBMODULE_STATUS="$(git -C "$REPO_ROOT" submodule status packages/picolet-runtime/overlay/lib/lv_binding_micropython 2>&1)"
if echo "$SUBMODULE_STATUS" | grep -qE "^[ \-]+[a-f0-9]{40}"; then
    pass "$NAME"
    echo "       $SUBMODULE_STATUS"
else
    fail "$NAME" "unexpected submodule state: $SUBMODULE_STATUS"
fi

NAME="A9 lv_conf-overlay-token-present (gate 16)"
if grep -q "PICOLET_LVGL_CONFIG" "$PKG_ROOT/overlay/ports/unix/variants/picolet-lvgl/lv_conf.h"; then
    pass "$NAME"
else
    fail "$NAME" "PICOLET_LVGL_CONFIG marker not found in overlay lv_conf.h"
fi

NAME="A10 manifest-lvgl-exists (gate 14)"
if [[ -f "$PKG_ROOT/manifests/manifest_lvgl.py" ]] && \
   grep -q "picolet_ui" "$PKG_ROOT/manifests/manifest_lvgl.py" && \
   grep -q "picolet" "$PKG_ROOT/manifests/manifest_lvgl.py"; then
    pass "$NAME"
else
    fail "$NAME" "manifest_lvgl.py missing or incomplete"
fi

echo

# ---------------------------------------------------------------------------
# Group B: SDL2 rendering under xvfb (gate 5)
# ---------------------------------------------------------------------------

echo "--- Group B: SDL2 rendering under xvfb ---"

NAME="B1 sanity-test (gate 5, FR-LV-1/2)"
if ! command -v xvfb-run >/dev/null 2>&1; then
    skip "$NAME" "xvfb-run not on PATH"
else
    xvfb-run -a -s '-screen 0 1024x768x24' timeout 15 \
        "$LVGL_RUNTIME" -c 'import picolet_ui._sanity as t; t.run_lvgl_sanity_test()' \
        > "$WORKDIR/b1.out" 2>&1 || true
    if grep -q "PICOLET_LV_SANITY_OK" "$WORKDIR/b1.out"; then
        pass "$NAME"
        grep "PICOLET_LV_SANITY_OK" "$WORKDIR/b1.out" | sed 's/^/       /'
    else
        fail "$NAME" "expected PICOLET_LV_SANITY_OK"
        echo "       output: $(cat "$WORKDIR/b1.out")"
    fi
fi

NAME="B2 ipc-probe (gate 8, FR-LV-4)"
if ! command -v xvfb-run >/dev/null 2>&1; then
    skip "$NAME" "xvfb-run not on PATH"
else
    # run_ipc_probe does not need a display.
    env -u DISPLAY timeout 15 \
        "$LVGL_RUNTIME" -c 'import picolet_ui._sanity as t; t.run_ipc_probe()' \
        > "$WORKDIR/b2.out" 2>&1 || true
    if grep -q "PICOLET_LV_IPC_OK" "$WORKDIR/b2.out"; then
        pass "$NAME"
    else
        fail "$NAME" "expected PICOLET_LV_IPC_OK; output: $(cat "$WORKDIR/b2.out")"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group C: hello-lvgl-min end-to-end (gate 6)
# ---------------------------------------------------------------------------

echo "--- Group C: picolet build (lvgl pipeline) ---"

if [[ "$SKIP_FIXTURE" -eq 1 ]]; then
    skip "C1-C2" "--skip-fixture"
elif ! command -v uv >/dev/null 2>&1; then
    skip "C1-C2" "uv not on PATH"
else
    NAME="C1 picolet-build-lvgl"
    (
        cd "$FIXTURE" && \
        uv run python -m picolet_cli build \
            --target linux-x64 \
            --runtime "$LVGL_RUNTIME" \
            > "$WORKDIR/c1.log" 2>&1
    ) || true
    BUILT="$FIXTURE/target/linux-x64/hello-lvgl-min"
    if [[ -f "$BUILT" ]]; then
        pass "$NAME"
        echo "       binary: $BUILT ($(wc -c < "$BUILT") bytes)"
    else
        fail "$NAME" "binary not produced; see $WORKDIR/c1.log"
        tail -20 "$WORKDIR/c1.log"
    fi

    if [[ -f "$BUILT" ]]; then
        NAME="C2 fixture-launches (gate 6 e2e)"
        if ! command -v xvfb-run >/dev/null 2>&1; then
            skip "$NAME" "xvfb-run not on PATH"
        else
            xvfb-run -a -s '-screen 0 1024x768x24' timeout 15 \
                "$BUILT" > "$WORKDIR/c2.out" 2>&1 || true
            if grep -q "PICOLET_LV_SANITY_OK" "$WORKDIR/c2.out"; then
                pass "$NAME"
            else
                fail "$NAME" "expected PICOLET_LV_SANITY_OK"
                echo "       output: $(cat "$WORKDIR/c2.out")"
            fi
        fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: non-regression (gates 11, 13, 19)
# ---------------------------------------------------------------------------

echo "--- Group D: non-regression ---"

NAME="D1 cli-runtime-present"
if [[ -f "$CLI_RUNTIME" ]]; then
    pass "$NAME"
    SIZE=$(wc -c < "$CLI_RUNTIME")
    echo "       cli runtime: $SIZE bytes"
else
    skip "$NAME" "cli runtime not present: $CLI_RUNTIME"
fi

if [[ "$SKIP_NON_REGRESSION" -eq 1 ]]; then
    skip "D2-D3" "--skip-non-regression"
elif [[ ! -f "$CLI_RUNTIME" ]]; then
    skip "D2-D3" "cli runtime not present"
else
    NAME="D2 ph06-stdio-roundtrip-still-works"
    APP='
import picolet
@picolet.command
async def greet(args):
    return "hi " + args["name"]
picolet.run()
'
    ACTUAL="$(printf '%s' '{"id":1,"cmd":"greet","args":{"name":"world"}}' | \
        "$CLI_RUNTIME" -c "$APP" 2>/dev/null || true)"
    EXPECTED='{"result": "hi world", "id": 1, "ok": true}'
    if [[ "$ACTUAL" == "$EXPECTED" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "expected $(printf '%q' "$EXPECTED"); got $(printf '%q' "$ACTUAL")"
    fi

    NAME="D3 cli-size-still-le-1mib"
    SIZE=$(wc -c < "$CLI_RUNTIME")
    if [[ "$SIZE" -le 1048576 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "cli runtime grew to $SIZE bytes (NFR-1 ceiling 1048576)"
    fi
fi

# Webview variant: simple import check (no xvfb required).
NAME="D4 webview-import-still-works (gate 19)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "webview runtime not present"
else
    actual="$(env -u DISPLAY "$WV_RUNTIME" -c 'import picolet_ui; print("picolet_ui-ok")' 2>&1 || true)"
    if echo "$actual" | grep -q "picolet_ui-ok"; then
        pass "$NAME"
    else
        fail "$NAME" "webview picolet_ui import failed: $actual"
    fi
fi

NAME="D5 windows-lvgl-binary-present (gate 13)"
# PH12 delivers the real windows-x64/lvgl build; PH12's own harness tests
# the full build pipeline.  Here we simply confirm the artifact exists on
# disk, mirroring the pattern used by D1 for the CLI runtime.
WIN_LVGL_BIN="$PKG_ROOT/build/picolet-runtime-windows-x64-lvgl.exe"
if [[ -f "$WIN_LVGL_BIN" ]]; then
    pass "$NAME"
    echo "       windows lvgl: $WIN_LVGL_BIN ($(wc -c < "$WIN_LVGL_BIN") bytes)"
else
    skip "$NAME" "windows-x64-lvgl runtime not present at $WIN_LVGL_BIN"
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH11 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
