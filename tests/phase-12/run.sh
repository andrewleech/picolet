#!/usr/bin/env bash
# tests/phase-12/run.sh — PH12 exit gate verification harness.
#
# Covers: FR-LV-{1,2,3,4} (Windows half), FR-RT-2 (Windows lvgl),
#         NFR-3 (Windows), NFR-9.
# Gates mapped:
#   2  — artifact present
#   3  — import lvgl
#   4  — NFR-3 size <= 3 MiB (windows-x64/lvgl; relaxed from 2 MiB while upstream SDL2 binary is used)
#   5  — SDL2 window (MANUAL — requires Windows host display via WSL interop)
#   6  — hello-lvgl-win-min end-to-end (requires display)
#   7  — IPC probe
#   8  — SDL2.dll present alongside artifact (dynamic linkage from upstream release)
#   9  — Linux lvgl non-regression
#  10  — Windows cli + webview non-regression
#  11  — manifest freezes picolet_ui (not picolet_ui_win)
#  12  — asyncio import
#  13  — SDL2 upstream cache gate (second build skips re-download)
#  14  — PICOLET_LVGL_CONFIG token in binary (or lv_conf.h)
#  15  — lv_conf.h used is the overlay copy
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-12/run.sh [--skip-build] [--skip-display] \
#                               [--skip-non-regression] [--verbose]
#
# Flags:
#   --skip-build          Skip building the runtime (use existing artifact).
#   --skip-display        Skip gates requiring a Windows display (5, 6).
#   --skip-non-regression Skip PH09/PH10/PH11 non-regression checks.
#   --verbose             Print extra output on failures.
#
# Prerequisites:
#   - docker with dockcross/windows-static-x64-posix image.
#   - packages/picolet-runtime/build/picolet-runtime-windows-x64-lvgl.exe
#     (built by build-runtime.sh or passed via --skip-build).
#   - packages/picolet-runtime/build/SDL2.dll (copied by build-runtime.sh).
#   - WSL interop enabled (to run .exe under WSL).
#   - For gates 5 and 6: Windows display accessible via WSL interop.
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
LVGL_WIN_RUNTIME="$PKG_ROOT/build/picolet-runtime-windows-x64-lvgl.exe"
LVGL_LIN_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-lvgl"
CLI_WIN_RUNTIME="$PKG_ROOT/build/picolet-runtime-windows-x64-cli.exe"
FIXTURE="$SCRIPT_DIR/fixtures/hello-lvgl-win-min"

SKIP_BUILD=0
SKIP_DISPLAY=0
SKIP_NON_REGRESSION=0
VERBOSE=0

for arg in "$@"; do
    case "$arg" in
        --skip-build)          SKIP_BUILD=1 ;;
        --skip-display)        SKIP_DISPLAY=1 ;;
        --skip-non-regression) SKIP_NON_REGRESSION=1 ;;
        --verbose)             VERBOSE=1 ;;
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

# run_win: run a Windows .exe and strip CRLF from output.
# Windows binaries emit \r\n on stdout even under WSL; strip \r to normalise.
run_win() { "$@" 2>&1 | tr -d '\r' || true; }

WORKDIR="/tmp/picolet-ph12-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH12 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $LVGL_WIN_RUNTIME"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: Build (gate 2)
# ---------------------------------------------------------------------------

echo "--- Group A: build ---"

NAME="A1 build-runtime (gate 2)"
if [[ "$SKIP_BUILD" -eq 1 ]]; then
    if [[ -f "$LVGL_WIN_RUNTIME" ]]; then
        pass "$NAME (skipped; existing artifact)"
    else
        fail "$NAME" "--skip-build but $LVGL_WIN_RUNTIME absent"
        echo "Aborting: runtime not present."
        exit 1
    fi
elif ! command -v docker >/dev/null 2>&1; then
    skip "$NAME" "docker not on PATH"
    if [[ ! -f "$LVGL_WIN_RUNTIME" ]]; then
        echo "Aborting: no docker and no existing runtime."
        exit 1
    fi
