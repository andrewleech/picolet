#!/usr/bin/env bash
# tests/phase-10/run.sh — PH10 exit gate verification harness.
#
# Covers: gates 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 18, 19
# (subset of the planner's 20 gates; gates 5, 16, 17, 20 are deferred
# to the SQE pass or are opt-in negatives).
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-10/run.sh [--skip-build] [--skip-regression]
#                              [--verbose]
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-windows-x64-webview.exe
#     (run packages/picolet-runtime/scripts/build-runtime.sh first or pass
#      --skip-build with the binary already present).
#   - WSL2 with Windows interop enabled (to run .exe files).
#   - Edge WebView2 Runtime installed on the Windows host (default on
#     Windows 11; available via Microsoft Update on Windows 10 21H2+).
#   - node on PATH (for the JS unit tests).
#   - uv on PATH (for picolet invocation).
#
# Returns 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
WV_RUNTIME="$PKG_ROOT/build/picolet-runtime-windows-x64-webview.exe"
CLI_RUNTIME="$PKG_ROOT/build/picolet-runtime-windows-x64-cli.exe"
LINUX_WV_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-webview"
E2E_FIXTURE="$SCRIPT_DIR/fixtures/hello-webview-min-e2e"

SKIP_BUILD=0
SKIP_REGRESSION=0
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        --skip-build)      SKIP_BUILD=1 ;;
        --skip-regression) SKIP_REGRESSION=1 ;;
        --verbose)         VERBOSE=1 ;;
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

WORKDIR="/tmp/picolet-ph10-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH10 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $WV_RUNTIME"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: build + smoke (gates 2, 3, 4, 7)
# ---------------------------------------------------------------------------

echo "--- Group A: build + smoke ---"

NAME="A1 build-runtime (gate 2)"
if [[ "$SKIP_BUILD" -eq 1 ]]; then
    if [[ -f "$WV_RUNTIME" ]]; then
        pass "$NAME (skipped; existing artifact)"
    else
        fail "$NAME" "--skip-build but $WV_RUNTIME absent"
    fi
elif ! command -v docker >/dev/null 2>&1; then
    skip "$NAME" "docker not on PATH"
else
    if bash "$PKG_ROOT/scripts/build-runtime.sh" \
            --target windows-x64 --variant webview \
            > "$WORKDIR/a1.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "build failed; see $WORKDIR/a1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -20 "$WORKDIR/a1.log"; fi
    fi
fi

NAME="A2 size-gate (gate 4 / NFR-2)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
else
    SIZE=$(wc -c < "$WV_RUNTIME")
    CEILING=2097152
    if [[ "$SIZE" -le "$CEILING" ]]; then
        PCT=$(( SIZE * 100 / CEILING ))
        pass "$NAME ($SIZE bytes, ${PCT}% of 2 MiB ceiling)"
    else
        fail "$NAME" "size $SIZE > ceiling $CEILING"
    fi
fi

NAME="A3 import-picolet-ui (gate 3)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
else
    # The unified picolet_ui picks the WebView2 backend on Windows via
    # sys.platform; importing the package must succeed without
    # triggering loader-DLL extract or COM init.
    OUT=$("$WV_RUNTIME" -c 'import picolet_ui; print("picolet_ui-ok platform=" + __import__("sys").platform)' 2>&1 || true)
    if echo "$OUT" | grep -q "picolet_ui-ok platform=win32"; then
        pass "$NAME"
    else
        fail "$NAME" "import failed: $OUT"
    fi
fi

NAME="A4 pe-imports (gate 7 / FR-WV-1)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
elif ! command -v objdump >/dev/null 2>&1; then
    skip "$NAME" "objdump not on PATH"
else
    DLLS=$(objdump -p "$WV_RUNTIME" | grep "DLL Name" | awk '{print $3}' | sort)
    if echo "$DLLS" | grep -qi 'WebView2Loader'; then
        fail "$NAME" "static WebView2Loader.dll import found"
    else
        pass "$NAME (imports: $(echo "$DLLS" | tr '\n' ' '))"
    fi
fi

