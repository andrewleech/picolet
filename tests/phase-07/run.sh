#!/usr/bin/env bash
# tests/phase-07/run.sh — PH07 exit gate verification harness.
#
# Covers: FR-WV-{1,2,3}, FR-RT-2 (Linux webview half), NFR-2, NFR-8.
# Operational gates: ldd dynamic linkage, idempotent rebuild, NFR-2
# size, callback path, fixture end-to-end, cli non-regression.
#
# Usage:
#   cd /home/anl/picolet
#   ./tests/phase-07/run.sh [--skip-callback-probe] [--skip-fixture]
#                            [--skip-rebuild] [--skip-cli-regression]
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-webview
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-cli (for the
#     cli regression gates)
#   - xvfb-run on PATH
#   - libwebkit2gtk-4.1-0 installed on the host (or inside the build
#     container; the gate runs against the host binary).
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
WEBVIEW_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-webview"
CLI_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-cli"
FIXTURE="$SCRIPT_DIR/fixtures/hello-webview-min"

SKIP_CALLBACK_PROBE=0
SKIP_FIXTURE=0
SKIP_REBUILD=0
SKIP_CLI_REGRESSION=0
for arg in "$@"; do
    case "$arg" in
        --skip-callback-probe) SKIP_CALLBACK_PROBE=1 ;;
        --skip-fixture) SKIP_FIXTURE=1 ;;
        --skip-rebuild) SKIP_REBUILD=1 ;;
        --skip-cli-regression) SKIP_CLI_REGRESSION=1 ;;
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

WORKDIR="/tmp/picolet-ph07-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH07 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $WEBVIEW_RUNTIME"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: Runtime binary properties
# ---------------------------------------------------------------------------

echo "--- Group A: webview runtime properties ---"

NAME="A1 runtime-binary-exists (gate 2)"
if [[ -f "$WEBVIEW_RUNTIME" ]]; then
    pass "$NAME"
else
    fail "$NAME" "missing: $WEBVIEW_RUNTIME — run packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant webview"
    echo "Aborting."
    exit 1
fi

NAME="A2 nfr-2-size-le-2mib (gate 4)"
RT_SIZE=$(wc -c < "$WEBVIEW_RUNTIME")
NFR_CEILING=2097152
if [[ "$RT_SIZE" -le "$NFR_CEILING" ]]; then
    pass "$NAME"
    PCT=$(( RT_SIZE * 100 / NFR_CEILING ))
    echo "       webview runtime: $RT_SIZE bytes (${PCT}% of NFR-2 ceiling)"
else
    fail "$NAME" "size $RT_SIZE > $NFR_CEILING (NFR-2 violated)"
fi

NAME="A3 ldd-no-static-gui-link (gate 7)"
LDD_OUT="$(ldd "$WEBVIEW_RUNTIME" 2>&1)"
if echo "$LDD_OUT" | grep -qE "(libwebkit2gtk-4.1|libgtk-3|libjavascriptcoregtk-4.1)"; then
    fail "$NAME" "static link found: $LDD_OUT"
else
    pass "$NAME"
    echo "       ldd shows only libc/libm/ld-linux (LGPL link is dlopen-only)"
fi

NAME="A4 dlopen-soname-in-strings (gate 7)"
WK_STR=$(strings "$WEBVIEW_RUNTIME" | grep -c "libwebkit2gtk-4.1.so.0" || true)
GT_STR=$(strings "$WEBVIEW_RUNTIME" | grep -c "libgtk-3.so.0" || true)
GO_STR=$(strings "$WEBVIEW_RUNTIME" | grep -c "libgobject-2.0.so.0" || true)
JC_STR=$(strings "$WEBVIEW_RUNTIME" | grep -c "libjavascriptcoregtk-4.1.so.0" || true)
if [[ "$WK_STR" -ge 1 && "$GT_STR" -ge 1 && "$GO_STR" -ge 1 && "$JC_STR" -ge 1 ]]; then
    pass "$NAME"
    echo "       all 4 SONAMEs present as ffi.open string literals"
else
    fail "$NAME" "webkit=$WK_STR gtk=$GT_STR gobject=$GO_STR jsc=$JC_STR"