else
    if bash "$PKG_ROOT/scripts/build-runtime.sh" \
            --target windows-x64 --variant lvgl \
            > "$WORKDIR/a1.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "build failed; see $WORKDIR/a1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/a1.log"; fi
        echo "Aborting: build failed."
        exit 1
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: Runtime binary properties (gates 3, 4, 8, 11, 12, 14, 15)
# ---------------------------------------------------------------------------

echo "--- Group B: runtime binary properties ---"

NAME="B1 artifact-present (gate 2)"
if [[ -f "$LVGL_WIN_RUNTIME" ]]; then
    pass "$NAME"
else
    fail "$NAME" "missing: $LVGL_WIN_RUNTIME"
    echo "Aborting."
    exit 1
fi

NAME="B2 nfr-3-size-le-3mib (gate 4, NFR-3)"
RT_SIZE=$(wc -c < "$LVGL_WIN_RUNTIME")
# NFR-3 windows-x64/lvgl: relaxed to 3 MiB while SDL2 is sourced from the
# official upstream MinGW binary release (dynamic linkage via SDL2.dll).
# The 2 MiB target via custom from-source SDL2 (-ffunction-sections +
# --gc-sections) is deferred to the roadmap.
NFR_CEILING=3145728   # 3 MiB (NFR-3, relaxed)
if [[ "$RT_SIZE" -le "$NFR_CEILING" ]]; then
    PCT=$(( RT_SIZE * 100 / NFR_CEILING ))
    pass "$NAME ($RT_SIZE bytes, ${PCT}% of NFR-3 ceiling)"
else
    fail "$NAME" "size $RT_SIZE > $NFR_CEILING (NFR-3 violated)"
fi