NAME="A5 webview2loader-string-in-binary (gate 7 supplemental)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
elif ! command -v strings >/dev/null 2>&1; then
    skip "$NAME" "strings not on PATH"
else
    # Wide-string literal — strings -e l surfaces it.
    if strings -e l "$WV_RUNTIME" | grep -q 'WebView2Loader.dll'; then
        pass "$NAME"
    else
        fail "$NAME" "wide-string 'WebView2Loader.dll' not found in .rdata"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: bridge-JS feature-detect (gates 12, 13)
# ---------------------------------------------------------------------------

echo "--- Group B: bridge-JS feature-detect ---"

NAME="B1 channel-detect-webview2 (gate 12)"
if ! command -v node >/dev/null 2>&1; then
    skip "$NAME" "node not on PATH"
else
    if node "$SCRIPT_DIR/test_bridge_channel_detect.js" > "$WORKDIR/b1.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "see $WORKDIR/b1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/b1.log"; fi
    fi
fi

NAME="B2 channel-detect-webkit (gate 13)"
if ! command -v node >/dev/null 2>&1; then
    skip "$NAME" "node not on PATH"
else
    if node "$SCRIPT_DIR/test_bridge_channel_legacy.js" > "$WORKDIR/b2.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "see $WORKDIR/b2.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/b2.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group C: end-to-end fixture (gates 6, 8, 9, 10, 11, 14)
# ---------------------------------------------------------------------------

echo "--- Group C: end-to-end fixture (hello-webview-min-e2e) ---"

E2E_EXE="$E2E_FIXTURE/target/windows-x64/hello-webview-min-e2e.exe"

NAME="C1 picolet-build (gate 20 / FR-BP-1)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
elif ! command -v uv >/dev/null 2>&1; then
    skip "$NAME" "uv not on PATH"
else
    (
        cd "$E2E_FIXTURE" && \
        uv run --project "$REPO_ROOT" python -m picolet_cli build \
            --target windows-x64 \
            --runtime "$WV_RUNTIME" \
            > "$WORKDIR/c1.log" 2>&1
    ) || true
    if [[ -f "$E2E_EXE" ]]; then
        BSIZE=$(wc -c < "$E2E_EXE")
        pass "$NAME (binary: $BSIZE bytes)"
    else
        fail "$NAME" "see $WORKDIR/c1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/c1.log"; fi
    fi
fi

NAME="C2 romfs-trailer (gate 14)"
if [[ ! -f "$E2E_EXE" ]]; then
    skip "$NAME" "C1 did not produce binary"
else
    LIST=$("$E2E_EXE" -c 'import os; print(sorted(os.listdir("/rom")))' 2>&1 || true)
    if echo "$LIST" | grep -q "'picolet.toml'" && \
       echo "$LIST" | grep -q "'ui'" && \
       echo "$LIST" | grep -q "'picolet'"; then
        pass "$NAME"
        if [[ "$VERBOSE" -eq 1 ]]; then echo "       /rom contents: $LIST"; fi
    else
        fail "$NAME" "romfs missing expected entries: $LIST"
    fi
fi

NAME="C3 e2e-run (gates 6, 8, 9, 10, 11)"
if [[ ! -f "$E2E_EXE" ]]; then
    skip "$NAME" "C1 did not produce binary"