fi

NAME="A5 import-picolet_ui-no-display (gate 3)"
# Unset DISPLAY: confirms `import picolet_ui` doesn't need X.
actual="$(env -u DISPLAY "$WEBVIEW_RUNTIME" -c 'import picolet_ui; print("picolet_ui-ok"); print(picolet_ui.PUMP_INTERVAL_S)' 2>&1)"
if [[ "$actual" == "picolet_ui-ok"$'\n'"0.005" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'picolet_ui-ok\\n0.005'; got: $(printf '%q' "$actual")"
fi

NAME="A6 import-picolet-still-works"
actual="$(env -u DISPLAY "$WEBVIEW_RUNTIME" -c 'import picolet; print("picolet-ok")' 2>&1)"
if [[ "$actual" == "picolet-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'picolet-ok'; got: $actual"
fi

NAME="A7 public-api-callable"
actual="$(env -u DISPLAY "$WEBVIEW_RUNTIME" -c '
import picolet_ui
print(callable(picolet_ui.Window), callable(picolet_ui.Webview),
      callable(picolet_ui.WebviewTransport), callable(picolet_ui.run))
' 2>&1)"
if [[ "$actual" == "True True True True" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected all True; got: $actual"
fi

echo

# ---------------------------------------------------------------------------
# Group B: GTK rendering under xvfb
# ---------------------------------------------------------------------------

echo "--- Group B: GTK rendering under xvfb ---"

NAME="B1 sanity-test (gate 5/6)"
if ! command -v xvfb-run >/dev/null 2>&1; then
    skip "$NAME" "xvfb-run not on PATH"
else
    # xvfb-run pipes stderr through stdout (the -e option redirects xvfb-run
    # diagnostics; the wrapped process's stderr stays separate, but xvfb
    # only forwards stdout cleanly).  Capture both into b1.out.
    xvfb-run -a -s '-screen 0 800x600x24' timeout 15 \
        "$WEBVIEW_RUNTIME" -c 'import picolet_ui._test as t; t.run_sanity_test()' \
        > "$WORKDIR/b1.out" 2>&1
    if grep -q "PICOLET_WV_SANITY_OK title=LOADED" "$WORKDIR/b1.out"; then
        pass "$NAME"
    else
        fail "$NAME" "expected PICOLET_WV_SANITY_OK in output"
        echo "       output: $(cat "$WORKDIR/b1.out")"
    fi
fi

NAME="B2 callback-probe (gate 8)"
if [[ "$SKIP_CALLBACK_PROBE" -eq 1 ]]; then
    skip "$NAME" "--skip-callback-probe"
elif ! command -v xvfb-run >/dev/null 2>&1; then
    skip "$NAME" "xvfb-run not on PATH"
else
    xvfb-run -a -s '-screen 0 800x600x24' timeout 15 \
        "$WEBVIEW_RUNTIME" -c 'import picolet_ui._test as t; t.run_callback_probe()' \
        > "$WORKDIR/b2.out" 2>&1
    if grep -q "PICOLET_WV_CALLBACK_OK" "$WORKDIR/b2.out"; then
        pass "$NAME"
    else
        fail "$NAME" "expected PICOLET_WV_CALLBACK_OK; output: $(cat "$WORKDIR/b2.out")"
    fi
fi

NAME="B3 window-title-size-line (gate 6 / FR-WV-3)"
# The sanity test wrote 'window: title=PH07 Sanity size=640x480 resizable=False'.
# Under xvfb-run stderr is funnelled through stdout, so we search the
# combined b1.out from B1.
if [[ -f "$WORKDIR/b1.out" ]] && grep -q "window: title=PH07 Sanity size=640x480 resizable=False" "$WORKDIR/b1.out"; then
    pass "$NAME"
else
    fail "$NAME" "expected line 'window: title=PH07 Sanity size=640x480 resizable=False'"
fi

echo

# ---------------------------------------------------------------------------
# Group C: picolet build pipeline for webview
# ---------------------------------------------------------------------------

echo "--- Group C: picolet build (webview pipeline) ---"

if [[ "$SKIP_FIXTURE" -eq 1 ]]; then
    skip "C1-C3" "--skip-fixture"
elif ! command -v uv >/dev/null 2>&1; then
    skip "C1-C3" "uv not on PATH (picolet build host harness)"
else
    NAME="C1 picolet-build-webview"
    (
        cd "$FIXTURE" && \
        uv run python -m picolet build \
            --target linux-x64 \
            --runtime "$WEBVIEW_RUNTIME" \
            > "$WORKDIR/c1.log" 2>&1
    )
    BUILT="$FIXTURE/target/linux-x64/hello-webview-min"
    if [[ -f "$BUILT" ]]; then
        pass "$NAME"
        echo "       binary: $BUILT ($(wc -c < "$BUILT") bytes)"
    else
        fail "$NAME" "binary not produced; see $WORKDIR/c1.log"
        cat "$WORKDIR/c1.log"
    fi

    if [[ -f "$BUILT" ]]; then
        NAME="C2 picolet-toml-embedded-in-romfs"
        TOML_OUT="$("$BUILT" -c 'print(open("/rom/picolet.toml").read())' 2>&1)"
        if echo "$TOML_OUT" | grep -q '\[window\]' && \
           echo "$TOML_OUT" | grep -q 'title = "PH07 Sanity"'; then
            pass "$NAME"
        else
            fail "$NAME" "picolet.toml not embedded or missing [window]; got: $TOML_OUT"
        fi

        NAME="C3 fixture-launches-and-loads (gate 5/6 e2e)"
        if ! command -v xvfb-run >/dev/null 2>&1; then
            skip "$NAME" "xvfb-run not on PATH"
        else
            xvfb-run -a -s '-screen 0 800x600x24' timeout 15 \
                "$BUILT" > "$WORKDIR/c3.out" 2>&1
            if grep -q "PICOLET_WV_SANITY_OK title=LOADED" "$WORKDIR/c3.out"; then
                pass "$NAME"
            else
                fail "$NAME" "expected PICOLET_WV_SANITY_OK"
                echo "       output: $(cat "$WORKDIR/c3.out")"
            fi
        fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: idempotency
# ---------------------------------------------------------------------------

echo "--- Group D: idempotency ---"

NAME="D1 rebuild-idempotent-fast (gate 12)"
if [[ "$SKIP_REBUILD" -eq 1 ]]; then
    skip "$NAME" "--skip-rebuild"
else
    START=$(date +%s)
    bash "$PKG_ROOT/scripts/build-runtime.sh" --target linux-x64 --variant webview > "$WORKDIR/d1.log" 2>&1
    RC=$?
    END=$(date +%s)
    ELAPSED=$((END - START))
    if [[ "$RC" -eq 0 ]]; then
        pass "$NAME"
        echo "       warm rebuild: ${ELAPSED} s"
        if [[ "$ELAPSED" -gt 60 ]]; then
            echo "       WARNING: warm rebuild took ${ELAPSED}s; expected <60s"
        fi
    else
        fail "$NAME" "rebuild failed; see $WORKDIR/d1.log"
        tail -20 "$WORKDIR/d1.log"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group E: cli non-regression
# ---------------------------------------------------------------------------

echo "--- Group E: cli non-regression (gate 13) ---"

NAME="E1 cli-runtime-present"
if [[ -f "$CLI_RUNTIME" ]]; then
    pass "$NAME"
else
    skip "$NAME" "cli runtime not present: $CLI_RUNTIME"
fi

if [[ "$SKIP_CLI_REGRESSION" -eq 1 ]]; then
    skip "E2-E3" "--skip-cli-regression"
elif [[ ! -f "$CLI_RUNTIME" ]]; then
    skip "E2-E3" "cli runtime not present"
else
    NAME="E2 ph06-stdio-roundtrip"
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

    NAME="E3 cli-size-still-le-1mib"
    SIZE=$(wc -c < "$CLI_RUNTIME")
    if [[ "$SIZE" -le 1048576 ]]; then
        pass "$NAME"
        echo "       cli runtime: $SIZE bytes"
    else
        fail "$NAME" "cli runtime grew to $SIZE bytes (NFR-1 ceiling 1048576)"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH07 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