NAME="B3 import-lvgl-ok (gate 3, FR-LV-3)"
actual="$(run_win "$LVGL_WIN_RUNTIME" -c 'import lvgl as lv; print("ok")')"
if [[ "$actual" == "ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'ok'; got: $(printf '%q' "$actual")"
fi

NAME="B4 import-asyncio-ok (gate 12)"
actual="$(run_win "$LVGL_WIN_RUNTIME" -c 'import asyncio; print("aio-ok")')"
if [[ "$actual" == "aio-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'aio-ok'; got: $(printf '%q' "$actual")"
fi

NAME="B5 import-picolet-ok"
actual="$(run_win "$LVGL_WIN_RUNTIME" -c 'import picolet; print("picolet-ok")')"
if [[ "$actual" == "picolet-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'picolet-ok'; got: $(printf '%q' "$actual")"
fi

NAME="B6 import-picolet_ui-ok"
actual="$(run_win "$LVGL_WIN_RUNTIME" -c 'import picolet_ui; print(picolet_ui.LVGL_TICK_MS)')"
if [[ "$actual" == "5" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected '5'; got: $(printf '%q' "$actual")"
fi

NAME="B7 SDL2-dll-present-alongside-artifact (gate 8)"
# SDL2 is now dynamically linked via the upstream MinGW release; SDL2.dll must
# be present in the build directory alongside the .exe.
SDL2_DLL="$(dirname "$LVGL_WIN_RUNTIME")/SDL2.dll"
if [[ -f "$SDL2_DLL" ]]; then
    pass "$NAME ($SDL2_DLL)"
else
    fail "$NAME" "SDL2.dll not found at $SDL2_DLL (must be redistributed alongside the binary)"
fi

NAME="B8 manifest-freezes-picolet_ui-not-win (gate 11)"
MANIFEST_PATH="$PKG_ROOT/manifests/manifest_lvgl_windows.py"
if [[ ! -f "$MANIFEST_PATH" ]]; then
    fail "$NAME" "manifest_lvgl_windows.py missing"
elif grep -v '^#' "$MANIFEST_PATH" | grep -q 'picolet_ui_win'; then
    # Check non-comment lines only: picolet_ui_win must not appear in code
    fail "$NAME" "manifest code (non-comment) freezes picolet_ui_win (must freeze picolet_ui only)"
elif grep -q 'freeze.*picolet_ui' "$MANIFEST_PATH"; then
    pass "$NAME"
else
    fail "$NAME" "manifest_lvgl_windows.py does not freeze picolet_ui"
fi

NAME="B9 lv_conf-picolet-token-present (gate 15)"
LV_CONF="$PKG_ROOT/variants/lvgl/unix/lv_conf.h"
if grep -q "PICOLET_LVGL_CONFIG" "$LV_CONF"; then
    pass "$NAME"
else
    fail "$NAME" "PICOLET_LVGL_CONFIG token not found in $LV_CONF"
fi

echo

# ---------------------------------------------------------------------------
# Group C: IPC probe (gate 7, FR-LV-4)
# ---------------------------------------------------------------------------

echo "--- Group C: IPC probe (gate 7) ---"

NAME="C1 ipc-probe (gate 7, FR-LV-4)"
timeout 20 "$LVGL_WIN_RUNTIME" \
    -c 'import picolet_ui._sanity as t; t.run_ipc_probe()' \
    > "$WORKDIR/c1.out" 2>&1 || true
if grep -q "PICOLET_LV_IPC_OK" "$WORKDIR/c1.out"; then
    pass "$NAME"
    grep "PICOLET_LV_IPC_OK" "$WORKDIR/c1.out" | sed 's/^/       /'
else
    fail "$NAME" "expected PICOLET_LV_IPC_OK"
    if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/c1.out"; fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: SDL2 window tests (gates 5, 6) — require Windows display
# ---------------------------------------------------------------------------

echo "--- Group D: SDL2 display gates (manual / display required) ---"

if [[ "$SKIP_DISPLAY" -eq 1 ]]; then
    skip "D1 lvgl-sanity-test (gate 5)" "--skip-display"
    skip "D2 hello-lvgl-win-min e2e (gate 6)" "--skip-display"
else
    NAME="D1 lvgl-sanity-test (gate 5, FR-LV-1/2)"
    echo "  [MANUAL] Requires Windows display via WSL interop."
    timeout 20 "$LVGL_WIN_RUNTIME" \
        -c 'import picolet_ui._sanity as t; t.run_lvgl_sanity_test()' \
        > "$WORKDIR/d1.out" 2>&1 || true
    if grep -q "PICOLET_LV_SANITY_OK" "$WORKDIR/d1.out"; then
        pass "$NAME"
        grep "PICOLET_LV_SANITY_OK" "$WORKDIR/d1.out" | sed 's/^/       /'
    else
        fail "$NAME" "expected PICOLET_LV_SANITY_OK; may need display"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/d1.out"; fi
    fi

    NAME="D2 hello-lvgl-win-min e2e (gate 6)"
    E2E_EXE="$FIXTURE/target/windows-x64/hello-lvgl-win-min.exe"
    if ! command -v uv >/dev/null 2>&1; then
        skip "$NAME" "uv not on PATH"
    else
        (
            cd "$FIXTURE" && \
            uv run --project "$REPO_ROOT" python -m picolet_cli build \
                --target windows-x64 \
                --runtime "$LVGL_WIN_RUNTIME" \
                > "$WORKDIR/d2-build.log" 2>&1
        ) || true
        if [[ -f "$E2E_EXE" ]]; then
            timeout 20 "$E2E_EXE" > "$WORKDIR/d2-run.out" 2>&1 || true
            if grep -q "PICOLET_LV_SANITY_OK" "$WORKDIR/d2-run.out"; then
                pass "$NAME"
                grep "PICOLET_LV_SANITY_OK" "$WORKDIR/d2-run.out" | sed 's/^/       /'
            else
                fail "$NAME" "expected PICOLET_LV_SANITY_OK in e2e output"
                if [[ "$VERBOSE" -eq 1 ]]; then
                    cat "$WORKDIR/d2-build.log"
                    cat "$WORKDIR/d2-run.out"
                fi
            fi
        else
            fail "$NAME" "e2e build did not produce $E2E_EXE; see $WORKDIR/d2-build.log"
            if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/d2-build.log"; fi
        fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group E: MXE SDL2 cache gate (gate 13)
# ---------------------------------------------------------------------------

echo "--- Group E: MXE SDL2 cache gate (gate 13) ---"

NAME="E1 sdl2-cache-gate (gate 13)"
if [[ "$SKIP_BUILD" -eq 1 ]]; then
    skip "$NAME" "--skip-build"
elif ! command -v docker >/dev/null 2>&1; then
    skip "$NAME" "docker not on PATH"
else
    # Second build: should log "sdl2: upstream binary cached; skipping download"
    # because the extracted upstream release is already present from the first run.
    if bash "$PKG_ROOT/scripts/build-runtime.sh" \
            --target windows-x64 --variant lvgl \
            > "$WORKDIR/e1.log" 2>&1; then
        if grep -q "sdl2: upstream binary cached; skipping download" "$WORKDIR/e1.log"; then
            pass "$NAME"
        else
            fail "$NAME" "second build did not skip SDL2 download step"
            if [[ "$VERBOSE" -eq 1 ]]; then grep -i sdl2 "$WORKDIR/e1.log" || true; fi
        fi
    else
        fail "$NAME" "second build failed"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -20 "$WORKDIR/e1.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group F: Non-regression (gates 9, 10)
# ---------------------------------------------------------------------------

echo "--- Group F: non-regression ---"

NAME="F1 linux-lvgl-import-ok (gate 9)"
if [[ ! -f "$LVGL_LIN_RUNTIME" ]]; then
    skip "$NAME" "linux lvgl runtime not present"
else
    actual="$(env -u DISPLAY "$LVGL_LIN_RUNTIME" -c 'import lvgl as lv; print("ok")' 2>&1 || true)"
    if [[ "$actual" == "ok" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "linux lvgl regressed: got $(printf '%q' "$actual")"
    fi
fi

NAME="F2 ph11-run (gate 9 full)"
if [[ "$SKIP_NON_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-non-regression"
elif [[ ! -x "$REPO_ROOT/tests/phase-11/run.sh" ]]; then
    skip "$NAME" "tests/phase-11/run.sh not executable"
else
    if bash "$REPO_ROOT/tests/phase-11/run.sh" \
            --skip-rebuild --skip-non-regression \
            > "$WORKDIR/f2.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "PH11 regression; see $WORKDIR/f2.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/f2.log"; fi
    fi
fi

NAME="F3 windows-cli-builds (gate 10)"
if [[ ! -f "$CLI_WIN_RUNTIME" ]]; then
    skip "$NAME" "windows cli runtime not present; build separately if needed"
else
    actual="$(run_win "$CLI_WIN_RUNTIME" -c 'print("cli-ok")')"
    if [[ "$actual" == "cli-ok" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "windows cli smoke failed: $actual"
    fi
fi

NAME="F4 ph10-run (gate 10 webview)"
if [[ "$SKIP_NON_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-non-regression"
elif [[ ! -x "$REPO_ROOT/tests/phase-10/run.sh" ]]; then
    skip "$NAME" "tests/phase-10/run.sh not executable"
else
    if bash "$REPO_ROOT/tests/phase-10/run.sh" --skip-build \
            > "$WORKDIR/f4.log" 2>&1; then
        pass "$NAME"
    else
        fail "$NAME" "PH10 regression; see $WORKDIR/f4.log"
        if [[ "$VERBOSE" -eq 1 ]]; then tail -30 "$WORKDIR/f4.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH12 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