else
    timeout 45 "$E2E_EXE" > "$WORKDIR/c3.stdout" 2> "$WORKDIR/c3.stderr" || true

    # Gate 6: window stderr line.
    GATE6_OK=0
    if grep -q "window: title=PH10 E2E size=800x600 resizable=False" "$WORKDIR/c3.stderr"; then
        GATE6_OK=1
    fi
    # Gates 8, 9, 10, 11: sentinel tokens on stdout.
    GATE8_OK=0
    GATE9_OK=0
    GATE10_OK=0
    GATE11_OK=0
    grep -q "PICOLET_PH10_BRIDGE_INJECT_OK" "$WORKDIR/c3.stdout" && GATE8_OK=1
    grep -q "PICOLET_PH10_INVOKE_OK"        "$WORKDIR/c3.stdout" && GATE9_OK=1
    grep -q "PICOLET_PH10_ERROR_OK"         "$WORKDIR/c3.stdout" && GATE10_OK=1
    grep -q "PICOLET_PH10_EVENT_OK"         "$WORKDIR/c3.stdout" && GATE11_OK=1

    TOTAL=$((GATE6_OK + GATE8_OK + GATE9_OK + GATE10_OK + GATE11_OK))
    if [[ "$TOTAL" -eq 5 ]]; then
        pass "$NAME (5/5 sentinels)"
    else
        fail "$NAME" \
            "gates: 6=$GATE6_OK 8=$GATE8_OK 9=$GATE9_OK 10=$GATE10_OK 11=$GATE11_OK"
        if [[ "$VERBOSE" -eq 1 ]]; then
            echo "       --- stdout ---"; cat "$WORKDIR/c3.stdout"
            echo "       --- stderr ---"; cat "$WORKDIR/c3.stderr"
        fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: cross-platform isolation (gate 15)
# ---------------------------------------------------------------------------

echo "--- Group D: cross-platform isolation ---"

NAME="D1 _win_ffi-unavailable-on-linux (gate 15)"
if [[ ! -f "$LINUX_WV_RUNTIME" ]]; then
    skip "$NAME" "linux webview runtime missing"
else
    # picolet_ui itself imports cleanly on both platforms (the win32
    # backend code paths are dead on Linux).  The FFI module that
    # binds the in-process picolet_webview2 C overlay must NOT load
    # on Linux — the .exe has no such symbols and ffi.open(None)
    # would resolve to a Linux binary with no picolet_wv2_* exports.
    OUT=$("$LINUX_WV_RUNTIME" -c 'from picolet_ui import _win_ffi' 2>&1 || true)
    if echo "$OUT" | grep -qE "ImportError|ModuleNotFoundError|cannot import|Error|error"; then
        pass "$NAME"
    else
        fail "$NAME" "expected import failure; got: $OUT"
    fi
fi

NAME="D2 _gtk_ffi-unavailable-on-windows (gate 15)"
if [[ ! -f "$WV_RUNTIME" ]]; then
    skip "$NAME" "runtime missing"
else
    # The GTK FFI module dlopens libwebkit2gtk-4.1.so.0 at import
    # time; on Windows this resolves nothing and raises OSError
    # (which _safe_open wraps as ImportError).
    OUT=$("$WV_RUNTIME" -c 'from picolet_ui import _gtk_ffi' 2>&1 || true)
    if echo "$OUT" | grep -qE "ImportError|ModuleNotFoundError|cannot import|Error|error"; then
        pass "$NAME"
    else
        fail "$NAME" "expected import failure; got: $OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group E: regression (gates 18, 19)
# ---------------------------------------------------------------------------

echo "--- Group E: regression ---"

NAME="E1 ph09-linux-still-passes (gate 18)"
if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-regression"
elif [[ ! -x "$REPO_ROOT/tests/phase-09/run.sh" ]]; then
    skip "$NAME" "tests/phase-09/run.sh not executable"
else
    if bash "$REPO_ROOT/tests/phase-09/run.sh" --skip-rebuild \
            > "$WORKDIR/e1.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "see $WORKDIR/e1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/e1.log"; fi
    fi
fi

NAME="E2 ph04-windows-still-passes (gate 19)"
if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-regression"
elif [[ ! -x "$REPO_ROOT/tests/phase-04/run.sh" ]]; then
    skip "$NAME" "tests/phase-04/run.sh not executable"
elif [[ ! -f "$CLI_RUNTIME" ]]; then
    skip "$NAME" "windows cli runtime missing"
else
    if bash "$REPO_ROOT/tests/phase-04/run.sh" --skip-build \
            > "$WORKDIR/e2.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "see $WORKDIR/e2.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/e2.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo "=== Summary ==="
printf "  PASS  %d\n" "$PASS"
printf "  FAIL  %d\n" "$FAIL"
printf "  SKIP  %d\n" "$SKIP"
if [[ "${#FAILED_GATES[@]}" -gt 0 ]]; then
    echo
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do printf "  - %s\n" "$g"; done
fi

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
